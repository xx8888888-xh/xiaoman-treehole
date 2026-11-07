#!/usr/bin/env bash
# 一键起语音服务（只监听本机回环）。把打印出的地址填进 App「设置 → 语音服务地址」即可。
# 默认走 edge-tts（在线、免费、无需 key）；服务不可达时 App 会自动落回柔化后的系统 TTS。
#
# 安全约定：默认 XIAOMAN_BIND_HOST=127.0.0.1，不监听 0.0.0.0。
#  - 模拟器：10.0.2.2 在 QEMU 用户态网络里就是宿主机回环，绑 127.0.0.1 也能连；
#  - 真机 USB：先 `adb reverse tcp:8903 tcp:8903`，App 里填 http://127.0.0.1:8903；
#  - 确有需要暴露到局域网时才显式 XIAOMAN_BIND_HOST=0.0.0.0（自己承担风险）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${XIAOMAN_TTS_PORT:-8903}"

export XIAOMAN_TTS_PROVIDER="${XIAOMAN_TTS_PROVIDER:-edge}"
export XIAOMAN_TTS_PORT="$PORT"
export XIAOMAN_BIND_HOST="${XIAOMAN_BIND_HOST:-127.0.0.1}"

PY="python3"
if [ -x "$ROOT/venv/bin/python" ]; then PY="$ROOT/venv/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; fi

"$PY" - "$PORT" <<'PY'
import sys
port = sys.argv[1]
print("── 语音服务地址（仅本机回环 %s）────────────────" % port)
print("  Android 模拟器（AVD）里填：http://10.0.2.2:%s" % port)
print("  真机 USB：先 adb reverse tcp:%s tcp:%s，再填 http://127.0.0.1:%s" % (port, port, port))
print("  本机自测：curl -s -o /dev/null -w '%%{http_code}' http://127.0.0.1:%s/health" % port)
print("─────────────────────────────────────────────")
PY

exec "$PY" "$ROOT/server/tts_server.py"
