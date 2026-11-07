#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_tts.py · 语音链路回环验证
1. 调 tts_server 生成"你好呀，我是小满，今晚也辛苦啦" mp3
2. 解码 PCM 断言：非静音 / 时长合理 / 未中途截断（无 TTS 环境也必须判定，不许 skip 通过）
3. 用 ASR（z-ai sdk）转写回文本，比对关键内容 → 输出 PASS/FAIL

退出码契约：
  0 = 全部通过（含 provider=none 时的离线假 TTS 断言），或 ASR 依赖缺失（[SKIP] 仅限 ASR 步骤）
  1 = 真实失败（mp3 生成失败 / PCM 静音 / 时长不合理 / 截断 / ffmpeg 失败 / ASR 比对失败）
"""
import base64
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave

BASE = os.path.dirname(os.path.abspath(__file__))
# 自测音频产物写入仓库内 gitignore 目录 .cache/audio/（不进 web/assets/，也不随 APK 分发）
OUT = os.path.normpath(os.path.join(BASE, "..", ".cache", "audio", "selftest.mp3"))
TEXT = "你好呀，我是小满，今晚也辛苦啦。"
MIN_BYTES = 10240  # 10KB


def _load_env():
    """端口/主机集中配置唯一真源：scripts/env.sh（经 bash source 读取）"""
    env_sh = os.path.join(BASE, "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c", f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_TTS_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return out[0], int(out[1])
    except Exception:
        return "127.0.0.1", 8903


_HOST, _TTS_PORT = _load_env()


def fail(step, reason):
    """结构化失败输出 + 非零退出（替代裸 traceback）"""
    print(f"[FAIL] {step}: {reason}")
    sys.exit(1)


# ---------- PCM 断言（无 TTS 环境也能判定「有没有响」） ----------

RATE_NORMAL = 1.02
CHAR_MS = 190
TAIL_MS = 240
PAUSE_MS = {
    "，": 180, ",": 180, "、": 130, "；": 240, ";": 240, "：": 200, ":": 200,
    "。": 360, "！": 320, "!": 320, "？": 360, "?": 360, "…": 420,
    "~": 200, "～": 200,
}
SILENT_CHARS = set("「」『』《》\"'“”‘’·—-")


def expected_ms(text, rate=RATE_NORMAL):
    """与 web/js/tts.js speechPlan 同口径：190ms/字 + 标点停顿 + 240ms 尾"""
    r = rate if rate and rate > 0 else RATE_NORMAL
    t = 0.0
    for ch in text:
        if ch in PAUSE_MS:
            t += PAUSE_MS[ch] / r
            continue
        if ch.isspace() or ch in SILENT_CHARS:
            continue
        t += CHAR_MS / r
    return round(t) + TAIL_MS


def analyze_wav_bytes(data):
    """WAV → 采样统计（16-bit PCM；单/双声道取第一声道）"""
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"unsupported sample width {w.getsampwidth()}")
        sr = w.getframerate()
        nch = w.getnchannels()
        n = w.getnframes()
        raw = w.readframes(n)
    vals = struct.unpack("<%dh" % (len(raw) // 2), raw)
    samples = [v / 32768.0 for v in vals[::nch]]
    n = len(samples)
    peak = max((abs(v) for v in samples), default=0.0)
    rms = math.sqrt(sum(v * v for v in samples) / n) if n else 0.0
    thr = max(0.005, peak * 0.05)
    first = last = -1
    silent = 0
    for i, v in enumerate(samples):
        if abs(v) > thr:
            if first < 0:
                first = i
            last = i
        else:
            silent += 1
    dur = (n / sr) * 1000 if sr else 0
    voiced = ((last - first + 1) / sr) * 1000 if first >= 0 else 0
    trailing = dur if last < 0 else ((n - last - 1) / sr) * 1000
    return {
        "durationMs": dur, "rms": rms, "peak": peak,
        "silentRatio": (silent / n) if n else 1.0,
        "voicedMs": voiced, "trailingSilenceMs": trailing,
    }


def judge_pcm(a, text, rate=RATE_NORMAL):
    exp = expected_ms(text, rate)
    nonzero = a["rms"] >= 0.02 and a["peak"] >= 0.05
    duration_ok = exp * 0.45 <= a["durationMs"] <= exp * 2.6
    truncated = a["durationMs"] < exp * 0.45 or (
        a["voicedMs"] < a["durationMs"] * 0.55 and a["trailingSilenceMs"] > a["durationMs"] * 0.30
    )
    reasons = []
    if not nonzero:
        reasons.append("silent")
    if not duration_ok:
        reasons.append("duration")
    if truncated:
        reasons.append("truncated")
    return {
        "ok": nonzero and duration_ok and not truncated,
        "nonzero": nonzero, "durationOk": duration_ok, "truncated": truncated,
        "durationMs": round(a["durationMs"]), "expectedMs": exp,
        "rms": round(a["rms"], 4), "voicedMs": round(a["voicedMs"]),
        "trailingSilenceMs": round(a["trailingSilenceMs"]), "reasons": reasons,
    }


def assert_pcm(data, text, label):
    try:
        a = analyze_wav_bytes(data)
    except Exception as e:
        fail(label, f"PCM 解析失败: {type(e).__name__}: {e}")
    j = judge_pcm(a, text)
    print(f"[PCM] {label}: {json.dumps(j, ensure_ascii=False)}")
    if not j["ok"]:
        fail(label, "音频判定未通过 " + ",".join(j["reasons"]))
    return j


def fake_tts_wav(text, rate=RATE_NORMAL, mode="good"):
    """离线假 TTS：按文本时间线合成 16-bit PCM（无需任何云端/模型）。

    mode=good 正常；silent 全静音；short 只给 40% 时长 —— 后两者用于证明判据真能抓问题。
    """
    sr = 16000
    dur = expected_ms(text, rate)
    if mode == "short":
        dur = round(dur * 0.4)
    n = round(sr * dur / 1000)
    voiced = 0 if mode == "silent" else n
    buf = bytearray()
    for i in range(n):
        if i < voiced:
            mod = 0.6 + 0.4 * math.sin(2 * math.pi * 10 * i / sr)
            v = 0.5 * mod * math.sin(2 * math.pi * 380 * i / sr)
        else:
            v = 0.0
        buf += struct.pack("<h", max(-32768, min(32767, round(v * 32767))))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(buf))
    return out.getvalue()


def offline_pcm_verify():
    """provider=none 时的兜底：用离线假 TTS 证明断言链路可用，且判据不是摆设。

    不把「环境没有 TTS」当通过：这里必须跑出 PASS，并同时验证
    silent/short 两个负例会被判红，否则整轮 FAIL。
    """
    good = fake_tts_wav(TEXT)
    j = assert_pcm(good, TEXT, "offline-fake-tts")
    neg_silent = judge_pcm(analyze_wav_bytes(fake_tts_wav(TEXT, mode="silent")), TEXT)
    neg_short = judge_pcm(analyze_wav_bytes(fake_tts_wav(TEXT, mode="short")), TEXT)
    print(f"[PCM] 负例 silent: {json.dumps(neg_silent, ensure_ascii=False)}")
    print(f"[PCM] 负例 short : {json.dumps(neg_short, ensure_ascii=False)}")
    if neg_silent["ok"] or "silent" not in neg_silent["reasons"]:
        fail("离线判据自检", "全静音负例没有被判红")
    if neg_short["ok"] or neg_short["truncated"] is not True:
        fail("离线判据自检", "截短负例没有被判红")
    return j


def sdk_available():
    """探测 z-ai-web-dev-sdk 是否可用。

    先用 require.resolve（CJS），失败再用动态 import 试跑（ESM-only 包）。
    返回 (可用: bool, 说明: str)。node 本身缺失/超时视为依赖不可用，不算被测功能失败。
    """
    def _run(args):
        return subprocess.run(args, capture_output=True, text=True, timeout=30)

    try:
        r = _run(["node", "-e", "require.resolve('z-ai-web-dev-sdk')"])
        if r.returncode == 0:
            return True, ""
        r2 = _run(["node", "--input-type=module", "-e",
                   "import('z-ai-web-dev-sdk').then(()=>process.exit(0))"
                   ".catch(()=>process.exit(1))"])
        if r2.returncode == 0:
            return True, ""
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        err = next((l for l in lines if l.startswith("Error:")), None)
        return False, err or (lines[-1] if lines else "module not found")
    except FileNotFoundError as e:
        return False, f"node 不可用: {e}"
    except subprocess.TimeoutExpired:
        return False, "node 探测超时(30s)"
    except OSError as e:
        return False, f"node 探测异常: {type(e).__name__}: {e}"


def main():
    # —— 0) 开头探测 ASR 依赖；缺失则跳过 ASR 步骤（环境问题，不是功能失败）——
    has_sdk, sdk_note = sdk_available()
    if not has_sdk:
        print("[SKIP] ASR 步骤：依赖 z-ai-web-dev-sdk 未安装")

    # —— 1) 生成 mp3 ——
    url = (f"http://{_HOST}:{_TTS_PORT}/tts?text=" + urllib.parse.quote(TEXT)
           + "&voice=zh-CN-XiaoyiNeural")
    try:
        data = urllib.request.urlopen(url, timeout=60).read()
    except urllib.error.HTTPError as e:
        # 默认 provider=none：服务端不合成，前端走系统 TTS → 视为环境跳过，不算失败
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        try:
            info = json.loads(body.decode())
        except Exception:
            info = {}
        if e.code == 503 and info.get("error") == "tts_provider_unavailable":
            # 不许把「环境没有 TTS」当通过：服务端合成不可用时，改用离线假 TTS
            # 产出 PCM，并对同一条文本时间线断言「有声音 / 时长合理 / 未截断」。
            print(f"[DEGRADED] TTS provider={info.get('provider')}：服务端未启用合成，"
                  f"改由离线假 TTS 做 PCM 断言（不是 SKIP）")
            offline_pcm_verify()
            print("[2] 离线假 TTS：非静音 / 时长合理 / 未截断 PASS（负例 silent/short 均判红）")
            return 0
        fail("mp3 生成", f"HTTP {e.code}: {(e.reason or '')}")
    except urllib.error.URLError as e:
        fail("mp3 生成", f"URLError: {e.reason}")
    except Exception as e:  # 超时、连接重置等
        fail("mp3 生成", f"{type(e).__name__}: {e}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    try:
        with open(OUT, "wb") as f:
            f.write(data)
    except OSError as e:
        fail("mp3 写盘", f"{type(e).__name__}: {e}")

    size = len(data)
    print(f"[1] mp3 生成: {size} bytes", "PASS" if size > MIN_BYTES else "FAIL")
    if size <= MIN_BYTES:
        fail("mp3 体积", f"{size} bytes ≤ {MIN_BYTES} bytes(10KB)")

    # —— 2) mp3 → wav（ASR 服务仅支持 WAV/WebM）——
    WAV = OUT.replace(".mp3", ".wav")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", OUT,
             "-ar", "16000", "-ac", "1", WAV],
            capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        fail("ffmpeg 转换", f"ffmpeg 未安装: {e}")
    except subprocess.TimeoutExpired:
        fail("ffmpeg 转换", "超时(120s)")
    except OSError as e:
        fail("ffmpeg 转换", f"{type(e).__name__}: {e}")
    if r.returncode != 0:
        fail("ffmpeg 转换", f"exit={r.returncode} {(r.stderr or '').strip()[-300:]}")

    # —— 2b) 真实音频的 PCM 断言：体积够 ≠ 有声音，必须解出采样再判 ——
    try:
        with open(WAV, "rb") as f:
            real_wav = f.read()
    except OSError as e:
        fail("wav 读取", f"{type(e).__name__}: {e}")
    assert_pcm(real_wav, TEXT, "server-tts")

    # —— 3) ASR 转写 + 关键词比对（依赖缺失 → 已在开头打印 [SKIP]，正常结束）——
    if not has_sdk:
        print(f"[SKIP] 原因: {sdk_note or 'z-ai-web-dev-sdk 未安装'}（仅 ASR 步骤跳过；PCM 断言已通过）")
        return 0

    try:
        with open(WAV, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except OSError as e:
        fail("wav 读取", f"{type(e).__name__}: {e}")

    script = f"""
const ZAI = (await import('z-ai-web-dev-sdk')).default;
const zai = await ZAI.create();
const b64 = "{b64}";
const res = await zai.audio.asr.create({{ file_base64: b64 }});
console.log(JSON.stringify({{text: res.text}}));
"""
    # 临时 mjs 必须放在项目内（向上解析能命中 node_modules）；
    # 放 /tmp 会因 ESM bare specifier 解析失败而 ERR_MODULE_NOT_FOUND
    tmpdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_asr")
    os.makedirs(tmpdir, exist_ok=True)
    try:
        mjs = os.path.join(tmpdir, ".asr_test.mjs")
        with open(mjs, "w", encoding="utf-8") as f:
            f.write(script)
        try:
            r = subprocess.run(["node", mjs], capture_output=True, text=True,
                               cwd=tmpdir, timeout=120)
        except FileNotFoundError as e:
            fail("ASR 调用", f"node 不可用: {e}")
        except subprocess.TimeoutExpired:
            fail("ASR 调用", "node 超时(120s)")
        except OSError as e:
            fail("ASR 调用", f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        print("[3] 回环比对: FAIL")
        fail("ASR 转写", f"node exit={r.returncode} "
                         f"stdout={out[-200:]!r} stderr={(r.stderr or '')[-300:]!r}")
    try:
        heard = json.loads(out.splitlines()[-1])["text"]
    except Exception as e:
        print("[3] 回环比对: FAIL")
        fail("ASR 转写", f"结果解析失败 {type(e).__name__}: {e}; "
                         f"stdout={out[-200:]!r} stderr={(r.stderr or '')[-300:]!r}")

    print(f"[2] ASR 转写: {heard}")
    key_ok = ("小满" in heard) or ("辛苦" in heard)
    print("[3] 回环比对:", "PASS" if key_ok else f"FAIL（ heard={heard} ）")
    if not key_ok:
        fail("回环比对", f"关键词(小满/辛苦)未命中 heard={heard!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
