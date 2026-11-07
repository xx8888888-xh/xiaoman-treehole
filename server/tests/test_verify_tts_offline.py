#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_verify_tts_offline.py · 「无 TTS 环境也必须判」的回归

覆盖 verify_tts.py 的离线 PCM 判定：
  - expected_ms 与 web/js/tts.js speechPlan 同口径
  - 正常假 TTS → 非静音 / 时长合理 / 未截断
  - 全静音、截短两个负例必须判红（证明判据不是摆设）
  - assert_pcm 失败时以 exit 1 结束（不许把无 TTS 当通过）
运行：python -m pytest server/tests -q
"""
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_tts.py"

spec = importlib.util.spec_from_file_location("verify_tts_offline", SCRIPT)
vt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vt)

TEXT = "你好呀，我是小满，今晚也辛苦啦"


class TestExpectedMs(unittest.TestCase):
    def test_two_chars(self):
        # 2×190/1.02 + 240 = 613
        self.assertEqual(vt.expected_ms("你好"), 613)

    def test_punctuation_adds_pause(self):
        # 标点算停顿、不计字（末位取整，允许 1ms 误差）
        self.assertAlmostEqual(vt.expected_ms("好，"), vt.expected_ms("好") + 180 / 1.02, delta=1)

    def test_rate_faster_shorter(self):
        self.assertLess(vt.expected_ms(TEXT, 1.16), vt.expected_ms(TEXT, 1.02))


class TestJudge(unittest.TestCase):
    def test_good_audio_passes(self):
        j = vt.judge_pcm(vt.analyze_wav_bytes(vt.fake_tts_wav(TEXT)), TEXT)
        self.assertTrue(j["ok"], j)
        self.assertTrue(j["nonzero"])
        self.assertTrue(j["durationOk"])
        self.assertFalse(j["truncated"])

    def test_silent_audio_fails(self):
        j = vt.judge_pcm(vt.analyze_wav_bytes(vt.fake_tts_wav(TEXT, mode="silent")), TEXT)
        self.assertFalse(j["ok"])
        self.assertIn("silent", j["reasons"])

    def test_short_audio_fails(self):
        j = vt.judge_pcm(vt.analyze_wav_bytes(vt.fake_tts_wav(TEXT, mode="short")), TEXT)
        self.assertFalse(j["ok"])
        self.assertTrue(j["truncated"])

    def test_assert_pcm_exits_nonzero_on_silent(self):
        # 「环境没有 TTS 就跳过」的反面：静音必须让整轮退出码为 1
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                vt.assert_pcm(vt.fake_tts_wav(TEXT, mode="silent"), TEXT, "unit-silent")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("[FAIL]", out.getvalue())

    def test_offline_verify_returns_pass(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            j = vt.offline_pcm_verify()
        self.assertTrue(j["ok"])
        self.assertIn("负例 silent", out.getvalue())
        self.assertIn("负例 short", out.getvalue())


if __name__ == "__main__":
    unittest.main()
