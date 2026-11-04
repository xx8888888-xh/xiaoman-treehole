#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure.py · 树洞前端性能采样（Playwright + CDP，只读）

覆盖：
  1) 首屏：FCP / LCP / 可交互（bind ok）/ 长任务 / JS 堆峰值 / 主线程占用
  2) 连续 200 轮对话：长任务、JS 堆增长（强制 GC 前后）、DOM 节点数
  3) 300 条历史滚动：帧率、掉帧（>33ms 间隔）
  4) 耗电：静止 60s / 连聊 60s 的 rAF 排帧数、定时器触发次数、WebSocket 数

用法：
  python tools/perf/measure.py --out artifacts/perf/baseline.json
  python tools/perf/measure.py --only load --out artifacts/perf/load.json

服务：WEB 8911（本 worktree 静态站），MOCK 8912（本 worktree mock_api）
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time

from playwright.async_api import async_playwright

WEB_PORT = int(os.environ.get("XIAOMAN_WEB_PORT", "8911"))
MOCK_PORT = int(os.environ.get("XIAOMAN_MOCK_PORT", "8912"))
BASE = f"http://127.0.0.1:{WEB_PORT}/index.html"
MOCK = f"http://127.0.0.1:{MOCK_PORT}"

# 页面内埋点：rAF 排帧数、定时器真实触发次数、longtask、FCP/LCP、WebSocket 实例
INSTRUMENT = r"""
(() => {
  const perf = window.__perf = {
    raf: 0, timeoutFires: 0, intervalFires: 0, wsCount: 0,
    longTasks: [], lcp: null, fcp: null, bindMs: null
  };
  const _raf = window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : null;
  if (_raf) window.requestAnimationFrame = function (cb) { perf.raf++; return _raf(cb); };
  const _st = window.setTimeout, _si = window.setInterval;
  window.setTimeout = function (fn, ms) {
    const args = Array.prototype.slice.call(arguments, 2);
    return _st(function () { perf.timeoutFires++; if (typeof fn === "function") fn.apply(null, args); }, ms);
  };
  window.setInterval = function (fn, ms) {
    const args = Array.prototype.slice.call(arguments, 2);
    return _si(function () { perf.intervalFires++; if (typeof fn === "function") fn.apply(null, args); }, ms);
  };
  const _WS = window.WebSocket;
  if (_WS) {
    window.WebSocket = function (u, p) { perf.wsCount++; return new _WS(u, p); };
    window.WebSocket.prototype = _WS.prototype;
  }
  try {
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) {
        perf.longTasks.push({ start: Math.round(e.startTime), dur: Math.round(e.duration) });
      }
    }).observe({ entryTypes: ["longtask"] });
  } catch (e) {}
  try {
    new PerformanceObserver(function (list) {
      const es = list.getEntries();
      if (es.length) perf.lcp = Math.round(es[es.length - 1].startTime);
    }).observe({ entryTypes: ["largest-contentful-paint"] });
  } catch (e) {}
  try {
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) {
        if (e.name === "first-contentful-paint") perf.fcp = Math.round(e.startTime);
      }
    }).observe({ entryTypes: ["paint"] });
  } catch (e) {}
})();
"""

CFG_OFFLINE = {"mode": "offline", "mockBase": MOCK, "tts": "off",
               "voice": "zh-CN-XiaoyiNeural", "ttsRate": "normal"}
CFG_MOCK = dict(CFG_OFFLINE, mode="mock")

WANTED_METRICS = ("JSHeapUsedSize", "JSHeapTotalSize", "Nodes", "Documents",
                  "LayoutCount", "RecalcStyleCount", "TaskDuration", "ScriptDuration")


def metrics_dict(raw):
    d = {m["name"]: m["value"] for m in raw.get("metrics", [])}
    return {k: d.get(k) for k in WANTED_METRICS}


async def attach(ctx, page):
    session = await ctx.new_cdp_session(page)
    await session.send("Performance.enable")
    return session


async def js_state(page, session):
    await session.send("HeapProfiler.collectGarbage")
    raw = await session.send("Performance.getMetrics")
    out = metrics_dict(raw)
    out["domNodes"] = await page.evaluate(
        "document.getElementsByTagName('*').length")
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


async def wait_bind(page, probe, timeout=10.0):
    """等 app.js init 的 [probe] bind ok 控制台行（输入区已可交互）"""
    end = time.time() + timeout
    while time.time() < end:
        for line in list(probe):
            if "[probe] bind ok ms=" in line:
                try:
                    return int(line.split("=")[-1].strip())
                except ValueError:
                    return None
        await asyncio.sleep(0.03)
    return None


async def measure_load(browser):
    ctx, page = await new_page(browser, CFG_MOCK)
    session = await attach(ctx, page)
    console = []

    def on_console(msg):
        txt = msg.text
        console.append(txt)
        if "[probe] bind ok ms=" in txt:
            pass

    page.on("console", on_console)

    heap_peak = {"v": 0}
    samples = []

    async def sampler():
        while True:
            try:
                raw = await session.send("Performance.getMetrics")
                d = metrics_dict(raw)
                v = d.get("JSHeapUsedSize") or 0
                heap_peak["v"] = max(heap_peak["v"], v)
                samples.append(v)
            except Exception:
                return
            await asyncio.sleep(0.1)

    task = asyncio.create_task(sampler())
    nav0 = time.perf_counter()
    await page.goto(BASE, wait_until="domcontentloaded")
    dcl = round((time.perf_counter() - nav0) * 1000)
    bind = await wait_bind(page, console)
    # 舞台走 requestIdleCallback(1.2s 超时) 挂载，留出稳定窗口
    await asyncio.sleep(2.0)
    task.cancel()

    perf = await page.evaluate("window.__perf")
    long_tasks = perf["longTasks"]
    after = await js_state(page, session)
    result = {
        "fcp": perf.get("fcp"),
        "lcp": perf.get("lcp"),
        "bindMs": bind,
        "navToDomContentLoaded": dcl,
        "longTasks": long_tasks,
        "longCount": len(long_tasks),
        "longMax": max([t["dur"] for t in long_tasks], default=0),
        "longAvg": round(statistics.mean([t["dur"] for t in long_tasks]), 1) if long_tasks else 0,
        "longTotal": sum(t["dur"] for t in long_tasks),
        "heapPeakMB": round(heap_peak["v"] / 1048576, 2),
        "heapSamplesMB": [round(v / 1048576, 2) for v in samples[:: max(1, len(samples) // 40)]],
        "afterGc": {k: after[k] for k in ("JSHeapUsedSize", "Nodes", "Documents",
                                          "LayoutCount", "RecalcStyleCount",
                                          "TaskDuration", "ScriptDuration")},
        "domNodes": after["domNodes"],
        "hasCanvas": await page.evaluate("!!document.querySelector('#stage canvas')"),
        "consoleProbe": [c for c in console if c.startswith("[probe]")][:40],
    }
    await ctx.close()
    return result


async def send_one(page, text):
    await page.evaluate(
        """(t) => {
            const ta = document.getElementById('textInput');
            ta.value = t;
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            document.getElementById('sendBtn').click();
        }""", text)


async def send_round(page, text, total_wait=6000):
    """发一轮并等回复彻底结束（typing 收起 + 总条数稳定）；遇互斥锁丢弃则重试。"""
    before = await page.evaluate("window.ChatPager.stats().total")
    await send_one(page, text)
    try:
        await page.wait_for_function(
            "(n) => window.ChatPager.stats().total >= n", arg=before + 1, timeout=total_wait)
    except Exception:
        await send_one(page, text)
        await page.wait_for_function(
            "(n) => window.ChatPager.stats().total >= n", arg=before + 1, timeout=total_wait)
    last = -1
    for _ in range(120):
        st = await page.evaluate(
            """() => ({t: window.ChatPager.stats().total,
                        h: document.getElementById('typingRow').classList.contains('hidden')})""")
        if st["h"] and st["t"] == last and st["t"] > before:
            return st["t"]
        last = st["t"]
        await asyncio.sleep(0.1)
    return last


async def measure_rounds(browser, rounds=200):
    ctx, page = await new_page(browser, CFG_OFFLINE)
    session = await attach(ctx, page)
    await page.goto(BASE, wait_until="domcontentloaded")
    await asyncio.sleep(1.5)
    s0 = await js_state(page, session)
    t_start = await page.evaluate("performance.now()")
    marks = []
    phase = []
    for i in range(rounds):
        await send_round(page, f"今天有点累，随便聊聊第{i}轮")
        if i % 40 == 39:
            st = await js_state(page, session)
            marks.append({"round": i + 1, "heapMB": round(st["JSHeapUsedSize"] / 1048576, 2),
                          "domNodes": st["domNodes"],
                          "rendered": (st["pager"] or {}).get("rendered")})
    # 等最后一条回复落地 + 收尾
    await asyncio.sleep(1.2)
    perf = await page.evaluate("window.__perf")
    t_end = await page.evaluate("performance.now()")
    lt = [t for t in perf["longTasks"] if t_start <= t["start"] <= t_end]
    s1 = await js_state(page, session)
    dom = await page.evaluate(
        """() => ({
            rows: document.querySelectorAll('#messages .msg-row').length,
            bubbles: document.querySelectorAll('#messages .bubble').length,
            listenersProbe: window.__perf ? 1 : 0
        })""")
    result = {
        "rounds": rounds,
        "durationSec": round((t_end - t_start) / 1000, 1),
        "longTasks": lt,
        "longCount": len(lt),
        "longMax": max([t["dur"] for t in lt], default=0),
        "longAvg": round(statistics.mean([t["dur"] for t in lt]), 1) if lt else 0,
        "longTotal": sum(t["dur"] for t in lt),
        "perRoundLongTaskMax": round(max([t["dur"] for t in lt], default=0)),
        "heapStartMB": round(s0["JSHeapUsedSize"] / 1048576, 2),
        "heapEndAfterGcMB": round(s1["JSHeapUsedSize"] / 1048576, 2),
        "heapGrowthMB": round((s1["JSHeapUsedSize"] - s0["JSHeapUsedSize"]) / 1048576, 2),
        "marks": marks,
        "domRows": dom["rows"],
        "domBubbles": dom["bubbles"],
        "pager": s1["pager"],
        "nodesMetric": s1["Nodes"],
        "nodesStart": s0["Nodes"],
        "scriptDurationDelta": round((s1["ScriptDuration"] or 0) - (s0["ScriptDuration"] or 0), 3),
        "taskDurationDelta": round((s1["TaskDuration"] or 0) - (s0["TaskDuration"] or 0), 3),
    }
    await ctx.close()
    return result


async def measure_micro(browser):
    """微基准：连续 push 300 条（真实 buildRow），测窗口推进的单次成本。"""
    ctx, page = await new_page(browser, CFG_OFFLINE)
    await page.goto(BASE, wait_until="domcontentloaded")
    await asyncio.sleep(1.5)
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
    await ctx.close()
    return {"push300Ms": [round(x, 2) for x in runs],
            "push300MinMs": round(min(runs), 2),
            "perPushUs": round(min(runs) * 1000 / 300, 1)}


async def seed_history(page, n=300):
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
        }""", [sid, n])
    return sid


async def measure_scroll(browser, n=300):
    ctx, page = await new_page(browser, CFG_OFFLINE)
    session = await attach(ctx, page)
    await page.goto(BASE, wait_until="domcontentloaded")
    await asyncio.sleep(1.5)
    sid = await seed_history(page, n)
    await page.reload(wait_until="domcontentloaded")
    await asyncio.sleep(1.5)
    await page.evaluate("window.__perf.longTasks.length = 0; window.__perf.raf = 0;")
    # 起帧记录
    await page.evaluate(
        """() => {
            window.__frames = [];
            const loop = (t) => { window.__frames.push(t); requestAnimationFrame(loop); };
            requestAnimationFrame(loop);
        }""")
    t0 = await page.evaluate("performance.now()")
    # 窗口仅 14 行时容器常常不可滚（scrollTop 恒 0），因此显式 older() 走同一条
    # 「翻页取数 + 整窗重建 + scrollTop 回填」热路径：24 页覆盖全部 300 条历史。
    box = await page.evaluate(
        """() => { const s = document.getElementById('chatScroll');
                   return {h: s.scrollHeight, c: s.clientHeight, t: s.scrollTop}; }""")
    for i in range(24):
        await page.evaluate("window.ChatPager.older()")
        await asyncio.sleep(0.2)
    await page.evaluate("window.ChatPager.jumpLatest()")
    await asyncio.sleep(0.2)
    frames = await page.evaluate("window.__frames")
    perf = await page.evaluate("window.__perf")
    t1 = await page.evaluate("performance.now()")
    gaps = [round(b - a, 1) for a, b in zip(frames, frames[1:])]
    dur = (frames[-1] - frames[0]) if len(frames) > 1 else 0
    fps = round((len(frames) - 1) / dur * 1000, 1) if dur else 0
    dropped = [g for g in gaps if g > 33.4]
    lt = [t for t in perf["longTasks"] if t0 <= t["start"] <= t1]
    stats = await page.evaluate("window.ChatPager.stats()")
    result = {
        "historySeeded": n,
        "scrollBox": box,
        "frames": len(frames),
        "durationSec": round(dur / 1000, 2),
        "fps": fps,
        "frameGapMax": max(gaps) if gaps else 0,
        "frameGapP95": round(statistics.quantiles(gaps, n=20)[18], 1) if len(gaps) > 20 else None,
        "droppedFrames": len(dropped),
        "droppedTop": sorted(dropped, reverse=True)[:10],
        "longTasks": lt,
        "longCount": len(lt),
        "longMax": max([t["dur"] for t in lt], default=0),
        "longTotal": sum(t["dur"] for t in lt),
        "pagerStats": stats,
        "sid": sid,
    }
    await ctx.close()
    return result


async def measure_power(browser):
    ctx, page = await new_page(browser, CFG_OFFLINE)
    await page.goto(BASE, wait_until="domcontentloaded")
    await asyncio.sleep(1.5)
    await asyncio.sleep(2.0)
    # 静止 60s
    await page.evaluate(
        """() => { const p = window.__perf; p.raf = 0; p.timeoutFires = 0;
                   p.intervalFires = 0; p.wsCount = 0; p.longTasks.length = 0; }""")
    t0 = await page.evaluate("performance.now()")
    await asyncio.sleep(60)
    idle = await page.evaluate("window.__perf")
    idle_dom = await page.evaluate(
        """() => ({nodes: document.getElementsByTagName('*').length,
                    canvas: !!document.querySelector('#stage canvas'),
                    preset: document.documentElement.dataset.preset,
                    animations: document.getAnimations ? document.getAnimations().length : -1})""")
    idle_result = {
        "seconds": 60,
        "rafScheduled": idle["raf"],
        "timeoutFires": idle["timeoutFires"],
        "intervalFires": idle["intervalFires"],
        "wsCount": idle["wsCount"],
        "longTasks": idle["longTasks"],
        "formulaLong": len(idle["longTasks"]),
        "dom": idle_dom,
        "startAt": t0,
    }
    # 连聊 60s（offline 模式，本地规则回复）
    await page.evaluate(
        """() => { const p = window.__perf; p.raf = 0; p.timeoutFires = 0;
                   p.intervalFires = 0; p.wsCount = 0; p.longTasks.length = 0; }""")
    t0 = await page.evaluate("performance.now()")
    n_sent = 0
    end = time.time() + 60
    while time.time() < end:
        await send_round(page, "在吗，跟我说说话", total_wait=4000)
        n_sent += 1
    chat = await page.evaluate("window.__perf")
    chat_result = {
        "seconds": 60,
        "rounds": n_sent,
        "rafScheduled": chat["raf"],
        "timeoutFires": chat["timeoutFires"],
        "intervalFires": chat["intervalFires"],
        "wsCount": chat["wsCount"],
        "longTasks": chat["longTasks"],
        "formulaLong": len(chat["longTasks"]),
    }
    await ctx.close()
    return {"idle": idle_result, "chat": chat_result}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/perf/measure.json")
    ap.add_argument("--only", default="")
    ap.add_argument("--rounds", type=int, default=200)
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else {"load", "rounds", "scroll", "power", "micro"}
    out = {"base": BASE, "mock": MOCK,
           "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--mute-audio", "--disable-features=AudioServiceOutOfProcess",
                  "--autoplay-policy=document-user-activation-required"])
        try:
            if "load" in only:
                t = time.time()
                out["load"] = await measure_load(browser)
                print(f"[load] done {time.time() - t:.1f}s", flush=True)
            if "rounds" in only:
                t = time.time()
                out["rounds"] = await measure_rounds(browser, args.rounds)
                print(f"[rounds] done {time.time() - t:.1f}s", flush=True)
            if "scroll" in only:
                t = time.time()
                out["scroll"] = await measure_scroll(browser)
                print(f"[scroll] done {time.time() - t:.1f}s", flush=True)
            if "power" in only:
                t = time.time()
                out["power"] = await measure_power(browser)
                print(f"[power] done {time.time() - t:.1f}s", flush=True)
            if "micro" in only:
                t = time.time()
                out["micro"] = await measure_micro(browser)
                print(f"[micro] done {time.time() - t:.1f}s", flush=True)
        finally:
            await browser.close()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved ->", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

