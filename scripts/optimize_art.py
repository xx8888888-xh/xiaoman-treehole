#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""optimize_art.py · 运行时美术资源调色板压缩（256 色，无抖动）

背景（docs/android-battery）：APK 里 assets/www 占 99%，其中 PNG 全部以 stored
方式打包（aapt2 默认不压缩图片），体积 = 原始字节。潮的水彩分层图色数有限，
256 色调色板 PNG 逐张肉眼无可辨差异，却能把 tier 纹理压到原体积的 1/5。
基准层 art/layers/* 与 art/thumb.png 运行时不请求，由 asset_excludes.sh 挡在 APK 外。

用法：
  python scripts/optimize_art.py            # 只报告，不改文件
  python scripts/optimize_art.py --apply    # 就地写回（幂等：已 ≤256 色的跳过）
"""
import argparse
import io
import os
import sys

from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "web", "assets", "live2d", "original")
ART = os.path.join(ROOT, "art")
MIN_BYTES = 20 * 1024       # 小图（眼睛/嘴型 1-9KB）不值得量化，保留原始细节
COLORS = 256

def targets():
    """运行时真正会加载的图：full / silhouette / 两档 tier 分层。"""
    out = [os.path.join(ART, n) for n in ("full.png", "silhouette.png")]
    for tier in ("s050", "s070"):
        d = os.path.join(ART, "tiers", tier, "layers")
        for name in sorted(os.listdir(d)):
            if name.endswith(".png"):
                out.append(os.path.join(d, name))
    return [p for p in out if os.path.exists(p)]


def unique_colors(im):
    """返回唯一色数；超过 COLORS 上限时返回 None（表示"多于 256 色"）。"""
    got = im.getcolors(maxcolors=COLORS + 1)
    return None if got is None else len(got)


def optimize(path, apply):
    if os.path.getsize(path) < MIN_BYTES:
        return None
    im = Image.open(path).convert("RGBA")
    n = unique_colors(im)
    if n is not None and n <= COLORS:
        return None
    q = im.quantize(colors=COLORS, method=Image.FASTOCTREE, dither=Image.NONE)
    buf = io.BytesIO()
    q.save(buf, "PNG", optimize=True, compress_level=9)
    data = buf.getvalue()
    orig = os.path.getsize(path)
    if len(data) >= orig:
        return None
    if apply:
        with open(path, "wb") as f:
            f.write(data)
    return orig, len(data), n


def main(argv=None):
    ap = argparse.ArgumentParser(description="运行时美术 PNG → 256 色调色板")
    ap.add_argument("--apply", action="store_true", help="就地写回（默认只报告）")
    args = ap.parse_args(argv)

    before = after = 0
    rows = []
    for path in targets():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        res = optimize(path, args.apply)
        if res is None:
            continue
        orig, new, n = res
        before += orig
        after += new
        rows.append((rel, orig, new, n))

    for rel, orig, new, n in rows:
        print("%-46s %8d → %7d  (%.0f%%, %s 色)" % (
            rel, orig, new, 100.0 * new / orig, ">256" if n is None else n))
    if not rows:
        print("没有需要压缩的图（已全部 ≤%d 色）" % COLORS)
        return 0
    print("\n合计 %d → %d（%.0f%%），节省 %.2f MiB%s" % (
        before, after, 100.0 * after / before, (before - after) / 1048576,
        "" if args.apply else "  [dry-run，未写回]"))
    if not args.apply:
        print("加 --apply 才会写回文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
