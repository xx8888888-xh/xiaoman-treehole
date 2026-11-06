#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf_r4.py · 第四轮性能验收（PERF_R4）采样脚本（只在云端跑，本地被 browser_guard 拦截）

只负责两项，测量语义全部复用 perf_r3.py（不改动它的口径）：
  1) load   首屏与首条消息：1x / 4x / 开场白空闲路径，各三次取中位数
  2) memory 200 轮长会话：堆增长、DOM 节点数、pager held（r3 原样结果 + summary 摘要）

用法（云端）：
  python scripts/perf_r4.py --out artifacts/perf/r4.json
  python scripts/perf_r4.py --only load,memory --tag after

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
import perf_r3 as r3  # noqa: E402  （模块级会 import playwright，云端已装）

from browser_guard import require_browser_allowed  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

# 固定口径：load / greet 各三次；memory 200 轮（warmup 与 r3 默认一致）
LOAD_REPEATS = 3
THROTTLE = 4
MEMORY_ROUNDS = 200
MEMORY_WARMUP = 40

# 开场白路径要取中位数的数值项 / 布尔项
GREET_NUM_KEYS = ("greetLandedMs", "greetDoneMs", "records")
GREET_BOOL_KEYS = ("played", "skipped")

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


# ---------------------------------------------------------------- 1) load
async def measure_load_r4(browser, repeats=LOAD_REPEATS, throttle=THROTTLE):
    """① 首屏与首条消息：三条路径各 repeats 次（每次全新 context = 冷启动），
    中位数直接复用 r3.load_median，字段名保持 firstSendMs / inputReadyMs / firstSend 等。"""
    runs_1x = []
    for i in range(repeats):
        r = await r3.measure_load_once(browser, throttle=1)
        runs_1x.append(r)
        print("[r4] load 1x run %d/%d firstSend=%sms inputReady=%sms lcp=%sms"
              % (i + 1, repeats, r.get("firstSendMs"), r.get("inputReadyMs"), r.get("lcp")),
              flush=True)
    runs_4x = []
    for i in range(repeats):
        r = await r3.measure_load_once(browser, throttle=throttle)
        runs_4x.append(r)
        print("[r4] load %sx run %d/%d firstSend=%sms inputReady=%sms lcp=%sms"
              % (throttle, i + 1, repeats, r.get("firstSendMs"), r.get("inputReadyMs"),
                 r.get("lcp")), flush=True)
    runs_greet = []
    for i in range(repeats):
        r = await r3.measure_greet_once(browser, throttle=throttle)
        runs_greet.append(r)
        print("[r4] greet %sx run %d/%d landed=%sms done=%sms played=%s records=%s"
              % (throttle, i + 1, repeats, r.get("greetLandedMs"), r.get("greetDoneMs"),
                 r.get("played"), r.get("records")), flush=True)
    return {
        "repeats": repeats,
        "runs": runs_1x,
        "median": r3.load_median(runs_1x),
        "throttled": {"rate": throttle, "runs": runs_4x, "median": r3.load_median(runs_4x)},
        "greet": {"rate": throttle, "runs": runs_greet,
                  "median": greet_median(runs_greet, throttle)},
    }


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
    ap.add_argument("--out", default="artifacts/perf/r4.json")
    ap.add_argument("--only", default="")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    require_browser_allowed("scripts/perf_r4.py")
    allowed = {"load", "memory"}
    only = set(x.strip() for x in args.only.split(",") if x.strip()) if args.only else set(allowed)
    unknown = only - allowed
    if unknown:
        print("[warn] 本脚本不支持：%s（仅 load,memory）" % ",".join(sorted(unknown)), flush=True)
    out = {"base": r3.BASE, "mock": r3.MOCK, "tag": args.tag,
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
                t = time.time(); out["load"] = await measure_load_r4(browser)
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
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
