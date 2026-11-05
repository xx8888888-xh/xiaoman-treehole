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
    longTasks: [], lcp: null, fcp: null,
    decode: { calls: 0, ms: 0, failed: 0 },
    draw: { calls: 0, totalMs: 0, slowest: [], hooked: location.search.indexOf("perfdraw=1") >= 0 }
  };
  try {
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) p.longTasks.push({ start: Math.round(e.startTime), dur: Math.round(e.duration) });
    }).observe({ entryTypes: ["longtask"] });
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
async def measure_load_once(browser):
    ctx, page = await new_page(browser, CFG_MOCK)
    session = await attach(ctx, page)
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
    # 「首条消息可发送」：bind 完成（发送键已挂 onclick）→ 等开场白说完 →
    # 真发一条并等自己的气泡落地，取全程时刻。
    await page.wait_for_function(
        "() => { const b = document.getElementById('sendBtn'), t = document.getElementById('textInput');"
        " return !!(b && t && b.onclick); }", timeout=15000, polling=25)
    input_ready = round(await page.evaluate("performance.now()"))
    await wait_greet_done(page)
    ready_to_send = round(await page.evaluate("performance.now()"))
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
    await asyncio.sleep(2.2)          # 舞台 idle 挂载 + 纹理预热窗口
    task.cancel()
    perf = await page.evaluate("window.__perf")
    after = await js_state(page, session)
    stage = await page.evaluate(
        """() => ({ mode: window.__stageMode, tier: window.__stageTier,
                    vram: window.__stageVram, decode: window.__stageDecode || null,
                    hasCanvas: !!document.querySelector('#stage canvas') })""")
    res = {
        "fcp": perf.get("fcp"), "lcp": perf.get("lcp"),
        "dclMs": dcl, "bindMs": bind, "mountMs": mount,
        "inputReadyMs": input_ready, "readyToSendMs": ready_to_send,
        "firstSendMs": first_send,
        "long": lt_summary(perf["longTasks"]),
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


async def measure_load(browser, repeats=3):
    runs = [await measure_load_once(browser) for _ in range(repeats)]

    def med(key):
        vals = [r[key] for r in runs if r.get(key) is not None]
        return round(statistics.median(vals)) if vals else None

    return {
        "runs": runs,
        "median": {
            "fcp": med("fcp"), "lcp": med("lcp"), "dclMs": med("dclMs"),
            "bindMs": med("bindMs"), "mountMs": med("mountMs"),
            "inputReadyMs": med("inputReadyMs"), "readyToSendMs": med("readyToSendMs"),
            "firstSendMs": med("firstSendMs"),
            "longMax": round(statistics.median([r["long"]["max"] for r in runs])),
            "longOver50": round(statistics.median([r["long"]["over50"] for r in runs])),
            "longOver50Max": round(statistics.median([r["long"]["over50max"] for r in runs])),
            "heapPeakMB": round(statistics.median([r["heapPeakMB"] for r in runs]), 2),
        },
    }


# ---------------------------------------------------------------- 2) stream
async def send_and_reply(page, text, timeout=15000):
    await wait_greet_done(page, timeout=8000)
    before = await page.evaluate("window.ChatPager.stats().total")
    await page.evaluate(
        """(t) => { const ta = document.getElementById('textInput');
            ta.value = t; ta.dispatchEvent(new Event('input', { bubbles: true }));
            document.getElementById('sendBtn').click(); }""", text)
    await page.wait_for_function("(n) => window.ChatPager.stats().total >= n",
                                 arg=before + 1, timeout=timeout)
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


# ---------------------------------------------------------------- 4) memory
async def measure_memory(browser, rounds=200):
    ctx, page = await new_page(browser, CFG_OFFLINE)
    session = await attach(ctx, page)
    await page.goto(BASE, wait_until="domcontentloaded")
    await wait_greet_done(page)
    await asyncio.sleep(1.5)
    s0 = await js_state(page, session)
    tex0 = await page.evaluate(
        """() => ({ vram: window.__stageVram || null,
                    decode: window.__stageDecode || null, mode: window.__stageMode })""")
    marks = []
    for i in range(rounds):
        await send_and_reply(page, f"今天有点累，随便聊聊第{i}轮")
        if i % 40 == 39:
            st = await js_state(page, session)
            marks.append({"round": i + 1,
                          "heapMB": round(st["JSHeapUsedSize"] / 1048576, 2),
                          "domNodes": st["domNodes"],
                          "rendered": (st["pager"] or {}).get("rendered")})
    await asyncio.sleep(1.2)
    s1 = await js_state(page, session)
    tex1 = await page.evaluate(
        """() => ({ vram: window.__stageVram || null,
                    decode: window.__stageDecode || null, mode: window.__stageMode,
                    imgDecode: window.__perf ? window.__perf.decode : null })""")
    perf = await page.evaluate("window.__perf")
    await ctx.close()
    return {
        "rounds": rounds,
        "heapStartMB": round(s0["JSHeapUsedSize"] / 1048576, 2),
        "heapEndMB": round(s1["JSHeapUsedSize"] / 1048576, 2),
        "heapGrowthMB": round((s1["JSHeapUsedSize"] - s0["JSHeapUsedSize"]) / 1048576, 2),
        "heapPer40MB": [marks[i]["heapMB"] - marks[i - 1]["heapMB"] for i in range(1, len(marks))],
        "marks": marks,
        "domNodesStart": s0["domNodes"], "domNodesEnd": s1["domNodes"],
        "pager": s1["pager"],
        "longMax": max([t["dur"] for t in perf["longTasks"]], default=0),
        "textureStart": tex0, "textureEnd": tex1,
        "nodesMetric": {"start": s0["Nodes"], "end": s1["Nodes"]},
    }


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
