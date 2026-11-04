#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_human_feel_eval.py · W11 真人感多轮对话评测（回归）

- 集子：server/tests/human_feel_cases.json（64 段 2-4 轮对话，覆盖 10 类场景）
- 引擎：server/mock_api.py 的 mock 引擎（与线上离线模式同一套回复）
- 判定：提示词硬约束可直接落成的通用规则（长度/条数/套话/总结/说教/建议轮次/
  复读/危机话术/跑题代码）+ 每条用例的人类判定标准（forbid_any / expect_any / silence）

注意：本文件只“测”不改引擎；mock 模板层的不足会如实计入失败，不调低标准。
运行：python -m pytest server/tests -q
"""
import json
import random
import re
import sys
import unittest
import zlib
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import mock_api  # noqa: E402
import memory_slots as ms  # noqa: E402

CASES_FILE = TESTS_DIR / "human_feel_cases.json"

BOILERPLATE_RE = re.compile(
    "首先|其次|再次|最后|总之|综上|总的来说|希望对你有帮助|希望对你|如有需要|"
    "建议你|你可以试试|作为一个朋友|作为AI")
SUMMARY_RE = re.compile(
    "听起来你|你的意思是|换句话说|所以你是说|也就是说|我理解你|我明白你的意思")
LECTURE_RE = re.compile(
    "你应该|你必须|你要坚强|想开点|看开点|别想太多|你这是|我应该教|大道理")
ADVICE_RE = re.compile(
    "建议|试试|试着|不妨|最好|你可以|应该|必须|方案|步骤|分析|去看看|去看医生|"
    "早睡|早点睡|作息|手机放远|晒晒太阳")
CRISIS_RE = re.compile("12356|心理援助|需要真人的帮助|心理咨询")
QUESTION_RE = re.compile("[?？]")
PUSH_RE = re.compile("说说|然后呢|接着|聊聊|展开|怎么了|发生什么|在吗|还有呢|继续")
CODE_RE = re.compile("```|def |import |SELECT|GROUP BY|HAVING|function|class ")
PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()]")


def load_cases():
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def reply_text(payload):
    return (payload or {}).get("reply") or ""


def reply_parts(reply):
    return [p.strip() for p in reply.split("||") if p.strip()]


def visible_len(reply):
    return len(PUNCT_RE.sub("", reply.replace("||", "")))


def assistant_text(payload):
    """用于记忆连续性判定：正文 + 追问气泡（气泡也是 ta 会看到的一句话）"""
    parts = [reply_text(payload)]
    hook = (payload or {}).get("hook")
    if hook:
        parts.append(str(hook))
    return "\n".join(parts)


class TurnResult:
    def __init__(self, payload):
        self.payload = payload or {}
        self.reply = reply_text(self.payload)
        self.parts = reply_parts(self.reply)
        self.text = assistant_text(self.payload)
        self.hook = self.payload.get("hook") or ""


def run_case(case):
    """返回 (passed, failures, turn_results, advice_turns)"""
    sid = "hf-eval-%s" % case["id"]
    # 会话隔离：同一 pytest 进程内多次跑也不串
    mock_api._STATE._sessions.pop(sid, None)
    mock_api._STATE._loaded.discard(sid)
    random.seed(20261105 + zlib.crc32(case["id"].encode("utf-8")))

    history = []
    results = []
    failures = []
    advice_turns = 0
    universal = load_cases()["universal"]
    max_chars = case.get("max_chars", universal["max_chars"])
    max_msgs = case.get("max_msgs", universal["max_msgs"])
    max_advice_turns = case.get("max_advice_turns", universal["max_advice_turns"])
    max_questions = case.get("max_questions_per_reply",
                             universal["max_questions_per_reply"])

    for idx, user_text in enumerate(case["turns"]):
        history.append({"role": "user", "content": user_text})
        payload = mock_api.make_reply(history, sid)
        res = TurnResult(payload)
        results.append(res)
        tag = "%s#%d" % (case["id"], idx + 1)
        reply = res.reply

        if not reply.strip():
            failures.append("%s 空回复" % tag)
            history.append({"role": "assistant", "content": " "})
            continue

        if visible_len(reply) > max_chars:
            failures.append("%s 超长 %d>%d：%s" % (tag, visible_len(reply), max_chars, reply))
        if len(res.parts) > max_msgs:
            failures.append("%s 条数 %d>%d" % (tag, len(res.parts), max_msgs))
        if BOILERPLATE_RE.search(reply):
            failures.append("%s 套话：%s" % (tag, BOILERPLATE_RE.search(reply).group(0)))
        if SUMMARY_RE.search(reply):
            failures.append("%s 总结/复述：%s" % (tag, SUMMARY_RE.search(reply).group(0)))
        if LECTURE_RE.search(reply):
            failures.append("%s 说教：%s" % (tag, LECTURE_RE.search(reply).group(0)))
        if case["category"] != "危机" and CRISIS_RE.search(reply):
            failures.append("%s 非危机场景用了危机话术" % tag)
        if len(QUESTION_RE.findall(reply)) > max_questions:
            failures.append("%s 问号过多" % tag)
        for bad in case.get("forbid_any", []):
            if bad in reply:
                failures.append("%s 出现禁用词“%s”" % (tag, bad))
        if case["category"] == "跑题" and CODE_RE.search(reply):
            failures.append("%s 跑题场景输出了代码/步骤" % tag)

        if case.get("silence"):
            if visible_len(reply) > 40:
                failures.append("%s 沉默轮回复过长 %d>40" % (tag, visible_len(reply)))
            if len(res.parts) > 2:
                failures.append("%s 沉默轮条数 %d>2" % (tag, len(res.parts)))
            if QUESTION_RE.search(reply):
                failures.append("%s 沉默轮追问：%s" % (tag, reply))
            if PUSH_RE.search(reply):
                failures.append("%s 沉默轮逼话：%s" % (tag, PUSH_RE.search(reply).group(0)))

        if ADVICE_RE.search(reply):
            advice_turns += 1

        # 复读：不与任何历史回复逐字相同；开头 4 字不连续重复
        for prev in results[:-1]:
            if reply == prev.reply:
                failures.append("%s 逐字复读上一轮：%s" % (tag, reply))
                break
        if len(results) >= 2:
            a, b = results[-2].reply, reply
            if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
                failures.append("%s 开头复读“%s”" % (tag, b[:4]))

        history.append({"role": "assistant", "content": reply})

    if advice_turns > max_advice_turns:
        failures.append("%s 建议轮次 %d>%d（默认不给建议）"
                        % (case["id"], advice_turns, max_advice_turns))

    expect = case.get("expect_any")
    if expect and results:
        final_text = results[-1].text
        if not any(word in final_text for word in expect):
            failures.append("%s 未体现记忆/追问（期望其一：%s；实际：%s）"
                            % (case["id"], "/".join(expect), results[-1].text))

    return not failures, failures, results, advice_turns


class HumanFeelEvalTest(unittest.TestCase):
    def test_case_set_size_and_coverage(self):
        data = load_cases()
        cases = data["cases"]
        self.assertGreaterEqual(len(cases), 60, "评测集不得少于 60 条")
        cats = {c["category"] for c in cases}
        self.assertGreaterEqual(len(cats), 10, "至少覆盖 10 类场景")
        ids = [c["id"] for c in cases]
        self.assertEqual(len(ids), len(set(ids)), "用例 id 必须唯一")
        for c in cases:
            self.assertTrue(c["turns"], "%s 缺对话轮次" % c["id"])
            self.assertTrue(c["criteria"], "%s 缺判定标准" % c["id"])

    def test_memory_slot_chain_covers_followup_cases(self):
        """服务端记忆槽若接入：追问类里所有"应记得"用例都应能召回。

        mock 引擎自身的记忆提取/冷却有已知缺陷（见 docs/HUMAN_FEEL.md 遗留项），
        这条用例验证的是 W11 新增的 memory_slots 链路本身可用。
        """
        store = ms.SlotStore()
        total, covered, misses = 0, 0, []
        for case in load_cases()["cases"]:
            if case["category"] != "追问" or not case.get("expect_any"):
                continue
            sid = "slot-%s" % case["id"]
            for text in case["turns"]:
                store.ingest(sid, text)
            injected = store.render(sid, case["turns"][-1])
            total += 1
            if any(w in injected for w in case["expect_any"]):
                covered += 1
            else:
                misses.append("%s 期望 %s / 实际注入：%s"
                              % (case["id"], case["expect_any"], injected or "(空)"))
        self.assertGreater(total, 0)
        self.assertEqual(covered, total,
                         "记忆槽链路漏召回：\n" + "\n".join(misses))

    def test_pass_rate_and_regression(self):
        data = load_cases()
        cases = data["cases"]
        passed, failures = [], []
        by_cat = {}
        for case in cases:
            ok, fails, _results, _adv = run_case(case)
            cat = case["category"]
            stat = by_cat.setdefault(cat, {"passed": 0, "total": 0})
            stat["total"] += 1
            if ok:
                stat["passed"] += 1
                passed.append(case["id"])
            else:
                failures.append((case["id"], cat, fails))

        rate = len(passed) / len(cases)
        lines = ["真人感评测：%d/%d 通过（%.1f%%）"
                 % (len(passed), len(cases), rate * 100)]
        for cat, stat in by_cat.items():
            lines.append("  %-6s %d/%d" % (cat, stat["passed"], stat["total"]))
        if failures:
            lines.append("失败明细：")
            for cid, cat, fails in failures:
                for f in fails[:3]:
                    lines.append("  [%s/%s] %s" % (cat, cid, f))
        print("\n".join(lines))

        # 回归门槛：模板层现状 ≥75%；提示词/记忆链路改动不得让通过率再掉。
        self.assertGreaterEqual(rate, 0.75, "\n".join(lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
