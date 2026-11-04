#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_log_redact.py · 日志脱敏单测（审计 P1-5 / w4-5）

证明：手机号打码、疑似密钥替换、超长文本折叠、请求查询串删除；
并验证 tts_server 的错误日志同样经过脱敏，不会把原文写进 docker 日志。
运行：python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import log_redact
import tts_server


class MaskSensitiveTest(unittest.TestCase):
    def test_phone_is_masked(self):
        out = log_redact.mask_sensitive("联系 13800138000 找我")
        self.assertIn("138****8000", out)
        self.assertNotIn("13800138000", out)

    def test_secret_keys_are_redacted(self):
        for raw in ("sk-abcdef1234567890", "Bearer abcdefghijklmnop",
                    "AIzaSyABCDEFGHIJKLMNOP1234567890", "ghp_abcdefghijklmnopqrstuvwxyz123456"):
            out = log_redact.mask_sensitive("key=" + raw)
            self.assertNotIn(raw, out)
            self.assertTrue("sk-***" in out or "[REDACTED" in out, out)

    def test_long_hex_token_redacted(self):
        token = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
        out = log_redact.mask_sensitive("token " + token)
        self.assertNotIn(token, out)
        self.assertIn("[REDACTED_TOKEN]", out)

    def test_long_text_is_folded(self):
        long_text = "这是一段很长的对话原文" * 12  # 120 字，无空格
        out = log_redact.mask_sensitive(long_text)
        self.assertNotIn(long_text, out)
        self.assertIn("长文本", out)
        self.assertIn("已脱敏", out)

    def test_short_text_untouched(self):
        self.assertEqual(log_redact.mask_sensitive("mock api 503"), "mock api 503")
        self.assertEqual(log_redact.mask_sensitive("hello"), "hello")

    def test_newlines_collapsed_and_truncated(self):
        out = log_redact.mask_sensitive("line1\nline2\r\n" + "x" * 1000, max_len=60)
        self.assertNotIn("\n", out)
        self.assertLessEqual(len(out), 61)

    def test_request_line_drops_query(self):
        out = log_redact.redact_request_line('"GET /tts?text=你好呀&voice=x HTTP/1.1" 200 -')
        self.assertEqual(out, '"GET /tts" 200 -')

    def test_tts_server_request_line_masks_phone(self):
        out = tts_server.redact_request_line('"GET /tts?text=13800138000 HTTP/1.1" 200 -')
        self.assertNotIn("13800138000", out)


class TtsErrorLogTest(unittest.TestCase):
    def test_error_log_masks_user_controlled_detail(self):
        """invalid voice 里可能塞入任意长文本/密钥，日志必须打码。"""
        handler = object.__new__(tts_server.Handler)
        captured = []

        def fake_json(obj, code=200):
            captured.append((obj, code))

        handler._json = fake_json
        secret = "sk-abcdef1234567890"
        phone = "13800138000"
        long_text = "对话原文" * 30
        with self.assertLogs("xiaoman-tts", level="ERROR") as logs:
            handler._error(400, f"invalid voice: {secret} {phone} {long_text}")
        joined = "\n".join(logs.output)
        self.assertNotIn(secret, joined)
        self.assertNotIn(phone, joined)
        self.assertNotIn(long_text, joined)
        self.assertIn("138****8000", joined)
        self.assertIn("已脱敏", joined)
        # 返回给客户端的错误信息不受影响（脱敏只作用于日志）
        self.assertEqual(captured[0][1], 400)


if __name__ == "__main__":
    unittest.main()
