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
  cases[] {id, category, status(valid|invalid|unratable|pending), passed, reasons[], turns[], attempts}
  summary {expected, valid, invalid, pending}

口径：
  - status=valid 的用例不再重跑（有效数据只消耗一次额度，判红也算有效）；
  - 429/超时/空响应：指数退避后重试同一条，最多 --max-attempts 次；
    配额类失败用尽次数就整轮停手（不换家硬刷、不无限重试），账本照写；
  - 只允许免费白名单模型；永不打印 key；账本只写本机 artifacts。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "server"))

import eval_human_feel as ev  # noqa: E402
import credentials  # noqa: E402

FREE_MODEL = "dots-studio/dots-3-note-preview:free"
BASE = "https://openrouter.ai/api/v1"
LEDGER = ROOT / "artifacts" / "live_retest" / "r8_ledger.json"
# followup-04 两次都因后端输出上限拿不到可评内容，保留记录但不再重跑。
UNRATABLE_IDS = ("followup-04",)
# 补例：与 followup-04 同类别（追问）、同判据、同 3 轮结构，只用来补满 10 个可评例。
BACKFILL_CASES = (
    {"id": "followup-04-backfill", "category": "追问",
     "replaces": "followup-04",
     "turns": ["最近老是失眠", "昨天也是三点多才睡着", "今天一早又要开会"],
     "criteria": "接住失眠的循环；最多一条具体建议，不每轮都劝早睡。",
     "max_advice_turns": 1},
)
BACKFILL_ID = "followup-04-backfill"
# R7 真实复测的 7 例（含 3 个名字用例）+ 3 例补 R7 未覆盖的长度/建议缺口 + 1 例替补
DEFAULT_IDS = (
    "followup-01", "detail-pet-01", "detail-pet-02", "detail-time-01",
    "name-pet-01", "name-pet-02", "name-boss-01",
    "detail-pet-03", "followup-04", "detail-food-01", BACKFILL_ID,
)


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"cases": [], "summary": {}, "stop_reason": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"cases": [], "summary": {}, "stop_reason": ""}


def write_ledger(path: Path, cases: list, expected: int, stop_reason: str,
                 model: str, requests: int, errors: int,
                 run_note: str = "", record_run: bool = True) -> dict:
    valid = sum(1 for c in cases if c.get("status") == "valid")
    invalid = sum(1 for c in cases if c.get("status") == "invalid")
    unratable = sum(1 for c in cases if c.get("status") == "unratable")
    counters = {"over30_msgs": 0, "over40_msgs": 0, "name_replaced_turns": 0,
                "pet_generic_turns": 0, "cases_with_over30": 0,
                "cases_with_name_replaced": 0, "cases_with_pet_generic": 0}
    for c in cases:
        a = c.get("audit") or {}
        cc = a.get("counters") or {}
        if not cc:
            continue
        for k in ("over30_msgs", "over40_msgs", "name_replaced_turns",
                  "pet_generic_turns"):
            counters[k] += cc.get(k, 0)
        counters["cases_with_over30"] += 1 if cc.get("over30_msgs") else 0
        counters["cases_with_name_replaced"] += 1 if cc.get("name_replaced_turns") else 0
        counters["cases_with_pet_generic"] += 1 if cc.get("pet_generic_turns") else 0
    # 断点续跑会只发少数请求；把每次进程的计数留档，顶层的 requests/errors 用累计值。
    runs = []
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        runs = list(old.get("runs") or [])
    except Exception:
        runs = []
    if record_run and (requests or errors):
        runs.append({"note": run_note, "requests": requests, "errors": errors,
                     "stop_reason": stop_reason})
    payload = {
        "model": model,
        "cases": cases,
        "stop_reason": stop_reason,
        "requests": sum(r.get("requests", 0) for r in runs) if runs else requests,
        "errors": sum(r.get("errors", 0) for r in runs) if runs else errors,
        "last_run": {"requests": requests, "errors": errors},
        "runs": runs,
        "summary": {"expected": expected, "valid": valid,
                    "invalid": invalid, "unratable": unratable,
                    "pending": expected - valid - invalid},
        "counters": counters,
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


# ---------------- 逐项判据与三类计数 ----------------
# 小满自己的猫名（server/prompt_core.py PERSONA_RULES）。
SELF_PET = "团子"
_PET_SPECIES = ("猫", "狗", "兔子", "兔")
# 物种词作定语（猫粮/猫砂…）不算“把宠物泛化成猫/狗”。
_SPECIES_COMPOUND = ("猫粮", "猫砂", "猫窝", "猫抓板", "猫条", "猫咖", "猫草",
                     "狗粮", "狗窝", "狗绳", "狗证", "狗碗", "兔粮", "兔笼", "兔毛")
_PET_NAME_RE = re.compile(
    r"(?:猫|狗|兔(?:子)?)\s*(?:其实|原来|现在)?\s*"
    r"(?:叫|名字叫|取名叫|取名)\s*([\u4e00-\u9fa5A-Za-z]{1,4}?)"
    r"(?=不是|才|已经|今年|三岁|啦|啊|呀|呢|吧|[，。、！？,.!?\s]|$)")
_REASON_KINDS = (
    ("expect", "未体现期望内容"),
    ("emotion", "没有接住情绪"),
    ("advice", "建议轮次"),
    ("cross_turn_repeat", "跨轮重复追问"),
    ("msg_len", "单条过长"),
    ("parts", "条数"),
    ("total_len", "超长"),
)


def _reason_kind(reason: str) -> str:
    for kind, prefix in _REASON_KINDS:
        if reason.startswith(prefix):
            return kind
    return "other"


def _pet_names(text: str) -> list:
    return [n for n in _PET_NAME_RE.findall(text or "") if n]


def _species_refs(reply: str) -> list:
    """回复里把宠物称作“猫/狗/兔”的处数（排除猫粮/猫砂这类定语）。"""
    hits = []
    for word in _PET_SPECIES:
        start = 0
        while True:
            i = reply.find(word, start)
            if i < 0:
                break
            start = i + len(word)
            tail = reply[i:i + 4]
            if any(tail.startswith(c) for c in _SPECIES_COMPOUND):
                continue
            hits.append(word)
    return hits


def pet_audit(turns: list) -> tuple:
    """逐轮检查“名字被替换 / 宠物泛化”。

    名字被替换：用户的宠物已经有具体名字，回复却出现小满自己的猫名“团子”，
    且没有用用户给的名字。
    宠物泛化：本轮用户刚用名字提到宠物（或上一轮用了名字），回复只叫“猫/狗/兔”
    而不叫名字。
    """
    known, per_turn = [], []
    prev_named = False
    for i, t in enumerate(turns, 1):
        names = _pet_names(t.user)
        for n in names:
            if n not in known:
                known.append(n)
        reply = t.reply or ""
        user_named_now = any(n in t.user for n in known)
        in_scene = user_named_now or prev_named
        named_in_reply = any(n in reply for n in known)
        own_pet = SELF_PET in reply and SELF_PET not in known
        species = _species_refs(reply)
        replaced = bool(own_pet and known and not named_in_reply and in_scene)
        generic = bool(species and known and not named_in_reply and in_scene)
        per_turn.append({"turn": i, "user": t.user, "reply": reply,
                         "user_pet_names": names,
                         "name_replaced": replaced, "pet_generic": generic,
                         "species_words": species})
        prev_named = user_named_now
    return known, per_turn


def audit_case(case: dict, res, universal: dict) -> dict:
    """按判据逐项给结论，并统计超 30 字单条 / 名字被替换 / 宠物泛化。"""
    cfg = ev._case_cfg(case, universal)
    turns = res.turns
    over30, over40 = [], []
    for t in turns:
        idx = t.index + 1
        parts = ev.reply_parts(t.reply) or [t.reply]
        for j, part in enumerate(parts, 1):
            n = ev.visible_len(part)
            if n > ev.MSG_SPEC_CHARS:
                over30.append({"turn": idx, "part": j, "len": n, "text": part})
                if n > ev.MSG_HARD_CHARS:
                    over40.append({"turn": idx, "part": j, "len": n, "text": part})
    known, pet_rows = pet_audit(turns)
    replaced = [r for r in pet_rows if r["name_replaced"]]
    generic = [r for r in pet_rows if r["pet_generic"]]

    kinds = {}
    for f in res.failures or []:
        kind = _reason_kind(f.get("reason", ""))
        kinds.setdefault(kind, []).append(f)

    def detail(kind):
        items = kinds.get(kind, [])
        return "；".join("T%s %s" % (f.get("turn"), f.get("reason")) for f in items)

    too_many_parts = [t for t in turns
                      if len(ev.reply_parts(t.reply) or [t.reply]) > cfg["max_msgs"]]
    criteria = [
        {"key": "turns", "label": "轮数完整",
         "ok": len(turns) == len(case["turns"]),
         "detail": "%d/%d 轮" % (len(turns), len(case["turns"]))},
        {"key": "msg_len_30", "label": "单条≤%d 字（规格）" % ev.MSG_SPEC_CHARS,
         "ok": not over30 and not over40,
         "detail": "超 %d 字单条 %d 条%s" % (
             ev.MSG_SPEC_CHARS, len(over30),
             "：T%s" % "/T".join(str(x["turn"]) for x in over30) if over30 else "")},
        {"key": "msg_len_40", "label": "单条≤%d 字（硬线）" % ev.MSG_HARD_CHARS,
         "ok": not over40, "detail": detail("msg_len") or "无超硬线"},
        {"key": "parts", "label": "条数≤%d" % cfg["max_msgs"],
         "ok": not too_many_parts,
         "detail": "超条轮次 %d（%s）" % (
             len(too_many_parts), detail("parts") or "无")},
        {"key": "redlines", "label": "红线（套话/说教/危机/复读/问号/内部状态）",
         "ok": not kinds.get("other"),
         "detail": detail("other") or "无"},
        {"key": "cross_turn_repeat", "label": "跨轮不重复追问同一句",
         "ok": not kinds.get("cross_turn_repeat"),
         "detail": detail("cross_turn_repeat") or "无"},
        {"key": "expect", "label": "期望内容命中",
         "ok": (None if not case.get("expect_any") else not kinds.get("expect")),
         "detail": detail("expect") or ("不适用" if not case.get("expect_any") else "命中")},
        {"key": "emotion", "label": "情绪接住",
         "ok": (None if not case.get("require_emotion") else not kinds.get("emotion")),
         "detail": detail("emotion") or ("不适用" if not case.get("require_emotion") else "命中")},
        {"key": "advice", "label": "建议轮次≤%d" % ev._advice_limit(case, universal),
         "ok": not kinds.get("advice"), "detail": detail("advice") or "无超限"},
        {"key": "name_fidelity", "label": "名字保真（不换成“%s”）" % SELF_PET,
         "ok": (None if not known else not replaced),
         "detail": ("不适用（本轮用户没给宠物名）" if not known else
                    "名字被替换 %d 轮%s" % (
                        len(replaced),
                        "：T%s" % "/T".join(str(r["turn"]) for r in replaced) if replaced else ""))},
        {"key": "pet_generic", "label": "宠物不泛化（用名字而非“猫/狗”）",
         "ok": (None if not known else not generic),
         "detail": ("不适用（本轮用户没给宠物名）" if not known else
                    "泛化 %d 轮%s" % (
                        len(generic),
                        "：T%s" % "/T".join(str(r["turn"]) for r in generic) if generic else ""))},
    ]
    return {
        "criteria": criteria,
        "counters": {
            "over30_msgs": len(over30),
            "over40_msgs": len(over40),
            "name_replaced_turns": len(replaced),
            "pet_generic_turns": len(generic),
            "pet_names_seen": known,
        },
        "over30": over30,
        "over40": over40,
        "pet": pet_rows,
    }



def _record_result(rec: dict):
    """把账本里存的 turns/reasons 包成 audit_case 需要的形状（不联网）。"""
    turns = [SimpleNamespace(user=t.get("user", ""), reply=t.get("reply", ""),
                             text=t.get("reply", ""), index=i)
             for i, t in enumerate(rec.get("turns") or [])]
    return SimpleNamespace(turns=turns, failures=list(rec.get("reasons") or []),
                           passed=rec.get("passed", False))


def write_report(path: Path, payload: dict) -> None:
    """把账本渲染成可读账（每例原始输出全文 + 逐项判据 + 三类计数）。"""
    s = payload.get("summary", {})
    c = payload.get("counters", {})
    cases = payload.get("cases", [])
    n_pass = sum(1 for x in cases if x.get("status") == "valid" and x.get("passed"))
    n_fail = sum(1 for x in cases if x.get("status") == "valid" and not x.get("passed"))
    lines = [
        "# R11 实时复测账本（第三层证据：改后的真实输出）",
        "",
        "- 模型：`%s`" % payload.get("model", ""),
        "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "- 请求 %s / 错误 %s / 停手：%s" % (
            payload.get("requests", 0), payload.get("errors", 0),
            payload.get("stop_reason") or "无"),
        "- 可评样本 %d/%d（有效 %d、无效 %d、待跑 %d），另有不可评 %d 例（后端上限，与产品无关）" % (
            s.get("valid", 0), s.get("expected", 0), s.get("valid", 0),
            s.get("invalid", 0), s.get("pending", 0), s.get("unratable", 0)),
        "- 判定（新判据）：PASS %d / FAIL %d；上面的“可评 %d/%d”是样本覆盖，"
        "不是通过率" % (n_pass, n_fail, s.get("valid", 0), s.get("expected", 0)),
        "",
        "## 三类计数（全账本合计）",
        "",
        "| 指标 | 计数 | 涉及用例 |",
        "| --- | --- | --- |",
        "| 超过 %d 字单条 | %d 条 | %d 例 |" % (
            ev.MSG_SPEC_CHARS, c.get("over30_msgs", 0), c.get("cases_with_over30", 0)),
        "| 超过 %d 字单条（硬线） | %d 条 | - |" % (
            ev.MSG_HARD_CHARS, c.get("over40_msgs", 0)),
        "| 名字被替换（换成“%s”） | %d 轮 | %d 例 |" % (
            SELF_PET, c.get("name_replaced_turns", 0),
            c.get("cases_with_name_replaced", 0)),
        "| 宠物泛化（该叫名字却叫“猫/狗”） | %d 轮 | %d 例 |" % (
            c.get("pet_generic_turns", 0), c.get("cases_with_pet_generic", 0)),
        "",
        "## 逐例",
        "",
    ]
    unratable = [x for x in payload.get("cases", [])
                 if x.get("status") == "unratable"]
    missing = [x for x in payload.get("cases", [])
               if x.get("status") not in ("valid", "unratable")]
    if unratable:
        lines += ["### 不可评案例（保留记录，不计失败、不重跑）", ""]
        for x in unratable:
            lines.append("- `%s`（%s）：status=unratable；%s" % (
                x.get("id"), x.get("category"),
                x.get("note") or "后端上限，不可评"))
        lines.append("")
    if missing:
        lines += ["### 缺失案例（缺第三层证据，不用离线复算冒充）", ""]
        for x in missing:
            lines.append("- `%s`（%s）：status=%s，缺真实输出；原因 %s" % (
                x.get("id"), x.get("category"), x.get("status"),
                x.get("error") or x.get("reasons") or "未跑"))
        lines.append("")
    for x in payload.get("cases", []):
        if x.get("status") == "unratable":
            continue
        verdict = ("PASS" if x.get("passed") else "FAIL")
        if x.get("verdict") == "rubric_flip" or x.get("rubric_flip"):
            verdict = "FAIL（rubric_flip：判据补齐，非产品退化）"
        if x.get("passed_legacy"):
            verdict += "；旧判据 PASS"
        lines += ["### %s（%s）" % (x.get("id"), x.get("category")),
                  "",
                  "- status：%s；评测器结论：%s；尝试次数 %s" % (
                      x.get("status"), verdict, x.get("attempts", 0)),
                  ""]
        a = x.get("audit") or {}
        if a.get("criteria"):
            lines += ["**逐项判据**", "",
                      "| 判据 | 结论 | 依据 |",
                      "| --- | --- | --- |"]
            for it in a["criteria"]:
                ok = it.get("ok")
                mark = "不适用" if ok is None else ("通过" if ok else "未通过")
                lines.append("| %s | %s | %s |" % (
                    it.get("label"), mark,
                    str(it.get("detail") or "").replace("|", "\\|")))
            lines.append("")
        cc = a.get("counters") or {}
        if cc:
            lines += ["**计数**：超 %d 字单条 %d 条；名字被替换 %d 轮；宠物泛化 %d 轮" % (
                ev.MSG_SPEC_CHARS, cc.get("over30_msgs", 0),
                cc.get("name_replaced_turns", 0), cc.get("pet_generic_turns", 0)),
                ""]
        if x.get("reasons"):
            lines += ["**评测器失败项**", ""]
            for f in x["reasons"]:
                lines.append("- T%s：%s" % (f.get("turn"), f.get("reason")))
            lines.append("")
        if x.get("turns"):
            lines += ["**原始输出全文**", ""]
            for i, t in enumerate(x["turns"], 1):
                lines.append("- T%d 用户：%s" % (i, t.get("user")))
                lines.append("  - 小满（%d 字 / %d 条）：%s" % (
                    t.get("len", 0), t.get("parts", 0), t.get("reply")))
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    ap.add_argument("--key-retries", type=int, default=3,
                    help="凭据读取偶发超时的重试次数")
    ap.add_argument("--report", default="", help="另写一份可读账（.md）")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not ev.is_free_model(args.model):
        print("拒绝：非免费白名单模型 %s" % args.model, file=sys.stderr)
        return 2

    data = ev.load_cases(Path(args.cases))
    universal = data["universal"]
    by_id = {c["id"]: c for c in list(data["cases"]) + list(BACKFILL_CASES)}
    missing = [i for i in args.ids if i not in by_id]
    if missing:
        print("用例不存在：%s" % ", ".join(missing), file=sys.stderr)
        return 2
    cases = [by_id[i] for i in args.ids]
    expected = sum(1 for i in args.ids if i not in UNRATABLE_IDS)

    out_path = Path(args.out)
    old = {"cases": []} if args.reset else load_ledger(out_path)
    prior = {c["id"]: c for c in old.get("cases", [])}
    records = {i: prior.get(i, {"id": i, "category": by_id[i]["category"],
                                "replaces": by_id[i].get("replaces"),
                                "status": "pending", "passed": False,
                                "reasons": [], "turns": [], "attempts": 0})
               for i in args.ids}
    for cid, rec in records.items():
        if by_id[cid].get("replaces") and not rec.get("replaces"):
            rec["replaces"] = by_id[cid]["replaces"]
    # 续跑时补算旧样本的逐项判据（不重发请求、不动已有全文）
    for cid, rec in records.items():
        if rec.get("status") == "valid" and rec.get("turns") and not rec.get("audit"):
            rec["audit"] = audit_case(by_id[cid], _record_result(rec), universal)
    # followup-04：后端输出上限，保留记录、标成不可评，不再算失败、不再重跑
    for cid in UNRATABLE_IDS:
        rec = records.get(cid)
        if rec and rec.get("status") != "valid" and rec.get("reasons"):
            rec["status"] = "unratable"
            rec["note"] = "后端上限，不可评（finish_reason=length/读超时，与产品无关）"

    if args.dry_run:
        print("== 断点复测计划（dry-run，不发请求） ==")
        for c in cases:
            r = records[c["id"]]
            print("  %-20s %-6s %s" % (c["id"], c["category"],
                                       r.get("status", "pending")))
        print("  可评目标 %d：已有效 %d，待跑 %d，不可评 %d，模型 %s"
              % (expected,
                 sum(1 for r in records.values() if r.get("status") == "valid"),
                 sum(1 for i, r in records.items()
                     if r.get("status") not in ("valid", "unratable")),
                 sum(1 for r in records.values() if r.get("status") == "unratable"),
                 args.model))
        return 0

    key = ""
    for i in range(max(1, args.key_retries)):
        key = credentials.api_key()
        if key:
            break
        if i + 1 < args.key_retries:
            print("凭据读取失败，%d 秒后重试（%d/%d）"
                  % (5, i + 1, args.key_retries), file=sys.stderr, flush=True)
            time.sleep(5)
    if not key:
        print("没有可用的 API key（不打印来源），先配置再跑", file=sys.stderr)
        return 2

    backend = ev.ApiBackend(args.base, args.model, key, delay=args.delay,
                            timeout=args.timeout, now="周五 22:10",
                            max_tokens=args.max_tokens, retries=1,
                            max_requests=args.max_requests,
                            provider_name="openrouter-free",
                            stop_on_quota=True)
    started = time.time()
    stop_reason = ""

    for idx, case in enumerate(cases, 1):
        cid = case["id"]
        rec = records[cid]
        if rec.get("status") == "valid":
            print("[%d/%d] %-20s SKIP（已有有效样本）" % (idx, len(cases), cid),
                  flush=True)
            continue
        if rec.get("status") == "unratable":
            print("[%d/%d] %-20s SKIP（后端上限，不可评，保留记录）" % (idx, len(cases), cid),
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
                            "turns": snapshot_turns(res), "error": "",
                            "audit": audit_case(case, res, universal)})
                a = rec["audit"]["counters"]
                print("[%d/%d] %-16s %s（有效样本，第 %d 次；超30字单条 %d，名字被替换 %d，宠物泛化 %d）"
                      % (idx, len(cases), cid, "PASS" if res.passed else "FAIL", attempt,
                         a["over30_msgs"], a["name_replaced_turns"], a["pet_generic_turns"]),
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
        write_ledger(out_path, [records[i] for i in args.ids], expected,
                     stop_reason, args.model, backend.calls, backend.errors,
                     record_run=False)
        if stop_reason:
            break

    payload = write_ledger(out_path, [records[i] for i in args.ids], expected,
                          stop_reason, args.model, backend.calls, backend.errors,
                          run_note="ids=%s max_tokens=%s stop=%s" % (
                              ",".join(args.ids), args.max_tokens,
                              stop_reason or "none"))
    if args.report:
        write_report(Path(args.report), payload)
    s = payload["summary"]
    c = payload["counters"]
    print("\n账本：%s" % out_path)
    if args.report:
        print("可读账：%s" % args.report)
    print("有效 %d/%d，无效 %d，待跑 %d，请求 %d，错误 %d，用时 %.0fs%s"
          % (s["valid"], s["expected"], s["invalid"], s["pending"],
             backend.calls, backend.errors, time.time() - started,
             ("，停手：%s" % stop_reason) if stop_reason else ""))
    print("三类计数：超30字单条 %d 条（%d 例）、名字被替换 %d 轮（%d 例）、宠物泛化 %d 轮（%d 例）"
          % (c["over30_msgs"], c["cases_with_over30"],
             c["name_replaced_turns"], c["cases_with_name_replaced"],
             c["pet_generic_turns"], c["cases_with_pet_generic"]))
    if s["valid"] == s["expected"]:
        return 0
    return 4 if stop_reason else 1


if __name__ == "__main__":
    sys.exit(main())
