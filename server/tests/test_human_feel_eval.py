#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_human_feel_eval.py · 真人感多轮对话评测（W11 起，W20 扩集 + 反套路）

- 集子：server/tests/human_feel_cases.json（112 段 2-4 轮对话，覆盖 16 类场景）
- 判定：统一由 scripts/eval_human_feel.py 提供（mock / 真实模型共用同一套规则），
  避免“pytest 一套标准、评测脚本另一套标准”。
- 本文件只测不改引擎；mock 模板层的不足如实计入失败，不调低标准。
- 真实模型跑分：python scripts/eval_human_feel.py --backend api --model <模型>

运行：python -m pytest server/tests -q
"""
import json
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
ROOT = SERVER_DIR.parent
SCRIPTS_DIR = ROOT / "scripts"
for p in (SERVER_DIR, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import memory_slots as ms  # noqa: E402
import eval_human_feel as ev  # noqa: E402

CASES_FILE = SERVER_DIR / "tests" / "human_feel_cases.json"

REQUIRED_CATEGORIES = {
    "工作吐槽": 6, "亲密关系": 6, "兴奋分享": 6, "拒绝说话": 6, "长期记忆": 4,
    "沉默": 6, "追问": 6, "情绪低落": 8, "日常倾诉": 8, "具体细节": 20,
    "愤怒": 6, "暧昧": 6, "玩笑": 6, "跑题": 6, "拒绝建议": 6, "重复话题": 6,
}


def _run_all():
    data = ev.load_cases(CASES_FILE)
    backend = ev.MockBackend(CASES_FILE)
    results = []
    for case in data["cases"]:
        res, _advice = ev.run_case(case, backend, data["universal"])
        results.append(res)
    advice_turns = sum(1 for r in results for t in r.turns
                       if ev.ADVICE_RE.search(t.reply))
    metrics = ev.compute_metrics(results, advice_turns)
    return data, results, metrics


class HumanFeelEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.results, cls.metrics = _run_all()

    def test_case_set_size_and_coverage(self):
        cases = self.data["cases"]
        self.assertGreaterEqual(len(cases), 60, "评测集不得少于 60 条")
        cats = {c["category"] for c in cases}
        self.assertGreaterEqual(len(cats), 10, "至少覆盖 10 类场景")
        self.assertEqual(len(cases), len({c["id"] for c in cases}), "用例 id 必须唯一")
        for c in cases:
            self.assertTrue(c["turns"], "%s 缺对话轮次" % c["id"])
            self.assertTrue(c["criteria"], "%s 缺判定标准" % c["id"])
        for cat, least in REQUIRED_CATEGORIES.items():
            self.assertIn(cat, cats, "缺少场景类别：%s" % cat)
            n = sum(1 for c in cases if c["category"] == cat)
            self.assertGreaterEqual(n, least, "%s 仅 %d 条，少于 %d" % (cat, n, least))

    def test_memory_slot_chain_covers_followup_cases(self):
        """服务端记忆槽若接入：追问/长期记忆里所有“应记得”用例都应能召回。"""
        store = ms.SlotStore()
        total, covered, misses = 0, 0, []
        for case in self.data["cases"]:
            if case["category"] not in ("追问", "长期记忆"):
                continue
            if not case.get("expect_any") or case.get("forget_after"):
                continue
            sid = "slot-%s" % case["id"]
            for text in case["turns"]:
                store.ingest(sid, text)
            injected = "\n".join(store.render(sid, text) for text in case["turns"])
            total += 1
            if any(w in injected for w in case["expect_any"]):
                covered += 1
            else:
                misses.append("%s 期望 %s / 实际注入：%s"
                              % (case["id"], case["expect_any"], injected or "(空)"))
        self.assertGreater(total, 0)
        self.assertEqual(covered, total,
                         "记忆槽链路漏召回：\n" + "\n".join(misses))

    def test_case_stats_keeps_prev_replies(self):
        """失败存档必须带失败轮之前 1-2 轮原文；取不到时为空，不编造。"""
        res = ev.CaseResult({"id": "t1", "category": "具体细节",
                             "turns": ["a", "b", "c"]})
        for i, text in enumerate(["甲", "乙", "丙"]):
            res.turns.append(ev.Turn("u", {"reply": text}, i))
        res.fail(1, "首轮失败")
        res.fail(3, "第三轮失败")
        first, third = ev.case_stats(res)["failures"]
        self.assertEqual(first["prev_replies"], [])
        self.assertEqual(third["prev_replies"], ["甲", "乙"])

        # 用例级失败（turn=0）与首轮失败都不崩，且不带上一轮
        res2 = ev.CaseResult({"id": "t2", "category": "追问", "turns": ["a"]})
        res2.turns.append(ev.Turn("u", {"reply": "甲"}, 0))
        res2.fail(0, "建议轮次 1>0")
        self.assertEqual(ev.case_stats(res2)["failures"][0]["prev_replies"], [])

    def test_archive_stores_prev_replies_on_real_failure(self):
        """走真实 rubric 的末轮失败：归档要落下前一至两轮回复原文。"""
        case = {"id": "x", "category": "具体细节",
                "turns": ["我把关东煮买回来了", "你猜怎么着"],
                "expect_any": ["关东煮"]}
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("我把关东煮买回来了",
                                 {"reply": "排队排了四十分钟"}, 0))
        res.turns.append(ev.Turn("你猜怎么着",
                                 {"reply": "团子闻着味儿过来了吧"}, 1))
        ev.check_case_expect(res, self.data["universal"])
        stats = ev.case_stats(res)
        self.assertFalse(stats["passed"])
        failure = stats["failures"][0]
        self.assertEqual(failure["turn"], 2)
        self.assertEqual(failure["prev_replies"], ["排队排了四十分钟"])

    def test_pass_rate_and_regression(self):
        by_cat = {}
        failures = []
        for res in self.results:
            cat = res.case["category"]
            st = by_cat.setdefault(cat, {"passed": 0, "total": 0})
            st["total"] += 1
            if res.passed:
                st["passed"] += 1
            else:
                failures.append((res.case["id"], cat, res.failures))

        total = len(self.results)
        passed = total - len(failures)
        rate = passed / total
        lines = ["真人感评测（mock）：%d/%d 通过（%.1f%%）" % (passed, total, rate * 100)]
        for cat, st in by_cat.items():
            lines.append("  %-6s %d/%d" % (cat, st["passed"], st["total"]))
        if failures:
            lines.append("失败明细：")
            for cid, cat, fails in failures:
                for f in fails[:3]:
                    lines.append("  [%s/%s] 第%d轮 %s" % (cat, cid, f["turn"], f["reason"]))
        print("\n".join(lines))

        # W20 门槛不降：mock 模板层必须全过（112/112）。
        # 新增场景不能靠“默认 98% 容错”混过去，单类也不许塌陷。
        self.assertEqual(failures, [], "\n".join(lines))
        for cat in ("沉默", "拒绝说话", "长期记忆", "工作吐槽", "亲密关系", "兴奋分享"):
            st = by_cat.get(cat, {"passed": 0, "total": 0})
            self.assertEqual(st["passed"], st["total"], "%s 类有失败\n%s"
                             % (cat, "\n".join(lines)))

    def test_anti_cliche_metrics(self):
        """W20 反套路指标：不许每条都带建议、不许四字总结式共情堆满、开头不重复。"""
        m = self.metrics
        detail = ("指标：建议 %.1f%% / 总结式共情 %.1f%% / 高频开头 %.1f%% / "
                  "会话内重复开头 %.1f%%"
                  % (m["advice_rate"] * 100, m["summary_empathy_rate"] * 100,
                     m["opening_tic_rate"] * 100, m["repeat_opening_rate"] * 100))
        self.assertLessEqual(m["advice_rate"], 0.25, "建议出现率超标：" + detail)
        self.assertLessEqual(m["repeat_opening_rate"], 0.08, "重复开头超标：" + detail)
        self.assertLessEqual(m["summary_empathy_rate"], 0.02,
                             "四字总结式共情过多：" + detail)
        self.assertLessEqual(m["opening_tic_rate"], 0.10, "客服腔开头过多：" + detail)

    def test_refusal_and_silence_hard_rules(self):
        """拒绝说话/沉默两类：无问号、无逼话、短；拒绝后下一轮不翻旧账。"""
        for res in self.results:
            case = res.case
            if case["category"] not in ("拒绝说话", "沉默"):
                continue
            self.assertTrue(res.passed, "%s 未通过：%s"
                            % (case["id"], res.failures[:2]))
            for t in res.turns:
                self.assertLessEqual(ev.visible_len(t.reply), 40,
                                     "%s 第%d轮太长：%s" % (case["id"], t.index, t.reply))
                self.assertIsNone(ev.QUESTION_RE.search(t.reply),
                                  "%s 第%d轮还在追问：%s" % (case["id"], t.index, t.reply))
                self.assertIsNone(ev.PUSH_RE.search(t.reply),
                                  "%s 第%d轮还在逼话：%s" % (case["id"], t.index, t.reply))
            for word in case.get("mute_any", []):
                for t in res.turns[1:]:
                    self.assertNotIn(word, t.text,
                                     "%s 拒绝后翻旧账“%s”：%s"
                                     % (case["id"], word, t.text))

    def test_memory_naturalness_cases(self):
        """记忆引用：同一轮最多一条；“忘了/别记”之后必须真的消失。"""
        for res in self.results:
            case = res.case
            if case["category"] != "长期记忆":
                continue
            self.assertTrue(res.passed, "%s 未通过：%s"
                            % (case["id"], res.failures[:2]))
            if case.get("forget_after"):
                for t in res.turns:
                    for vals in case["forget_after"].values():
                        for v in vals:
                            if t.index >= int(list(case["forget_after"])[0]):
                                self.assertNotIn(v, t.text,
                                                 "%s 遗忘后仍提到“%s”" % (case["id"], v))


class OnlyMissingSelectionTest(unittest.TestCase):
    """--only-missing：只挑零数据/失败/429 中断，已通过的不重跑。"""

    def setUp(self):
        self.cases = ev.load_cases(CASES_FILE)["cases"]

    def test_partial_archive_selects_exact_pending_list(self):
        archive = json.loads(
            (ROOT / "docs" / "eval_pending_w20.json").read_text(encoding="utf-8"))
        ids, reasons = ev.select_missing(self.cases, archive)
        self.assertEqual(ids, [
            "followup-04", "followup-05", "followup-06",
            "memory-01", "memory-02", "memory-03", "memory-04",
        ])
        self.assertEqual(reasons["followup-04"], "interrupted")
        self.assertEqual(reasons["memory-01"], "missing")
        self.assertNotIn("working-01", ids, "已通过的用例不许重跑")
        self.assertEqual(len(ids), 7, "只允许选中待补的 7 条")

    def test_full_archive_marks_unlisted_as_zero_data(self):
        cases = [{"id": "a", "category": "x"},
                 {"id": "b", "category": "x"},
                 {"id": "c", "category": "x"}]
        archive = {"case_stats": [
            {"id": "a", "passed": True, "turns": 2},
            {"id": "b", "passed": False, "turns": 1},
        ]}
        ids, reasons = ev.select_missing(cases, archive)
        self.assertEqual(ids, ["b", "c"])
        self.assertEqual(reasons, {"b": "failed", "c": "zero-data"})

    def test_category_filter_applies_to_missing_list(self):
        archive = json.loads(
            (ROOT / "docs" / "eval_pending_w20.json").read_text(encoding="utf-8"))
        ids, _ = ev.select_missing(self.cases, archive, ["长期记忆"])
        self.assertEqual(ids, ["memory-01", "memory-02", "memory-03", "memory-04"])


class RubricV2Test(unittest.TestCase):
    """W21: 同义集合 + 语义角色 + 场景专项判据（改前红/改后绿/仍红都要锁住）。"""

    def setUp(self):
        self.universal = ev.load_cases(CASES_FILE)["universal"]
        self.cases = {c["id"]: c for c in ev.load_cases(CASES_FILE)["cases"]}

    def _expect_pass(self, case, replies):
        res = ev.CaseResult(dict(case))
        for i, text in enumerate(replies, 1):
            res.turns.append(ev.Turn("u", {"reply": text}, i))
        ev.check_case_expect(res, self.universal)
        return res.passed

    def test_alias_hit_is_green(self):
        case = {"id": "sim", "category": "具体细节", "expect_any": ["下雨"],
                "expect_alias": {"下雨": ["淋", "伞"]}}
        self.assertTrue(self._expect_pass(case, ["别淋着啊，赶紧找个地方躲"]))

    def test_alias_inside_advice_stays_red(self):
        case = {"id": "sim", "category": "具体细节", "expect_any": ["下雨"],
                "expect_alias": {"下雨": ["淋", "伞"]}}
        self.assertFalse(self._expect_pass(case, ["别淋着啊||记得回去收伞"]))

    def test_semantic_role_needs_object(self):
        case = {"id": "sim", "category": "具体细节", "expect_any": ["嗓子疼"],
                "expect_alias": {"嗓子疼": ["嗓子", "喉咙"]}}
        self.assertTrue(self._expect_pass(case, ["喉咙吞口水都疼"]))
        self.assertFalse(self._expect_pass(case, ["听起来好疼"]))

    def test_scene_hooks_are_wired(self):
        for cid in ("daily-02", "daily-03", "daily-04", "daily-05",
                    "daily-06", "daily-07", "daily-08",
                    "repeat-01", "repeat-02", "repeat-03",
                    "repeat-04", "repeat-05", "repeat-06"):
            case = self.cases[cid]
            self.assertTrue(
                case.get("expect_any") or case.get("forbid_any")
                or case.get("require_emotion") or case.get("no_session_repeat"),
                "%s 仍只有通用硬约束" % cid)
        self.assertTrue(self.cases["daily-02"].get("require_emotion"))
        self.assertTrue(self.cases["repeat-01"].get("no_session_repeat"))

    def test_emotion_scene_requires_emotion_word(self):
        case = {"id": "sim", "category": "日常倾诉",
                "expect_any": ["需求"], "expect_scope": "any",
                "require_emotion": True}
        self.assertTrue(self._expect_pass(case, ["需求堆成这样，换谁都得炸"]))
        self.assertFalse(self._expect_pass(case, ["需求我看到了，然后呢"]))

    def test_repeat_topic_flags_non_adjacent_opening(self):
        case = {"id": "sim", "category": "重复话题", "no_session_repeat": True}
        res = ev.CaseResult(dict(case))
        cfg = ev._case_cfg(dict(case), self.universal)
        prev = []
        for i, text in enumerate(["太烦了吧", "换个角度", "太烦了吧真"]):
            turn = ev.Turn("u", {"reply": text}, i)
            ev.check_turn(res, turn, prev, cfg, self.universal)
            prev.append(turn)
            res.turns.append(turn)
        self.assertFalse(res.passed, "隔轮复用开头 4 字应判红")
        self.assertTrue(any("隔轮复读" in f["reason"] for f in res.failures))

    def test_every_case_has_specific_hook(self):
        for case in self.cases.values():
            self.assertTrue(
                any(k in case for k in (
                    "expect_any", "forbid_any", "silence", "refusal",
                    "max_advice_turns", "memory_values", "forget_after",
                    "mute_any", "expect_scope", "require_emotion",
                    "no_session_repeat")),
                "%s 没有任何专项判据" % case["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
