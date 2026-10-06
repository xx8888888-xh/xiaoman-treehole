#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tts_server.py · 语音回复服务（edge-tts 封装）
===========================================
GET /tts?text=你好&voice=zh-CN-XiaoyiNeural → audio/mpeg
语音质量验证：TTS 生成后用 ASR 回环核对文本（见 scripts/verify_tts.py）
"""
import json
import os
import urllib.parse
import hashlib
import asyncio
import logging
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ---------------- 日志配置 ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("xiaoman-tts")

# ---------------- 环境变量配置 ----------------
BIND_HOST = os.environ.get("XIAOMAN_BIND_HOST", "0.0.0.0")

# 代理：aiohttp 默认不读环境变量，必须显式传给 edge-tts，否则受限网络直连会超时
# 优先 HTTPS_PROXY → https_proxy → ALL_PROXY → all_proxy，都没有则为 None
PROXY = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
         or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy") or None)

# ---------------- edge-tts 可用性 ----------------
try:
    import edge_tts
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False
    edge_tts = None  # type: ignore

# ---------------- Voice 白名单 ----------------
# 包含默认 zh-CN-XiaoyiNeural 与常用 zh-CN 音色
ALLOWED_VOICES = frozenset({
    "zh-CN-XiaoyiNeural",      # 默认：女性，自然
    "zh-CN-YunjianNeural",     # 男性，沉稳
    "zh-CN-YunxiNeural",       # 男性，阳光
    "zh-CN-XiaoxiaoNeural",    # 女性，温柔
    "zh-CN-YunxiaNeural",      # 女性，知性
    "zh-CN-liaoning-XiaobeiNeural",  # 东北方言
    "zh-CN-shaanxi-XiaoniNeural",    # 陕西方言
})

# ---------------- 磁盘缓存 + single-flight ----------------
CACHE_DIR = Path("/tmp/xiaoman_tts_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MAX_CACHE_ENTRIES = 200
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7天

_cache_locks = {}  # key -> asyncio.Lock (single-flight)
_cache_locks_lock = threading.Lock()


def _cache_key(text: str, voice: str, rate: str, pitch: str) -> str:
    h = hashlib.sha1(f"{text}|{voice}|{rate}|{pitch}".encode()).hexdigest()
    return h


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.mp3"


def _cleanup_cache():
    """LRU 清理：按 mtime 删除最旧，保留 MAX_CACHE_ENTRIES"""
    try:
        files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for f in files[:-MAX_CACHE_ENTRIES]:
            f.unlink(missing_ok=True)
        # 清理过期
        now = time.time()
        for f in CACHE_DIR.glob("*.mp3"):
            if now - f.stat().st_mtime > CACHE_TTL_SECONDS:
                f.unlink(missing_ok=True)
    except Exception:
        logger.debug("Cache cleanup failed", exc_info=True)


async def _synthesize(text: str, voice: str, rate: str, pitch: str) -> bytes:
    """实际合成协程，带 10 秒超时"""
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, proxy=PROXY)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


async def _get_or_generate_audio(text: str, voice: str, rate: str = "+4%", pitch: str = "+12Hz") -> bytes:
    """带缓存 + single-flight 的合成入口"""
    key = _cache_key(text, voice, rate, pitch)
    cache_file = _cache_path(key)

    # 缓存命中
    if cache_file.exists():
        logger.debug("TTS cache hit: %s", key[:8])
        return cache_file.read_bytes()

    # Single-flight：同 key 并发请求只合成一次
    with _cache_locks_lock:
        if key not in _cache_locks:
            _cache_locks[key] = asyncio.Lock()
        lock = _cache_locks[key]

    async with lock:
        # 双重检查
        if cache_file.exists():
            return cache_file.read_bytes()

        logger.info("TTS generating: voice=%s text_len=%d", voice, len(text))
        try:
            audio = await asyncio.wait_for(_synthesize(text, voice, rate, pitch), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("TTS timeout: voice=%s", voice)
            raise RuntimeError("TTS synthesis timeout")
        except Exception as e:
            logger.error("TTS synthesis failed: voice=%s error=%s", voice, e)
            raise RuntimeError("TTS synthesis failed")

        # 写缓存（原子写入）
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(cache_file)
        _cleanup_cache()
        return audio


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # CORS：Origin 保留 *，收敛 Headers/Methods
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message):
        """统一 JSON 错误格式，不泄露内部异常细节"""
        logger.error("HTTP %d: %s", code, message)
        self._json({"error": message}, code)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        start = time.time()
        # 路径归一化：解析出纯 path 部分再 rstrip("/")
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            self._json({"ok": True, "edge_tts": HAS_EDGE})
            logger.debug("GET /health %dms", int((time.time() - start) * 1000))
            return

        if path != "/tts":
            self._error(404, "not found")
            logger.debug("GET %s 404 %dms", self.path, int((time.time() - start) * 1000))
            return

        if not HAS_EDGE:
            self._error(503, "edge-tts not installed")
            return

        qs = urllib.parse.parse_qs(parsed.query)
        text = (qs.get("text", ["你好呀"])[0])[:300]
        voice = qs.get("voice", ["zh-CN-XiaoyiNeural"])[0]

        # Voice 白名单校验：非法直接 400 JSON
        if voice not in ALLOWED_VOICES:
            self._error(400, f"invalid voice: {voice}")
            return

        try:
            audio = asyncio.run(_get_or_generate_audio(text, voice))
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            logger.debug("GET /tts 200 %dms audio_len=%d", int((time.time() - start) * 1000), len(audio))
        except RuntimeError as e:
            # 已在 _get_or_generate_audio 中记录详细日志，这里只返回通用错误
            self._error(500, "tts synthesis failed")
        except Exception as e:
            logger.exception("Unexpected TTS error")
            self._error(500, "internal server error")


if __name__ == "__main__":
    print(f"小满 TTS 服务就绪 → http://{BIND_HOST}:8903/tts （edge-tts: {'可用' if HAS_EDGE else '未安装'}） (bind: {BIND_HOST})")
    ThreadingHTTPServer((BIND_HOST, 8903), Handler).serve_forever()