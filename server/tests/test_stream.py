#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stream.py · W7 真流式 SSE 回归（审计 P0-1）

覆盖：mock 流式事件顺序 / mode 透传 / 首字 < 1.5s / 危机先于上游 /
Kilo 隐私策略 / 上游 500 / 上游 SSE 增量解析 / 中途断连 / 日志不含原文。
不触外网：上游一律用假连接/假响应（mock 掉 _up_acquire）。
运行：python -m pytest server/tests -q
"""
import http.client
import json
import os
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ["AI_MODE"] = "mock"
os.environ.pop("AI_API_BASE", None)
os.environ.pop("AI_API_KEY", None)
os.environ.pop("XIAOMAN_ALLOW_FREE_PERSONAL", None)

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import mock_api  # noqa: E402
import provider_policy  # noqa: E402

mock_api.STREAM_DELAY = 0.0   # 测试不等自然节奏

D1 = "我把东西都送人了，也跟家里人好好道过别，觉得这样对大家都轻松"
SECRET = "我的银行卡密码是741852，别告诉别人"


def parse_events(stream, limit=None, t0=None):
    """逐行读取 SSE；返回 [(event, data, 相对耗时ms)]"""
    out = []
    event = None
    data = []
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "":
            if event and data:
                out.append((event, json.loads("\n".join(data)),
                            int((time.time() - t0) * 1000) if t0 else 0))
            event, data = None, []
            if limit and len(out) >= limit:
                break
    return out


class FakeConn:
    """替代 http.client 上游连接（测试离线，不触外网）。"""

    def __init__(self, resp=None, request_error=None):
        self.resp = resp
        self.request_error = request_error
        self.sock = None
        self.sent = None

    def request(self, method, path, body=None, headers=None):
        if self.request_error:
            raise self.request_error
        self.sent = (method, path, body, headers)

    def getresponse(self):
        return self.resp

    def close(self):
        self.sock = None


class FakeResp:
    def __init__(self, status=200, ctype="application/json", body=b"", lines=None):
        self.status = status
        self.headers = {"Content-Type": ctype}
        self._body = body
        self._lines = lines or []

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._body

    def close(self):
        pass


class StreamServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), mock_api.Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def open_stream(self, payload, timeout=15):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        conn.request("POST", "/api/chat/stream",
                     json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                     {"Content-Type": "application/json; charset=utf-8"})
        return conn, conn.getresponse()

    def health(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/health")
        res = conn.getresponse()
        body = json.loads(res.read().decode())
        conn.close()
        return body

    # ---------- 1. mock 流式：meta → delta* → done ----------
    def test_mock_stream_order_mode_and_latency(self):
        t0 = time.time()
        conn, res = self.open_stream({
            "mode": "mock", "session_id": "s-mock",
            "messages": [{"role": "user", "content": "今天上班好累啊"}],
        })
        self.assertEqual(res.status, 200)
        self.assertIn("text/event-stream", res.getheader("Content-Type"))
        events = parse_events(res, t0=t0)
        conn.close()

        names = [e[0] for e in events]
        self.assertEqual(names[0], "meta")
        self.assertEqual(events[0][1]["mode"], "mock")
        self.assertEqual(names[-1], "done")
        self.assertGreaterEqual(names.count("delta"), 2)

        first_delta_ms = next(ms for name, _, ms in events if name == "delta")
        self.assertLess(first_delta_ms, 1500, "mock 首字 P95 目标 < 1.5s")

        text = "".join(d["text"] for n, d, _ in events if n == "delta")
        done = events[-1][1]
        self.assertEqual(done["mode"], "mock")
        self.assertIn("content", done)
        parsed = json.loads(done["content"])
        self.assertEqual(parsed["reply"], text)
        self.assertEqual(done["first_token_ms"], done["ttfb_ms"])
        self.assertLess(done["total_ms"], 5000)
        self.assertLessEqual(len(parsed["reply"]), 80, "长度硬约束")

    # ---------- 2. mode 透传：offline 不产生 delta ----------
    def test_offline_mode_passthrough(self):
        conn, res = self.open_stream({
            "mode": "offline", "session_id": "s-offline",
            "messages": [{"role": "user", "content": "你好"}],
        })
        events = parse_events(res)
        conn.close()
        self.assertEqual(events[0][0], "meta")
        self.assertEqual(events[0][1]["mode"], "offline")
        self.assertEqual(events[-1][0], "done")
        self.assertTrue(events[-1][1]["offline"])
        self.assertFalse([e for e in events if e[0] == "delta"])

    # ---------- 3. 危机闸门先于上游：绝不外发原文 ----------
    def test_crisis_blocks_upstream(self):
        with mock.patch.object(mock_api, "AI_API_BASE", "https://api.kilo.ai/api/gateway"), \
             mock.patch.object(mock_api, "_up_acquire",
                               side_effect=AssertionError("上游不应被调用")):
            conn, res = self.open_stream({
                "mode": "openai", "session_id": "s-crisis",
                "messages": [{"role": "user", "content": D1}],
            })
            events = parse_events(res)
            conn.close()
        self.assertEqual(events[0][1]["mode"], "crisis")
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["mode"], "crisis")
        payload = json.loads(events[-1][1]["content"])
        self.assertTrue(payload.get("crisis"))
        self.assertIn("12356", payload.get("reply", ""))
        self.assertIn("12356", "".join(e[1]["text"] for e in events if e[0] == "delta"))

    # ---------- 4. Kilo 免费 trial 隐私策略：默认拒绝 ----------
    def test_kilo_blocked_by_privacy_policy(self):
        with mock.patch.object(mock_api, "AI_API_BASE", "https://api.kilo.ai/api/gateway"), \
             mock.patch.object(provider_policy, "ALLOW_FREE_PERSONAL", False), \
             mock.patch.object(mock_api, "_up_acquire",
                               side_effect=AssertionError("上游不应被调用")):
            conn, res = self.open_stream({
                "mode": "openai", "session_id": "s-privacy",
                "messages": [{"role": "user", "content": "今天被领导骂了，好难受"}],
            })
            events = parse_events(res)
            conn.close()
        # 策略拒绝发生在任何上游请求之前，也不会先发"在线"meta 误导用户
        self.assertFalse([e for e in events if e[0] == "meta"])
        err = next(e for e in events if e[0] == "error")[1]
        self.assertEqual(err["kind"], "policy")
        self.assertEqual(err["status"], 403)
        self.assertEqual(err["mode"], "mock")
        self.assertNotIn("messages", err)

    # ---------- 5. provider 500：error 事件（客户端据此降级） ----------
    def test_upstream_500_emits_error_event(self):
        resp500 = FakeResp(status=500, body=b'{"error":"upstream exploded"}')
        with mock.patch.object(mock_api, "AI_API_BASE", "https://api.openai.com/v1"), \
             mock.patch.object(mock_api, "AI_API_KEY", "sk-test"), \
             mock.patch.object(mock_api, "_up_acquire",
                               return_value=FakeConn(resp500)):
            conn, res = self.open_stream({
                "mode": "openai", "session_id": "s-500",
                "messages": [{"role": "user", "content": "随便聊聊"}],
            })
            events = parse_events(res)
            conn.close()
        ev = next(e for e in events if e[0] == "error")
        self.assertEqual(ev[1]["kind"], "http")
        self.assertEqual(ev[1]["status"], 500)
        self.assertEqual(ev[1]["mode"], "openai")

    # ---------- 6. 上游 SSE 增量 → reply 前缀流式 ----------
    def test_upstream_sse_deltas(self):
        content = json.dumps({"reply": "在呢，累就歇会儿||别硬扛", "emotion": "gentle",
                              "motion": "Nod", "memory_updates": {}, "reminders": None},
                             ensure_ascii=False)
        pieces = [content[i:i + 6] for i in range(0, len(content), 6)]
        lines = []
        for p in pieces:
            lines.append(("data: " + json.dumps(
                {"choices": [{"delta": {"content": p}}]}, ensure_ascii=False)).encode())
            lines.append(b"")
        lines.append(b"data: [DONE]")
        lines.append(b"")

        sse_resp = FakeResp(status=200, ctype="text/event-stream; charset=utf-8", lines=lines)

        with mock.patch.object(mock_api, "AI_API_BASE", "https://api.openai.com/v1"), \
             mock.patch.object(mock_api, "AI_API_KEY", "sk-test"), \
             mock.patch.object(mock_api, "_up_acquire",
                               return_value=FakeConn(sse_resp)):
            conn, res = self.open_stream({
                "mode": "openai", "session_id": "s-sse",
                "messages": [{"role": "user", "content": "今天也一般般"}],
            })
            events = parse_events(res)
            conn.close()

        text = "".join(e[1]["text"] for e in events if e[0] == "delta")
        self.assertEqual(text, "在呢，累就歇会儿||别硬扛")
        done = events[-1][1]
        self.assertEqual(done["mode"], "openai")
        self.assertEqual(json.loads(done["content"])["reply"], "在呢，累就歇会儿||别硬扛")
        self.assertEqual(done["provider"], "openai")
        delta_ms = next(ms for n, _, ms in events if n == "delta")
        self.assertLess(delta_ms, 1500)

    # ---------- 7. 中途断连：服务不崩、后续请求可用 ----------
    def test_client_disconnect_mid_stream(self):
        conn, res = self.open_stream({
            "mode": "mock", "session_id": "s-abort",
            "messages": [{"role": "user", "content": "我回来了"}],
        }, timeout=5)
        first = parse_events(res, limit=2)
        self.assertEqual(first[0][0], "meta")
        conn.close()   # 客户端中途走人（等价 AbortController）
        time.sleep(0.2)
        self.assertTrue(self.health()["ok"], "断连后服务端仍存活")

    # ---------- 8. 计量日志：有耗时/token，无用户原文 ----------
    def test_metrics_logged_without_user_text(self):
        with self.assertLogs("xiaoman-mock", level="INFO") as cm:
            conn, res = self.open_stream({
                "mode": "mock", "session_id": "s-log",
                "messages": [{"role": "user", "content": SECRET}],
            })
            events = parse_events(res)
            conn.close()
        joined = "\n".join(cm.output)
        self.assertIn("chat route=stream", joined)
        self.assertIn("mode=mock", joined)
        self.assertIn("first_token_ms=", joined)
        self.assertIn("tokens=", joined)
        self.assertNotIn(SECRET, joined, "日志绝不能包含用户原文")
        self.assertNotIn("s-log", joined, "会话 id 只打 sha1 截断")


if __name__ == "__main__":
    unittest.main()
