#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mock_human_feel_fixes.py · W11 第二轮：mock 引擎 5 个卡点回归

覆盖（对应 docs/HUMAN_FEEL.md 的遗留项 1-5）：
1. 沉默轮：极短/无内容输入 → 1-5 字短回应，不追问、不带引导 hook
2. 姓名提取：老板/家人/室友的值不再等于键自身词
3. 记忆感知冷却：本轮刚写入的键不感知，冷却从下一轮起算
4. hook 优先级：主动回提命中时让位给固定新手引导
5. 失眠/熬夜/睡不着入"状态"槽，且能在失眠话题下被回提
"""
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import mock_api  # noqa: E402

PUNCT_RE = mock_api._SILENT_STRIP_RE
PUSH_RE = ("说说", "然后呢", "接着", "聊聊", "展开", "怎么了", "发生什么",
           "在吗", "还有呢", "继续")


def fresh(sid):
    mock_api._STATE._sessions.pop(sid, None)
    mock_api._STATE._loaded.discard(sid)
    return sid


def say(sid, turns):
    """按顺序喂多轮 user 文本，返回每轮 payload。"""
    fresh(sid)
    out = []
    history = []
    for text in turns:
        history.append({"role": "user", "content": text})
        out.append(mock_api.make_reply(history, sid))
    return out


def visible(reply):
    return len(PUNCT_RE.sub("", reply.replace("||", "")))


class SilenceTurnTest(unittest.TestCase):
    def test_short_inputs_get_short_company_not_questions(self):
        for i, text in enumerate(["……", "嗯", "哦", "累", "随便", "算了", "🙂"]):
            payload = say("silence-%d" % i, [text])[0]
            reply = payload["reply"]
            self.assertLessEqual(visible(reply), 6,
                                 "%r 应只回 1-5 字：%r" % (text, reply))
            self.assertNotIn("？", reply)
            self.assertNotIn("?", reply)
            for word in PUSH_RE:
                self.assertNotIn(word, reply, "%r 不应逼话：%r" % (text, reply))
            self.assertIsNone(payload["hook"], "%r 不应丢引导 hook" % text)
            self.assertEqual(payload["memory_updates"], {})

    def test_short_pool_contains_human_acknowledgement(self):
        """池子必须真的含 1-5 字的"在"式回应，而不是整段安慰。"""
        for item in ("嗯。", "在。", "我在这儿", "……"):
            self.assertIn(item, mock_api.SILENT_POOL)
            self.assertLessEqual(visible(item), 5)

    def test_informative_short_sentence_is_not_silent(self):
        for text in ("我最近失眠", "你猜我昨晚几点睡的", "今天被同事夸了"):
            self.assertFalse(mock_api.is_silent_input(text), text)
        payload = say("silence-neg", ["我最近失眠"])[0]
        self.assertNotIn(payload["reply"], mock_api.SILENT_POOL)


class NameExtractionTest(unittest.TestCase):
    def test_boss_name_excludes_role_word(self):
        self.assertEqual(mock_api.extract_memory("我们老板叫张三", {}), {"老板": "张三"})
        self.assertEqual(mock_api.extract_memory("我们领导是王总", {}), {"老板": "王总"})
        # 老守卫不回归：谓语垃圾仍回退基词，可信姓名仍可升级
        self.assertEqual(mock_api.extract_memory("我们老板又骂我", {}), {"老板": "老板"})
        self.assertEqual(mock_api.extract_memory("我们老板王总今天又作妖", {}),
                         {"老板": "王总"})

    def test_family_and_roommate_names(self):
        self.assertEqual(mock_api.extract_memory("我妈叫李芳", {}), {"家人": "李芳"})
        self.assertEqual(mock_api.extract_memory("我姐叫李静", {}), {"家人": "李静"})
        self.assertEqual(mock_api.extract_memory("室友叫小陈", {}), {"室友": "小陈"})
        self.assertEqual(mock_api.extract_memory("舍友是阿杰", {}), {"室友": "阿杰"})

    def test_roommate_recall_template_exists(self):
        self.assertIn("室友", mock_api.MEM_AWARE_POOL)
        self.assertIn("家人", mock_api.RECALL_TEMPLATES)


class MemoryAwareCooldownTest(unittest.TestCase):
    def test_write_turn_does_not_burn_the_cooldown(self):
        payloads = say("aware-cool", [
            "我家猫叫年糕",          # 写入轮：不感知
            "你猜年糕在干嘛",        # 下一轮：可感知（并开始冷却）
            "年糕今天拆家了",        # 冷却 1
            "年糕是不是欠揍",        # 冷却 2
            "年糕最近乖不乖",        # 冷却满 3：可再次感知
        ])
        self.assertNotIn("年糕", payloads[0]["reply"], "写入轮复述=炫耀记忆")
        self.assertIn("年糕", payloads[1]["reply"], "下一轮应能自然接住")
        self.assertNotIn("年糕", payloads[2]["reply"], "冷却期不应连提")
        self.assertNotIn("年糕", payloads[3]["reply"], "冷却期不应连提")
        self.assertIn("年糕", payloads[4]["reply"], "冷却结束后应可再提")

    def test_greet_turn_skips_awareness(self):
        """开场引用不是用户主动提，不应烧冷却（既有守卫保持）。"""
        payload = mock_api.make_reply(
            [{"role": "user", "content": "在吗"}], fresh("aware-greet"))
        self.assertIsNotNone(payload["reply"])


class HookPriorityTest(unittest.TestCase):
    def test_recall_yields_before_onboarding(self):
        payloads = say("hook-pri", [
            "我下个月要搬家",
            "东西还没收",
            "叫我阿秋就行",
            "感觉永远收不完",
        ])
        hook = payloads[3]["hook"] or ""
        self.assertIn("搬家", hook, "回提应优先于固定引导：%r" % hook)
        self.assertNotIn("怎么称呼你", hook)

    def test_onboarding_still_works_without_recall(self):
        payload = say("hook-onboard", ["今天天气不错"])[0]
        self.assertEqual(payload["hook"], mock_api.ONBOARD_NAME_HOOK)


class InsomniaStateSlotTest(unittest.TestCase):
    def test_state_extraction(self):
        self.assertEqual(mock_api.extract_memory("我最近失眠", {}), {"状态": "失眠"})
        self.assertEqual(mock_api.extract_memory("昨晚又熬夜了", {}), {"状态": "熬夜"})
        self.assertEqual(mock_api.extract_memory("最近总是睡不着", {}), {"状态": "睡不着"})
        self.assertEqual(mock_api.extract_memory("这两天有点焦虑", {}), {"状态": "焦虑"})

    def test_state_can_be_recalled_on_insomnia_topic(self):
        payloads = say("state-recall", [
            "我最近失眠",
            "今天工作好多",
            "开了三个会",
            "一整天都在犯困",
        ])
        hook = payloads[3]["hook"] or ""
        self.assertIn("失眠", hook, "状态槽应可回提：%r" % hook)
        self.assertIn("你上次说", hook)

    def test_state_aware_pool_exists(self):
        self.assertIn("状态", mock_api.RECALL_MAP["insomnia"])
        self.assertIn("状态", mock_api.RECALL_TEMPLATES)
        self.assertIn("状态", mock_api.MEM_AWARE_POOL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
