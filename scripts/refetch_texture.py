#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多镜像重下 texture_01.png，PIL 校验像素完整性（黑色比例>30%判损坏）"""
import urllib.request, io, os

DEST = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web", "assets", "models", "hiyori", "Hiyori.2048", "texture_01.png"))
MIRRORS = [
    "https://fastly.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
    "https://gcore.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
    "https://cdn.statically.io/gh/Live2D/CubismWebSamples/develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
    "https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori/Hiyori.2048/texture_01.png",
]

def check(data):
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    px = list(img.getdata())
    n = len(px)
    black = sum(1 for r, g, b, a in px if r < 12 and g < 12 and b < 12 and a > 200)
    ratio = black / n
    return ratio < 0.3, f"黑像素占比 {ratio:.1%}, 尺寸 {img.size}"

for i, url in enumerate(MIRRORS):
    try:
        print(f"[{i+1}] {url.split('/')[2]} ...", flush=True)
        data = urllib.request.urlopen(url, timeout=90).read()
        print(f"    {len(data)} bytes", flush=True)
        ok, msg = check(data)
        print(f"    {msg} → {'PASS' if ok else 'FAIL'}", flush=True)
        if ok:
            open(DEST, "wb").write(data)
            print("SAVED", flush=True)
            break
    except Exception as e:
        print(f"    ERR {str(e)[:80]}", flush=True)
print("DONE", flush=True)
