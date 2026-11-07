#!/usr/bin/env bash
# 一键起语音服务（手机/AVD 可直连）：把打印出的地址填进 App「设置 → 语音服务地址」即可。
# 默认走 edge-tts（在线、免费、无需 key）；断网时 App 会自动落回柔化后的系统 TTS。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${XIAOMAN_TTS_PORT:-8903}"

export XIAOMAN_TTS_PROVIDER="${XIAOMAN_TTS_PROVIDER:-edge}"
export XIAOMAN_TTS_PORT="$PORT"
export XIAOMAN_BIND_HOST="${XIAOMAN_BIND_HOST:-0.0.0.0}"   # 绑 0.0.0.0 才能被手机访问

PY="python3"
if [ -x "$ROOT/venv/bin/python" ]; then PY="$ROOT/venv/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; fi

"$PY" - "$PORT" <<'PY'
import socket, sys
port = sys.argv[1]
lan = ""
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))          # 不发包，只让系统选出口网卡
    lan = s.getsockname()[0]
    s.close()
except Exception:
    pass
print("── 语音服务地址 ────────────────────────────────")
if lan and not lan.startswith("127."):
    print("  手机设置页「语音服务地址」填：http://%s:%s" % (lan, port))
else:
    print("  未探测到局域网地址，请用本机 IP + 端口 %s" % port)
print("  Android 模拟器（AVD）里填：http://10.0.2.2:%s" % port)
print("─────────────────────────────────────────────")
PY

exec "$PY" "$ROOT/server/tts_server.py"
