#!/usr/bin/env bash
# ============================================================
# scripts/gen_release_keystore.sh · 生成 release 签名 keystore（一次性）
# ------------------------------------------------------------
# 铁律：keystore / 口令**绝不进仓库**。默认写到仓库外的 ~/.secrets，
#       并在还原子路径时直接拒绝执行。产出的 base64 用于 GitHub Secrets：
#         ANDROID_KEYSTORE_B64 / ANDROID_KEYSTORE_PASSWORD /
#         ANDROID_KEY_ALIAS / ANDROID_KEY_PASSWORD
#
# 用法：
#   bash scripts/gen_release_keystore.sh
#   TREEHOLE_SECRETS_DIR=/mnt/c/Users/xx/.secrets bash scripts/gen_release_keystore.sh   # WSL
#   FORCE=1 ...（仅在确认要换 key 时使用：换 key 会导致老用户无法覆盖升级）
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${TREEHOLE_SECRETS_DIR:-$HOME/.secrets}"
STORE="${OUT_DIR}/xiaoman-release.jks"
ALIAS="${ANDROID_KEY_ALIAS:-xiaoman}"
VALIDITY="${KEY_VALIDITY_DAYS:-10000}"

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

command -v keytool >/dev/null 2>&1 || die "找不到 keytool（请装 JDK 17+，或在 WSL 里跑）"

mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"
OUT_ABS="$(cd "$OUT_DIR" && pwd)"
case "$OUT_ABS" in
  "$ROOT"|"$ROOT"/*) die "输出目录在仓库内（$OUT_ABS）：keystore 绝不能提交进 git" ;;
esac

if [ -e "$STORE" ] && [ "${FORCE:-0}" != "1" ]; then
  say "已存在：$STORE"
  say "同一把 key 才能覆盖升级，不要重复生成；确实要换 key 请显式 FORCE=1（旧用户将无法覆盖安装）。"
  exit 0
fi

PW="${ANDROID_KEYSTORE_PASSWORD:-}"
if [ -z "$PW" ]; then
  # 32 位随机口令（去掉易混字符），PKCS12 下 store/key 口令必须相同
  PW="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-32)"
fi

say "生成 keystore → $STORE（alias=$ALIAS，RSA 2048，有效期 ${VALIDITY} 天）"
keytool -genkeypair \
  -keystore "$STORE" -storetype PKCS12 \
  -alias "$ALIAS" -keyalg RSA -keysize 2048 -validity "$VALIDITY" \
  -dname "CN=Xiaoman Treehole, OU=Release, O=Xiaoman, L=Shanghai, ST=Shanghai, C=CN" \
  -storepass "$PW" -keypass "$PW"

CERT_INFO="${OUT_DIR}/xiaoman-release.certinfo.txt"
keytool -list -v -keystore "$STORE" -storepass "$PW" > "$CERT_INFO"
B64="${OUT_DIR}/xiaoman-release.b64"
base64 -w0 "$STORE" > "$B64"

ENV_FILE="${OUT_DIR}/xiaoman-release.env"
umask 077
{
  echo "ANDROID_KEYSTORE_PASSWORD=$PW"
  echo "ANDROID_KEY_ALIAS=$ALIAS"
  echo "ANDROID_KEY_PASSWORD=$PW"
  echo "ANDROID_KEYSTORE_FILE=$STORE"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE" "$B64" "$STORE" "$CERT_INFO"

say ""
say "==== 生成完成（以下材料都在仓库外，请自行离线备份） ===="
say "keystore      : $STORE"
say "base64        : $B64   （→ GitHub Secret: ANDROID_KEYSTORE_B64）"
say "口令/别名文件 : $ENV_FILE  （→ 另外三个 Secrets，权限 600）"
say "证书信息      : $CERT_INFO"
say "keystore sha256: $(sha256sum "$STORE" | cut -d' ' -f1)"
say ""
say "证书指纹（发布时比对，防止悄悄换 key）："
grep -E "SHA256:|所有者:|Owner:" "$CERT_INFO" | sed 's/^/  /' || true
say ""
say "下一步：把 $B64 的内容贴进仓库 Secrets；keystore 本体与密码不要进 git、不要上传聊天工具。"
