#!/usr/bin/env bash
# 验证三端口可用性并把结果写入 artifacts/（容器 / 本地两条启动路径通用）
# 用法：bash scripts/verify_stack.sh [docker|dev]
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-dev}"
ART="$ROOT/artifacts"
mkdir -p "$ART"
LOG="$ART/stack_verify_${MODE}.log"

HOST="${XIAOMAN_HOST:-127.0.0.1}"
WEB="${XIAOMAN_WEB_PORT:-8901}"
MOCK="${XIAOMAN_MOCK_PORT:-8902}"
TTS="${XIAOMAN_TTS_PORT:-8903}"

fail=0
{
  echo "# stack verify ($MODE) $(date '+%F %T')"
  for item in "web http://$HOST:$WEB/index.html" \
              "mock http://$HOST:$MOCK/health" \
              "tts http://$HOST:$TTS/health"; do
    name="${item%% *}"
    url="${item#* }"
    body="$(mktemp)"
    code="$(curl -s -o "$body" -w '%{http_code}' --max-time 10 "$url" || echo 000)"
    echo "[$name] $url -> HTTP $code"
    head -c 300 "$body"; echo
    [ "$code" = "200" ] || fail=1
    rm -f "$body"
  done
  echo "[cors] mock ACAO(allow)=" \
    "$(curl -s -D - -o /dev/null -H 'Origin: http://127.0.0.1:8901' "http://$HOST:$MOCK/health" | tr -d '\r' | awk -F': ' 'tolower($1)=="access-control-allow-origin"{print $2}')"
  echo "[cors] mock ACAO(deny)=" \
    "$(curl -s -D - -o /dev/null -H 'Origin: http://evil.example' "http://$HOST:$MOCK/health" | tr -d '\r' | awk -F': ' 'tolower($1)=="access-control-allow-origin"{print $2}')"
  echo "RESULT=$([ $fail -eq 0 ] && echo PASS || echo FAIL)"
} | tee "$LOG"

exit "$fail"
