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
import tempfile
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
import live_retest as lr  # noqa: E402

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

    def test_single_message_over_hard_limit_fails(self):
        case = {"id": "msg-long", "category": "日常倾诉"}
        universal = self.data["universal"]
        cfg = ev._case_cfg(case, universal)
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("u", {"reply": "这" * 41}, 0))
        ev.check_turn(res, res.turns[-1], [], cfg, universal)
        self.assertFalse(res.passed)
        self.assertIn("单条过长", res.failures[0]["reason"])
        self.assertEqual(ev.visible_len(res.turns[0].reply), 41)

    def test_split_message_passes_and_counts(self):
        case = {"id": "msg-split", "category": "日常倾诉"}
        universal = self.data["universal"]
        cfg = ev._case_cfg(case, universal)
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("u", {"reply": "甲" * 20 + "||" + "乙" * 20}, 0))
        ev.check_turn(res, res.turns[-1], [], cfg, universal)
        self.assertTrue(res.passed)
        stats = ev.case_stats(res)
        self.assertEqual(stats["msg_count"], 2)
        self.assertEqual(stats["msg_over30"], 0)
        self.assertEqual(stats["msg_max"], 20)
        self.assertEqual(stats["split_turns"], 1)

    def test_name_confusion_with_own_pet_fails(self):
        """把 ta 的宠物名换成小满自己的“团子”：一次都不能有。"""
        case = {"id": "name-own", "category": "追问", "expect_any": ["年糕"],
                "forbid_any": ["团子"], "max_advice_turns": 0}
        universal = self.data["universal"]
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("你说年糕是不是欠揍",
                                 {"reply": "团子那眼神确实欠揍"}, 0))
        ev.check_turn(res, res.turns[-1], [], ev._case_cfg(case, universal), universal)
        self.assertFalse(res.passed)
        self.assertIn("团子", res.failures[0]["reason"])

    def test_alias_cat_does_not_count_as_named_pet(self):
        """只说“猫”不代表叫对了名字“年糕”（旧 alias 太宽的收紧）。"""
        case = {"id": "name-alias", "category": "追问",
                "expect_any": ["年糕"], "max_advice_turns": 0}
        universal = self.data["universal"]
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("你说年糕是不是欠揍",
                                 {"reply": "你家猫今天精神咋样"}, 0))
        ev.check_turn(res, res.turns[-1], [], ev._case_cfg(case, universal), universal)
        ev.check_case_expect(res, universal)
        self.assertFalse(res.passed)
        self.assertIn("未体现期望内容", res.failures[0]["reason"])

    def test_meta_memory_leak_fails_without_false_positive(self):
        """“我记岔了”属内部状态外露，判红；用户的“你记错了”不误杀。"""
        case = {"id": "meta", "category": "日常倾诉"}
        universal = self.data["universal"]
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("我猫叫年糕",
                                 {"reply": "哎呀我记岔了，年糕今天没吃饭吧？"}, 0))
        ev.check_turn(res, res.turns[-1], [], ev._case_cfg(case, universal), universal)
        self.assertFalse(res.passed)
        self.assertIn("内部状态外露", res.failures[0]["reason"])
        self.assertIsNone(ev.META_MEMORY_RE.search("你记错了？我没说过"))
        self.assertIsNone(ev.META_MEMORY_RE.search("我没记错的话，是年糕吧"))

    def test_cross_turn_repeat_question_fails(self):
        """助手在多轮里反复把同一句问题推回给用户：判红。"""
        case = {"id": "repeat-q", "category": "追问"}
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("最近老是失眠",
                                 {"reply": "这阵子我也老失眠。团子半夜跑酷吗？"}, 0))
        res.turns.append(ev.Turn("昨天也是三点多才睡着",
                                 {"reply": "作息乱套了。团子半夜跑酷吗？"}, 1))
        res.turns.append(ev.Turn("今天一早又要开会",
                                 {"reply": "一早开会更难熬。团子半夜跑酷吗？"}, 2))
        ev.check_case_question_repeat(res)
        self.assertFalse(res.passed)
        self.assertIn("跨轮重复追问", res.failures[0]["reason"])
        self.assertIn("T1/T2/T3", res.failures[0]["reason"])

    def test_user_repeat_question_is_not_a_product_defect(self):
        """用户自己重复问，助手照常回答：不算助手的锅。"""
        case = {"id": "user-repeat", "category": "追问"}
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("你睡了吗",
                                 {"reply": "还没，躺着呢"}, 0))
        res.turns.append(ev.Turn("你睡了吗",
                                 {"reply": "真没睡，在听你说"}, 1))
        ev.check_case_question_repeat(res)
        self.assertTrue(res.passed, res.failures)

    def test_short_backchannel_repeat_allowed(self):
        """“然后呢？”这类短接话重复，不算重复追问。"""
        case = {"id": "channel", "category": "日常倾诉"}
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("我今天被领导说了",
                                 {"reply": "然后呢？"}, 0))
        res.turns.append(ev.Turn("我就没说话",
                                 {"reply": "然后呢？"}, 1))
        ev.check_case_question_repeat(res)
        self.assertTrue(res.passed, res.failures)

    def test_quoted_question_not_counted(self):
        """助手转述用户问句（引号内）不算助手自己在重复追问。"""
        case = {"id": "quoted", "category": "日常倾诉"}
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn("我为什么会这样",
                                 {"reply": "你问“我为什么会这样？”，听着就累。"}, 0))
        res.turns.append(ev.Turn("我也不知道",
                                 {"reply": "还是那句“我为什么会这样？”在绕。"}, 1))
        ev.check_case_question_repeat(res)
        self.assertTrue(res.passed, res.failures)

    def test_user_echo_fails(self):
        """助手把用户本轮原句逐字抄回来（≥8 字且覆盖过半）：判红。"""
        case = {"id": "echo", "category": "日常倾诉"}
        res = ev.CaseResult(case)
        res.turns.append(ev.Turn(
            "它把我杯子推下去了",
            {"reply": "它把我杯子推下去了，我还没来得及捡。||真是欠揍"}, 0))
        ev.check_case_user_echo(res)
        self.assertFalse(res.passed)
        self.assertIn("复读用户原句", res.failures[0]["reason"])
        self.assertEqual(res.failures[0]["turn"], 1)

    def test_short_echo_allowed(self):
        """“真的吗/我也是”这类短接话重合不算复读。"""
        res = ev.CaseResult({"id": "short", "category": "日常倾诉"})
        res.turns.append(ev.Turn("真的吗", {"reply": "真的吗，我也觉得"}, 0))
        ev.check_case_user_echo(res)
        self.assertTrue(res.passed, res.failures)

    def test_partial_echo_of_long_user_not_flagged(self):
        """长句里只重合一个短片段（不足一半）不算复读用户原句。"""
        res = ev.CaseResult({"id": "partial", "category": "日常倾诉"})
        res.turns.append(ev.Turn(
            "今天早上上班的时候又被领导叫去改方案了我真的烦死了",
            {"reply": "又被领导叫去改方案了，换谁都烦"}, 0))
        ev.check_case_user_echo(res)
        self.assertTrue(res.passed, res.failures)

    def test_echo_paraphrase_not_flagged(self):
        """改写过的不算逐字复读。"""
        res = ev.CaseResult({"id": "para", "category": "日常倾诉"})
        res.turns.append(ev.Turn(
            "它把我杯子推下去了，我还没来得及捡。",
            {"reply": "杯子被它推下去了？还没捡吧"}, 0))
        ev.check_case_user_echo(res)
        self.assertTrue(res.passed, res.failures)

    def test_advice_skips_first_person_self_disclosure(self):
        """“我昨天也熬夜了”是第一人称自述，不算建议；真建议仍要抓。"""
        case = {"id": "adv", "category": "具体细节"}
        self.assertIsNone(ev.advice_hits(case, "三点睡，今天应该挺难受的。我昨天也熬夜了。"))
        self.assertIsNone(ev.advice_hits(case, "我以前也经常熬夜到三点。"))
        self.assertIsNotNone(ev.advice_hits(case, "你也早点睡吧。"))
        self.assertIsNotNone(ev.advice_hits(case, "我劝你也早睡。"))
        self.assertIsNotNone(ev.advice_hits(case, "我昨天也熬夜了，你还是早点睡吧。"))

    def test_ids_backfill_keeps_prior_seven(self):
        """先跑 7 例、再用 --ids 补 3 例：写新 run 目录，前 7 条必须仍在。"""
        by_id = {"c%d" % i: {"id": "c%d" % i, "category": "具体细节", "turns": []}
                 for i in range(10)}
        first_ids = ["c%d" % i for i in range(7)]
        backfill_ids = ["c7", "c8", "c9"]

        def fake_manifest(run_dir, ids, rr):
            lr.write_manifest(run_dir / "manifest.json",
                              {"run_id": run_dir.name, "ids": list(ids),
                               "model": "mock", "mode": "first",
                               "rerun_index": rr})

        with tempfile.TemporaryDirectory() as td:
            old_runs = lr.RUNS_DIR
            lr.RUNS_DIR = Path(td) / "runs"
            try:
                run1 = lr.new_run_dir(first_ids)
                fake_manifest(run1, first_ids, 1)
                ledger1 = run1 / "ledger.json"
                recs7 = lr.merge_records([], first_ids, by_id)
                for r in recs7.values():
                    r.update({"status": "valid", "passed": True, "attempts": 1})
                lr.write_ledger(ledger1, list(recs7.values()), 7, "", "mock", 0, 0,
                                record_run=False)
                before = ledger1.read_bytes()

                # 补跑 3 例：落到新目录，来源是 run1
                run2 = lr.new_run_dir(backfill_ids)
                self.assertNotEqual(run1, run2)
                fake_manifest(run2, backfill_ids, 1)
                prior = lr.load_ledger(ledger1)["cases"]
                recs10 = lr.merge_records(prior, backfill_ids, by_id)
                lr.write_ledger(run2 / "ledger.json", list(recs10.values()), 10,
                                "", "mock", 0, 0, record_run=False)
                after = {c["id"]: c for c in lr.load_ledger(run2 / "ledger.json")["cases"]}
                merged = {c["id"]: c for c in lr.load_ledger(run1 / "ledger.json")["cases"]}
            finally:
                lr.RUNS_DIR = old_runs

            # 旧 run 一个字节都没动
            self.assertEqual(ledger1.read_bytes(), before)
            self.assertEqual(len(merged), 7)
            # 新 run 10 条，且前 7 条原样保留
            self.assertEqual(len(after), 10)
            for cid in first_ids:
                self.assertEqual(after[cid]["status"], "valid")
                self.assertTrue(after[cid]["passed"])
            for cid in backfill_ids:
                self.assertEqual(after[cid]["status"], "pending")

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
