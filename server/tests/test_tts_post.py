#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tts_post.py · TTS POST 协议 + 日志脱敏 + provider 语义单测（审计 P1-5）

不触网：只测纯函数、provider 探测，以及本地回环 Handler 的 400/413/OPTIONS/503 行为。
默认 provider=none —— /tts 必须回 503 JSON，由前端降级到系统 TTS。
运行：python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import threading
import types
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


class ProviderTest(unittest.TestCase):
    """provider 语义：默认 none 不可用；可选 provider 需显式环境变量，不联网不下载"""

    def test_default_provider_is_none(self):
        with mock.patch.dict(os.environ, {"XIAOMAN_TTS_PROVIDER": "",
                                          "TTS_PROVIDER": ""}, clear=False):
            self.assertEqual(tts_server._provider_name(), "none")

    def test_unknown_provider_falls_back_none(self):
        with mock.patch.dict(os.environ, {"XIAOMAN_TTS_PROVIDER": "cloud-tts"}, clear=False):
            self.assertEqual(tts_server._provider_name(), "none")

    def test_supported_providers(self):
        for name in ("cmd", "sherpa", "edge"):
            with mock.patch.dict(os.environ, {"XIAOMAN_TTS_PROVIDER": name}, clear=False):
                self.assertEqual(tts_server._provider_name(), name)

    def test_none_never_available(self):
        self.assertFalse(tts_server.provider_available("none"))

    def test_cmd_requires_env(self):
        with mock.patch.dict(os.environ, {"XIAOMAN_TTS_CMD": ""}, clear=False):
            self.assertFalse(tts_server.provider_available("cmd"))
        with mock.patch.dict(os.environ, {"XIAOMAN_TTS_CMD": "tts-cli --stdout"}, clear=False):
            self.assertTrue(tts_server.provider_available("cmd"))

    def test_sherpa_requires_local_model_files(self):
        with mock.patch.dict(os.environ, {"XIAOMAN_TTS_SHERPA_MODEL": "",
                                          "XIAOMAN_TTS_SHERPA_TOKENS": ""}, clear=False):
            self.assertFalse(tts_server.provider_available("sherpa"))

    def test_rate_table_matches_frontend(self):
        # normal=0.95 与 EDGE_RATES 的 "-5%" 同义（B4 首选参数）；cmd/sherpa 吃倍率
        self.assertEqual(tts_server.TTS_RATES,
                         {"slow": 0.84, "normal": 0.95, "fast": 1.12})

    def test_edge_rate_table_is_percentage(self):
        self.assertEqual(tts_server.EDGE_RATES,
                         {"slow": "-12%", "normal": "-5%", "fast": "+5%"})

    def test_voice_whitelist_drops_nonexistent_xiaoyuan(self):
        """zh-CN-XiaoyuanNeural 在 edge-tts 里不存在（实测 NoAudioReceived），必须拒绝"""
        with self.assertRaises(ValueError):
            tts_server.parse_tts_payload(
                json.dumps({"text": "你好", "voice": "zh-CN-XiaoyuanNeural"}).encode())
        for ok in ("zh-CN-XiaoyiNeural", "zh-CN-shaanxi-XiaoniNeural",
                   "zh-CN-YunxiaNeural"):
            with self.subTest(ok=ok):
                p = tts_server.parse_tts_payload(
                    json.dumps({"text": "你好", "voice": ok}).encode())
                self.assertEqual(p["voice"], ok)


class CmdProviderTest(unittest.TestCase):
    """cmd provider：原文只走 stdin，进程只回音频；失败只报退出码（不泄露输出）"""

    def _run(self, code, argv):
        with mock.patch.object(tts_server, "PROVIDER", "cmd"), \
                mock.patch.object(tts_server, "_cmd_argv", return_value=tuple(argv)):
            return tts_server._cmd_synthesize("机密原文", "zh-CN-XiaoyiNeural", "normal")

    def test_output_is_returned(self):
        audio = self._run(0, [sys.executable, "-c",
                              "import sys;sys.stdout.buffer.write(b'ID3fake')"])
        self.assertEqual(audio, b"ID3fake")

    def test_nonzero_exit_raises_without_echo(self):
        with self.assertRaises(tts_server.TtsProviderUnavailable) as cm:
            self._run(0, [sys.executable, "-c", "import sys;sys.exit(3)"])
        self.assertNotIn("机密原文", str(cm.exception))

    def test_empty_output_raises(self):
        with self.assertRaises(tts_server.TtsProviderUnavailable):
            self._run(0, [sys.executable, "-c", "pass"])


class EdgeProviderTest(unittest.TestCase):
    """edge provider：只验证拼包、参数映射与失败语义（用假模块，不触网）"""

    def _fake(self, audio=b"ID3edge", seen=None):
        class FakeComm:
            def __init__(self, text, voice, rate=None, pitch=None):
                if seen is not None:
                    seen.update(text=text, voice=voice, rate=rate, pitch=pitch)

            async def stream(self):
                yield {"type": "WordBoundary", "offset": 0}
                yield {"type": "audio", "data": audio}

        return types.SimpleNamespace(Communicate=FakeComm)

    def test_joins_audio_chunks_and_maps_rate_pitch(self):
        seen = {}
        with mock.patch.dict(sys.modules, {"edge_tts": self._fake(seen=seen)}), \
                mock.patch.dict(os.environ, {"XIAOMAN_TTS_EDGE_PITCH": "+3Hz"}):
            out = tts_server._edge_synthesize("你好", "zh-CN-XiaoyiNeural", "slow")
        self.assertEqual(out, b"ID3edge")
        self.assertEqual(seen["rate"], "-12%")
        self.assertEqual(seen["pitch"], "+3Hz")

    def test_missing_package_raises(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "edge_tts":
                raise ImportError("no edge_tts")
            return real_import(name, *a, **kw)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(tts_server.TtsProviderUnavailable) as cm:
                tts_server._edge_synthesize("机密原文", "zh-CN-XiaoyiNeural", "normal")
        self.assertNotIn("机密原文", str(cm.exception))

    def test_empty_audio_raises_without_echo(self):
        with mock.patch.dict(sys.modules, {"edge_tts": self._fake(audio=b"")}):
            with self.assertRaises(tts_server.TtsProviderUnavailable):
                tts_server._edge_synthesize("机密原文", "zh-CN-XiaoyiNeural", "normal")


class HandlerHTTPTest(unittest.TestCase):
    """本地回环起真实 Handler，验证 400/413/OPTIONS/503（不触发合成、不触网）"""

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

    def _get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers=headers or {"Origin": ORIGIN})
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

    def test_default_provider_none_post_returns_503(self):
        """默认不启用任何合成后端：POST 必须回 503 JSON，供前端静默降级"""
        with mock.patch.object(tts_server, "PROVIDER", "none"), \
                mock.patch.object(tts_server, "provider_available", return_value=False), \
                self.assertLogs("xiaoman-tts", level="INFO") as cm:
            code, body, headers = self._post({"text": SECRET})

        self.assertEqual(code, 503)
        payload = json.loads(body.decode())
        self.assertEqual(payload, {"error": "tts_provider_unavailable", "provider": "none"})
        self.assertEqual(headers.get("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        joined = "\n".join(cm.output)
        self.assertNotIn(SECRET, joined)
        self.assertIn("tts unavailable", joined)

    def test_default_provider_none_get_returns_503_without_text_in_log(self):
        qs = urllib.parse.urlencode({"text": SECRET, "voice": tts_server.DEFAULT_VOICE})
        with mock.patch.object(tts_server, "PROVIDER", "none"), \
                mock.patch.object(tts_server, "provider_available", return_value=False), \
                self.assertLogs("xiaoman-tts", level="INFO") as cm:
            code, body, _ = self._get(f"/tts?{qs}")

        self.assertEqual(code, 503)
        self.assertEqual(json.loads(body.decode()),
                         {"error": "tts_provider_unavailable", "provider": "none"})
        self.assertNotIn(SECRET, "\n".join(cm.output))

    def test_health_reports_provider_and_no_tts_text(self):
        with mock.patch.object(tts_server, "PROVIDER", "none"), \
                mock.patch.object(tts_server, "provider_available", return_value=False):
            code, body, _ = self._get("/health")
        self.assertEqual(code, 200)
        payload = json.loads(body.decode())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "none")
        self.assertFalse(payload["available"])

    def test_get_compat_still_works_and_text_not_logged(self):
        def fake_synth(text, voice, rate="normal"):
            return b"ID3legacy", "audio/mpeg"

        qs = urllib.parse.urlencode({"text": SECRET, "voice": tts_server.DEFAULT_VOICE})
        with mock.patch.object(tts_server, "provider_available", return_value=True), \
                mock.patch.object(tts_server, "_get_or_generate_audio", fake_synth), \
                self.assertLogs("xiaoman-tts", level="INFO") as cm:
            code, body, _ = self._get(f"/tts?{qs}")

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

        def fake_synth(text, voice, rate="normal"):
            calls.update(text=text, voice=voice, rate=rate)
            return b"ID3fake-audio", "audio/mpeg"

        with mock.patch.object(tts_server, "provider_available", return_value=True), \
                mock.patch.object(tts_server, "_get_or_generate_audio", fake_synth), \
                self.assertLogs("xiaoman-tts", level="INFO") as cm:
            code, body, headers = self._post({"text": SECRET, "rate": "fast"})

        self.assertEqual(code, 200)
        self.assertEqual(body, b"ID3fake-audio")
        self.assertEqual(headers.get("Content-Type"), "audio/mpeg")
        self.assertEqual(calls["text"], SECRET)
        self.assertEqual(calls["voice"], tts_server.DEFAULT_VOICE)
        self.assertEqual(calls["rate"], "fast")
        self.assertEqual(tts_server.TTS_RATES[calls["rate"]], 1.12)
        self.assertNotIn(SECRET, "\n".join(cm.output))


class PreferredVoiceAndFallbackTest(unittest.TestCase):
    """B4 首选音色固化 + edge 断网落到 E1(Kokoro/sherpa) 降级链"""

    def test_b4_is_default_voice(self):
        # 画像结论：晓伊（Xiaoyi）为首选；normal 语速 = −5%，音高 = +2Hz
        self.assertEqual(tts_server.DEFAULT_VOICE, "zh-CN-XiaoyiNeural")
        self.assertEqual(tts_server.TTS_RATES["normal"], 0.95)
        self.assertEqual(tts_server._edge_pitch(), "+2Hz")

    def test_provider_chain_includes_fallback(self):
        saved = (tts_server.PROVIDER, tts_server.FALLBACK)
        try:
            tts_server.PROVIDER, tts_server.FALLBACK = "edge", "sherpa"
            self.assertEqual(tts_server.provider_chain(), ["edge", "sherpa"])
            tts_server.FALLBACK = ""
            self.assertEqual(tts_server.provider_chain(), ["edge"])
        finally:
            tts_server.PROVIDER, tts_server.FALLBACK = saved

    def test_fallback_wav_used_when_edge_fails(self):
        def edge_down(*a, **k):
            raise tts_server.TtsProviderUnavailable("edge-tts failed: TimeoutError")

        saved = (tts_server.PROVIDER, tts_server.FALLBACK)
        try:
            tts_server.PROVIDER, tts_server.FALLBACK = "edge", "cmd"
            with mock.patch.object(tts_server, "provider_available", return_value=True), \
                    mock.patch.object(tts_server, "_edge_synthesize", edge_down), \
                    mock.patch.object(tts_server, "_cmd_synthesize", return_value=b"RIFF-cmd"):
                audio, ctype = tts_server.synthesize("测试", tts_server.DEFAULT_VOICE)
        finally:
            tts_server.PROVIDER, tts_server.FALLBACK = saved
        self.assertEqual(audio, b"RIFF-cmd")
        self.assertEqual(ctype, "audio/wav")   # 跟着真正出声的 provider 走

    def test_both_down_raises(self):
        def down(*a, **k):
            raise tts_server.TtsProviderUnavailable("down")

        saved = (tts_server.PROVIDER, tts_server.FALLBACK)
        try:
            tts_server.PROVIDER, tts_server.FALLBACK = "edge", "sherpa"
            with mock.patch.object(tts_server, "provider_available", return_value=True), \
                    mock.patch.object(tts_server, "_edge_synthesize", down), \
                    mock.patch.object(tts_server, "_sherpa_synthesize", down):
                with self.assertRaises(tts_server.TtsProviderUnavailable):
                    tts_server.synthesize("测试", tts_server.DEFAULT_VOICE)
        finally:
            tts_server.PROVIDER, tts_server.FALLBACK = saved

    def test_any_provider_available_uses_fallback(self):
        saved = (tts_server.PROVIDER, tts_server.FALLBACK)
        try:
            tts_server.PROVIDER, tts_server.FALLBACK = "edge", "sherpa"
            with mock.patch.object(tts_server, "provider_available",
                                   side_effect=lambda name=None: name == "sherpa"):
                self.assertTrue(tts_server.any_provider_available())
        finally:
            tts_server.PROVIDER, tts_server.FALLBACK = saved


if __name__ == "__main__":
    unittest.main()
