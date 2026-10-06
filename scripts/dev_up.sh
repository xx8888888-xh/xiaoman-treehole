#!/usr/bin/env bash
# 一条命令启动小满树洞的整个本地环境（重复执行安全，已在运行的服务会被跳过）
#   8901 前端静态站（web/ 目录）  8902 mock 对话服务  8903 TTS 语音服务
# 用法: bash scripts/dev_up.sh
# 日志: logs/xiaoman_web.log / logs/xiaoman_mock.log / logs/xiaoman_tts.log
# PID 文件: /tmp/xiaoman_web.pid / /tmp/xiaoman_mock.pid / /tmp/xiaoman_tts.pid
set -uo pipefail

# ---------- 固定配置 ----------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # 项目根目录
# 端口集中配置（唯一真源 scripts/env.sh，可用环境变量覆盖）
# shellcheck source=scripts/env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
PORT_WEB="$XIAOMAN_WEB_PORT"                              # 前端静态站端口
PORT_MOCK="$XIAOMAN_MOCK_PORT"                            # mock 对话服务端口
PORT_TTS="$XIAOMAN_TTS_PORT"                              # TTS 语音服务端口
LOG_DIR="$ROOT/logs"
LOG_WEB="$LOG_DIR/xiaoman_web.log"                        # 前端日志
LOG_MOCK="$LOG_DIR/xiaoman_mock.log"                      # 对话服务日志
LOG_TTS="$LOG_DIR/xiaoman_tts.log"                        # 语音服务日志
PID_WEB="/tmp/xiaoman_web.pid"
PID_MOCK="/tmp/xiaoman_mock.pid"
PID_TTS="/tmp/xiaoman_tts.pid"
WAIT_MAX=15                                               # 健康检查最长等待秒数

cd "$ROOT" || { echo "❌ 无法进入项目根目录 $ROOT"; exit 1; }
mkdir -p "$LOG_DIR"

# ---------- 工具函数 ----------

# 端口是否已有进程在监听（幂等判断：在监听就不再启动）
port_listening() {
  local port="$1"
  # 优先用 bash 内建的 /dev/tcp 探测
  if ( exec 3<>"/dev/tcp/127.0.0.1/${port}" ) >/dev/null 2>&1; then
    return 0
  fi
  # /dev/tcp 不可用时退回 curl 探测
  if curl -s -o /dev/null --connect-timeout 1 "http://127.0.0.1:${port}/" 2>/dev/null; then
    return 0
  fi
  return 1
}

# 校验端口上的服务是否为本项目服务（通过 /health 指纹）
# 用法: verify_service_fingerprint <端口> <预期健康检查路径> <预期响应片段>
verify_service_fingerprint() {
  local port="$1" path="$2" expected="$3"
  local resp
  resp=$(curl -s --connect-timeout 2 "http://127.0.0.1:${port}${path}" 2>/dev/null || true)
  [[ "$resp" == *"$expected"* ]]
}

# 后台启动一个服务：setsid nohup 脱离终端，日志重定向到指定文件，记录 PID
# 用法: launch <PID文件> <日志文件> <要执行的命令...>
launch() {
  local pid_file="$1" log="$2"; shift 2
  # 截断日志（避免新旧日志混排）
  : > "$log"
  setsid nohup "$@" >>"$log" 2>&1 </dev/null &
  local pid=$!
  echo "$pid" > "$pid_file"
  echo "$pid"
}

# 轮询健康检查，最多等 WAIT_MAX 秒
# 用法: wait_ready <服务名> <一条 curl 检查命令>
wait_ready() {
  local desc="$1" check="$2" waited=0
  while [ "$waited" -lt "$WAIT_MAX" ]; do
    # $check 不加引号 → 按词拆分后原样执行（例如 curl -s http://127.0.0.1:8902/health）
    if $check >/dev/null 2>&1; then
      echo "   ${desc} 就绪（已等待 ${waited}s）"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

# 按幂等规则启动某个服务：端口已在监听则校验指纹，匹配则跳过；不匹配则报错
# 用法: ensure_up <服务名> <端口> <PID文件> <日志文件> <启动工作目录> <健康检查路径> <预期响应片段> <启动命令...>
ensure_up() {
  local name="$1" port="$2" pid_file="$3" log="$4" dir="$5" health_path="$6" expected="$7"; shift 7
  if port_listening "$port"; then
    if verify_service_fingerprint "$port" "$health_path" "$expected"; then
      echo "   ${name} (:${port}) 已在运行"
      return 0
    else
      echo "❌ 端口 ${port} 被陌生进程占用（指纹校验失败：${health_path} 不含 ${expected}）" >&2
      return 1
    fi
  fi
  local pid
  # bash -c 以位置参数安全传递工作目录与命令，避免嵌套引号被外层 shell 提前截断
  pid=$(launch "$pid_file" "$log" bash -c 'cd "$1" && shift && exec "$@"' _ "$dir" "$@")
  echo "   ${name} (:${port}) 启动中 (PID=$pid)，日志 → ${log}"
}

# ---------- 1) 启动三个服务 ----------
echo "▶ 启动小满树洞本地环境 ..."

ensure_up "对话API" "$PORT_MOCK" "$PID_MOCK" "$LOG_MOCK" "$ROOT" "/health" "xiaoman-mock" python3 server/mock_api.py
ensure_up "语音API" "$PORT_TTS" "$PID_TTS" "$LOG_TTS" "$ROOT" "/health" "edge_tts" python3 server/tts_server.py
ensure_up "前端"    "$PORT_WEB" "$PID_WEB" "$LOG_WEB" "$ROOT/web" "/index.html" "<!DOCTYPE" python3 -m http.server "$PORT_WEB"

# ---------- 2) 健康检查（最多等 15 秒） ----------
echo "▶ 健康检查（最多等待 ${WAIT_MAX}s） ..."

FAILED=()

# mock 对话服务: curl -s http://127.0.0.1:8902/health
if ! wait_ready "对话API(:${PORT_MOCK})" "curl -s http://127.0.0.1:${PORT_MOCK}/health"; then
  FAILED+=("对话API(:${PORT_MOCK}) 日志 ${LOG_MOCK}")
fi

# 前端静态站: curl -s -o /dev/null http://127.0.0.1:8901/index.html
if ! wait_ready "前端(:${PORT_WEB})" "curl -s -o /dev/null http://127.0.0.1:${PORT_WEB}/index.html"; then
  FAILED+=("前端(:${PORT_WEB}) 日志 ${LOG_WEB}")
fi

# TTS 语音服务: 走它自带的 /health
if ! wait_ready "语音API(:${PORT_TTS})" "curl -s http://127.0.0.1:${PORT_TTS}/health"; then
  FAILED+=("语音API(:${PORT_TTS}) 日志 ${LOG_TTS}")
fi

# ---------- 3) 结果 ----------
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "❌ 本地环境未就绪，以下服务健康检查失败："
  for item in "${FAILED[@]}"; do
    echo "   - ${item}"
  done
  exit 1
fi

echo "✅ 本地环境就绪"
echo "   前端    http://127.0.0.1:${PORT_WEB}"
echo "   对话API http://127.0.0.1:${PORT_MOCK}"
echo "   语音API http://127.0.0.1:${PORT_TTS}"
exit 0
