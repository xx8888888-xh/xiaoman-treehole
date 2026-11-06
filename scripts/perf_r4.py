#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf_r4.py · 第四/六轮性能验收（PERF_R4 / R6）采样脚本（只在云端跑，本地被 browser_guard 拦截）

只负责两项，测量语义全部复用 perf_r3.py（不改动它的口径）：
  1) load   首屏与首条消息：1x / 4x / 开场白空闲路径，各重复 --repeat 次（默认 5）
            每次给 firstSendMs 记录阶段时间戳，r6 里给出分布与阶段中位数
  2) memory 200 轮长会话：堆增长、DOM 节点数、pager held（r3 原样结果 + summary 摘要）

输出两份：
  --out    artifacts/perf/r4.json   （兼容 R4/R5：load/memory 结构与字段名不变）
  --out-r6 artifacts/perf/r6.json   （R6：load 原始逐轮值 + distributions 统计 + 阶段拆分）

用法（云端）：
  python scripts/perf_r4.py --out artifacts/perf/r4.json --repeat 5
  python scripts/perf_r4.py --only load,memory --tag after --repeat 5

服务：WEB 8911（本 worktree 的 web/ 静态站）、MOCK 8912（server/mock_api.py，AI_MODE=mock）
"""
import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perf_r3 as r3  # noqa: E402  （模块级会 import playwright，云端已装）

from browser_guard import require_browser_allowed  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

# 固定口径：load / greet 各 LOAD_REPEATS 次（--repeat 可覆盖）；memory 200 轮
LOAD_REPEATS = 5
THROTTLE = 4
MEMORY_ROUNDS = 200
MEMORY_WARMUP = 40

# 开场白路径要取中位数的数值项 / 布尔项
GREET_NUM_KEYS = ("greetLandedMs", "greetDoneMs", "records")
GREET_BOOL_KEYS = ("played", "skipped")

# 阶段拆分（都从 r3 的 phaseMarks 原始时间戳换算）：
#   inputToSendMs      填字/派发 input/点击 → send() 入口（含 submitText 里的 syncComposer 布局）
#   sendSyncMs         send() 入口 → 气泡插入（ChatPager.push）开始：风险分类/历史写入/typing 反馈
#   domCommitMs        ChatPager.push 内部：建行、插 DOM、scrollTop 贴底（含一次强制同步布局）
#   awaitFirstRenderMs push 返回 → 下一帧可见（样式/布局/绘制提交）
#   pollResidualMs     Playwright 观测侧：首帧之后到 wait_for_function 读到节点的时间（25ms 轮询+IPC）
# 说明：自己气泡的插入在 fetch 之前（首包 await 不在 firstSend 关键路径），故"await 首包/渲染"
# 记为 awaitFirstRenderMs；首包往返另可在 long/probe 里看，不影响 firstSend。
PHASE_KEYS = ("inputFillMs", "clickToSendMs", "inputToSendMs", "sendSyncMs",
              "domCommitMs", "awaitFirstRenderMs", "secondFrameMs",
              "inPageTotalMs", "pollResidualMs")
CANONICAL_PHASES = ("inputToSendMs", "sendSyncMs", "domCommitMs", "awaitFirstRenderMs")

# 翻页自检里需要"全 True 才算过"的布尔项
PAGING_FLAGS = ("movedAll", "totalStable", "overlapOk", "stepOk",
                "dupInPage", "firstRepeat", "fetchKeepsWindow", "idsPresent")


def _median(vals):
    """只对非 None 值取中位数（缺测项不能把结果拖成 0）"""
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals)) if vals else None


def greet_median(runs, throttle):
    """开场白三次明细的中位数：数值项取 median，布尔项按 1/0 取多数（median 语义）"""
    med = {"throttle": throttle}
    for k in GREET_NUM_KEYS:
        med[k] = _median([r.get(k) for r in runs])
    for k in GREET_BOOL_KEYS:
        vals = [1 if r.get(k) else 0 for r in runs]
        med[k] = bool(statistics.median(vals)) if vals else None
    return med


# ---------------------------------------------------------------- 统计 / 阶段
def _stats(vals, nd=1):
    """逐轮原始值 + 分布统计（median/min/max/mean/p95/stdev）。
    p95 用最近秩（n=5 时即最大值，任其如实反映最差轮，不插值掩盖抖动）。"""
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    ordered = sorted(vals)
    p95 = ordered[max(0, math.ceil(len(vals) * 0.95) - 1)]
    return {
        "n": len(vals),
        "median": round(statistics.median(vals), nd),
        "min": round(ordered[0], nd),
        "max": round(ordered[-1], nd),
        "mean": round(statistics.fmean(vals), nd),
        "p95": round(p95, nd),
        "stdev": round(statistics.stdev(vals), nd) if len(vals) > 1 else 0.0,
        "values": [round(v, nd) for v in vals],
    }


def _delta(start, end):
    if start is None or end is None:
        return None
    return round(float(end) - float(start), 1)


def phase_segments(marks, observed_ms):
    """把 r3 记录的 phaseMarks（performance.now 时间戳）换算成段耗时。
    锚点：t0（填字前）→ frame1（push 后第一帧）→ observed_ms（Playwright 读到气泡的时刻）。
    只有 push 埋点缺失（如首条消息走早退分支）时返回 None。"""
    if not marks or marks.get("t0") is None or marks.get("tPushStart") is None:
        return None
    t0 = marks.get("t0")
    t_in = marks.get("tInputFilled")
    t_entry = marks.get("tSendEntry")
    t_p0 = marks.get("tPushStart")
    t_p1 = marks.get("tPushEnd")
    f1 = marks.get("frame1")
    return {
        "inputFillMs": _delta(t0, t_in),
        "clickToSendMs": _delta(t_in, t_entry),
        "inputToSendMs": _delta(t0, t_entry),
        "sendSyncMs": _delta(t_entry, t_p0),
        "domCommitMs": _delta(t_p0, t_p1),
        "awaitFirstRenderMs": _delta(t_p1, f1),
        "secondFrameMs": _delta(f1, marks.get("frame2")),
        "inPageTotalMs": _delta(t0, f1),
        "pollResidualMs": _delta(f1, observed_ms),
    }


LOAD_DIST_KEYS = ("firstSendMs", "inputReadyMs", "bindMs", "dclMs", "fcp", "lcp", "longMax")


def _metric(run, key):
    if key == "longMax":
        return (run.get("long") or {}).get("max")
    return run.get(key)


def load_distributions(load):
    """1x / 4x 两个档位的 firstSendMs 分布 + 各阶段分布 + 开场白分布。
    逐轮原始值留在 load.runs / load.throttled.runs / load.greet.runs（r3 结构不变）。"""
    groups = (("1x", load.get("runs") or []),
              ("4x", (load.get("throttled") or {}).get("runs") or []))
    out = {"repeat": load.get("repeats"), "metrics": {}, "segments": {}, "greet": {}}
    for key in LOAD_DIST_KEYS:
        out["metrics"][key] = {label: _stats([_metric(r, key) for r in runs])
                               for label, runs in groups}
    for label, runs in groups:
        rows = [r.get("phases") for r in runs if r.get("phases")]
        out["segments"][label] = {k: _stats([row.get(k) for row in rows]) for k in PHASE_KEYS}
    greet_runs = (load.get("greet") or {}).get("runs") or []
    for key in GREET_NUM_KEYS:
        out["greet"][key] = _stats([r.get(key) for r in greet_runs])
    out["greet"]["played"] = {"true": sum(1 for r in greet_runs if r.get("played")),
                              "n": len(greet_runs)}
    return out


# ---------------------------------------------------------------- 1) load
def _phase_line(label, seg):
    keys = CANONICAL_PHASES + ("pollResidualMs",)
    if not seg:
        return "%s phases=missing" % label
    return "%s phases %s" % (label, " ".join("%s=%s" % (k, seg.get(k)) for k in keys))


async def measure_load_r4(browser, repeats=LOAD_REPEATS, throttle=THROTTLE, phase_probe=True):
    """① 首屏与首条消息：三条路径各 repeats 次（每次全新 context = 冷启动），
    中位数直接复用 r3.load_median，字段名保持 firstSendMs / inputReadyMs / firstSend 等。
    phase_probe=True 时每次另记阶段时间戳（只加性能标记，firstSendMs 的测量点不变）。"""
    runs_1x = []
    for i in range(repeats):
        r = await r3.measure_load_once(browser, throttle=1, phase_probe=phase_probe)
        r["phases"] = phase_segments(r.get("phaseMarks"), r.get("firstSendMs"))
        runs_1x.append(r)
        print("[r4] load 1x run %d/%d firstSend=%sms inputReady=%sms lcp=%sms"
              % (i + 1, repeats, r.get("firstSendMs"), r.get("inputReadyMs"), r.get("lcp")),
              flush=True)
        print("[r6] load 1x run %d/%d %s" % (i + 1, repeats, _phase_line("1x", r["phases"])),
              flush=True)
    runs_4x = []
    for i in range(repeats):
        r = await r3.measure_load_once(browser, throttle=throttle, phase_probe=phase_probe)
        r["phases"] = phase_segments(r.get("phaseMarks"), r.get("firstSendMs"))
        runs_4x.append(r)
        print("[r4] load %sx run %d/%d firstSend=%sms inputReady=%sms lcp=%sms"
              % (throttle, i + 1, repeats, r.get("firstSendMs"), r.get("inputReadyMs"),
                 r.get("lcp")), flush=True)
        print("[r6] load %sx run %d/%d %s"
              % (throttle, i + 1, repeats, _phase_line("%sx" % throttle, r["phases"])),
              flush=True)
    runs_greet = []
    for i in range(repeats):
        r = await r3.measure_greet_once(browser, throttle=throttle)
        runs_greet.append(r)
        print("[r4] greet %sx run %d/%d landed=%sms done=%sms played=%s records=%s"
              % (throttle, i + 1, repeats, r.get("greetLandedMs"), r.get("greetDoneMs"),
                 r.get("played"), r.get("records")), flush=True)
    out = {
        "repeats": repeats,
        "runs": runs_1x,
        "median": r3.load_median(runs_1x),
        "throttled": {"rate": throttle, "runs": runs_4x, "median": r3.load_median(runs_4x)},
        "greet": {"rate": throttle, "runs": runs_greet,
                  "median": greet_median(runs_greet, throttle)},
        "phaseProbe": phase_probe,
    }
    out["distributions"] = load_distributions(out)
    return out


# ---------------------------------------------------------------- 2) memory
def memory_summary(mem):
    """② 把验收要看的绝对量提到一处：堆绝对值、DOM、pager held、翻页布尔值。
    steady 段（r3 顶层字段，warmup=200）就是长会话稳态口径。"""
    pager = mem.get("pager") or {}
    paging = mem.get("paging") or {}
    return {
        "rounds": mem.get("rounds"), "warmup": mem.get("warmup"),
        "heapStartMB": mem.get("heapStartMB"), "heapEndMB": mem.get("heapEndMB"),
        "heapGrowthMB": mem.get("heapGrowthMB"),
        "marks": mem.get("marks"),
        "domNodesStart": mem.get("domNodesStart"), "domNodesEnd": mem.get("domNodesEnd"),
        "pager": {"held": pager.get("held"), "evicted": pager.get("evicted"),
                  "total": pager.get("total"), "rendered": pager.get("rendered")},
        "phase2GrowthMB": mem.get("phase2GrowthMB"),
        "paging": {k: paging.get(k) for k in PAGING_FLAGS},
        "pagingMoves": paging.get("moves"), "pagingCalls": paging.get("calls"),
    }


async def measure_memory_r4(browser, rounds=MEMORY_ROUNDS, warmup=MEMORY_WARMUP):
    """r3.measure_memory 原样调用（steady / cold / consoleOn / paging 都保留），
    只在返回里追加 summary，方便云端摘要与人工验收对齐绝对量。"""
    mem = await r3.measure_memory(browser, rounds=rounds, warmup=warmup)
    mem["summary"] = memory_summary(mem)
    return mem


# ---------------------------------------------------------------- 摘要行
def _fmt_stats(s):
    if not s or not s.get("n"):
        return "n=0"
    return ("median=%s min=%s max=%s mean=%s p95=%s stdev=%s (n=%s)"
            % (s["median"], s["min"], s["max"], s["mean"], s["p95"], s["stdev"], s["n"]))


def print_load_summary(load):
    m1 = load["median"]
    m4 = load["throttled"]["median"]
    g = load["greet"]["median"]
    print("[r4] firstSend 1x median=%sms runs=%s"
          % (m1["firstSendMs"], json.dumps([r["firstSendMs"] for r in load["runs"]])), flush=True)
    print("[r4] firstSend %sx median=%sms runs=%s"
          % (load["throttled"]["rate"], m4["firstSendMs"],
             json.dumps([r["firstSendMs"] for r in load["throttled"]["runs"]])), flush=True)
    print("[r4] load 1x inputReady=%sms lcp=%sms fcp=%sms longMax=%sms over50=%s"
          % (m1["inputReadyMs"], m1["lcp"], m1["fcp"], m1["longMax"], m1["longOver50"]),
          flush=True)
    print("[r4] load %sx inputReady=%sms lcp=%sms fcp=%sms longMax=%sms over50Max=%sms"
          % (load["throttled"]["rate"], m4["inputReadyMs"], m4["lcp"], m4["fcp"],
             m4["longMax"], m4["longOver50Max"]), flush=True)
    print("[r4] greet landed median=%sms done=%sms played=%s records=%s runs=%s"
          % (g["greetLandedMs"], g["greetDoneMs"], g["played"], g["records"],
             json.dumps([{"landed": r.get("greetLandedMs"), "done": r.get("greetDoneMs"),
                          "played": r.get("played"), "records": r.get("records")}
                         for r in load["greet"]["runs"]], ensure_ascii=False)), flush=True)
    # R6：分布 + 阶段中位数（回答“4x 多出的时间落在哪一段”）
    dist = load.get("distributions") or {}
    metrics = dist.get("metrics") or {}
    for label in ("1x", "4x"):
        s = (metrics.get("firstSendMs") or {}).get(label)
        print("[r6] firstSend %s %s" % (label, _fmt_stats(s)), flush=True)
    print("[r6] firstSend 原始值 %s" % json.dumps(
        {"1x": (metrics.get("firstSendMs") or {}).get("1x", {}).get("values"),
         "4x": (metrics.get("firstSendMs") or {}).get("4x", {}).get("values")}), flush=True)
    segs = dist.get("segments") or {}
    for label in ("1x", "4x"):
        row = segs.get(label) or {}
        print("[r6] %s 阶段中位数 %s" % (label, " ".join(
            "%s=%s" % (k, (row.get(k) or {}).get("median")) for k in PHASE_KEYS)), flush=True)
    for key in ("greetLandedMs", "greetDoneMs"):
        print("[r6] %s %s" % (key, _fmt_stats((dist.get("greet") or {}).get(key))), flush=True)


def print_memory_summary(mem):
    s = mem["summary"]
    print("[r4] memory heapStart=%sMB heapEnd=%sMB growth=%sMB marks=%s"
          % (s["heapStartMB"], s["heapEndMB"], s["heapGrowthMB"],
             json.dumps(s["marks"], ensure_ascii=False)), flush=True)
    print("[r4] memory dom=%s->%s held=%s evicted=%s total=%s phase2GrowthMB=%s"
          % (s["domNodesStart"], s["domNodesEnd"], s["pager"]["held"],
             s["pager"]["evicted"], s["pager"]["total"], s["phase2GrowthMB"]), flush=True)
    print("[r4] memory paging=%s moves=%s calls=%s"
          % (json.dumps(s["paging"], ensure_ascii=False), s["pagingMoves"], s["pagingCalls"]),
          flush=True)


# ---------------------------------------------------------------- main
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/perf/r4.json",
                    help="R4/R5 兼容输出（含 load/memory）")
    ap.add_argument("--out-r6", default="artifacts/perf/r6.json",
                    help="R6 输出（load 逐轮原始值 + distributions 分布/阶段统计）")
    ap.add_argument("--repeat", type=int, default=LOAD_REPEATS,
                    help="load 三条路径每档重复次数（默认 %d）" % LOAD_REPEATS)
    ap.add_argument("--only", default="")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    repeats = max(1, args.repeat)
    require_browser_allowed("scripts/perf_r4.py")
    allowed = {"load", "memory"}
    only = set(x.strip() for x in args.only.split(",") if x.strip()) if args.only else set(allowed)
    unknown = only - allowed
    if unknown:
        print("[warn] 本脚本不支持：%s（仅 load,memory）" % ",".join(sorted(unknown)), flush=True)
    out = {"base": r3.BASE, "mock": r3.MOCK, "tag": args.tag,
           "repeat": repeats,
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
                t = time.time(); out["load"] = await measure_load_r4(browser, repeats=repeats)
                print_load_summary(out["load"])
                print("[r4] load done %.1fs" % (time.time() - t), flush=True)
            if "memory" in only:
                t = time.time(); out["memory"] = await measure_memory_r4(browser)
                print_memory_summary(out["memory"])
                print("[r4] memory done %.1fs" % (time.time() - t), flush=True)
        finally:
            await browser.close()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved ->", args.out)
    # R6：只留 load（逐轮原始值 + 分布 + 阶段）与 memory 摘要；不带长会话原始明细
    r6 = {"script": "perf_r4.py", "base": out["base"], "mock": out["mock"],
          "tag": out["tag"], "repeat": repeats, "generatedAt": out["generatedAt"],
          "runner": out["runner"], "load": out.get("load")}
    if "load" in out:
        r6["distributions"] = out["load"].get("distributions")
    if "memory" in out:
        r6["memory"] = {"summary": (out["memory"] or {}).get("summary")}
    r6_dir = os.path.dirname(os.path.abspath(args.out_r6))
    os.makedirs(r6_dir, exist_ok=True)
    with open(args.out_r6, "w", encoding="utf-8") as f:
        json.dump(r6, f, ensure_ascii=False, indent=2)
    print("saved ->", args.out_r6)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
