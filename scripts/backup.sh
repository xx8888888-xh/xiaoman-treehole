#!/usr/bin/env bash
# 用法: ./backup.sh <标签>  —— 在每次重大变更前对项目做快照
set -e
LABEL="${1:-unnamed}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$DIR/backups/pre_${LABEL}_${STAMP}.tar.gz"
cd "$DIR" && tar czf "$OUT" --exclude='backups' --exclude='node_modules' --exclude='.git' docs web server scripts android 2>/dev/null || true
echo "BACKUP_OK $OUT"
