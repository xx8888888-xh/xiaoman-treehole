#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_tts.py · 语音链路回环验证
1. 调 tts_server 生成"你好呀，我是小满，今晚也辛苦啦" mp3
2. 检查文件大小合理（>10KB）
3. 用 ASR（z-ai sdk）转写回文本，比对关键内容 → 输出 PASS/FAIL

退出码契约：
  0 = 全部通过，或环境缺依赖（z-ai-web-dev-sdk 未安装 → [SKIP]，不算失败）
  1 = 真实失败（mp3 生成失败 / 体积<10KB / ffmpeg 失败 / ASR 转写或比对失败）
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

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

    # —— 3) ASR 转写 + 关键词比对（依赖缺失 → 已在开头打印 [SKIP]，正常结束）——
    if not has_sdk:
        print(f"[SKIP] 原因: {sdk_note or 'z-ai-web-dev-sdk 未安装'}")
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
