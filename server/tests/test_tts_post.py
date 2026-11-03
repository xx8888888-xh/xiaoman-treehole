#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tts_post.py · TTS POST 协议 + 日志脱敏单测（审计 P1-5）

不触网：只测纯函数，以及本地回环 Handler 的 400/413/OPTIONS 行为。
运行：python -m unittest discover -s server/tests -p "test_*.py"
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

# 导入 tts_server 前把缓存目录指到临时目录，避免污染 ~/.cache
os.environ.setdefault("TTS_CACHE_DIR", tempfile.mkdtemp(prefix="xiaoman-tts-test-"))
SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import tts_server  # noqa: E402

SECRET = "我的银行卡密码是123456"
ORIGIN = "http://127.0.0.1:8901"


class RedactTest(unittest.TestCase):
    """日志脱敏：绝不让 text 原文进入日志行"""

    def test_query_string_removed(self):
        line = f'"GET /tts?text={SECRET}&voice=zh-CN-XiaoyiNeural HTTP/1.1" 200 -'
        out = tts_server.redact_request_line(line)
        self.assertNotIn(SECRET, out)
        self.assertNotIn("text=", out)
        self.assertNotIn("?", out)
        self.assertIn("GET", out)
        self.assertIn("/tts", out)

    def test_post_request_line_kept(self):
        out = tts_server.redact_request_line('"POST /tts HTTP/1.1" 200 -')
        self.assertIn("POST /tts", out)
        self.assertNotIn("HTTP/1.1", out)

    def test_plain_path_kept(self):
        out = tts_server.redact_request_line('"GET /health HTTP/1.1" 200 -')
        self.assertIn("/health", out)

    def test_empty_line(self):
        self.assertEqual(tts_server.redact_request_line(""), "-")

    def test_handler_log_never_contains_text(self):
        h = object.__new__(tts_server.Handler)
        h.client_address = ("127.0.0.1", 12345)
        with self.assertLogs("xiaoman-tts", level="INFO") as cm:
            h.log_message('"%s" %s %s', f"GET /tts?text={SECRET} HTTP/1.1", 200, "-")
        joined = "\n".join(cm.output)
        self.assertNotIn(SECRET, joined)
        self.assertIn("/tts", joined)


class ParsePayloadTest(unittest.TestCase):
    """POST body → text/voice/rate 的参数解析"""

    def test_minimal_text_uses_defaults(self):
        p = tts_server.parse_tts_payload(json.dumps({"text": "你好"}).encode())
        self.assertEqual(p["text"], "你好")
        self.assertEqual(p["voice"], tts_server.DEFAULT_VOICE)
        self.assertEqual(p["rate"], "normal")

    def test_missing_text_raises(self):
        for body in (b"", b"{}", b'{"text": ""}', b'{"text": "   "}', b'{"text": 123}'):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    tts_server.parse_tts_payload(body)

    def test_invalid_json_raises(self):
        for body in (b"{not json", b"[]", b'"str"'):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    tts_server.parse_tts_payload(body)

    def test_overlong_text_truncated(self):
        long_text = "啊" * 1000 + SECRET
        p = tts_server.parse_tts_payload(json.dumps({"text": long_text}).encode())
        self.assertEqual(len(p["text"]), tts_server.MAX_TEXT_LEN)
        self.assertNotIn(SECRET, p["text"])

    def test_invalid_rate_falls_back_normal(self):
        for rate in ("turbo", "", None, 12):
            with self.subTest(rate=rate):
                p = tts_server.parse_tts_payload(
                    json.dumps({"text": "你好", "rate": rate}).encode())
                self.assertEqual(p["rate"], "normal")

    def test_valid_rate_kept(self):
        for rate in ("slow", "normal", "fast"):
            with self.subTest(rate=rate):
                p = tts_server.parse_tts_payload(
                    json.dumps({"text": "你好", "rate": rate}).encode())
                self.assertEqual(p["rate"], rate)

    def test_voice_whitelist(self):
        p = tts_server.parse_tts_payload(
            json.dumps({"text": "你好", "voice": "zh-CN-XiaoxiaoNeural"}).encode())
        self.assertEqual(p["voice"], "zh-CN-XiaoxiaoNeural")
        for bad in ("evil", "  ", "zh-CN-XiaoyiNeural; DROP TABLE", 42):
            with self.subTest(voice=bad):
                with self.assertRaises(ValueError):
                    tts_server.parse_tts_payload(
                        json.dumps({"text": "你好", "voice": bad}).encode())


class HandlerHTTPTest(unittest.TestCase):
    """本地回环起真实 Handler，验证 400/413/OPTIONS（不触发合成、不触网）"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = tts_server.ThreadingHTTPServer(("127.0.0.1", 0), tts_server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _post(self, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/tts", data=data, method="POST",
            headers={"Content-Type": "application/json", "Origin": ORIGIN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_missing_text_returns_400(self):
        code, body, _ = self._post({})
        self.assertEqual(code, 400)
        self.assertIn("missing text", json.loads(body.decode())["error"])

    def test_empty_body_returns_400(self):
        code, _, _ = self._post(b"")
        self.assertEqual(code, 400)

    def test_invalid_json_returns_400(self):
        code, _, _ = self._post(b"{oops")
        self.assertEqual(code, 400)

    def test_invalid_voice_returns_400(self):
        code, body, _ = self._post({"text": "你好", "voice": "evil"})
        self.assertEqual(code, 400)
        self.assertIn("invalid voice", json.loads(body.decode())["error"])

    def test_body_too_large_returns_413(self):
        big = json.dumps({"text": "x" * (tts_server.MAX_BODY_BYTES + 100)}).encode()
        code, _, _ = self._post(big)
        self.assertEqual(code, 413)

    def test_options_preflight_allows_json_post(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/tts", method="OPTIONS",
            headers={"Origin": ORIGIN,
                     "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "content-type"})
        with urllib.request.urlopen(req, timeout=5) as r:
            headers, status = dict(r.headers), r.status
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("Content-Type", headers.get("Access-Control-Allow-Headers", ""))

    def test_get_compat_still_works_and_text_not_logged(self):
        async def fake_synth(text, voice, rate="+4%", pitch="+12Hz"):
            return b"ID3legacy"

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/tts?"
            + urllib.parse.urlencode({"text": SECRET, "voice": tts_server.DEFAULT_VOICE}),
            headers={"Origin": ORIGIN})
        with mock.patch.object(tts_server, "HAS_EDGE", True), \
                mock.patch.object(tts_server, "_get_or_generate_audio", fake_synth), \
                self.assertLogs("xiaoman-tts", level="INFO") as cm:
            with urllib.request.urlopen(req, timeout=5) as r:
                code, body = r.status, r.read()

        self.assertEqual(code, 200)
        self.assertEqual(body, b"ID3legacy")
        self.assertNotIn(SECRET, "\n".join(cm.output))

    def test_post_body_never_logged(self):
        # 非法 voice 让请求在合成前 400，既能验证日志，又不触网
        with self.assertLogs("xiaoman-tts", level="INFO") as cm:
            self._post({"text": SECRET, "voice": "evil"})
        joined = "\n".join(cm.output)
        self.assertNotIn(SECRET, joined)

    def test_post_success_returns_audio_without_logging_text(self):
        calls = {}

        async def fake_synth(text, voice, rate="+4%", pitch="+12Hz"):
            calls.update(text=text, voice=voice, rate=rate)
            return b"ID3fake-audio"

        with mock.patch.object(tts_server, "HAS_EDGE", True), \
                mock.patch.object(tts_server, "_get_or_generate_audio", fake_synth), \
                self.assertLogs("xiaoman-tts", level="INFO") as cm:
            code, body, headers = self._post({"text": SECRET, "rate": "fast"})

        self.assertEqual(code, 200)
        self.assertEqual(body, b"ID3fake-audio")
        self.assertEqual(headers.get("Content-Type"), "audio/mpeg")
        self.assertEqual(calls["text"], SECRET)
        self.assertEqual(calls["voice"], tts_server.DEFAULT_VOICE)
        self.assertEqual(calls["rate"], tts_server.TTS_RATES["fast"])
        self.assertNotIn(SECRET, "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
