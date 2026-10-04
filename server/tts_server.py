#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tts_server.py · 语音回复服务（edge-tts 封装）
=============================================
GET /tts?text=你好&voice=zh-CN-XiaoyiNeural → audio/mpeg
语音质量验证：TTS 生成后用 ASR 回环核对文本（见 scripts/verify_tts.py）
"""
import json, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import edge_tts
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            body = json.dumps({"ok": True, "edge_tts": HAS_EDGE}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path != "/tts":
            self.send_error(404); return

        if not HAS_EDGE:
            self.send_error(503, "edge-tts not installed"); return

        qs = urllib.parse.parse_qs(parsed.query)
        text = (qs.get("text", ["你好呀"])[0])[:300]
        voice = qs.get("voice", ["zh-CN-XiaoyiNeural"])[0]

        async def gen():
            import asyncio
            communicate = edge_tts.Communicate(text, voice, rate="+4%", pitch="+12Hz")
            chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        import asyncio
        try:
            audio = asyncio.get_event_loop().run_until_complete(gen()) if True else b""
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
        except Exception as e:
            print("tts error:", e)
            self.send_error(500, str(e))

if __name__ == "__main__":
    print(f"小满 TTS 服务就绪 → http://127.0.0.1:8903/tts （edge-tts: {'可用' if HAS_EDGE else '未安装'}）")
    HTTPServer(("0.0.0.0", 8903), Handler).serve_forever()
