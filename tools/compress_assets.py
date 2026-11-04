#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compress_assets.py · 包体盘点 + 无损图片压缩

口径与 scripts/verify_assets.py 完全一致（发布物 = web/ 减去 tests/docs/package.json/.orig）。
默认只做**无损** PNG 重编码（Pillow optimize），且严格跳过 live2d 模型资源
（web/assets/models/**，另一会话占用，禁止改动）。--dry-run 只统计不写盘。

用法：
  python tools/compress_assets.py --dry-run
  python tools/compress_assets.py                 # 压非模型 PNG
  python tools/compress_assets.py --include-docs  # 连 docs 截图一起压（仅仓库，不进包体）
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEB = os.path.join(ROOT, "web")

EXCLUDED_DIRS = {"node_modules", "tests", "docs"}
EXCLUDED_FILES = {"package.json", "package-lock.json", "biome.json", "ASSET_MANIFEST.json"}
EXCLUDED_SUFFIXES = (".orig",)
MODEL_DIR = os.path.join(WEB, "assets", "models")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def is_excluded(rel):
    parts = rel.split("/")
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in EXCLUDED_FILES or name.endswith(EXCLUDED_SUFFIXES):
        return True
    return rel.startswith("assets/audio/selftest.")


def package_files(include_docs):
    """返回 [(绝对路径, 相对 web 路径, 字节)]；默认去掉 docs。"""
    out = []
    for base, dirs, names in os.walk(WEB):
        dirs[:] = sorted(d for d in dirs if not (not include_docs and d == "docs"))
        for name in sorted(names):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, WEB).replace(os.sep, "/")
            if is_excluded(rel) and not (include_docs and rel.startswith("docs/")):
                continue
            out.append((full, rel, os.path.getsize(full)))
    return out


def summarize(files):
    by_top = {}
    for _full, rel, size in files:
        head = rel.split("/")[0]
        sub = rel.split("/")[1] if "/" in rel else ""
        key = f"{head}/{sub}" if head == "assets" and sub else head
        by_top[key] = by_top.get(key, 0) + size
    return dict(sorted(by_top.items(), key=lambda kv: -kv[1]))


def optimize_pngs(files, dry_run):
    try:
        from PIL import Image
    except ImportError:
        print("PIL 未安装，跳过了无损重编码（包体统计不受影响）")
        return []
    rows = []
    for full, rel, size in files:
        if rel.startswith("assets/models/"):
            continue  # live2d 资源冻结
        if not rel.lower().endswith(".png") or size < 8 * 1024:
            continue
        before = size
        tmp = full + ".tmp.png"
        try:
            with Image.open(full) as im:
                im.load()
                im.save(tmp, "PNG", optimize=True, compress_level=9)
            after = os.path.getsize(tmp)
            if after < before and not dry_run:
                os.replace(tmp, full)
            else:
                os.remove(tmp)
            rows.append({"path": rel, "before": before,
                         "after": min(after, before), "saved": max(0, before - after)})
        except Exception as exc:
            if os.path.exists(tmp):
                os.remove(tmp)
            rows.append({"path": rel, "error": str(exc)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-docs", action="store_true",
                    help="连 web/docs 截图一起盘点/压缩（不计入 APK 包体）")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    files = package_files(args.include_docs)
    total = sum(s for _f, _r, s in files)
    top = summarize(files)
    models = sum(s for _f, r, s in files if r.startswith("assets/models/"))
    print(f"发布物口径：{len(files)} 文件 / {total / 1048576:.2f} MB")
    for k, v in top.items():
        print(f"  {k:32s} {v / 1024:9.1f} KB  ({v / total * 100:5.1f}%)")
    print(f"live2d 模型（冻结，不可动）：{models / 1048576:.2f} MB")

    rows = optimize_pngs(files, args.dry_run)
    saved = sum(r.get("saved", 0) for r in rows)
    print(f"无损可压 PNG：{len(rows)} 个，可省 {saved / 1024:.1f} KB"
          f"{'（dry-run，未写盘）' if args.dry_run else ''}")
    for r in rows:
        if "error" in r:
            print(f"  ! {r['path']}: {r['error']}")
        else:
            print(f"  {r['path']}: {r['before'] / 1024:.1f} → {r['after'] / 1024:.1f} KB")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"total": total, "by_top": top, "models": models,
                       "png": rows, "saved": saved, "dryRun": args.dry_run}, f,
                      ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
