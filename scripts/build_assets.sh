#!/usr/bin/env bash
# ============================================================
# build_assets.sh · 发布物唯一真源
#   web/ → android/app/src/main/assets/www（tar 同步，排除规则见 asset_excludes.sh）
#   同时生成 android/app/src/main/assets/www/ASSET_MANIFEST.json：
#     相对路径 + sha256 + 字节数 + 生成时间 + 源目录 git commit
# build_apk.sh 打包前强制调用本脚本，打包后按清单校验 APK；
# Dockerfile 只 COPY assets/www；compose web 容器启动前按清单校验。
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/web"
ASSETS_DIR="$ROOT/android/app/src/main/assets"
DST="$ASSETS_DIR/www"
[ -d "$SRC" ] || { echo "❌ 源目录不存在: $SRC" >&2; exit 1; }
# shellcheck source=scripts/asset_excludes.sh
. "$ROOT/scripts/asset_excludes.sh"

# assets/www 是生成物、不入库（见 .gitignore），目录可能不存在 → 先建。
# 注意：只清空内容、不删除目录本身，否则 compose 里对 assets/www 的 bind mount
# 会指向被删掉的旧 inode，运行中的 web 容器会突然 404。
mkdir -p "$DST"
find "$DST" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
( cd "$SRC" && tar cf - "${ASSET_TAR_EXCLUDES[@]}" . ) | ( cd "$DST" && tar xf - )

# 源 commit：能拿到就写；web/ 有未提交改动时标记 dirty（发布物不完全等于任一提交）
COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
DIRTY=false
if [ -n "$COMMIT" ] && [ -n "$(git -C "$ROOT" status --porcelain -- web 2>/dev/null || true)" ]; then
  DIRTY=true
fi

command -v python3 >/dev/null 2>&1 || { echo "❌ 需要 python3 生成/校验清单" >&2; exit 1; }
python3 - "$DST" "$COMMIT" "$DIRTY" <<'PY'
import datetime, hashlib, json, os, sys

dst, commit, dirty = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
files, total = [], 0
for root, dirs, names in os.walk(dst):
    dirs.sort()
    for name in sorted(names):
        if name == "ASSET_MANIFEST.json":
            continue
        full = os.path.join(root, name)
        rel = os.path.relpath(full, dst).replace(os.sep, "/")
        digest = hashlib.sha256()
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        size = os.path.getsize(full)
        files.append({"path": rel, "sha256": digest.hexdigest(), "bytes": size})
        total += size
files.sort(key=lambda e: e["path"])

manifest = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "source_dir": "web",
    "source_commit": commit or None,
    "source_dirty": dirty,
    "file_count": len(files),
    "total_bytes": total,
    "files": files,
}
path = os.path.join(dst, "ASSET_MANIFEST.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"ASSETS_MANIFEST files={len(files)} bytes={total} path={path}")
PY

# 生成后自检：发布目录必须与工作树逐文件一致（同一排除口径），否则立刻失败，
# 避免把半同步/过期副本挂给容器或打进 APK。
python3 "$ROOT/scripts/verify_assets.py" --src "$SRC" --dst "$DST"

echo "ASSETS_SYNCED $(du -sh "$DST" | cut -f1) -> $DST"
