#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键断点复测：真实免费模型，同一用例只跑一次，429/超时自动退避但不无限重试。

用法：
  python scripts/live_retest.py --dry-run     # 只打印计划与账本状态，不发请求
  python scripts/live_retest.py               # 有额度时补跑；可反复执行（断点续跑）
  python scripts/live_retest.py --reset       # 清账重跑
  python scripts/live_retest.py --ids followup-01 detail-pet-01

默认 10 例 = R7 那批 7 例（含 3 个名字用例）+ 3 例补长度/建议缺口。
账本：artifacts/live_retest/r8_ledger.json
  cases[] {id, category, status(valid|invalid|pending), passed, reasons[], turns[], attempts}
  summary {expected, valid, invalid, pending}

口径：
  - status=valid 的用例不再重跑（有效数据只消耗一次额度，判红也算有效）；
  - 429/超时/空响应：指数退避后重试同一条，最多 --max-attempts 次；
    配额类失败用尽次数就整轮停手（不换家硬刷、不无限重试），账本照写；
  - 只允许免费白名单模型；永不打印 key；账本只写本机 artifacts。
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "server"))

import eval_human_feel as ev  # noqa: E402
import credentials  # noqa: E402

FREE_MODEL = "dots-studio/dots-3-note-preview:free"
BASE = "https://openrouter.ai/api/v1"
LEDGER = ROOT / "artifacts" / "live_retest" / "r8_ledger.json"
# R7 真实复测的 7 例（含 3 个名字用例）+ 3 例补 R7 未覆盖的长度/建议缺口
DEFAULT_IDS = (
    "followup-01", "detail-pet-01", "detail-pet-02", "detail-time-01",
    "name-pet-01", "name-pet-02", "name-boss-01",
    "detail-pet-03", "followup-04", "detail-food-01",
)


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"cases": [], "summary": {}, "stop_reason": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"cases": [], "summary": {}, "stop_reason": ""}


def write_ledger(path: Path, cases: list, expected: int, stop_reason: str,
                 model: str, requests: int, errors: int) -> dict:
    valid = sum(1 for c in cases if c.get("status") == "valid")
    invalid = sum(1 for c in cases if c.get("status") == "invalid")
    payload = {
        "model": model,
        "cases": cases,
        "stop_reason": stop_reason,
        "requests": requests, "errors": errors,
        "summary": {"expected": expected, "valid": valid,
                    "invalid": invalid, "pending": expected - valid - invalid},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def classify(res, backend, case) -> tuple:
    """返回 (status, 原因)。status=valid 才算拿到这例的真实样本。"""
    if getattr(res, "limit_skipped", False):
        return "invalid", "request_limit"
    reasons = [f.get("reason", "") for f in res.failures]
    backend_errors = [r for r in reasons if r.startswith("后端不可用")]
    if backend_errors:
        first = backend_errors[0]
        if backend.last_status == 429 or "429" in first:
            return "invalid", "quota_429"
        low = first.lower()
        if ("timed out" in low or "timeouterror" in low or "urlopen error" in low
                or "空响应" in first):
            return "invalid", "timeout_or_empty"
        return "invalid", "backend_error"
    if len(res.turns) != len(case["turns"]):
        return "invalid", "turn_incomplete"
    return "valid", ""


def snapshot_turns(res) -> list:
    return [{"user": t.user, "reply": t.reply,
             "len": ev.visible_len(t.reply),
             "parts": len(ev.reply_parts(t.reply) or [t.reply])}
            for t in res.turns]


def main() -> int:
    ap = argparse.ArgumentParser(description="一键断点复测（真实免费模型）")
    ap.add_argument("--cases", default=str(ev.CASES_FILE))
    ap.add_argument("--ids", nargs="*", default=list(DEFAULT_IDS))
    ap.add_argument("--out", default=str(LEDGER))
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--model", default=FREE_MODEL)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="同一条最多尝试次数（含首次）")
    ap.add_argument("--backoff-base", type=float, default=45.0)
    ap.add_argument("--backoff-max", type=float, default=300.0)
    ap.add_argument("--max-requests", type=int, default=60)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not ev.is_free_model(args.model):
        print("拒绝：非免费白名单模型 %s" % args.model, file=sys.stderr)
        return 2

    data = ev.load_cases(Path(args.cases))
    universal = data["universal"]
    by_id = {c["id"]: c for c in data["cases"]}
    missing = [i for i in args.ids if i not in by_id]
    if missing:
        print("用例不存在：%s" % ", ".join(missing), file=sys.stderr)
        return 2
    cases = [by_id[i] for i in args.ids]

    out_path = Path(args.out)
    old = {"cases": []} if args.reset else load_ledger(out_path)
    prior = {c["id"]: c for c in old.get("cases", [])}
    records = {i: prior.get(i, {"id": i, "category": by_id[i]["category"],
                                "status": "pending", "passed": False,
                                "reasons": [], "turns": [], "attempts": 0})
               for i in args.ids}

    if args.dry_run:
        print("== 断点复测计划（dry-run，不发请求） ==")
        for c in cases:
            r = records[c["id"]]
            print("  %-16s %-6s %s" % (c["id"], c["category"],
                                       r.get("status", "pending")))
        print("  valid 已跳过 %d，待跑 %d，模型 %s"
              % (sum(1 for r in records.values() if r.get("status") == "valid"),
                 sum(1 for r in records.values() if r.get("status") != "valid"),
                 args.model))
        return 0

    key = credentials.api_key()
    if not key:
        print("没有可用的 API key（不打印来源），先配置再跑", file=sys.stderr)
        return 2

    backend = ev.ApiBackend(args.base, args.model, key, delay=args.delay,
                            timeout=args.timeout, now="周五 22:10",
                            max_tokens=args.max_tokens, retries=0,
                            max_requests=args.max_requests,
                            provider_name="openrouter-free",
                            stop_on_quota=True)
    started = time.time()
    stop_reason = ""

    for idx, case in enumerate(cases, 1):
        cid = case["id"]
        rec = records[cid]
        if rec.get("status") == "valid":
            print("[%d/%d] %-16s SKIP（已有有效样本）" % (idx, len(cases), cid),
                  flush=True)
            continue
        if stop_reason:
            print("[%d/%d] %-16s SKIP（%s）" % (idx, len(cases), cid, stop_reason),
                  flush=True)
            continue
        for attempt in range(1, args.max_attempts + 1):
            res, _adv = ev.run_case(case, backend, universal)
            status, why = classify(res, backend, case)
            rec["attempts"] = attempt
            if status == "valid":
                rec.update({"status": "valid", "passed": res.passed,
                            "reasons": list(res.failures),
                            "turns": snapshot_turns(res), "error": ""})
                print("[%d/%d] %-16s %s（有效样本，第 %d 次）"
                      % (idx, len(cases), cid, "PASS" if res.passed else "FAIL", attempt),
                      flush=True)
                break
            rec.update({"status": "invalid", "passed": False, "error": why,
                        "reasons": list(res.failures)})
            print("[%d/%d] %-16s 无效：%s（第 %d/%d 次）"
                  % (idx, len(cases), cid, why, attempt, args.max_attempts),
                  flush=True)
            quota_like = why in ("quota_429", "timeout_or_empty")
            if backend.billing_stop:
                stop_reason = "billing_stop"
                break
            if not quota_like:
                break
            if attempt >= args.max_attempts:
                stop_reason = why + "（重试已用尽，停手）"
                break
            wait = min(args.backoff_base * (2 ** (attempt - 1)), args.backoff_max)
            print("  …退避 %.0fs 后重试同一条" % wait, flush=True)
            time.sleep(wait)
        write_ledger(out_path, [records[i] for i in args.ids], len(cases),
                     stop_reason, args.model, backend.calls, backend.errors)
        if stop_reason:
            break

    payload = write_ledger(out_path, [records[i] for i in args.ids], len(cases),
                          stop_reason, args.model, backend.calls, backend.errors)
    s = payload["summary"]
    print("\n账本：%s" % out_path)
    print("有效 %d/%d，无效 %d，待跑 %d，请求 %d，错误 %d，用时 %.0fs%s"
          % (s["valid"], s["expected"], s["invalid"], s["pending"],
             backend.calls, backend.errors, time.time() - started,
             ("，停手：%s" % stop_reason) if stop_reason else ""))
    if s["valid"] == s["expected"]:
        return 0
    return 4 if stop_reason else 1


if __name__ == "__main__":
    sys.exit(main())
