#!/usr/bin/env bash
# 把 web/ 注入 android assets/www。
# 排除项（审计 android D22）：node_modules、npm 清单、测试音频 selftest、
# 文档截图 docs/shots（非运行时资源）、模型回滚备份 *.orig（仅手术用）。
set -e
SRC="$(cd "$(dirname "$0")/../web" 2>/dev/null && pwd)" || { echo "❌ 目录不存在: $(dirname "$0")/../web" >&2; exit 1; }
DST="$(cd "$(dirname "$0")/../android/app/src/main/assets" 2>/dev/null && pwd)" || { echo "❌ 目录不存在: $(dirname "$0")/../android/app/src/main/assets" >&2; exit 1; }
EXCLUDES=(
  --exclude=node_modules
  --exclude=package.json
  --exclude=package-lock.json
  --exclude='assets/audio/selftest.*'
  --exclude='docs/shots'
  --exclude='*.orig'
)
rm -rf "$DST/www"; mkdir -p "$DST/www"
cd "$SRC" && tar cf - "${EXCLUDES[@]}" . | (cd "$DST/www" && tar xf -)
echo "ASSETS_SYNCED $(du -sh "$DST/www" | cut -f1)"
