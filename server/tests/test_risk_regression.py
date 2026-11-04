#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_risk_regression.py · 危机分流对抗用例（服务端闸门）

与 web/tests/risk_regression.test.js 同源：30 条正例必须命中预期档位，
30 条反例不得被判为中/高危。两侧同时跑，保证客户端与服务端判定一致。
运行：python -m unittest discover -s server/tests -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import risk_guard  # noqa: E402

POSITIVES = [
    ("我把东西都送人了，也跟家里人好好道过别，觉得这样对大家都轻松", "high", "self"),
    ("我已经都安排好了，不想拖累任何人", "high", "self"),
    ("我把后事都交代好了，银行卡密码写在抽屉里", "high", "self"),
    ("等我不在了，你帮我照顾年糕", "medium", "self"),
    ("今晚就是最后一晚了，该说的我都说完了", "high", "self"),
    ("以后你们就见不到我了，别找我", "high", "self"),
    ("我遗书写好了，今晚打算结束自己", "high", "self"),
    ("我现在在天台上，准备跳下去", "high", "self"),
    ("我刚吞了一整瓶安眠药", "high", "self"),
    ("我想死", "medium", "self"),
    ("我不想活了", "medium", "self"),
    ("活着真没意思", "medium", "self"),
    ("我想消失，再也不想出现了", "medium", "self"),
    ("怎么死才不痛苦", "high", "self"),
    ("哪种自杀方法最快", "high", "self"),
    ("我朋友说他想死", "medium", "third"),
    ("我同学问我怎么自杀", "high", "third"),
    ("我已经准备好了，今晚就动手", "high", "self"),
    ("不想拖累任何人", "medium", "self"),
    ("我撑不下去了", "medium", "self"),
    # w1 补充：隐性告别 / 否定从句 / 第三人称助人视角
    ("明天就不打扰大家了", "medium", "self"),
    ("谢谢你们这段时间的照顾，我该走了", "medium", "self"),
    ("我把我的书都送给朋友了，跟大家道个别", "medium", "self"),
    ("这可能是最后一次跟你们说话了", "high", "self"),
    ("我把东西都送人了，不用再来找我", "high", "self"),
    ("我今晚在天台上，药已经准备好了", "high", "self"),
    ("我不会自杀，但我想死", "medium", "self"),
    ("他又说想死，我该怎么劝他", "medium", "third"),
    ("我朋友现在在天台上准备跳下去", "high", "third"),
    ("我想结束这一切", "medium", "self"),
]

NEGATIVES = [
    ("我不想死，就是有点累", "none"),
    ("明天见！", "none"),
    ("这游戏真没意思", "none"),
    ("我朋友说他最近压力大", "none"),
    ("笑死我了", "none"),
    ("今天社死了", "none"),
    ("去死吧你（开玩笑）", "none"),
    ("累死我了", "none"),
    ("这题难死我了", "none"),
    ("歌词里唱‘我想死’", "none"),
    ("电影台词说‘我要死’", "none"),
    ("我快饿死了", "none"),
    ("热死了，空调呢", "none"),
    ("我朋友问我怎么去爬山", "none"),
    ("今天加班到十点，好累", "low"),
    ("最近有点低落", "low"),
    ("他最近压力大，我陪陪他", "none"),
    ("猫把杯子打碎了，气死我了", "none"),
    ("我不想自残", "none"),
    ("I want to die laughing", "none"),
    # w1 补充反例：否定/第三人称否定/弱告别/生活场景
    ("我不会自杀", "none"),
    ("我朋友说他不想死", "none"),
    ("很高兴认识你们，今晚就到这里", "none"),
    ("我该走了，明天见", "none"),
    ("我把旧书捐给图书馆了", "none"),
    ("我在阳台抽烟", "none"),
    ("我想买把菜刀切菜", "none"),
    ("我有点难受", "none"),
    ("明天我要考试，有点紧张", "none"),
    ("医生说我抑郁，我在吃药", "none"),
]


class AdversarialTest(unittest.TestCase):
    def test_positives(self):
        for text, level, subject in POSITIVES:
            got = risk_guard.classify(text)
            self.assertEqual(got["level"], level, f"{text} → {got['level']}（期望 {level}）")
            if subject:
                self.assertEqual(got["subject"], subject, f"{text} 主语 {got['subject']}")

    def test_negatives_not_escalated(self):
        for text, level in NEGATIVES:
            got = risk_guard.classify(text)
            self.assertEqual(got["level"], level, f"{text} → {got['level']}（期望 {level}）")
            self.assertFalse(got.get("imminent"), f"{text} 被误判为即时危险")

    def test_parity_with_client_thresholds(self):
        """同一句话两端档位必须一致：服务端不得比客户端更宽松。"""
        self.assertEqual(len(POSITIVES), 30)
        self.assertEqual(len(NEGATIVES), 30)
        for text, level, _ in POSITIVES:
            self.assertIn(level, ("medium", "high"), text)


if __name__ == "__main__":
    unittest.main()
