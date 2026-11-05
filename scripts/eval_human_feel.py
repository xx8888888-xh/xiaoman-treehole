#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_human_feel.py · 真人感评测器（W20，可跑 mock / 真实模型两种后端）

为什么要有这个脚本：
  W11 的 84 条 100% 是 mock 模板引擎的成绩；模板引擎的分数不能当真实模型分。
  同一个评测集、同一套判定，必须能换后端重跑，并在报告里明确区分
  “mock 分”与“真实模型分”。

用法：
  python scripts/eval_human_feel.py --backend mock
  python scripts/eval_human_feel.py --backend api --model <模型名> --delay 1.5
  python scripts/eval_human_feel.py --backend api --limit 12 --categories 工作吐槽,拒绝说话
  python scripts/eval_human_feel.py --backend mock --json out.json --md out.md

判定分三层：
  1. 通用硬约束（长度/条数/套话/总结/说教/危机话术/跑题代码/复读）
  2. 场景专项（沉默轮、拒绝说话轮、记忆引用自然度、遗忘后消失）
  3. 反套路统计（建议出现率、四字总结共情率、高频开头、会话内重复开头）

退出码：通过率低于 --min-rate（默认 0.98）→ 1；后端不可用 → 2。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

CASES_FILE = SERVER_DIR / "tests" / "human_feel_cases.json"
LOG = "[eval]"

# ---------------- 判定词表（与提示词硬约束一一对应） ----------------
BOILERPLATE_RE = re.compile(
    "首先，|其次，|再次，|最后，|总之|综上|总的来说|希望对你有帮助|希望对你|"
    "如有需要|建议你|你可以试试|作为一个朋友|作为AI|作为你的朋友|我在这里陪着你")
SUMMARY_RE = re.compile(
    "听起来你|你的意思是|换句话说|所以你是说|也就是说|我理解你|我明白你的意思")
LECTURE_RE = re.compile(
    "你应该|你必须|你要坚强|想开点|看开点|别想太多|你这是|我应该教|大道理")
ADVICE_RE = re.compile(
    "建议|试试|试着|不妨|最好|你可以|你应该|你必须|去看看|去看医生|"
    "早睡|早点睡|作息|手机放远|晒晒太阳|调整心态|放松心态|多沟通|找人聊聊")
CRISIS_RE = re.compile("12356|心理援助|需要真人的帮助|心理咨询")
QUESTION_RE = re.compile("[?？]")
PUSH_RE = re.compile("说说|然后呢|接着|聊聊|展开|怎么了|发生什么|在吗|还有呢|继续|快说")
CODE_RE = re.compile("```|def |import |SELECT|GROUP BY|HAVING|function|class ")
PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()~～—\-_…·]+")

# 高频开头（客服腔 / 模板腔）：出现率单独统计，不直接判失败
OPENING_TICS = ("听起来", "我理解", "确实", "其实", "老实说", "说真的",
                "首先", "嗯，", "你说的")
# 四字总结式共情：把对方的处境概括成一句空话
SUMMARY_EMPATHY = ("太不容易了", "辛苦了", "心疼你", "确实不容易", "真的很难",
                   "我能理解", "我懂你", "抱抱你", "能理解你", "很正常",
                   "这很正常", "不容易啊", "太难受了")
# 记忆回指的“像人”标记：只在用户本轮没提该记忆、却主动提起时要求
RECALL_MARKERS = ("你上次", "你之前", "你说过", "你提过", "上次说", "之前说",
                  "那个", "那件事", "还在", "怎么样了")
MUTE_RE = re.compile(
    "别问|不想说|不想聊|不想提|别追问|换个话题|不想讲|算了|不说了|别打听|别提")


def visible(text: str) -> str:
    return PUNCT_RE.sub("", (text or "").replace("||", ""))


def visible_len(text: str) -> int:
    return len(visible(text))


def reply_parts(reply: str) -> list:
    return [p.strip() for p in (reply or "").split("||") if p.strip()]


def assistant_text(payload: dict) -> str:
    parts = [payload.get("reply") or ""]
    hook = payload.get("hook")
    if hook:
        parts.append(str(hook))
    return "\n".join(parts)


def opening_key(text: str) -> str:
    """开头指纹：去掉标点后的前 6 个可见字。"""
    return visible(text)[:6]


def questions(text: str) -> int:
    return len(QUESTION_RE.findall(text or ""))


class Turn:
    __slots__ = ("user", "payload", "reply", "text", "parts", "index")

    def __init__(self, user: str, payload: dict, index: int):
        self.user = user
        self.payload = payload or {}
        self.reply = str(self.payload.get("reply") or "")
        self.text = assistant_text(self.payload)
        self.parts = reply_parts(self.reply)
        self.index = index


class CaseResult:
    def __init__(self, case: dict):
        self.case = case
        self.turns: list = []
        self.failures: list = []

    @property
    def passed(self) -> bool:
        return not self.failures

    def fail(self, turn_index: int, reason: str):
        self.failures.append({"case": self.case["id"], "category": self.case["category"],
                              "turn": turn_index, "reason": reason})


# ---------------- 判定 ----------------
def _case_cfg(case: dict, universal: dict) -> dict:
    return {
        "max_chars": case.get("max_chars", universal["max_chars"]),
        "max_msgs": case.get("max_msgs", universal["max_msgs"]),
        "max_advice_turns": case.get("max_advice_turns", universal["max_advice_turns"]),
        "max_questions": case.get("max_questions_per_reply",
                                  universal["max_questions_per_reply"]),
    }


def check_turn(result: CaseResult, turn: Turn, prev: list, cfg: dict,
               universal: dict) -> bool:
    """单轮通用判定。返回本轮是否带建议（供统计）。"""
    case = result.case
    reply = turn.reply
    idx = turn.index + 1
    if not reply.strip():
        result.fail(idx, "空回复")
        return False

    if visible_len(reply) > cfg["max_chars"]:
        result.fail(idx, "超长 %d>%d：%s" % (visible_len(reply), cfg["max_chars"], reply))
    if len(turn.parts) > cfg["max_msgs"]:
        result.fail(idx, "条数 %d>%d" % (len(turn.parts), cfg["max_msgs"]))
    m = BOILERPLATE_RE.search(reply)
    if m:
        result.fail(idx, "套话“%s”：%s" % (m.group(0), reply))
    m = SUMMARY_RE.search(reply)
    if m:
        result.fail(idx, "总结/复述“%s”：%s" % (m.group(0), reply))
    m = LECTURE_RE.search(reply)
    if m:
        result.fail(idx, "说教“%s”：%s" % (m.group(0), reply))
    if case["category"] != "危机" and CRISIS_RE.search(reply):
        result.fail(idx, "非危机场景用了危机话术：%s" % reply)
    if questions(reply) > cfg["max_questions"]:
        result.fail(idx, "问号过多 %d>%d：%s" % (questions(reply), cfg["max_questions"], reply))
    for bad in case.get("forbid_any", []):
        if bad in reply:
            result.fail(idx, "出现禁用词“%s”：%s" % (bad, reply))
    if case["category"] == "跑题" and CODE_RE.search(reply):
        result.fail(idx, "跑题场景输出了代码/步骤")
    # 说明：模型偶尔不按 JSON 输出。线上 web/js/api.js 有纯文本兜底、不会拒答，
    # 所以协议不合规不计入“像不像人”的失败，只作为单独指标统计。

    # 沉默轮：短、不追问、不逼话、不连续两问
    if case.get("silence"):
        if visible_len(reply) > 40:
            result.fail(idx, "沉默轮过长 %d>40" % visible_len(reply))
        if len(turn.parts) > 2:
            result.fail(idx, "沉默轮条数 %d>2" % len(turn.parts))
        if QUESTION_RE.search(reply):
            result.fail(idx, "沉默轮追问：%s" % reply)
        m = PUSH_RE.search(reply)
        if m:
            result.fail(idx, "沉默轮逼话“%s”：%s" % (m.group(0), reply))

    # 拒绝说话：真的停下来，不打听细节，下一轮不翻旧账
    if case.get("refusal") or case["category"] == "拒绝说话":
        if visible_len(reply) > 40:
            result.fail(idx, "拒绝轮过长 %d>40：%s" % (visible_len(reply), reply))
        if QUESTION_RE.search(reply):
            result.fail(idx, "拒绝轮还在追问：%s" % reply)
        m = PUSH_RE.search(reply)
        if m:
            result.fail(idx, "拒绝轮逼话“%s”：%s" % (m.group(0), reply))
    for word in case.get("mute_any", []):
        if word in turn.text:
            result.fail(idx, "拒绝后仍翻旧账“%s”：%s" % (word, turn.text))

    # 记忆自然度：用户本轮没提、却被主动回溯 → 必须有“像人”的回指标记；
    # 同一轮最多引一条；被“忘了/别记”后必须消失。
    values = case.get("memory_values", [])
    if values:
        hit = [v for v in values if v and v in turn.text]
        if len(hit) > 1:
            result.fail(idx, "同一轮连引 %d 条记忆：%s" % (len(hit), turn.text))
        for v in hit:
            if v not in turn.user and not any(k in turn.text for k in RECALL_MARKERS):
                result.fail(idx, "记忆引用不像回忆（缺“你上次/之前说”）：%s" % turn.text)
    for start, vals in (case.get("forget_after") or {}).items():
        if turn.index >= int(start):
            for v in vals:
                if v in turn.text:
                    result.fail(idx, "说了“忘了”之后又提“%s”：%s" % (v, turn.text))

    # 复读：不逐字重复；不与上一轮同开头 4 字
    for p in prev:
        if reply == p.reply:
            result.fail(idx, "逐字复读历史回复：%s" % reply)
            break
    if prev and visible_len(prev[-1].reply) >= 4 and visible_len(reply) >= 4:
        if opening_key(prev[-1].reply)[:4] == opening_key(reply)[:4] \
                and opening_key(reply):
            result.fail(idx, "开头复读“%s”" % opening_key(reply)[:4])

    return bool(ADVICE_RE.search(reply))


def check_case_expect(result: CaseResult, universal: dict):
    case = result.case
    if not result.turns:
        return
    expect = case.get("expect_any")
    if expect:
        pool = result.turns if case.get("expect_scope") == "any" else result.turns[-1:]
        if not any(w in t.text for t in pool for w in expect):
            result.fail(len(result.turns),
                        "未体现期望内容（期望其一：%s；实际：%s）"
                        % ("/".join(expect), result.turns[-1].text))
    advice_turns = sum(1 for t in result.turns if ADVICE_RE.search(t.reply))
    limit = case.get("max_advice_turns", universal["max_advice_turns"])
    if advice_turns > limit:
        result.fail(0, "建议轮次 %d>%d（默认不给建议）" % (advice_turns, limit))


# ---------------- 后端 ----------------
class MockBackend:
    name = "mock"

    def __init__(self, cases_file: Path = CASES_FILE):
        import mock_api  # 延迟导入，--help 不依赖服务端
        self.mock = mock_api
        self.cases_file = cases_file

    def reset_case(self, case: dict):
        sid = "hf-eval-%s" % case["id"]
        self.mock._STATE._sessions.pop(sid, None)
        self.mock._STATE._loaded.discard(sid)
        random.seed(20261105 + zlib.crc32(case["id"].encode("utf-8")))
        self.sid = sid

    def chat(self, history: list, user: str, case: dict) -> dict:
        history.append({"role": "user", "content": user})
        return self.mock.make_reply(history, self.sid)

    def snapshot(self) -> dict:
        return {"memories": dict(self.mock._STATE.get_memories(self.sid)),
                "silent": self.mock._STATE.get_session(self.sid).get("no_probe_until", 0)}

    def cleanup(self):
        pass


class ApiBackend:
    """真实模型后端：走 OpenAI 兼容 /chat/completions。

    与线上服务端一致：system = prompt_core.system_prompt(memory_section=…)，
    记忆由 memory_slots.SlotStore 维护（每轮先取旧记忆，再 ingest 本轮）。
    """
    name = "api"

    def __init__(self, base: str, model: str, key: str, delay: float = 1.0,
                 timeout: float = 60.0, temperature: float = 0.85,
                 now: str = "周五 22:10", max_tokens: int = 1500,
                 retries: int = 3, dry_run: bool = False):
        import prompt_core
        import memory_slots
        self.prompt_core = prompt_core
        self.memory_slots = memory_slots
        self.base = (base or "").rstrip("/")
        self.model = model
        self.key = key
        self.delay = delay
        self.timeout = timeout
        self.temperature = temperature
        self.now = now
        self.max_tokens = max_tokens
        self.retries = retries
        self.dry_run = dry_run
        self.calls = 0
        self.errors = 0
        self.throttled = 0
        self.slots = None
        self.sid = ""

    def reset_case(self, case: dict):
        self.slots = self.memory_slots.SlotStore()
        self.sid = "api-%s" % case["id"]

    def _url(self) -> str:
        return self.base + "/chat/completions"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": "xiaoman-human-feel-eval/1.0"}
        if self.key:
            h["Authorization"] = "Bearer %s" % self.key
        return h

    def _request(self, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._url(), data=data, method="POST",
                                     headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def chat(self, history: list, user: str, case: dict) -> dict:
        mem = self.slots.render(self.sid, user) if self.slots else ""
        system = self.prompt_core.system_prompt(memory_section=mem, now=self.now,
                                               strict_json=True)
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user})
        body = {"model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "messages": messages}
        raw = ""
        last_err = ""
        reasoning_only = False
        for attempt in range(self.retries):
            try:
                self.calls += 1
                obj = self._request(body)
                choice = (obj.get("choices") or [{}])[0]
                raw = (choice.get("message") or {}).get("content") or ""
                if not raw and choice.get("finish_reason") == "length":
                    reasoning_only = bool((choice.get("message") or {}).get("reasoning"))
                break
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8")[:200]
                except Exception:
                    pass
                last_err = "HTTP %s %s" % (e.code, detail)
                if e.code in (429, 503, 502):
                    self.throttled += 1
                    time.sleep(min(30, 4 * (attempt + 1)))
                    continue
                break
            except Exception as e:  # 超时/网络
                last_err = "%s: %s" % (type(e).__name__, e)
                time.sleep(min(10, 2 * (attempt + 1)))
        if not raw:
            self.errors += 1
            if reasoning_only:
                return {"reply": "", "hook": None,
                        "_backend_error": "模型把 max_tokens 用在 reasoning，content 为空"}
            return {"reply": "", "hook": None, "_backend_error": last_err or "空响应"}
        payload = parse_model_json(raw)
        if not payload.get("_parse_error"):
            self.slots.ingest(self.sid, user)
            updates = payload.get("memory_updates")
            if isinstance(updates, dict) and updates:
                self.slots.apply_updates(self.sid, updates, source="model")
        if self.delay:
            time.sleep(self.delay)
        return payload

    def snapshot(self) -> dict:
        if not self.slots:
            return {}
        return {"slots": [{"key": s["key"], "value": s["value"]}
                          for s in self.slots.all(self.sid)]}

    def cleanup(self):
        pass


def parse_model_json(raw: str) -> dict:
    """真实模型输出解析：容忍 ```json 围栏、前后缀、半截 JSON。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("reply"), str):
            return obj
    # 降级：取 JSON 里的 reply 字符串（哪怕整段不合法）
    m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        try:
            reply = json.loads('"%s"' % m.group(1))
        except ValueError:
            reply = m.group(1)
        return {"reply": reply, "_parse_error": True}
    return {"reply": text, "_parse_error": True}


# ---------------- 评测主流程 ----------------
def load_cases(path: Path = CASES_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_case(case: dict, backend, universal: dict) -> tuple:
    """返回 (CaseResult, 建议轮次数)。"""
    backend.reset_case(case)
    result = CaseResult(case)
    cfg = _case_cfg(case, universal)
    history: list = []
    prev: list = []
    advice_turns = 0
    for idx, user in enumerate(case["turns"]):
        payload = backend.chat(history, user, case)
        if payload.get("_backend_error"):
            result.fail(idx + 1, "后端不可用：%s" % payload["_backend_error"])
            break
        turn = Turn(user, payload, idx)
        result.turns.append(turn)
        if check_turn(result, turn, prev, cfg, universal):
            advice_turns += 1
        prev.append(turn)
        history.append({"role": "assistant", "content": turn.reply or " "})
    check_case_expect(result, universal)
    return result, advice_turns


def evaluate(cases_file: Path, backend, limit: int = 0, categories=None,
             on_case=None) -> dict:
    data = load_cases(cases_file)
    universal = data["universal"]
    cases = data["cases"]
    if categories:
        cats = set(categories)
        cases = [c for c in cases if c["category"] in cats]
    if limit:
        cases = cases[:limit]

    results = []
    advice_turns = 0
    total_cases = len(cases)
    for i, case in enumerate(cases, 1):
        res, adv = run_case(case, backend, universal)
        advice_turns += adv
        results.append(res)
        if not on_case:
            print("%s [%d/%d] %-4s %s" % (LOG, i, total_cases, case["id"],
                                          "PASS" if res.passed else "FAIL"),
                  flush=True)
        elif on_case:
            on_case(res)
    metrics = compute_metrics(results, advice_turns)
    return {"results": results, "metrics": metrics, "universal": universal}


def compute_metrics(results: list, advice_turns: int) -> dict:
    """反套路指标：建议率、总结式共情、高频开头、会话内重复开头、记忆引用。"""
    turns = [t for r in results for t in r.turns]
    total = len(turns) or 1
    advice_rate = advice_turns / total
    summary_empathy = sum(1 for t in turns if any(w in t.reply for w in SUMMARY_EMPATHY))
    tic_hits = Counter()
    for t in turns:
        for w in OPENING_TICS:
            if w in t.reply:
                tic_hits[w] += 1

    # 会话内重复开头：同一会话里，后出现的开头指纹与之前的雷同
    repeat = 0
    session_turns = 0
    for r in results:
        seen = set()
        for t in r.turns:
            key = opening_key(t.reply)
            if not key:
                continue
            session_turns += 1
            if key in seen:
                repeat += 1
            seen.add(key)

    # 记忆引用自然度：主动回溯时带“你上次/之前说”的比例
    recall_checks, recall_natural = 0, 0
    for r in results:
        for v in r.case.get("memory_values", []):
            for t in r.turns:
                if v in t.text and v not in t.user:
                    recall_checks += 1
                    if any(k in t.text for k in RECALL_MARKERS):
                        recall_natural += 1

    return {
        "turns": len(turns),
        "protocol_errors": sum(1 for t in turns if t.payload.get("_parse_error")),
        "advice_turns": advice_turns,
        "advice_rate": round(advice_rate, 4),
        "summary_empathy_turns": summary_empathy,
        "summary_empathy_rate": round(summary_empathy / total, 4),
        "opening_tic_hits": dict(tic_hits.most_common()),
        "opening_tic_rate": round(sum(tic_hits.values()) / total, 4),
        "session_turns": session_turns,
        "repeat_opening": repeat,
        "repeat_opening_rate": round(repeat / (session_turns or 1), 4),
        "recall_checks": recall_checks,
        "recall_natural": recall_natural,
        "recall_natural_rate": round(recall_natural / (recall_checks or 1), 4),
    }


def summarize(data: dict) -> dict:
    results = data["results"]
    passed = [r for r in results if r.passed]
    by_cat = OrderedDict()
    for r in results:
        cat = r.case["category"]
        st = by_cat.setdefault(cat, {"passed": 0, "total": 0})
        st["total"] += 1
        if r.passed:
            st["passed"] += 1
    reasons = Counter(f["reason"].split("：")[0].split("“")[0] for r in results
                      for f in r.failures)
    return {
        "cases": len(results),
        "passed": len(passed),
        "rate": round(len(passed) / (len(results) or 1), 4),
        "by_category": by_cat,
        "failure_reasons": dict(reasons.most_common()),
        "failures": [{"case": r.case["id"], "category": r.case["category"],
                      "reasons": [f["reason"] for f in r.failures]}
                     for r in results if not r.passed],
        "metrics": data["metrics"],
    }


def render_markdown(summary: dict, backend_name: str, model: str = "",
                    notes: str = "") -> str:
    m = summary["metrics"]
    lines = ["### 真人感评测（后端：%s%s）" % (backend_name, (" · " + model) if model else ""),
             "",
             "| 场景 | 通过 | 通过率 |", "|---|---|---|",
             "| %d | %d | %.1f%% |" % (summary["cases"], summary["passed"],
                                       summary["rate"] * 100),
             "", "| 类别 | 通过/总数 |", "|---|---|"]
    for cat, st in summary["by_category"].items():
        lines.append("| %s | %d/%d |" % (cat, st["passed"], st["total"]))
    lines += ["", "| 反套路指标 | 实测 | 目标 |", "|---|---|---|",
              "| 建议出现率 | %.1f%% | ≤25%% |" % (m["advice_rate"] * 100),
              "| 四字总结式共情 | %.1f%% | 越低越好 |" % (m["summary_empathy_rate"] * 100),
              "| 高频开头（听起来/我理解/确实…） | %.1f%% | 越低越好 |"
              % (m["opening_tic_rate"] * 100),
              "| 会话内重复开头 | %.1f%% | ≤8%% |" % (m["repeat_opening_rate"] * 100),
              "| 记忆引用自然度 | %d/%d | 全部带“上次/之前” |"
              % (m["recall_natural"], m["recall_checks"]),
              "| JSON 协议不合规 | %d/%d 轮 | 仅统计，客户端有纯文本兜底 |"
              % (m.get("protocol_errors", 0), m["turns"])]
    if m.get("opening_tic_hits"):
        lines.append("| 开头词分布 | %s | — |"
                     % "、".join("%s×%d" % (k, v) for k, v in
                                 m["opening_tic_hits"].items()))
    if summary["failure_reasons"]:
        lines += ["", "失败原因归类：%s"
                  % "；".join("%s×%d" % (k, v) for k, v in
                             summary["failure_reasons"].items())]
    if summary["failures"]:
        lines += ["", "失败案例（前 5）："]
        for f in summary["failures"][:5]:
            lines.append("- [%s/%s] %s" % (f["category"], f["case"],
                                           " / ".join(f["reasons"][:3])))
    if notes:
        lines += ["", notes]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真人感评测（mock / 真实模型）")
    ap.add_argument("--backend", choices=("mock", "api"), default="mock")
    ap.add_argument("--cases", default=str(CASES_FILE))
    ap.add_argument("--base", default=os.environ.get("AI_API_BASE") or
                    "https://openrouter.ai/api/v1")
    ap.add_argument("--model", default=os.environ.get("AI_MODEL") or
                    "nvidia/nemotron-3.5-lightning:free")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--categories", default="")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--now", default="周五 22:10")
    ap.add_argument("--min-rate", type=float, default=0.98)
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.backend == "mock":
        backend = MockBackend(Path(args.cases))
    else:
        import credentials
        key = os.environ.get("AI_API_KEY") or credentials.api_key()
        if not key:
            print("%s 未找到 API key（AI_API_KEY / 凭据管理器均不可用）" % LOG, file=sys.stderr)
            return 2
        backend = ApiBackend(args.base, args.model, key, delay=args.delay,
                             timeout=args.timeout, now=args.now,
                             max_tokens=args.max_tokens, retries=args.retries)

    cats = [c for c in args.categories.split(",") if c]

    progress = {"n": 0}

    def on_case(res):
        progress["n"] += 1
        print("%s [%d] %-4s %s" % (LOG, progress["n"],
                                   "PASS" if res.passed else "FAIL", res.case["id"]),
              flush=True)
        if not res.passed:
            for f in res.failures[:3]:
                print("      - %s" % f["reason"], flush=True)

    started = time.time()
    data = evaluate(Path(args.cases), backend, limit=args.limit, categories=cats,
                    on_case=on_case)
    summary = summarize(data)
    if args.backend == "api":
        summary["backend_calls"] = getattr(backend, "calls", 0)
        summary["backend_errors"] = getattr(backend, "errors", 0)
        summary["backend_throttled"] = getattr(backend, "throttled", 0)
    summary["backend"] = backend.name
    summary["model"] = args.model if args.backend == "api" else "mock-template"
    summary["seconds"] = round(time.time() - started, 1)
    if args.backend == "api" and summary["backend_errors"]:
        summary["notes"] = ("后端调用失败 %d 次（限流 %d 次）；失败用例不计入质量分"
                            % (summary["backend_errors"], summary["backend_throttled"]))
    else:
        summary["notes"] = ("仅 mock 模板引擎，不代表真实模型表现"
                            if args.backend == "mock" else "")

    md = render_markdown(summary, summary["backend"], summary["model"], summary["notes"])
    print(md)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print("%s 明细写入 %s" % (LOG, args.json))
    if args.md:
        Path(args.md).write_text(md + "\n", encoding="utf-8")
        print("%s 报告写入 %s" % (LOG, args.md))

    if summary.get("backend_errors") and summary["passed"] == 0:
        return 2
    if summary["rate"] < args.min_rate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
