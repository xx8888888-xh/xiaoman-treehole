#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure_ttft.py · W7 首字延迟实测（mock / 真实 provider）

用本地起服务 + HTTP SSE 采样，不改主工作区、不起 8901/8902/8903。

  python scripts/measure_ttft.py            # mock 20 次 + 真实 5 次（读主工作区 .env）
  python scripts/measure_ttft.py --mock-only

真实 provider 只发送**非个人信息**的测试短句（"你好，随便回一句"）；
隐私策略要求 XIAOMAN_ALLOW_FREE_PERSONAL=1 才允许 Kilo，绝大多数运行不需要。
"""
import argparse
import http.client
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT.parent / "treehole" / ".env"


def pick_port():
    """动态取空闲端口，避免与其它 worktree/残留进程争用固定端口。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_env(path):
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def wait_ready(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def start_server(port, extra_env):
    env = os.environ.copy()
    env.update(extra_env)
    env["XIAOMAN_MOCK_PORT"] = str(port)
    env["XIAOMAN_BIND_HOST"] = "127.0.0.1"
    env["XIAOMAN_ALLOW_ORIGINS"] = "http://127.0.0.1:18901"
    env.setdefault("XIAOMAN_STREAM_DELAY", "0.02")
    proc = subprocess.Popen([sys.executable, str(ROOT / "server" / "mock_api.py")],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_ready(port):
        proc.kill()
        raise SystemExit(f"server {port} not ready")
    return proc


def sample(port, mode, prompt, timeout=40):
    body = json.dumps({
        "mode": mode,
        "session_id": f"ttft-{int(time.time() * 1000)}",
        "messages": [{"role": "user", "content": prompt}],
    }, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    t0 = time.perf_counter()
    conn.request("POST", "/api/chat/stream", body,
                 {"Content-Type": "application/json; charset=utf-8"})
    res = conn.getresponse()
    ttfb = first = total = None
    ev = None
    err = ""
    for raw in res:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
            if ev == "meta" and ttfb is None:
                ttfb = (time.perf_counter() - t0) * 1000
            elif ev == "delta" and first is None:
                first = (time.perf_counter() - t0) * 1000
            elif ev == "done":
                total = (time.perf_counter() - t0) * 1000
        elif line.startswith("data:") and ev == "error":
            err = line.split(":", 1)[1].strip()[:200]
    conn.close()
    return ttfb, first, total, err


def pct(values, p):
    if not values:
        return float("nan")
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * p))))
    return vals[idx]


def report(name, ttfbs, firsts, totals, errors=None):
    ok = [v for v in firsts if v is not None]
    fb = [v for v in ttfbs if v is not None]
    tt = [v for v in totals if v is not None]
    print(f"\n[{name}] n={len(ok)}/{len(firsts)}")
    if fb:
        print(f"  TTFB(meta) ms: p50={pct(fb, 0.5):.0f} p95={pct(fb, 0.95):.0f}")
    if ok:
        print(f"  首字 ms: min={min(ok):.0f} p50={pct(ok, 0.5):.0f} "
              f"p95={pct(ok, 0.95):.0f} max={max(ok):.0f}")
    if tt:
        print(f"  整段 ms: p50={pct(tt, 0.5):.0f} p95={pct(tt, 0.95):.0f} "
              f"max={max(tt):.0f}")
    print(f"  样本首字: {[round(x) for x in ok]}")
    errs = [e for e in (errors or []) if e]
    if errs:
        print(f"  失败/降级 {len(errs)} 次: {errs[:3]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock-only", action="store_true")
    ap.add_argument("--mock-n", type=int, default=20)
    ap.add_argument("--real-n", type=int, default=5)
    args = ap.parse_args()

    socket.setdefaulttimeout(45)
    mock_port = pick_port()
    real_port = pick_port()
    while real_port == mock_port:
        real_port = pick_port()
    procs = []
    try:
        mock = start_server(mock_port, {"AI_MODE": "mock", "AI_API_BASE": "", "AI_API_KEY": ""})
        procs.append(mock)
        firsts, totals, errs, ttfbs = [], [], [], []
        for i in range(args.mock_n):
            fb, f, t, e = sample(mock_port, "mock", f"今天有点累（采样{i}）")
            ttfbs.append(fb)
            firsts.append(f)
            totals.append(t)
            errs.append(e)
        report(f"mock（本地 SSE，端口 {mock_port}）", ttfbs, firsts, totals, errs)

        if args.mock_only:
            return 0
        env = load_env(ENV_FILE)
        if not env.get("AI_API_BASE"):
            print("\n[真实 provider] 跳过：主工作区 .env 未配置 AI_API_BASE")
            return 0
        real = start_server(real_port, {
            "AI_MODE": "openai",
            "AI_API_BASE": env.get("AI_API_BASE", ""),
            "AI_API_KEY": env.get("AI_API_KEY", ""),
            "AI_MODEL": env.get("AI_MODEL", ""),
            "XIAOMAN_ALLOW_FREE_PERSONAL": "1",   # 仅本次非个人信息测速
        })
        procs.append(real)
        firsts, totals, errs, ttfbs = [], [], [], []
        prompts = [
            "你好，随便回一句短话",
            "今天天气怎么样，随口说说",
            "用一句话讲个冷笑话",
            "你觉得周末适合做什么",
            "随便说一句安慰人的话",
        ]
        for i in range(args.real_n):
            fb, f, t, e = sample(real_port, "openai", prompts[i % len(prompts)], timeout=45)
            print(f"  real[{i}] TTFB={fb and round(fb)}ms 首字={f and round(f)}ms "
                  f"整段={t and round(t)}ms err={e}")
            ttfbs.append(fb)
            firsts.append(f)
            totals.append(t)
            errs.append(e)
            time.sleep(1.0)
        report(f"真实 provider（服务端代理 SSE，端口 {real_port}）", ttfbs, firsts, totals, errs)
        return 0
    finally:
        for p in procs:
            p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
