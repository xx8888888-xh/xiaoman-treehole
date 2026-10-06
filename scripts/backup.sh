#!/usr/bin/env bash
# 用法: ./backup.sh <标签>  —— 在每次重大变更前对项目做快照
set -e
LABEL="${1:-unnamed}"
# 清洗标签：只保留字母数字 . _ -，防止路径穿越/注入
LABEL="${LABEL//[^A-Za-z0-9._-]/_}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$DIR/backups"
OUT="$DIR/backups/pre_${LABEL}_${STAMP}.tar.gz"
cd "$DIR" && tar czf "$OUT" --exclude='backups' --exclude='node_modules' --exclude='.git' docs web server scripts android
# 校验产物真实存在且非空
if [ ! -s "$OUT" ]; then
  echo "❌ 备份产物异常：$OUT 不存在或为空" >&2
  exit 1
fi
echo "BACKUP_OK $OUT"
