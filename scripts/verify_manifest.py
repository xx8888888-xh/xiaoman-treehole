#!/usr/bin/env python3
"""发布物一致性校验：目录 / APK 内 assets/www 与 ASSET_MANIFEST.json 比对。

用法：
  python3 scripts/verify_manifest.py --dir <目录> [--manifest <清单路径>]
  python3 scripts/verify_manifest.py --apk <apk 文件>

清单（由 scripts/build_assets.sh 生成）格式：
  {"generated_at","source_dir","source_commit","source_dirty",
   "file_count","total_bytes","files":[{"path","sha256","bytes"}]}
缺失 / 多余 / 哈希不一致 / 字节数不一致 → 逐条打印并 exit 1。
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile

MANIFEST_NAME = "ASSET_MANIFEST.json"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(text, origin):
    try:
        data = json.loads(text)
        files = data["files"]
        return {e["path"]: e for e in files}
    except (ValueError, KeyError, TypeError) as exc:
        print(f"❌ 清单无法解析（{origin}）：{exc}", file=sys.stderr)
        return None


def report(kind, entries):
    print(f"❌ {kind}（{len(entries)} 个）：")
    for line in entries[:50]:
        print(f"   - {line}")
    if len(entries) > 50:
        print(f"   ... 其余 {len(entries) - 50} 个省略")


def summarize(expected, errors, total_bytes, scope):
    if errors:
        print(f"ASSET_CHECK FAIL scope={scope} expected_files={len(expected)}", file=sys.stderr)
        return 1
    print(f"ASSET_CHECK PASS scope={scope} files={len(expected)} bytes={total_bytes}")
    return 0


def verify_dir(dirpath, manifest_path):
    if not os.path.isfile(manifest_path):
        print(f"❌ 清单不存在：{manifest_path}", file=sys.stderr)
        return 2
    with open(manifest_path, "r", encoding="utf-8") as f:
        expected = load_manifest(f.read(), manifest_path)
    if expected is None:
        return 2

    missing, mismatch, extras = [], [], []
    total = 0
    for rel, entry in sorted(expected.items()):
        full = os.path.join(dirpath, *rel.split("/"))
        if not os.path.isfile(full):
            missing.append(rel)
            continue
        size = os.path.getsize(full)
        digest = sha256_file(full)
        total += size
        if size != entry.get("bytes"):
            mismatch.append(f"{rel} 字节 期望 {entry.get('bytes')} 实际 {size}")
        if digest != entry.get("sha256"):
            mismatch.append(f"{rel} sha256 期望 {entry.get('sha256')} 实际 {digest}")

    for root, _dirs, names in os.walk(dirpath):
        for name in names:
            if name == MANIFEST_NAME:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, dirpath).replace(os.sep, "/")
            if rel not in expected:
                extras.append(rel)

    errors = 0
    if missing:
        report("缺失文件", sorted(missing))
        errors += len(missing)
    if extras:
        report("多余文件", sorted(extras))
        errors += len(extras)
    if mismatch:
        report("内容不一致", sorted(mismatch))
        errors += len(mismatch)
    return summarize(expected, errors, total, dirpath)


def verify_apk(apk_path):
    prefix = "assets/www/"
    if not os.path.isfile(apk_path):
        print(f"❌ APK 不存在：{apk_path}", file=sys.stderr)
        return 2
    try:
        zf = zipfile.ZipFile(apk_path)
    except zipfile.BadZipFile as exc:
        print(f"❌ 无法读取 APK（{apk_path}）：{exc}", file=sys.stderr)
        return 2
    with zf:
        names = set(zf.namelist())
        manifest_name = prefix + MANIFEST_NAME
        if manifest_name not in names:
            print(f"❌ APK 内缺少 {manifest_name}（assets 未同步？）", file=sys.stderr)
            return 2
        expected = load_manifest(zf.read(manifest_name).decode("utf-8"), f"{apk_path}:{manifest_name}")
        if expected is None:
            return 2

        missing, mismatch, extras = [], [], []
        total = 0
        for rel, entry in sorted(expected.items()):
            name = prefix + rel
            if name not in names:
                missing.append(rel)
                continue
            data = zf.read(name)
            total += len(data)
            if len(data) != entry.get("bytes"):
                mismatch.append(f"{rel} 字节 期望 {entry.get('bytes')} 实际 {len(data)}")
            digest = sha256_bytes(data)
            if digest != entry.get("sha256"):
                mismatch.append(f"{rel} sha256 期望 {entry.get('sha256')} 实际 {digest}")

        for name in names:
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if rel == MANIFEST_NAME or rel.endswith("/"):
                continue
            if rel not in expected:
                extras.append(rel)

    errors = 0
    if missing:
        report("缺失文件", sorted(missing))
        errors += len(missing)
    if extras:
        report("多余文件", sorted(extras))
        errors += len(extras)
    if mismatch:
        report("内容不一致", sorted(mismatch))
        errors += len(mismatch)
    return summarize(expected, errors, total, apk_path)


def main():
    ap = argparse.ArgumentParser(description="校验发布物与 ASSET_MANIFEST.json 一致")
    ap.add_argument("--apk", help="要校验的 APK（比对其中 assets/www）")
    ap.add_argument("--dir", help="要校验的已发布目录（通常是 android/.../assets/www）")
    ap.add_argument("--manifest", help="清单路径，默认 <dir>/ASSET_MANIFEST.json")
    args = ap.parse_args()

    if args.apk:
        return verify_apk(args.apk)
    if args.dir:
        manifest = args.manifest or os.path.join(args.dir, MANIFEST_NAME)
        return verify_dir(args.dir, manifest)
    ap.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
