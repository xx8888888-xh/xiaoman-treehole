#!/usr/bin/env bash
# 停掉 scripts/dev_up.sh 启动的三个本地服务（前端 8901 / 对话 8902 / 语音 8903）
# 用法: bash scripts/dev_down.sh
# 优先按 PID 文件停止，pkill -f 作兜底；结束前校验端口释放，残留则非零退出。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 端口集中配置（唯一真源 scripts/env.sh，可用环境变量覆盖）
# shellcheck source=scripts/env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
PID_WEB="/tmp/xiaoman_web.pid"
PID_MOCK="/tmp/xiaoman_mock.pid"
PID_TTS="/tmp/xiaoman_tts.pid"

PORT_WEB="$XIAOMAN_WEB_PORT"
PORT_MOCK="$XIAOMAN_MOCK_PORT"
PORT_TTS="$XIAOMAN_TTS_PORT"

# 尝试按 PID 文件优雅停止
kill_by_pid() {
  local pid_file="$1" desc="$2"
  if [ -f "$pid_file" ]; then
    local pid
    pid=$(cat "$pid_file" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && echo "   已停止 ${desc} (PID=$pid，SIGTERM)"
      # 等待进程退出，最多 3 秒
      local waited=0
      while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 3 ]; do
        sleep 1
        waited=$((waited + 1))
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null && echo "   强制杀掉 ${desc} (PID=$pid，SIGKILL)"
      fi
      rm -f "$pid_file"
      return 0
    fi
    rm -f "$pid_file"
  fi
  return 1
}

# 兜底：用收窄的模式 pkill
pkill_fallback() {
  local desc="$1" pattern="$2"
  if pkill -f "$pattern" 2>/dev/null; then
    echo "   已停止 ${desc}（兜底 pkill -f \"${pattern}\"）"
    return 0
  fi
  return 1
}

# 检查端口是否仍被监听
port_still_listening() {
  local port="$1"
  if ( exec 3<>"/dev/tcp/127.0.0.1/${port}" ) >/dev/null 2>&1; then
    return 0
  fi
  if curl -s -o /dev/null --connect-timeout 1 "http://127.0.0.1:${port}/" 2>/dev/null; then
    return 0
  fi
  return 1
}

echo "▶ 停止小满树洞本地环境 ..."

# 1) 优先按 PID 文件停止（描述与 pkill 兜底模式均取自 env.sh 端口变量，不再硬编码）
kill_by_pid "$PID_MOCK" "mock 对话服务(:${PORT_MOCK})" || pkill_fallback "mock 对话服务(:${PORT_MOCK})" "mock_api\.py"
kill_by_pid "$PID_TTS"  "TTS 语音服务(:${PORT_TTS})"  || pkill_fallback "TTS 语音服务(:${PORT_TTS})"  "tts_server\.py"
kill_by_pid "$PID_WEB"  "前端静态站(:${PORT_WEB})"    || pkill_fallback "前端静态站(:${PORT_WEB})"    "http\.server ${PORT_WEB}"

# 2) 校验端口释放
FAILED=()
if port_still_listening "$PORT_MOCK"; then FAILED+=("端口 ${PORT_MOCK} 仍被占用"); fi
if port_still_listening "$PORT_TTS";  then FAILED+=("端口 ${PORT_TTS} 仍被占用"); fi
if port_still_listening "$PORT_WEB";  then FAILED+=("端口 ${PORT_WEB} 仍被占用"); fi

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "❌ 停止后仍有端口未释放："
  for item in "${FAILED[@]}"; do
    echo "   - ${item}"
  done
  exit 1
fi

echo "✅ 已停止，端口 ${PORT_WEB}/${PORT_MOCK}/${PORT_TTS} 均已释放"
exit 0
