#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tts_server.py · 语音回复服务（edge-tts 封装）
===========================================
POST /tts  {"text": "你好", "voice": "zh-CN-XiaoyiNeural"} → audio/mpeg
GET  /tts?text=你好&voice=… → audio/mpeg（保留兼容旧客户端，新客户端请用 POST）
安全约定：对话原文只走 POST body；日志只记「方法 + path」，查询串/body 均不落盘。
语音质量验证：TTS 生成后用 ASR 回环核对文本（见 scripts/verify_tts.py）
"""
import json
import os
import re
import urllib.parse
import hashlib
import asyncio
import logging
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import xm_config

# ---------------- 日志配置 ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("xiaoman-tts")

# ---------------- 环境变量配置 ----------------
# 默认仅本机回环；容器内由 compose 注入 0.0.0.0，再由端口映射回宿主机回环
BIND_HOST = xm_config.BIND_HOST
PORT = xm_config.TTS_PORT

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
    "zh-CN-XiaoyuanNeural",    # 女性，甜妹（设置页可选音色）
    "zh-CN-YunxiaNeural",      # 女性，知性
    "zh-CN-liaoning-XiaobeiNeural",  # 东北方言
    "zh-CN-shaanxi-XiaoniNeural",    # 陕西方言
})

# 语速档位 → edge-tts rate 参数（与前端设置页 slow/normal/fast 对齐）
TTS_RATES = {
    "slow": "-12%",
    "normal": "+4%",
    "fast": "+18%",
}

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
MAX_TEXT_LEN = 300        # 单条语音文本上限（超出截断，防止超长请求拖垮合成）
MAX_BODY_BYTES = 8 * 1024  # POST JSON body 上限

_QUERY_RE = re.compile(r"\?\S*")
_HTTP_VER_RE = re.compile(r"\s+HTTP/\d+(?:\.\d+)?")


# ---------------- 纯函数：日志脱敏 / POST 参数解析 ----------------
def redact_request_line(line):
    """日志脱敏：抹掉查询串与 HTTP 版本，只留「方法 + path」及状态码。

    审计 P1-5：旧版 GET /tts?text=… 会把对话原文整行写进日志。任何日志行
    进入 logger 前都要过这里；body 从不参与日志拼接。

    >>> redact_request_line('"GET /tts?text=你好 HTTP/1.1" 200 -')
    '"GET /tts" 200 -'
    """
    if not line:
        return "-"
    s = str(line).replace("\r", " ").replace("\n", " ")
    s = _QUERY_RE.sub("", s)      # ? 及其后到空白前的查询串整体删除
    s = _HTTP_VER_RE.sub("", s)   # 去掉 HTTP/1.1 版本号
    return s.strip()


def parse_tts_payload(raw_body):
    """解析 POST /tts 的 JSON body，返回 {"text", "voice", "rate"}。

    - text 缺失/空白/非字符串 → ValueError("missing text")
    - voice 不在白名单 → ValueError("invalid voice: …")
    - rate 非法 → 回退 normal（不报错）
    - text 超长 → 截断到 MAX_TEXT_LEN
    """
    if isinstance(raw_body, (bytes, bytearray)):
        try:
            raw_body = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("invalid json body")
    try:
        data = json.loads(raw_body or "{}")
    except (json.JSONDecodeError, TypeError):
        raise ValueError("invalid json body")
    if not isinstance(data, dict):
        raise ValueError("invalid json body")

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("missing text")
    text = text[:MAX_TEXT_LEN]

    voice = data.get("voice") or DEFAULT_VOICE
    if not isinstance(voice, str) or voice not in ALLOWED_VOICES:
        raise ValueError(f"invalid voice: {voice}")

    rate_key = data.get("rate") or "normal"
    if not isinstance(rate_key, str) or rate_key not in TTS_RATES:
        rate_key = "normal"

    return {"text": text, "voice": voice, "rate": rate_key}


# ---------------- 磁盘缓存 + single-flight ----------------
# 缓存目录可配置（容器内挂 volume 到 /data/tts；本地 dev 默认落在项目 .cache/tts）
CACHE_DIR = Path(
    os.environ.get("TTS_CACHE_DIR")
    or os.environ.get("XIAOMAN_TTS_CACHE_DIR")
    or Path.home() / ".cache" / "xiaoman_tts"
)
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
            # 首次连接 edge-tts 端点可能因 TLS/DNS 冷启动 >10s；生成结果会落缓存，
            # 客户端另有 7s 超时兜底（先出文字），服务端给 20s 让冷请求有机会成功
            audio = await asyncio.wait_for(_synthesize(text, voice, rate, pitch), timeout=20.0)
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
        # 审计 P1-5：只记录「方法 + path」，查询串（旧 GET 的 ?text=…）整体抹掉；
        # body 从不参与日志拼接。
        try:
            msg = format % args
        except Exception:
            msg = str(format)
        logger.info("%s - - [%s] %s", self.address_string(),
                    self.log_date_time_string(), redact_request_line(msg))

    def _cors(self):
        """CORS 白名单；未命中不发 ACAO"""
        for k, v in xm_config.cors_headers(self.headers.get("Origin")).items():
            self.send_header(k, v)

    def _cors_audio(self):
        """音频响应：公开资源，直接放行任意 Origin。

        安卓 WebView（file:// → Origin: null，或 https://appassets.androidplatform.net）
        播放时前端会给 <audio> 设 crossOrigin="anonymous" 才能用 WebAudio 取音量包络；
        没有 ACAO 头时浏览器会直接静音（不是播放失败，是"有画面无声"）。
        """
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Accept-Ranges", "none")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message):
        """统一 JSON 错误格式，不泄露内部异常细节"""
        logger.error("HTTP %d: %s", code, message)
        self._json({"error": message}, code)

    def do_OPTIONS(self):
        # POST application/json 会触发预检，必须放行 Content-Type 与 POST
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def _serve_tts(self, text, voice, rate_key, start):
        """GET/POST 共用的合成 + 音频响应"""
        if not HAS_EDGE:
            self._error(503, "edge-tts not installed")
            return

        try:
            audio = asyncio.run(_get_or_generate_audio(text, voice, TTS_RATES[rate_key]))
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self._cors_audio()
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            logger.debug("tts 200 %dms audio_len=%d",
                         int((time.time() - start) * 1000), len(audio))
        except RuntimeError:
            # 已在 _get_or_generate_audio 中记录详细日志，这里只返回通用错误
            self._error(500, "tts synthesis failed")
        except Exception:
            logger.exception("Unexpected TTS error")
            self._error(500, "internal server error")

    def do_GET(self):
        start = time.time()
        # 路径归一化：解析出纯 path 部分再 rstrip("/")
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            self._json({"ok": True, "service": "xiaoman-tts", "edge_tts": HAS_EDGE,
                        "cache_dir": str(CACHE_DIR)})
            logger.debug("GET /health %dms", int((time.time() - start) * 1000))
            return

        if path != "/tts":
            self._error(404, "not found")
            logger.debug("GET %s 404 %dms", redact_request_line(self.path),
                         int((time.time() - start) * 1000))
            return

        # 兼容旧客户端：GET /tts?text=… 仍可用；新客户端一律走 POST（原文不进日志）
        qs = urllib.parse.parse_qs(parsed.query)
        text = (qs.get("text", ["你好呀"])[0])[:MAX_TEXT_LEN]
        voice = qs.get("voice", [DEFAULT_VOICE])[0]
        rate_key = qs.get("rate", ["normal"])[0]
        if rate_key not in TTS_RATES:
            rate_key = "normal"

        # Voice 白名单校验：非法直接 400 JSON
        if voice not in ALLOWED_VOICES:
            self._error(400, f"invalid voice: {voice}")
            return

        self._serve_tts(text, voice, rate_key, start)

    def do_POST(self):
        start = time.time()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path != "/tts":
            self._error(404, "not found")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(400, "invalid content length")
            return
        if length < 1:
            self._error(400, "missing text")
            return
        if length > MAX_BODY_BYTES:
            self._error(413, "request body too large")
            return

        try:
            payload = parse_tts_payload(self.rfile.read(length))
        except ValueError as e:
            self._error(400, str(e))
            return

        # body（对话原文）只用于合成，绝不写日志
        self._serve_tts(payload["text"], payload["voice"], payload["rate"], start)


if __name__ == "__main__":
    print(f"小满 TTS 服务就绪 → http://{BIND_HOST}:{PORT}/tts "
          f"（edge-tts: {'可用' if HAS_EDGE else '未安装'}） (bind: {BIND_HOST}, cache: {CACHE_DIR})")
    ThreadingHTTPServer((BIND_HOST, PORT), Handler).serve_forever()