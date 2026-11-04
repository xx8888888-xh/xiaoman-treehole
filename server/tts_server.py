#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tts_server.py · 语音回复服务（可插拔 TTS provider）
===================================================
默认 provider = none：不内置任何云 TTS、不下载任何模型，/tts 直接回
503 {"error": "tts_provider_unavailable", "provider": "none"}，
前端据此静默降级到「安卓系统 TTS / 浏览器 speechSynthesis」，文字先出不受影响。

可选 provider（全部需要显式环境变量开启，且不新增任何运行时 pip 依赖）：
  XIAOMAN_TTS_PROVIDER=cmd    调用本机已有的 TTS 可执行文件：
                               XIAOMAN_TTS_CMD /path/to/tts-cli      （必填）
                               XIAOMAN_TTS_CMD_TYPE audio/wav|audio/mpeg（默认 audio/wav）
                               文本经 stdin 传入，音频写 stdout；
                               N 也可把 sherpa-onnx（Apache-2.0）CLI 包成这个命令。
  XIAOMAN_TTS_PROVIDER=sherpa 进程内调用 sherpa-onnx（需自行 pip 安装 + 自备模型）：
                               XIAOMAN_TTS_SHERPA_MODEL   model.onnx 路径
                               XIAOMAN_TTS_SHERPA_TOKENS  tokens.txt 路径
                               可选 XIAOMAN_TTS_SHERPA_LEXICON / _DATA_DIR / _DICT_DIR / _SID
                               模型文件必须已存在本地，服务不会联网下载。

接口：
POST /tts  {"text": "你好", "voice": "zh-CN-XiaoyiNeural", "rate": "normal"} → audio/*
GET  /tts?text=你好&voice=… → audio/*（保留兼容旧客户端，新客户端请用 POST）
GET  /health → {"ok": true, "provider": ..., "available": ...}
安全约定：对话原文只走 POST body；日志只记「方法 + path」，查询串/body 均不落盘。
"""
import hashlib
import io
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.parse
import wave
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

SUPPORTED_PROVIDERS = ("none", "cmd", "sherpa")


def _provider_name():
    raw = (os.environ.get("XIAOMAN_TTS_PROVIDER")
           or os.environ.get("TTS_PROVIDER") or "none").strip().lower()
    return raw if raw in SUPPORTED_PROVIDERS else "none"


PROVIDER = _provider_name()

# ---------------- Voice 白名单 ----------------
# 包含默认 zh-CN-XiaoyiNeural 与常用 zh-CN 音色（前端音色白名单与之对齐）
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

# 语速档位 → 数值倍率（与前端 slow/normal/fast 对齐；cmd/sherpa 都吃这个倍率）
TTS_RATES = {
    "slow": 0.86,
    "normal": 1.02,
    "fast": 1.18,
}

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
MAX_TEXT_LEN = 300        # 单条语音文本上限（超出截断，防止超长请求拖垮合成）
MAX_BODY_BYTES = 8 * 1024  # POST JSON body 上限
CMD_TIMEOUT = 30          # 外部 TTS 命令超时（秒）

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


# ---------------- provider 探测 ----------------
class TtsProviderUnavailable(RuntimeError):
    """当前 provider 不可用（未安装 / 未配置 / 模型缺失 / 外部命令失败）"""


def _cmd_argv():
    """外部 TTS 命令 → argv 元组；未配置返回空元组。text 只走 stdin，不进命令。"""
    raw = (os.environ.get("XIAOMAN_TTS_CMD") or "").strip()
    if not raw:
        return ()
    try:
        return tuple(shlex.split(raw, posix=(os.name != "nt")))
    except ValueError:
        return ()


def _sherpa_paths():
    """sherpa-onnx 本地模型文件路径；缺失的键为 None。"""
    return {
        "model": os.environ.get("XIAOMAN_TTS_SHERPA_MODEL"),
        "tokens": os.environ.get("XIAOMAN_TTS_SHERPA_TOKENS"),
        "lexicon": os.environ.get("XIAOMAN_TTS_SHERPA_LEXICON"),
        "data_dir": os.environ.get("XIAOMAN_TTS_SHERPA_DATA_DIR"),
        "dict_dir": os.environ.get("XIAOMAN_TTS_SHERPA_DICT_DIR"),
    }


def _sherpa_files_ok():
    """只做文件存在性检查（不 import 库、不联网、不下载模型）。"""
    paths = _sherpa_paths()
    required = (paths["model"], paths["tokens"])
    if not all(p and os.path.isfile(p) for p in required):
        return False
    if paths["lexicon"] and not os.path.isfile(paths["lexicon"]):
        return False
    return True


def provider_available(name=None):
    """provider 是否可用。默认 none 永远不可用（返回 503 由前端降级）。"""
    name = (name or PROVIDER)
    if name == "none":
        return False
    if name == "cmd":
        return bool(_cmd_argv())
    if name == "sherpa":
        return _sherpa_files_ok()
    return False


def audio_content_type():
    """当前 provider 产出音频的 Content-Type（none 不会被调用）"""
    if PROVIDER == "cmd":
        raw = (os.environ.get("XIAOMAN_TTS_CMD_TYPE") or "audio/wav").strip().lower()
        return raw if raw in ("audio/wav", "audio/mpeg", "audio/ogg") else "audio/wav"
    return "audio/wav"


def _audio_suffix(content_type):
    return {"audio/mpeg": ".mp3", "audio/ogg": ".ogg"}.get(content_type, ".wav")


# ---------------- 各 provider 的合成实现 ----------------
def _cmd_synthesize(text, voice, rate_key):
    argv = _cmd_argv()
    if not argv:
        raise TtsProviderUnavailable("XIAOMAN_TTS_CMD not configured")
    env = os.environ.copy()
    env["XIAOMAN_TTS_VOICE"] = voice
    env["XIAOMAN_TTS_RATE"] = "%.3f" % TTS_RATES.get(rate_key, TTS_RATES["normal"])
    try:
        proc = subprocess.run(argv, input=text.encode("utf-8"),
                              capture_output=True, timeout=CMD_TIMEOUT, env=env)
    except FileNotFoundError:
        raise TtsProviderUnavailable("XIAOMAN_TTS_CMD not found")
    except subprocess.TimeoutExpired:
        raise TtsProviderUnavailable("XIAOMAN_TTS_CMD timeout")
    except OSError as e:
        raise TtsProviderUnavailable(f"XIAOMAN_TTS_CMD failed: {type(e).__name__}")
    if proc.returncode != 0:
        # 只记退出码：stdout/stderr 可能含原文，绝不落日志
        raise TtsProviderUnavailable(f"XIAOMAN_TTS_CMD exit={proc.returncode}")
    audio = proc.stdout or b""
    if not audio:
        raise TtsProviderUnavailable("XIAOMAN_TTS_CMD produced no audio")
    return audio


def _sherpa_synthesize(text, voice, rate_key):
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError:
        raise TtsProviderUnavailable("sherpa-onnx not installed")

    paths = _sherpa_paths()
    if not _sherpa_files_ok():
        raise TtsProviderUnavailable("sherpa-onnx model files missing")

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=paths["model"],
        lexicon=paths["lexicon"] or "",
        tokens=paths["tokens"],
        data_dir=paths["data_dir"] or "",
        dict_dir=paths["dict_dir"] or "",
    )
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=2, provider="cpu")
    )
    tts = sherpa_onnx.OfflineTts(config)
    try:
        sid = int(os.environ.get("XIAOMAN_TTS_SHERPA_SID") or 0)
    except ValueError:
        sid = 0
    audio = tts.generate(text, sid=sid,
                         speed=TTS_RATES.get(rate_key, TTS_RATES["normal"]))
    samples = np.clip(np.asarray(audio.samples), -1.0, 1.0)
    pcm16 = (samples * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(audio.sample_rate))
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def synthesize(text, voice, rate_key="normal"):
    """按当前 provider 合成音频；不可用时抛 TtsProviderUnavailable。"""
    if PROVIDER == "cmd":
        return _cmd_synthesize(text, voice, rate_key)
    if PROVIDER == "sherpa":
        return _sherpa_synthesize(text, voice, rate_key)
    raise TtsProviderUnavailable("provider=none")


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
_AUDIO_SUFFIXES = (".mp3", ".wav", ".ogg")

_cache_locks = {}  # key -> threading.Lock (single-flight)
_cache_locks_lock = threading.Lock()


def _cache_key(text, voice, rate_key):
    h = hashlib.sha1(f"{PROVIDER}|{text}|{voice}|{rate_key}".encode()).hexdigest()
    return h


def _cache_path(key, content_type):
    return CACHE_DIR / f"{key}{_audio_suffix(content_type)}"


def _key_lock(key):
    with _cache_locks_lock:
        return _cache_locks.setdefault(key, threading.Lock())


def _cleanup_cache():
    """LRU 清理：按 mtime 删除最旧，保留 MAX_CACHE_ENTRIES"""
    try:
        files = sorted((p for p in CACHE_DIR.iterdir() if p.suffix in _AUDIO_SUFFIXES),
                       key=lambda p: p.stat().st_mtime)
        for f in files[:-MAX_CACHE_ENTRIES]:
            f.unlink(missing_ok=True)
        # 清理过期
        now = time.time()
        for f in files[-MAX_CACHE_ENTRIES:]:
            if now - f.stat().st_mtime > CACHE_TTL_SECONDS:
                f.unlink(missing_ok=True)
    except Exception:
        logger.debug("Cache cleanup failed", exc_info=True)


def _get_or_generate_audio(text, voice, rate_key="normal"):
    """带缓存 + single-flight 的合成入口（同步；无 asyncio 依赖）"""
    content_type = audio_content_type()
    key = _cache_key(text, voice, rate_key)
    cache_file = _cache_path(key, content_type)

    if cache_file.exists():
        logger.debug("TTS cache hit: %s", key[:8])
        return cache_file.read_bytes()

    with _key_lock(key):
        if cache_file.exists():
            return cache_file.read_bytes()

        logger.info("TTS generating: provider=%s voice=%s text_len=%d",
                    PROVIDER, voice, len(text))
        audio = synthesize(text, voice, rate_key)
        if not audio:
            raise RuntimeError("TTS synthesis produced empty audio")

        # 写缓存（原子写入）
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(cache_file)
        _cleanup_cache()
        return audio


# ---------------- HTTP Handler ----------------
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
        """音频/错误响应：公开资源，直接放行任意 Origin。

        安卓 WebView（file:// → Origin: null，或 https://appassets.androidplatform.net）
        播放时前端会给 <audio> 设 crossOrigin="anonymous" 才能用 WebAudio 取音量包络；
        没有 ACAO 头时浏览器会直接静音（不是播放失败，是"有画面无声"）。503 的
        JSON 错误体同理需要 ACAO，前端才能读到 error/provider 并降级。
        """
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Accept-Ranges", "none")

    def _json(self, obj, code=200, public=False):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if public:
            self._cors_audio()
        else:
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
        if not provider_available():
            # 默认 provider=none：不合成、不联网，交给前端系统 TTS 降级
            logger.warning("tts unavailable: provider=%s", PROVIDER)
            self._json({"error": "tts_provider_unavailable", "provider": PROVIDER},
                       503, public=True)
            return

        try:
            audio = _get_or_generate_audio(text, voice, rate_key)
            self.send_response(200)
            self.send_header("Content-Type", audio_content_type())
            self._cors_audio()
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            logger.debug("tts 200 %dms audio_len=%d",
                         int((time.time() - start) * 1000), len(audio))
        except TtsProviderUnavailable as e:
            logger.error("TTS provider unavailable: provider=%s detail=%s", PROVIDER, e)
            self._json({"error": "tts_provider_unavailable", "provider": PROVIDER},
                       503, public=True)
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
            self._json({"ok": True, "service": "xiaoman-tts",
                        "provider": PROVIDER, "available": provider_available(),
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
    avail = provider_available()
    print(f"小满 TTS 服务就绪 → http://{BIND_HOST}:{PORT}/tts "
          f"（provider: {PROVIDER}{'' if not avail else ' 可用'}；"
          f"未启用时 /tts 回 503 由前端系统 TTS 降级） "
          f"(bind: {BIND_HOST}, cache: {CACHE_DIR})")
    ThreadingHTTPServer((BIND_HOST, PORT), Handler).serve_forever()
