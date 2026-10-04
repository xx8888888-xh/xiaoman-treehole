#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_tts.py · 语音链路回环验证
1. 调 tts_server 生成"你好呀，我是小满，今晚也辛苦啦" mp3
2. 检查文件大小合理（>10KB）
3. 用 ASR（z-ai sdk）转写回文本，比对关键内容 → 输出 PASS/FAIL
"""
import urllib.request, sys, json, subprocess, os

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "..", "web", "assets", "audio", "selftest.mp3")
TEXT = "你好呀，我是小满，今晚也辛苦啦。"

url = f"http://127.0.0.1:8903/tts?text={urllib.parse.quote(TEXT)}&voice=zh-CN-XiaoyiNeural"
data = urllib.request.urlopen(url, timeout=60).read()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "wb").write(data)
print(f"[1] mp3 生成: {len(data)} bytes", "PASS" if len(data) > 10240 else "FAIL")

import base64, subprocess as sp
# mp3 → wav（ASR 服务仅支持 WAV/WebM）
WAV = OUT.replace(".mp3", ".wav")
sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", OUT, "-ar", "16000", "-ac", "1", WAV], check=True)
b64 = base64.b64encode(open(WAV, "rb").read()).decode()
script = f"""
const ZAI = (await import('z-ai-web-dev-sdk')).default;
const zai = await ZAI.create();
const b64 = "{b64}";
const res = await zai.audio.asr.create({{ file_base64: b64 }});
console.log(JSON.stringify({{text: res.text}}));
"""
open("/home/z/my-project/.asr_test.mjs", "w").write(script)
r = subprocess.run(["node", "/home/z/my-project/.asr_test.mjs"], capture_output=True, text=True, cwd="/home/z/my-project", timeout=120)
out = r.stdout.strip()
try:
    heard = json.loads(out.splitlines()[-1])["text"]
    print(f"[2] ASR 转写: {heard}")
    key_ok = ("小满" in heard) or ("辛苦" in heard)
    print("[3] 回环比对:", "PASS" if key_ok else f"FAIL（ heard={heard} ）")
except Exception as e:
    print("[2] ASR 失败:", r.stdout[-200:], r.stderr[-300:])
    print("[3] 回环比对: FAIL")
