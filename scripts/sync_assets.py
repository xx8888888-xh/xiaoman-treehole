#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_assets.py · web/ → android/app/src/main/assets/www（跨平台版 build_assets.sh）

用途：Gradle / Android Studio 直接 Build 时的 preBuild 钩子。`assets/www` 是
.gitignore 的生成物，不先同步就会把旧副本打进 APK（「APK 行为落后于 web/」的根因）。
本脚本不依赖 bash / tar / find，Windows 与 Linux 行为一致；失败一律非零退出。

做的事与 scripts/build_assets.sh 等价：
  1. 按同一排除口径清空并重建 assets/www（保留目录本身，避免 compose bind mount 失效）
  2. 生成 ASSET_MANIFEST.json（source_commit 必须可解析，拿不到 git HEAD 直接失败）
  3. 用 verify_assets.py 的同一套比对函数自检（缺失/多余/哈希不一致即失败）

用法：
  python scripts/sync_assets.py [--src web] [--dst android/app/src/main/assets/www]
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)
import verify_assets  # noqa: E402  复用同一排除口径与比对实现，避免两份规则漂移

MANIFEST_NAME = "ASSET_MANIFEST.json"


def git_output(root, *args):
    """返回 (是否成功, stdout.strip())。git 不在 PATH / 非仓库 → (False, '')。"""
    try:
        proc = subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", check=False,
        )
    except OSError:
        return False, ""
    return proc.returncode == 0, proc.stdout.strip()


def clear_dir(path):
    """只清空内容、保留目录本身（compose 对 assets/www 是 bind mount，删目录会丢 inode）。"""
    os.makedirs(path, exist_ok=True)
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full)
        else:
            os.unlink(full)


def copy_tree(src, dst):
    """按 verify_assets.is_excluded 的口径复制（与 asset_excludes.sh 一致）。"""
    copied = 0
    for base, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in verify_assets.EXCLUDED_DIRS)
        for name in sorted(names):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, src).replace(os.sep, "/")
            if verify_assets.is_excluded(rel):
                continue
            target = os.path.join(dst, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target)
            copied += 1
    return copied


def write_manifest(dst, commit, dirty):
    files, total = [], 0
    for base, dirs, names in os.walk(dst):
        dirs.sort()
        for name in sorted(names):
            if name == MANIFEST_NAME:
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, dst).replace(os.sep, "/")
            files.append({
                "path": rel,
                "sha256": verify_assets.sha256_file(full),
                "bytes": os.path.getsize(full),
            })
            total += files[-1]["bytes"]
    files.sort(key=lambda e: e["path"])

    manifest = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_dir": "web",
        "source_commit": commit,
        "source_dirty": dirty,
        "file_count": len(files),
        "total_bytes": total,
        "files": files,
    }
    path = os.path.join(dst, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return len(files), total, path


def main(argv=None):
    ap = argparse.ArgumentParser(description="同步 web/ 到发布目录并生成/自检 ASSET_MANIFEST.json")
    ap.add_argument("--src", default="web", help="唯一真源目录（默认 web）")
    ap.add_argument("--dst", default=os.path.join("android", "app", "src", "main", "assets", "www"),
                    help="发布物目录（默认 android/app/src/main/assets/www）")
    args = ap.parse_args(argv)

    src = os.path.abspath(args.src)
    dst = os.path.abspath(args.dst)
    if not os.path.isdir(src):
        print(f"ASSET_SYNC FAIL 源目录不存在：{src}", file=sys.stderr)
        return 2
    if os.path.abspath(os.path.dirname(dst)) == src:
        print("ASSET_SYNC FAIL --dst 不能位于 --src 内部", file=sys.stderr)
        return 2

    # 可溯源：git HEAD 拿不到就拒绝生成 source_commit=null 的发布物（同 build_assets.sh）
    ok, commit = git_output(REPO_ROOT, "rev-parse", "HEAD")
    if not ok or not commit:
        print(f"ASSET_SYNC FAIL 无法解析 git HEAD：{REPO_ROOT}", file=sys.stderr)
        print("  发布物必须带可溯源的 commit，拒绝生成 source_commit=null 的 ASSET_MANIFEST.json。", file=sys.stderr)
        print("  常见原因：git 不在 PATH，或工作树是从其他机器拷贝的。", file=sys.stderr)
        print("  也可改用 `bash scripts/build_assets.sh`（Git Bash）生成发布物。", file=sys.stderr)
        return 1
    status_ok, status = git_output(REPO_ROOT, "status", "--porcelain", "--", "web")
    dirty = status_ok and bool(status)

    clear_dir(dst)
    copied = copy_tree(src, dst)
    count, total, manifest_path = write_manifest(dst, commit, dirty)

    # 同步后自检：发布目录必须与工作树逐文件一致（同一排除口径）
    missing, extra, mismatch = verify_assets.compare(src, dst)
    if missing or extra or mismatch:
        print(f"ASSET_SYNC FAIL 同步后自检不通过：missing={len(missing)} "
              f"extra={len(extra)} mismatch={len(mismatch)}", file=sys.stderr)
        for label, entries in (("缺失", missing), ("多余", extra), ("内容不一致", mismatch)):
            if entries:
                print(f"  {label}（{len(entries)}）：", file=sys.stderr)
                for line in entries[:20]:
                    print(f"   - {line}", file=sys.stderr)
        return 1

    print(f"ASSET_SYNC PASS files={count} bytes={total} copied={copied} "
          f"commit={commit[:8]}{'-dirty' if dirty else ''} dst={dst} manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
