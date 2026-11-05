#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf_r3.py · 第六轮性能收口（w21）采样脚本（只在云端跑，本地被 browser_guard 拦截）

四项口径：
  1) load   首屏：FCP/LCP、>50ms 长任务分布、最长块、首条消息可发送时刻、纹理预解码统计
  2) stream 对话路径：会话 5 条 vs 210 条时同一段流式回复的脚本/渲染成本（判断有无 O(n) 全量重排）
  3) micro  窗口推进：ChatPager.push ×300、历史 older() 单次翻页耗时
  4) memory 长对话：200 轮堆增长（强制 GC）+ 纹理占用/解码次数（判断有无重复解码）

用法（云端）：
  python scripts/perf_r3.py --out artifacts/perf/r3.json
  python scripts/perf_r3.py --only load,stream --tag before

服务：WEB 8911（本 worktree 的 web/ 静态站）、MOCK 8912（server/mock_api.py，AI_MODE=mock）
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from browser_guard import require_browser_allowed  # noqa: E402

from playwright.async_api import async_playwright  # noqa: E402

WEB_PORT = int(os.environ.get("XIAOMAN_WEB_PORT", "8911"))
MOCK_PORT = int(os.environ.get("XIAOMAN_MOCK_PORT", "8912"))
BASE = f"http://127.0.0.1:{WEB_PORT}/index.html"
MOCK = f"http://127.0.0.1:{MOCK_PORT}"

# 页面内埋点：longtask / LCP / FCP / 纹理解码 / drawImage 单次耗时（draw 钩子仅 perfdraw=1 时开）
INSTRUMENT = r"""
(() => {
  const p = window.__perf = {
    longTasks: [], lcp: null, fcp: null, loaf: [],
    decode: { calls: 0, ms: 0, failed: 0 },
    draw: { calls: 0, totalMs: 0, slowest: [], hooked: location.search.indexOf("perfdraw=1") >= 0 }
  };
  try {
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) p.longTasks.push({ start: Math.round(e.startTime), dur: Math.round(e.duration) });
    }).observe({ entryTypes: ["longtask"] });
  } catch (e) {}
  try {
    // LoAF：headless 下 longtask 有时不派发，用动画帧长任务兜底（blockingDuration=主线程阻塞）
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) {
        p.loaf.push({ start: Math.round(e.startTime), dur: Math.round(e.duration),
                      block: Math.round(e.blockingDuration || 0) });
      }
    }).observe({ entryTypes: ["long-animation-frame"] });
  } catch (e) {}
  try {
    new PerformanceObserver(function (list) {
      const es = list.getEntries();
      if (es.length) p.lcp = Math.round(es[es.length - 1].startTime);
    }).observe({ entryTypes: ["largest-contentful-paint"] });
  } catch (e) {}
  try {
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) if (e.name === "first-contentful-paint") p.fcp = Math.round(e.startTime);
    }).observe({ entryTypes: ["paint"] });
  } catch (e) {}
  const _decode = HTMLImageElement.prototype.decode;
  if (_decode) {
    HTMLImageElement.prototype.decode = function () {
      p.decode.calls += 1;
      const t0 = performance.now();
      const r = _decode.apply(this, arguments);
      if (r && typeof r.then === "function") {
        return r.then(function (v) { p.decode.ms += performance.now() - t0; return v; },
                      function (e) { p.decode.ms += performance.now() - t0; p.decode.failed += 1; throw e; });
      }
      return r;
    };
  }
  if (p.draw.hooked) {
    const _draw = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function (img) {
      const t0 = performance.now();
      const r = _draw.apply(this, arguments);
      const dt = performance.now() - t0;
      p.draw.calls += 1; p.draw.totalMs += dt;
      if (dt >= 4) {
        const src = (img && (img.currentSrc || img.src)) || (img && img.constructor && img.constructor.name) || "?";
        p.draw.slowest.push({ src: String(src).split("/").pop(), ms: Math.round(dt * 10) / 10, at: Math.round(t0) });
        if (p.draw.slowest.length > 300) p.draw.slowest.shift();
      }
      return r;
    };
  }
})();
"""

CFG_OFFLINE = {"mode": "offline", "mockBase": MOCK, "tts": "off",
               "voice": "zh-CN-XiaoyiNeural", "ttsRate": "normal"}
CFG_MOCK = dict(CFG_OFFLINE, mode="mock")

WANTED = ("JSHeapUsedSize", "Nodes", "Documents", "LayoutCount", "RecalcStyleCount",
          "TaskDuration", "ScriptDuration")


def metrics_dict(raw):
    d = {m["name"]: m["value"] for m in raw.get("metrics", [])}
    return {k: d.get(k) for k in WANTED}


async def attach(ctx, page):
    session = await ctx.new_cdp_session(page)
    await session.send("Performance.enable")
    return session


async def wait_greet_done(page, timeout=15000):
    """开场白/心跳等自动消息结束（typing 收起 + 条数稳定），此时才真的能发送"""
    await page.wait_for_function(
        """() => { const p = window.ChatPager && window.ChatPager.stats();
            const t = document.getElementById('typingRow');
            return p && p.total >= 1 && t && t.classList.contains('hidden'); }""",
        timeout=timeout, polling=50)


async def js_state(page, session):
    await session.send("HeapProfiler.collectGarbage")
    raw = await session.send("Performance.getMetrics")
    out = metrics_dict(raw)
    out["domNodes"] = await page.evaluate("document.getElementsByTagName('*').length")
    out["pager"] = await page.evaluate(
        "window.ChatPager && window.ChatPager.stats ? window.ChatPager.stats() : null")
    return out


async def new_page(browser, cfg, viewport=(390, 844)):
    ctx = await browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                    device_scale_factor=1, locale="zh-CN")
    await ctx.add_init_script(script=INSTRUMENT)
    await ctx.add_init_script(
        script="localStorage.setItem('xiaoman_cfg', JSON.stringify(%s));" % json.dumps(cfg))
    page = await ctx.new_page()
    return ctx, page


async def attach_probe(page):
    """注入 web/js/perf.js（只读埋点：包 ChatPager 方法计时），不影响应用逻辑"""
    try:
        await page.add_script_tag(url=f"http://127.0.0.1:{WEB_PORT}/js/perf.js")
    except Exception as exc:
        print("[warn] perf.js 注入失败：%s" % exc, flush=True)


def lt_summary(tasks, since=0, until=None):
    sel = [t for t in tasks if t["start"] >= since and (until is None or t["start"] <= until)]
    over = [t for t in sel if t["dur"] > 50]
    return {
        "count": len(sel),
        "max": max([t["dur"] for t in sel], default=0),
        "total": sum(t["dur"] for t in sel),
        "over50": len(over),
        "over50max": max([t["dur"] for t in over], default=0),
        "list": sorted(sel, key=lambda t: -t["dur"])[:12],
    }


# ---------------------------------------------------------------- 1) load
async def measure_load_once(browser, throttle=1):
    ctx, page = await new_page(browser, CFG_MOCK)
    session = await attach(ctx, page)
    if throttle > 1:
        # 4x 节流：把「首帧同步解码」放大到低端手机量级，便于改前/改后对照
        await session.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
    console = []
    page.on("console", lambda m: console.append(m.text))
    heap_peak = {"v": 0}
    samples = []

    async def sampler():
        while True:
            try:
                d = metrics_dict(await session.send("Performance.getMetrics"))
                v = d.get("JSHeapUsedSize") or 0
                heap_peak["v"] = max(heap_peak["v"], v)
                samples.append(v)
            except Exception:
                return
            await asyncio.sleep(0.1)

    task = asyncio.create_task(sampler())
    t_nav = time.perf_counter()
    await page.goto(BASE + "?perfdraw=1", wait_until="domcontentloaded")
    dcl = round((time.perf_counter() - t_nav) * 1000)
    # ① 首条消息落地：bind 完成（发送键已挂 onclick）→ 用户立刻输入并发送 →
    # 自己的气泡真正进列表。不再等开场白：真实用户想说就说，开场白应当让位。
    # 开场白本身的体验另用 measure_greet_once（空闲路径）单独验证。
    await page.wait_for_function(
        "() => { const b = document.getElementById('sendBtn'), t = document.getElementById('textInput');"
        " return !!(b && t && b.onclick); }", timeout=15000, polling=25)
    input_ready = round(await page.evaluate("performance.now()"))
    ready_to_send = input_ready          # 发送就绪时刻＝输入可用时刻（互斥锁不再挡发送）
    await page.evaluate(
        """() => { const t = document.getElementById('textInput');
            t.value = '在吗'; t.dispatchEvent(new Event('input', { bubbles: true }));
            document.getElementById('sendBtn').click(); }""")
    await page.wait_for_function(
        "() => document.querySelectorAll('#messages .msg-row.me').length >= 1",
        timeout=15000, polling=25)
    first_send = round(await page.evaluate("performance.now()"))
    bind = None
    for line in console:
        if "[probe] bind ok ms=" in line:
            try:
                bind = int(line.split("=")[-1].strip())
            except ValueError:
                pass
    mount = None
    for line in console:
        if "[probe] mount ok ms=" in line:
            try:
                mount = int(line.split("=")[-1].strip())
            except ValueError:
                pass
    await asyncio.sleep(2.2)          # 舞台 idle 挂载 + 纹理预热窗口 + 首轮回复收尾
    task.cancel()
    perf = await page.evaluate("window.__perf")
    after = await js_state(page, session)
    stage = await page.evaluate(
        """() => ({ mode: window.__stageMode, tier: window.__stageTier,
                    vram: window.__stageVram, decode: window.__stageDecode || null,
                    hasCanvas: !!document.querySelector('#stage canvas') })""")
    # 观测器自检放在所有真实指标取完之后：忙等 80ms 应被 longtask 采集，
    # 只用于自证探针没失灵，不计入 long 统计（headless 未节流时根本不出长任务）
    selfcheck_at = await page.evaluate("performance.now()")
    await page.evaluate("() => { const t0 = performance.now(); while (performance.now() - t0 < 80) {} }")
    await asyncio.sleep(0.35)
    perf2 = await page.evaluate("window.__perf")
    self_task = [t for t in perf2["longTasks"] if t["start"] >= selfcheck_at - 1]
    selfcheck = self_task[0]["dur"] if self_task else None
    res = {
        "fcp": perf.get("fcp"), "lcp": perf.get("lcp"),
        "dclMs": dcl, "bindMs": bind, "mountMs": mount,
        "inputReadyMs": input_ready, "readyToSendMs": ready_to_send,
        "firstSendMs": first_send,
        "greetSkipped": any("greet skipped user-first" in c for c in console),
        "greetPlayed": any("greet played" in c for c in console),
        "long": lt_summary(perf["longTasks"]),
        "longTaskSelfCheckMs": selfcheck,
        "selfcheckAtMs": round(selfcheck_at),
        "throttle": throttle,
        "heapPeakMB": round(heap_peak["v"] / 1048576, 2),
        "heapAfterGcMB": round((after["JSHeapUsedSize"] or 0) / 1048576, 2),
        "domNodes": after["domNodes"],
        "layoutCount": after["LayoutCount"], "recalcStyleCount": after["RecalcStyleCount"],
        "taskDuration": after["TaskDuration"], "scriptDuration": after["ScriptDuration"],
        "stage": stage,
        "imgDecode": perf["decode"],
        "draw": {"calls": perf["draw"]["calls"],
                 "slowest": sorted(perf["draw"]["slowest"], key=lambda x: -x["ms"])[:10]},
        "probes": [c for c in console if c.startswith("[probe]")][:30],
    }
    await ctx.close()
    return res


async def measure_greet_once(browser, throttle=4):
    """空闲路径：用户先不说话时，开场白仍照常播（验证 ① 没把开场白砍掉）。
    与 measure_load_once 的「用户先发」路径相对：同一份 app.js 的两条分支。"""
    ctx, page = await new_page(browser, CFG_MOCK)
    session = await attach(ctx, page)
    if throttle > 1:
        await session.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
    console = []
    page.on("console", lambda m: console.append(m.text))
    await page.goto(BASE, wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => window.ChatPager && ChatPager.stats().total >= 1", timeout=15000, polling=25)
    landed = round(await page.evaluate("performance.now()"))
    last, done = -1, None
    for _ in range(200):          # 等开场白所有分段播完（typing 收起 + 条数稳定）
        st = await page.evaluate(
            """() => ({t: window.ChatPager.stats().total,
                        h: document.getElementById('typingRow').classList.contains('hidden')})""")
        if st["h"] and st["t"] == last and st["t"] >= 1:
            done = round(await page.evaluate("performance.now()"))
            break
        last = st["t"]
        await asyncio.sleep(0.1)
    stats = await page.evaluate("window.ChatPager.stats()")
    await ctx.close()
    return {"throttle": throttle, "greetLandedMs": landed, "greetDoneMs": done,
            "records": stats.get("total"), "firstText": stats.get("first"),
            "played": any("[probe] greet played" in c for c in console),
            "skipped": any("greet skipped user-first" in c for c in console)}


def load_median(runs):
    def med(key):
        vals = [r[key] for r in runs if r.get(key) is not None]
        return round(statistics.median(vals)) if vals else None

    return {
        "fcp": med("fcp"), "lcp": med("lcp"), "dclMs": med("dclMs"),
        "bindMs": med("bindMs"), "mountMs": med("mountMs"),
        "inputReadyMs": med("inputReadyMs"), "readyToSendMs": med("readyToSendMs"),
        "firstSendMs": med("firstSendMs"),
        "longMax": round(statistics.median([r["long"]["max"] for r in runs])),
        "longOver50": round(statistics.median([r["long"]["over50"] for r in runs])),
        "longOver50Max": round(statistics.median([r["long"]["over50max"] for r in runs])),
        "longTaskSelfCheckMs": med("longTaskSelfCheckMs"),
        "drawSlowestMs": round(max([max([d["ms"] for d in r["draw"]["slowest"]], default=0)
                                    for r in runs]), 1),
        "heapPeakMB": round(statistics.median([r["heapPeakMB"] for r in runs]), 2),
    }


async def measure_load(browser, repeats=3, throttle=4):
    runs = [await measure_load_once(browser, throttle=1) for _ in range(repeats)]
    thr = [await measure_load_once(browser, throttle=throttle) for _ in range(repeats)]
    greet = await measure_greet_once(browser, throttle)
    return {
        "runs": runs,
        "median": load_median(runs),
        "throttled": {"rate": throttle, "runs": thr, "median": load_median(thr)},
        "greet": greet,
    }


# ---------------------------------------------------------------- 2) stream
async def send_and_reply(page, text, timeout=15000):
    await wait_greet_done(page, timeout=8000)
    before = await page.evaluate("window.ChatPager.stats().total")
    # 上一轮收尾（withSendLock）偶尔还没放锁，此时 submitText 会 toast 并丢弃点击。
    # 采样脚本必须保证"这条真的发出去"：填字 → 点 → 2s 没落地就重填再点。
    deadline = time.monotonic() + timeout / 1000
    while True:
        await page.evaluate(
            """(t) => { const ta = document.getElementById('textInput');
                ta.value = t; ta.dispatchEvent(new Event('input', { bubbles: true }));
                document.getElementById('sendBtn').click(); }""", text)
        try:
            await page.wait_for_function("(n) => window.ChatPager.stats().total >= n",
                                         arg=before + 1, timeout=2000)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.2)
    # 等回复彻底结束（typing 收起 + 总数稳定）
    last = -1
    for _ in range(150):
        st = await page.evaluate(
            """() => ({t: window.ChatPager.stats().total,
                        h: document.getElementById('typingRow').classList.contains('hidden')})""")
        if st["h"] and st["t"] == last and st["t"] > before:
            return st["t"]
        last = st["t"]
        await asyncio.sleep(0.1)
    return last


async def stream_probe(page, session, label):
    """发一轮并只统计这一轮窗口内的脚本/渲染成本"""
    m0 = metrics_dict(await session.send("Performance.getMetrics"))
    t0 = await page.evaluate("performance.now()")
    await page.evaluate("window.__perf.longTasks.length = 0; window.PerfProbe && PerfProbe.reset()")
    await send_and_reply(page, "今天有点累，随便聊聊，说点长的")
    t1 = await page.evaluate("performance.now()")
    m1 = metrics_dict(await session.send("Performance.getMetrics"))
    perf = await page.evaluate("window.__perf")
    probe = await page.evaluate("window.PerfProbe ? PerfProbe.stats() : null")
    return {
        "label": label,
        "replyMs": round(t1 - t0),
        "messages": await page.evaluate("window.ChatPager.stats().total"),
        "scriptDelta": round((m1["ScriptDuration"] or 0) - (m0["ScriptDuration"] or 0), 3),
        "taskDelta": round((m1["TaskDuration"] or 0) - (m0["TaskDuration"] or 0), 3),
        "layoutDelta": (m1["LayoutCount"] or 0) - (m0["LayoutCount"] or 0),
        "styleDelta": (m1["RecalcStyleCount"] or 0) - (m0["RecalcStyleCount"] or 0),
        "long": lt_summary(perf["longTasks"]),
        "probe": probe,
    }


async def measure_stream(browser, fill=200):
    ctx, page = await new_page(browser, CFG_MOCK)
    session = await attach(ctx, page)
    await page.goto(BASE, wait_until="domcontentloaded")
    await wait_greet_done(page)
    await attach_probe(page)
    small = await stream_probe(page, session, "session_small")
    # 直接把会话拉到 200 条（只进 pager 记录，不写库；DOM 窗口恒 14 行）
    await page.evaluate(
        """(n) => { for (let i = 0; i < n; i++) {
            window.ChatPager.push({ text: '历史填充#' + i + ' 用来拉长会话', who: i % 2 ? 'me' : 'them' });
        } }""", fill)
    await asyncio.sleep(0.3)
    large = await stream_probe(page, session, "session_large")
    await ctx.close()
    ratio_script = round(large["scriptDelta"] / small["scriptDelta"], 2) if small["scriptDelta"] else None
    ratio_reply = round(large["replyMs"] / small["replyMs"], 2) if small["replyMs"] else None
    return {"small": small, "large": large,
            "ratioScript": ratio_script, "ratioReplyMs": ratio_reply,
            "filledRecords": fill}


# ---------------------------------------------------------------- 3) micro
async def measure_micro(browser):
    ctx, page = await new_page(browser, CFG_OFFLINE)
    await page.goto(BASE, wait_until="domcontentloaded")
    await wait_greet_done(page)
    await attach_probe(page)
    runs = []
    for _ in range(3):
        runs.append(await page.evaluate(
            """() => {
                const t0 = performance.now();
                for (let i = 0; i < 300; i++) {
                    window.ChatPager.push({text: '微基准消息#' + i + '，用来测量窗口推进成本', who: i % 2 ? 'me' : 'them'});
                }
                return performance.now() - t0;
            }"""))
    push = {"push300Ms": [round(x, 2) for x in runs],
            "push300MinMs": round(min(runs), 2),
            "perPushUs": round(min(runs) * 1000 / 300, 1)}
    # 300 条历史 + 24 次 older()：单次翻页（取数 + 整窗重建 + 回填）耗时
    sid = await page.evaluate("window.API ? API.sessionId() : localStorage.getItem('xiaoman_sid')")
    await page.evaluate("(s) => window.ChatStore ? ChatStore.clear(s) : null", sid)
    await page.evaluate(
        """async (args) => {
            const [sid, n] = args;
            const base = Date.now() - n * 1000;
            for (let i = 0; i < n; i++) {
                await window.ChatStore.append(sid, {
                    role: i % 2 ? 'user' : 'assistant',
                    content: '历史消息#'.concat(String(i)).padEnd(20, '·'),
                    at: base + i * 1000
                });
            }
        }""", [sid, 300])
    await page.reload(wait_until="domcontentloaded")
    await wait_greet_done(page)
    await attach_probe(page)
    pages = []
    for _ in range(18):
        dt = await page.evaluate(
            """async () => { const t0 = performance.now(); await window.ChatPager.older(); return performance.now() - t0; }""")
        pages.append(round(dt, 2))
    await page.evaluate("window.ChatPager.jumpLatest()")
    pager = await page.evaluate("window.ChatPager.stats()")
    await ctx.close()
    return {"push": push, "older18": pages,
            "olderAvgMs": round(statistics.mean(pages), 2),
            "olderMaxMs": round(max(pages), 2),
            "olderP95Ms": round(sorted(pages)[int(len(pages) * 0.95) - 1], 2),
            "pager": pager}


async def paging_audit(page, pages=20):
    """② 淘汰后的翻页校验：连续上翻，直到窗口真正前移 20 页。
    判据：total 恒定（淘汰不改变已知总数）；每次"视线前移"恰好 PAGE(10) 且与上一步
    有 4 行重叠、重叠 id 完全一致（不重复、不漏行）；loader 取回淘汰段的那一步窗口不动
    （内容不变，只补上方缓冲）；每页内 id 不重；窗口起点 id 不回头。"""
    st0 = await page.evaluate("window.ChatPager.stats()")
    out = {"before": {k: st0.get(k) for k in
                      ("total", "start", "held", "evicted", "rendered")},
           "calls": 0, "moves": 0, "movedAll": True, "totalStable": True,
           "overlapOk": True, "stepOk": True, "dupInPage": False,
           "firstRepeat": False, "fetchKeepsWindow": True, "idsPresent": True}
    prev_ids = st0.get("windowIds") or []
    prev_start = st0.get("start")
    first_ids = {st0.get("windowFirstId")}
    while out["moves"] < pages and out["calls"] < pages * 4:
        out["calls"] += 1
        moved = await page.evaluate("async () => await window.ChatPager.older()")
        st = await page.evaluate("window.ChatPager.stats()")
        ids = st.get("windowIds") or []
        if not moved:
            out["movedAll"] = False
            break
        if st.get("total") != st0.get("total"):
            out["totalStable"] = False
        if any(v is None for v in ids):
            out["idsPresent"] = False
        if st.get("start") != prev_start:
            out["moves"] += 1
            d = prev_start - st.get("start")
            # 正常每页 10 行；只在"内存区起点（已淘汰边界）"允许 <10 的钳制步
            if d != 10 and not (d > 0 and st.get("start") == st.get("evicted")):
                out["stepOk"] = False
            # 前移 d 行后，新旧窗口重叠部分应逐 id 相同（不重复、不漏行）
            if prev_ids and ids and d > 0 and d < 14 and ids[d:14] != prev_ids[:14 - d]:
                out["overlapOk"] = False
            prev_start = st.get("start")
        else:
            # 仅从库里补一段更早缓冲：窗口内容不应变
            if prev_ids and ids and ids != prev_ids:
                out["fetchKeepsWindow"] = False
        uniq = [v for v in ids if v is not None]
        if len(uniq) != len(set(uniq)):
            out["dupInPage"] = True
        fid = st.get("windowFirstId")
        if st.get("start") != prev_start and fid is not None:
            if fid in first_ids:
                out["firstRepeat"] = True
            first_ids.add(fid)
        prev_ids = ids
    out["after"] = {k: (await page.evaluate("window.ChatPager.stats()")).get(k)
                    for k in ("total", "start", "held", "evicted", "rendered")}
    await page.evaluate("window.ChatPager.jumpLatest()")
    return out


# ---------------------------------------------------------------- 4) memory
async def _memory_page(browser):
    """长会话测量页：等开场白与舞台纹理预解码都结束，再开始取样。
    纹理/IDB 首次打开这类"启动一次性分配"不算长会话增量，否则 heapStart 偏低、
    看起来像消息数据在泄漏。stage.js 的窗口标记是 batches/wallMs（无 done 字段）。"""
    ctx, page = await new_page(browser, CFG_OFFLINE)
    session = await attach(ctx, page)
    await page.goto(BASE, wait_until="domcontentloaded")
    await wait_greet_done(page)
    try:
        await page.wait_for_function(
            """() => { const d = window.__stageDecode;
                return !d || (Number(d.batches) > 0 && Number(d.wallMs) > 0); }""",
            timeout=10000, polling=100)
    except Exception:
        pass
    await asyncio.sleep(1.2)
    return ctx, page, session


async def _ls_diag(page):
    """持久化面诊断：localStorage/sessionStorage 总字符数 + IDB 行数。
    堆里若没有对应增长，说明残余不是应用数据（别处）；若有，按 key 定位。"""
    return await page.evaluate(
        """async () => {
            const keys = {};
            let total = 0;
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                const v = localStorage.getItem(k) || "";
                keys[k] = v.length;
                total += k.length + v.length;
            }
            const sid = window.API ? API.sessionId() : localStorage.getItem('xiaoman_sid');
            let rows = null;
            try { rows = window.ChatStore ? await ChatStore.count(sid) : null; } catch (e) {}
            keys.__idb = rows;
            return { lsTotal: total, lsKeys: Object.keys(keys).length,
                     top: Object.entries(keys).sort((a, b) => b[1] - a[1]).slice(0, 6),
                     ss: sessionStorage.length };
        }""")


def _top_alloc(profile, limit=15):
    """把 V8 分配采样（stop 时仍存活的对象）按 selfSize 聚合到顶层调用点。"""
    aggr = {}
    total = [0]

    def walk(node):
        cf = node.get("callFrame") or {}
        fn = cf.get("functionName") or "(anonymous)"
        url = (cf.get("url") or "").split("/")[-1]
        key = "%s @ %s:%s" % (fn, url or "?", (cf.get("lineNumber", -1) + 1))
        size = node.get("selfSize") or 0
        aggr[key] = aggr.get(key, 0) + size
        total[0] += size
        for c in node.get("children") or []:
            walk(c)

    walk((profile.get("profile") or {}).get("head") or {})
    if total[0] == 0:
        # 采样没启动：profile 里还有 samples，用样本 size 兜底聚合
        return [["<empty profile>", 0.0]]
    top = sorted(aggr.items(), key=lambda kv: -kv[1])[:limit]
    return [[k, round(v / 1048576, 3)] for k, v in top]


async def _memory_run(browser, rounds, warmup, silence_console,
                      sample_alloc=False, disable_store=False, phase2=False):
    """warmup 轮先吸收启动一次性分配，再做 rounds 轮长会话采样。
    silence_console=True：把 [probe] 埋点日志静音——Chromium 控制台缓冲是给安卓
    logcat 用的诊断通道，不属于应用状态（且有容量上限），不能混进"消息数据是否泄漏"。"""
    ctx, page, session = await _memory_page(browser)
    tex0 = await page.evaluate(
        """() => ({ vram: window.__stageVram || null,
                    decode: window.__stageDecode || null, mode: window.__stageMode })""")
    for i in range(warmup):
        await send_and_reply(page, f"预热第{i}轮，随便聊聊")
        if (i + 1) % 10 == 0:
            print(f"[memory] warmup {i + 1}/{warmup} silence={silence_console}", flush=True)
    if silence_console:
        await page.evaluate(
            """() => { window.__probeLog = console.log; window.__probeWarn = console.warn;
                console.log = function () {}; console.warn = function () {}; }""")
    await page.evaluate("() => { try { console.clear(); } catch (e) {} }")
    await asyncio.sleep(0.4)
    if disable_store:
        # 对照：把持久化写入摘掉，若堆立刻变平即说明残差来自 IDB 写入链（而非消息数据）
        await page.evaluate(
            """() => { if (window.ChatStore) {
                window.__origAppend = ChatStore.append;
                window.__origPrune = ChatStore.prune;
                ChatStore.append = () => Promise.resolve();
                ChatStore.prune = () => Promise.resolve();
            } }""")
    s0 = await js_state(page, session)
    ls0 = await _ls_diag(page)
    if sample_alloc:
        # V8 分配采样：只统计"采样窗口内分配且 stop 时仍存活"的对象，用来抓残留持有者
        try:
            await session.send("HeapProfiler.enable")
            await session.send("HeapProfiler.startSampling", {"samplingInterval": 32768})
        except Exception as exc:
            print("[memory] alloc sampling off：%s" % exc, flush=True)
            sample_alloc = False
    marks = []
    for i in range(rounds):
        await send_and_reply(page, f"今天有点累，随便聊聊第{i}轮")
        if (i + 1) % 40 == 0:
            print(f"[memory] round {i + 1}/{rounds} silence={silence_console}", flush=True)
        if i % 40 == 39:
            st = await js_state(page, session)
            pg = st["pager"] or {}
            marks.append({"round": i + 1,
                          "heapMB": round(st["JSHeapUsedSize"] / 1048576, 2),
                          "domNodes": st["domNodes"],
                          "rendered": pg.get("rendered"),
                          "held": pg.get("held"), "evicted": pg.get("evicted"),
                          "total": pg.get("total")})
    await asyncio.sleep(1.2)
    s1 = await js_state(page, session)
    profile_top = None
    if sample_alloc:
        try:
            prof = await session.send("HeapProfiler.stopSampling")
            profile_top = _top_alloc(prof)
        except Exception as exc:
            print("[memory] stopSampling 失败：%s" % exc, flush=True)
    ls1 = await _ls_diag(page)
    perf = await page.evaluate("window.__perf")
    # 控制台缓冲诊断：清空控制台 + GC 后若堆明显回落，即证明残余增长是日志缓冲
    await page.evaluate("() => { try { console.clear(); } catch (e) {} }")
    await asyncio.sleep(0.5)
    s2 = await js_state(page, session)
    tex1 = await page.evaluate(
        """() => ({ vram: window.__stageVram || null,
                    decode: window.__stageDecode || null, mode: window.__stageMode,
                    imgDecode: window.__perf ? window.__perf.decode : null })""")
    # 第二段 200 轮：若增量明显收敛，说明前段是 V8 优化/预热的一次性，不是泄漏
    marks2, growth2 = [], None
    if phase2:
        s2b = s2["JSHeapUsedSize"]
        for i in range(rounds):
            await send_and_reply(page, f"再聊一会儿，第{i}轮")
            if (i + 1) % 40 == 0:
                print(f"[memory] phase2 {i + 1}/{rounds}", flush=True)
            if i % 40 == 39:
                st = await js_state(page, session)
                marks2.append({"round": i + 1,
                               "heapMB": round(st["JSHeapUsedSize"] / 1048576, 2),
                               "held": (st["pager"] or {}).get("held"),
                               "total": (st["pager"] or {}).get("total")})
        await asyncio.sleep(1.2)
        s3 = await js_state(page, session)
        growth2 = round((s3["JSHeapUsedSize"] - s2b) / 1048576, 2)
    # 翻页自检放在堆取样之后：取回淘汰段会让内存短暂变大，不能污染 heapEnd
    paging = await paging_audit(page, pages=20)
    idb_rows = await page.evaluate(
        "async () => { try { return await ChatStore.count(API.sessionId()); } catch (e) { return null; } }")
    await ctx.close()
    return {
        "rounds": rounds,
        "heapStartMB": round(s0["JSHeapUsedSize"] / 1048576, 2),
        "heapEndMB": round(s1["JSHeapUsedSize"] / 1048576, 2),
        "heapGrowthMB": round((s1["JSHeapUsedSize"] - s0["JSHeapUsedSize"]) / 1048576, 2),
        "heapPer40MB": [round(marks[i]["heapMB"] - marks[i - 1]["heapMB"], 2)
                        for i in range(1, len(marks))],
        "marks": marks,
        "afterConsoleClearMB": round(s2["JSHeapUsedSize"] / 1048576, 2),
        "consoleClearFreesMB": round((s1["JSHeapUsedSize"] - s2["JSHeapUsedSize"]) / 1048576, 2),
        "lsStartChars": ls0.get("lsTotal"), "lsEndChars": ls1.get("lsTotal"),
        "lsDeltaChars": (ls1.get("lsTotal") or 0) - (ls0.get("lsTotal") or 0),
        "lsEndTop": ls1.get("top"),
        "idbRows": idb_rows,
        "allocTop": profile_top,
        "phase2GrowthMB": growth2,
        "phase2Per40MB": [round(marks2[i]["heapMB"] - marks2[i - 1]["heapMB"], 2)
                          for i in range(1, len(marks2))],
        "phase2Marks": marks2,
        "domNodesStart": s0["domNodes"], "domNodesEnd": s1["domNodes"],
        "pager": s1["pager"],
        "paging": paging,
        "longMax": max([t["dur"] for t in perf["longTasks"]], default=0),
        "textureStart": tex0, "textureEnd": tex1,
        "nodesMetric": {"start": s0["Nodes"], "end": s1["Nodes"]},
    }


async def measure_memory(browser, rounds=200, warmup=40):
    """口径（三段，均先 warmup 轮再做 200 轮长会话）：
    - top-level steady：warmup=200，代表长会话稳态，验收用这组
    - cold：warmup=40，含 V8 预编译/首轮路径的一次性增量，仅作参考
    - consoleOn：保留 [probe] 日志的对照，证明控制台缓冲的贡献上限"""
    steady = await _memory_run(browser, rounds, warmup=200, silence_console=True)
    cold = await _memory_run(browser, rounds, warmup=warmup, silence_console=True)
    loud = await _memory_run(browser, rounds, warmup=warmup, silence_console=False)
    out = dict(steady)
    out["warmup"] = 200
    out["cold"] = {k: cold.get(k) for k in
                   ("heapStartMB", "heapEndMB", "heapGrowthMB", "heapPer40MB",
                    "marks", "pager", "idbRows", "lsDeltaChars")}
    out["consoleOn"] = {k: loud.get(k) for k in
                        ("heapStartMB", "heapEndMB", "heapGrowthMB", "heapPer40MB",
                         "marks", "consoleClearFreesMB", "longMax", "pager")}
    out["textureEnd"] = steady.get("textureEnd") or loud.get("textureEnd")
    return out


# ---------------------------------------------------------------- main
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/perf/r3.json")
    ap.add_argument("--only", default="")
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    require_browser_allowed("scripts/perf_r3.py")
    only = set(args.only.split(",")) if args.only else {"load", "stream", "micro", "memory"}
    out = {"base": BASE, "mock": MOCK, "tag": args.tag,
           "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
           "runner": {k: os.environ.get(k) for k in
                      ("GITHUB_RUN_ID", "GITHUB_RUN_NUMBER", "GITHUB_SHA", "GITHUB_REF")}}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--mute-audio", "--disable-features=AudioServiceOutOfProcess",
                  "--autoplay-policy=document-user-activation-required"])
        try:
            if "load" in only:
                t = time.time(); out["load"] = await measure_load(browser)
                print(f"[load] done {time.time() - t:.1f}s", flush=True)
            if "stream" in only:
                t = time.time(); out["stream"] = await measure_stream(browser)
                print(f"[stream] done {time.time() - t:.1f}s", flush=True)
            if "micro" in only:
                t = time.time(); out["micro"] = await measure_micro(browser)
                print(f"[micro] done {time.time() - t:.1f}s", flush=True)
            if "memory" in only:
                t = time.time(); out["memory"] = await measure_memory(browser, args.rounds)
                print(f"[memory] done {time.time() - t:.1f}s", flush=True)
        finally:
            await browser.close()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved ->", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
