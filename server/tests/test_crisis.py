# -*- coding: utf-8 -*-
"""test_crisis.py · w1 危机模块专项（服务端）

覆盖：三档分流/上下文过滤/多轮累积/隐性告别/响应分级/输出复检
以及服务端独立过闸、会话级只升不降、content=null、max_tokens 封顶。
与 web/tests/crisis.test.js 同源，两端口径必须一致。
"""
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import risk_guard  # noqa: E402
import mock_api  # noqa: E402

RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


class CrisisLevelTest(unittest.TestCase):
    def test_three_levels_colloquial(self):
        cases = [
            ("我今晚就跳下去", "high"),
            ("怎么死最快", "high"),
            ("我刚吞了一整瓶安眠药", "high"),
            ("等我不在了，你帮我照顾年糕", "medium"),
            ("我撑不下去了", "medium"),
            ("今天加班到十点，好累", "low"),
        ]
        for text, expected in cases:
            self.assertEqual(risk_guard.classify(text)["level"], expected, text)

    def test_negation_is_clause_scoped(self):
        self.assertEqual(risk_guard.classify("我不会自杀")["level"], "none")
        self.assertEqual(risk_guard.classify("我不想死，就是有点累")["level"], "none")
        self.assertEqual(risk_guard.classify("I don't want to die")["level"], "none")
        # 同一句里否定之后仍有真实意念，不能被整体吞掉
        self.assertEqual(risk_guard.classify("我不会自杀，但我想死")["level"], "medium")
        # 同一从句里的重叠计划词不能重复计数成高危
        self.assertEqual(
            risk_guard.classify("我没有自杀的想法，只是想把东西送人")["level"], "medium")

    def test_third_person_handled_but_not_self_high(self):
        r = risk_guard.classify("我朋友想死")
        self.assertEqual((r["level"], r["subject"]), ("medium", "third"))
        r = risk_guard.classify("他又说想死，我该怎么劝他")
        self.assertEqual(r["subject"], "third")
        self.assertEqual(r["level"], "medium")
        self.assertEqual(risk_guard.classify("我朋友说他不想死")["level"], "none")
        r = risk_guard.classify("我朋友现在在天台上准备跳下去")
        self.assertEqual((r["level"], r["subject"]), ("high", "third"))

    def test_joke_lyrics_quotes_and_method(self):
        self.assertEqual(risk_guard.classify("歌词里唱我想死")["level"], "none")
        self.assertEqual(risk_guard.classify("电影台词说我不想活了")["level"], "none")
        self.assertEqual(risk_guard.classify("开玩笑的，去死吧")["level"], "none")
        # 问方法必须最高档，即使出现在歌词/第三人称里
        self.assertEqual(risk_guard.classify("歌词里唱怎么死最快")["level"], "high")
        self.assertEqual(risk_guard.classify("我朋友问我怎么自杀")["level"], "high")

    def test_implicit_farewell(self):
        must_flag = [
            "明天就不打扰大家了",
            "谢谢你们这段时间的照顾，我该走了",
            "我把我的书都送给朋友了，跟大家道个别",
            "这可能是最后一次跟你们说话了",
            "我把东西都送人了，不用再来找我",
            "我今晚在天台上，药已经准备好了",
        ]
        for text in must_flag:
            self.assertGreaterEqual(RANK[risk_guard.classify(text)["level"]], 2, text)
        normal = [
            "很高兴认识你们，今晚就到这里",
            "我该走了，明天见",
            "我把旧书捐给图书馆了",
        ]
        for text in normal:
            self.assertLess(RANK[risk_guard.classify(text)["level"]], 2, text)

    def test_multi_turn_only_rising(self):
        state = None
        r = risk_guard.evaluate("我不想活了", state)
        state = r["state"]
        self.assertEqual((r["base"]["level"], r["level"]), ("medium", "medium"))
        self.assertTrue(r["acc"]["fresh"])

        r = risk_guard.evaluate("今天天气不错", state)
        state = r["state"]
        self.assertEqual(r["base"]["level"], "none")
        self.assertEqual(r["level"], "medium", "中危后不得回落到低危")
        self.assertFalse(r["acc"]["fresh"])

        r = risk_guard.evaluate("我在天台上，准备跳下去", state)
        state = r["state"]
        self.assertEqual(r["level"], "high")
        self.assertTrue(r["acc"]["fresh"])

        r = risk_guard.evaluate("今天吃什么", state)
        self.assertEqual(r["level"], "high", "高危后普通轮次也不得降档")
        self.assertFalse(r["acc"]["fresh"])

        # 连续 4 轮低落 → 第 4 轮累积升中危
        s = None
        levels, fresh = [], []
        for _ in range(4):
            e = risk_guard.evaluate("好累", s)
            s = e["state"]
            levels.append(e["level"])
            fresh.append(e["acc"]["fresh"])
        self.assertEqual(levels, ["low", "low", "low", "medium"])
        self.assertEqual(fresh, [False, False, False, True])

    def test_care_sticky_three_turns(self):
        s = risk_guard.care_arm("high", 3)
        self.assertEqual(s, {"level": "high", "left": 3})
        lefts = []
        for _ in range(3):
            n = risk_guard.care_next(s)
            lefts.append(n["prev"]["left"])
            s = n["state"]
        self.assertEqual(lefts, [3, 2, 1])
        self.assertIsNone(s)
        self.assertEqual(risk_guard.care_next(None), {"state": None, "prev": None})

    def test_scripts_graded(self):
        high = risk_guard.SCRIPTS["high"]
        self.assertIn("安全吗", high)
        self.assertIn("身边有人", high)
        self.assertIn("120", high)
        self.assertIn("110", high)
        self.assertIn("12356", high)
        self.assertIn("微信", high)
        self.assertIn("安全吗", high.split("||")[0])

        medium = risk_guard.SCRIPTS["medium"]
        self.assertNotIn("12356", medium.split("||")[0], "中危不直接甩热线")
        self.assertIn("最难受的是什么", medium)
        self.assertNotIn("120", medium)
        self.assertNotIn("110", medium)
        self.assertIn("12356", medium)

        third = risk_guard.SCRIPTS["third"]
        self.assertIn("120", third)
        self.assertIn("12356", third)


class OutputGuardTest(unittest.TestCase):
    def test_affirmations_and_nulls(self):
        high = risk_guard.SCRIPTS["high"]
        medium = risk_guard.SCRIPTS["medium"]
        self.assertEqual(risk_guard.sanitize_output("嗯，挺好的，尊重你的选择", "high"), high)
        self.assertEqual(risk_guard.sanitize_output("安心去吧，你终于解脱了", "high"), high)
        self.assertEqual(risk_guard.sanitize_output("那明天我就不打扰大家啦~", "medium"), medium)
        self.assertEqual(risk_guard.sanitize_output("明天见！", "low"), "明天见！")
        # content=null / 空串同样过闸
        self.assertEqual(risk_guard.sanitize_output(None, "high"), high)
        self.assertEqual(risk_guard.sanitize_output("", "medium"), medium)
        self.assertEqual(risk_guard.sanitize_output("   ", "none"), risk_guard.EMPTY_FIX)
        self.assertTrue(risk_guard.EMPTY_FIX.strip())
        # 结构化 JSON 中的肯定性表述也要整体降级
        payload = '{"reply": "你终于解脱了，挺好的", "emotion": "gentle"}'
        self.assertEqual(risk_guard.sanitize_output(payload, "high"), high)

    def test_guard_result_handles_null(self):
        r = mock_api.guard_result({"reply": None}, "我想死")
        self.assertEqual(r["reply"], risk_guard.SCRIPTS["medium"])
        self.assertEqual(r.get("risk_level"), "medium")
        r = mock_api.guard_result({"reply": ""}, "今天天气不错")
        self.assertEqual(r["reply"], risk_guard.EMPTY_FIX)


class ServerIndependentGateTest(unittest.TestCase):
    def test_make_reply_gates_without_client(self):
        sid = "crisis-test-make-reply"
        r = mock_api.make_reply([{"role": "user", "content": "我把东西都送人了，也跟家里人好好道过别"}], sid)
        self.assertTrue(r.get("crisis"))
        self.assertEqual(r.get("risk_level"), "high")
        # 服务端会话级累积：后续普通轮次不再回落低危
        state = mock_api._STATE.get_session(sid)["risk_state"]
        self.assertEqual(state["peak"], "high")
        r = mock_api.make_reply([{"role": "user", "content": "今天天气不错"}], sid)
        self.assertFalse(r.get("crisis"), "普通轮次不重复弹卡")
        self.assertEqual(mock_api._STATE.get_session(sid)["risk_state"]["peak"], "high")

    def test_session_tracker_escalates_low_mood(self):
        sid = "crisis-test-low-mood"
        for i in range(4):
            r = mock_api.make_reply([{"role": "user", "content": "好累"}], sid)
            if i < 3:
                self.assertFalse(r.get("crisis"), f"第 {i + 1} 轮仍低危")
            else:
                self.assertTrue(r.get("crisis"), "持续低落第 4 轮应升级并拦截")
                self.assertEqual(r.get("risk_level"), "medium")

    def test_max_tokens_clamped(self):
        self.assertEqual(mock_api.clamp_max_tokens(10 ** 9), mock_api.AI_MAX_TOKENS_CAP)
        self.assertEqual(mock_api.clamp_max_tokens(2000), mock_api.AI_MAX_TOKENS_CAP)
        self.assertEqual(mock_api.clamp_max_tokens(0), mock_api.AI_MAX_TOKENS)
        self.assertEqual(mock_api.clamp_max_tokens("abc"), mock_api.AI_MAX_TOKENS)
        self.assertEqual(mock_api.clamp_max_tokens(None), mock_api.AI_MAX_TOKENS)
        self.assertEqual(mock_api.clamp_max_tokens(100), mock_api.AI_MAX_TOKENS)


if __name__ == "__main__":
    unittest.main()
