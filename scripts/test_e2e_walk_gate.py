#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e_walk_gate.py 单元测试：回复没到达必须判红（防止"对静音做验收"）。

只依赖标准库，可在 emulator 启动前跑（workflow: Gate self-test）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_walk_gate import GateFail, require_reply, require_true, require_voice  # noqa: E402


class RequireReplyTest(unittest.TestCase):
    def test_arrived_passes(self):
        require_reply("03_voice", True, {"sent": "ok"})   # 不抛

    def test_missing_raises(self):
        with self.assertRaises(GateFail) as ctx:
            require_reply("03_voice", False, {"sent": "tap", "rowsAfterSend": 5})
        self.assertIn("replyArrived=false", str(ctx.exception))
        self.assertIn("03_voice", str(ctx.exception))

    def test_none_counts_as_missing(self):
        with self.assertRaises(GateFail):
            require_reply("02_chat", None)


class RequireTrueTest(unittest.TestCase):
    def test_true_passes(self):
        require_true("05_reentry", True, "ok")   # 不抛

    def test_false_raises_with_measurements(self):
        with self.assertRaises(GateFail) as ctx:
            require_true("05_reentry", False, "before=8 after=9 greetLike=['晚上好呀']")
        self.assertIn("before=8 after=9", str(ctx.exception))


class RequireVoiceTest(unittest.TestCase):
    def test_event_passes(self):
        require_voice("03_voice", {"txt": "接着说", "at": 24421})

    def test_no_event_raises(self):
        with self.assertRaises(GateFail) as ctx:
            require_voice("03_voice", None)
        self.assertIn("speakSafe", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
