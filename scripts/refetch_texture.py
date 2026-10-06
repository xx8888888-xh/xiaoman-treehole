#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多镜像重下 texture_01.png，PIL 校验像素完整性（尺寸+哈希+黑像素占比）"""
import hashlib
import io
import os
import sys
import urllib.request
from PIL import Image

DEST = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web", "assets", "models", "hiyori", "Hiyori.2048", "texture_01.png"))
MIRRORS = [
    "https://fastly.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
    "https://gcore.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
    "https://cdn.statically.io/gh/Live2D/CubismWebSamples/develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
    "https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
]

# 期望基线（官方原版 Hiyori texture_01.png）：尺寸 2048x2048，SHA-256
EXPECTED_SIZE = (2048, 2048)
EXPECTED_SHA256 = "87fe9ab7db81ab3025e0407229e449233581e00fa92c8e911923bc6c7d98ce84"

def check(data):
    """校验：尺寸、SHA-256、黑像素占比 < 30%"""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    # 尺寸校验
    if img.size != EXPECTED_SIZE:
        return False, f"尺寸不符: {img.size} (期望 {EXPECTED_SIZE})"
    # SHA-256 校验
    if EXPECTED_SHA256:
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != EXPECTED_SHA256:
            return False, f"SHA-256 不符: {actual_sha}"
    # 黑像素占比：用 tobytes() 分块采样统计（避免 getdata() 物化百万像素 / 弃用告警）
    # 判据：R<12 且 G<12 且 B<12 且 alpha>200（排除透明背景）
    w, h = img.size
    total = w * h
    raw = img.tobytes()  # RGBA 连续字节
    stride = 4 * 10 if total > 500_000 else 4   # 大图每 10 像素采 1，控制耗时
    black = sampled = 0
    for i in range(0, len(raw), stride):
        if raw[i] < 12 and raw[i + 1] < 12 and raw[i + 2] < 12 and raw[i + 3] > 200:
            black += 1
        sampled += 1
    ratio = black / max(sampled, 1)
    if ratio >= 0.3:
        return False, f"黑像素占比 {ratio:.1%} ≥ 30% (尺寸 {img.size})"
    return True, f"PASS: 尺寸 {img.size}, 黑像素占比 {ratio:.1%}"

saved = False
for i, url in enumerate(MIRRORS):
    try:
        print(f"[{i+1}] {url.split('/')[2]} ...", flush=True)
        data = urllib.request.urlopen(url, timeout=90).read()
        print(f"    {len(data)} bytes", flush=True)
        ok, msg = check(data)
        print(f"    {msg}", flush=True)
        if ok:
            os.makedirs(os.path.dirname(DEST), exist_ok=True)
            with open(DEST, "wb") as f:
                f.write(data)
            print("SAVED", flush=True)
            saved = True
            break
    except Exception as e:
        print(f"    ERR {str(e)[:80]}", flush=True)

if not saved:
    print("❌ 所有镜像均失败，未保存文件", flush=True)
    sys.exit(1)

print("DONE", flush=True)
