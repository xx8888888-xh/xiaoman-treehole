#!/usr/bin/env bash
# 把 web/ 注入 android assets/www（排除 node_modules）
set -e
SRC="$(cd "$(dirname "$0")/../web" && pwd)"
DST="$(cd "$(dirname "$0")/../android/app/src/main/assets" && pwd)"
rm -rf "$DST/www"; mkdir -p "$DST/www"
cd "$SRC" && tar cf - --exclude=node_modules --exclude=package.json --exclude=package-lock.json . | (cd "$DST/www" && tar xf -)
echo "ASSETS_SYNCED $(du -sh "$DST/www" | cut -f1)"
