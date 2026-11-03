#!/usr/bin/env bash
# ============================================================
# verify_assets.sh · 发布物漂移检测（CI / compose 前置可复用）
#   对比工作树 web/ 与 android/app/src/main/assets/www：
#   缺失（该进包但发布目录没有）/ 多余（发布目录多出文件）/ 哈希或字节不一致，
#   并校验 ASSET_MANIFEST.json 自身与发布目录一致。任一差异 → 非零退出。
# 用法：bash scripts/verify_assets.sh [源目录] [发布目录]
# ============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:-$ROOT/web}"
DST="${2:-$ROOT/android/app/src/main/assets/www}"
MANIFEST="$DST/ASSET_MANIFEST.json"

[ -d "$SRC" ] || { echo "❌ 源目录不存在: $SRC" >&2; exit 2; }
[ -d "$DST" ] || { echo "❌ 发布目录不存在: $DST（先跑 bash scripts/build_assets.sh）" >&2; exit 2; }
[ -f "$MANIFEST" ] || { echo "❌ 清单不存在: $MANIFEST（先跑 bash scripts/build_assets.sh）" >&2; exit 2; }
command -v sha256sum >/dev/null 2>&1 || { echo "❌ 需要 sha256sum" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "❌ 需要 python3（校验清单）" >&2; exit 2; }

# shellcheck source=scripts/asset_excludes.sh
. "$ROOT/scripts/asset_excludes.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# 1) 源侧应发布文件清单：与 build_assets.sh 完全相同的 tar 排除口径
( cd "$SRC" && tar cf - "${ASSET_TAR_EXCLUDES[@]}" . ) | tar tf - \
  | grep -v '/$' | sed 's|^\./||' | LC_ALL=C sort > "$TMP/src.list"
# 2) 发布侧实际文件清单（清单自身不计入发布内容）
( cd "$DST" && find . -type f | sed 's|^\./||' ) \
  | grep -v '^ASSET_MANIFEST\.json$' | LC_ALL=C sort > "$TMP/dst.list"

comm -23 "$TMP/src.list" "$TMP/dst.list" > "$TMP/missing.list"
comm -13 "$TMP/src.list" "$TMP/dst.list" > "$TMP/extra.list"
comm -12 "$TMP/src.list" "$TMP/dst.list" > "$TMP/common.list"

: > "$TMP/hash.list"
while IFS= read -r rel; do
  a="$(sha256sum "$SRC/$rel" | cut -d' ' -f1)"
  b="$(sha256sum "$DST/$rel" | cut -d' ' -f1)"
  [ "$a" = "$b" ] || echo "$rel" >> "$TMP/hash.list"
done < "$TMP/common.list"

N_SRC=$(wc -l < "$TMP/src.list")
N_DST=$(wc -l < "$TMP/dst.list")
N_MISSING=$(wc -l < "$TMP/missing.list")
N_EXTRA=$(wc -l < "$TMP/extra.list")
N_HASH=$(wc -l < "$TMP/hash.list")
SRC_BYTES=$( ( cd "$SRC" && tr '\n' '\0' < "$TMP/src.list" | xargs -0 -r du -cb 2>/dev/null | tail -1 | cut -f1 ) )
SRC_BYTES=${SRC_BYTES:-0}

echo "ASSET_VERIFY src=$SRC"
echo "ASSET_VERIFY dst=$DST"
echo "文件数 src=$N_SRC dst=$N_DST 源总字节=$SRC_BYTES"
if [ "$N_MISSING" -gt 0 ]; then
  echo "❌ 缺失（应发布但不存在，$N_MISSING）："; sed 's/^/   - /' "$TMP/missing.list"
fi
if [ "$N_EXTRA" -gt 0 ]; then
  echo "❌ 多余（发布目录多出，$N_EXTRA）："; sed 's/^/   - /' "$TMP/extra.list"
fi
if [ "$N_HASH" -gt 0 ]; then
  echo "❌ 哈希不一致（$N_HASH）："; sed 's/^/   - /' "$TMP/hash.list"
fi

MANIFEST_OK=1
python3 "$ROOT/scripts/verify_manifest.py" --dir "$DST" --manifest "$MANIFEST" || MANIFEST_OK=0

if [ "$N_MISSING" -eq 0 ] && [ "$N_EXTRA" -eq 0 ] && [ "$N_HASH" -eq 0 ] && [ "$MANIFEST_OK" -eq 1 ]; then
  echo "RESULT=PASS files=$N_SRC bytes=$SRC_BYTES"
  exit 0
fi
echo "RESULT=FAIL missing=$N_MISSING extra=$N_EXTRA hash_mismatch=$N_HASH manifest=$([ "$MANIFEST_OK" -eq 1 ] && echo PASS || echo FAIL)"
exit 1
