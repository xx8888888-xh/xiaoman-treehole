#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_assets.py · 发布物与工作树一致性自检（纯标准库，跨平台）

与 scripts/asset_excludes.sh 保持同一排除口径，比对：
  工作树 web/（唯一真源）  ↔  android/app/src/main/assets/www（发布物）

缺失 / 多余 / sha256 或字节不一致 → 逐条打印并 exit 1。
容器（compose web 服务）启动前也跑本脚本：工作树与挂载的 assets/www 一旦
漂移就拒绝启动，避免「容器 8901 提供的 index.html/app.js 与仓库不一致」。

用法：
  python3 scripts/verify_assets.py [--src web] [--dst android/app/src/main/assets/www]
"""
import argparse
import hashlib
import os
import sys

# 与 asset_excludes.sh 对齐：目录名命中即整体跳过，文件名/后缀单独判断
EXCLUDED_DIRS = {"node_modules", "tests", "docs"}
EXCLUDED_FILES = {"package.json", "package-lock.json", "biome.json", "ASSET_MANIFEST.json"}
EXCLUDED_SUFFIXES = (".orig",)
EXCLUDED_PREFIX_PATHS = (
    "assets/audio/selftest.",
    # Hiyori 全档纹理（4.12MiB）无任何路径引用，不参与发布物一致性比对
    "assets/models/hiyori/Hiyori.2048/",
    # 全尺寸基准分层与缩略图运行时不请求（设备按档取图），不进 APK
    "assets/live2d/original/art/layers/",
    "assets/live2d/original/art/thumb.png",
)


def is_excluded(rel_path):
    """rel_path 使用 / 分隔；判断是否属于发布物排除项。"""
    parts = rel_path.split("/")
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in EXCLUDED_FILES:
        return True
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    for prefix in EXCLUDED_PREFIX_PATHS:
        if rel_path.startswith(prefix):
            return True
    return False


def collect_files(root):
    """返回 {相对路径: (sha256, 字节数)}；排除项不计入。"""
    root = os.path.abspath(root)
    out = {}
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(names):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if is_excluded(rel):
                continue
            out[rel] = (sha256_file(full), os.path.getsize(full))
    return out


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare(src_dir, dst_dir):
    """返回 (missing, extra, mismatch)，均为相对路径/描述列表。"""
    src = collect_files(src_dir)
    dst = collect_files(dst_dir)
    missing = sorted(set(src) - set(dst))
    extra = sorted(set(dst) - set(src))
    mismatch = []
    for rel in sorted(set(src) & set(dst)):
        if src[rel] != dst[rel]:
            mismatch.append(
                f"{rel} 字节 期望 {src[rel][1]} 实际 {dst[rel][1]}；"
                f"sha256 期望 {src[rel][0]} 实际 {dst[rel][0]}")
    return missing, extra, mismatch


def report(label, entries):
    print(f"❌ {label}（{len(entries)} 个）：")
    for line in entries[:50]:
        print(f"   - {line}")
    if len(entries) > 50:
        print(f"   ... 其余 {len(entries) - 50} 个省略")


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验发布物 assets/www 与工作树 web/ 一致")
    ap.add_argument("--src", default="web", help="唯一真源目录（默认 web）")
    ap.add_argument("--dst", default=os.path.join("android", "app", "src", "main", "assets", "www"),
                    help="发布物目录（默认 android/app/src/main/assets/www）")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.src):
        print(f"❌ 源目录不存在：{args.src}", file=sys.stderr)
        return 2
    if not os.path.isdir(args.dst):
        print(f"❌ 发布目录不存在：{args.dst}（先运行 bash scripts/build_assets.sh）",
              file=sys.stderr)
        return 2

    missing, extra, mismatch = compare(args.src, args.dst)
    if missing:
        report("发布物缺失（工作树有、发布目录没有）", missing)
    if extra:
        report("发布物多余（发布目录有、工作树没有）", extra)
    if mismatch:
        report("发布物内容不一致（sha256/字节）", mismatch)

    if missing or extra or mismatch:
        print(f"ASSET_VERIFY FAIL src={args.src} dst={args.dst} "
              f"missing={len(missing)} extra={len(extra)} mismatch={len(mismatch)}",
              file=sys.stderr)
        print("请先运行 `python scripts/sync_assets.py`（等价 `bash scripts/build_assets.sh`）"
              "重新生成发布物；容器会拒绝启动以避免对外提供过期页面。", file=sys.stderr)
        return 1

    files = collect_files(args.dst)
    total = sum(size for _digest, size in files.values())
    print(f"ASSET_VERIFY PASS src={args.src} dst={args.dst} files={len(files)} bytes={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
