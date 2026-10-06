#!/usr/bin/env bash
# 把 web/ 注入 android assets/www（排除 node_modules、npm 清单、测试产物 selftest 音频）
set -e
SRC="$(cd "$(dirname "$0")/../web" 2>/dev/null && pwd)" || { echo "❌ 目录不存在: $(dirname "$0")/../web" >&2; exit 1; }
DST="$(cd "$(dirname "$0")/../android/app/src/main/assets" 2>/dev/null && pwd)" || { echo "❌ 目录不存在: $(dirname "$0")/../android/app/src/main/assets" >&2; exit 1; }
rm -rf "$DST/www"; mkdir -p "$DST/www"
cd "$SRC" && tar cf - --exclude=node_modules --exclude=package.json --exclude=package-lock.json --exclude='assets/audio/selftest.*' . | (cd "$DST/www" && tar xf -)
echo "ASSETS_SYNCED $(du -sh "$DST/www" | cut -f1)"
