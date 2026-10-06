#!/usr/bin/env bash
# ============================================================
# scripts/env.sh · 端口集中配置（唯一真源）
#   bash 脚本：`. scripts/env.sh` 后使用 $XIAOMAN_* 变量
#   Python 脚本：经 bash source 本文件读取同名环境变量（见各脚本 _load_env）
# 覆盖方式：在外部先 export 同名变量即可（本文件用 ${VAR:-默认} 保留外部值）
# ============================================================
: "${XIAOMAN_HOST:=127.0.0.1}"      # 服务探测地址（本机回环）
: "${XIAOMAN_WEB_PORT:=8901}"       # 前端静态站
: "${XIAOMAN_MOCK_PORT:=8902}"      # mock 对话服务
: "${XIAOMAN_TTS_PORT:=8903}"       # TTS 语音服务
export XIAOMAN_HOST XIAOMAN_WEB_PORT XIAOMAN_MOCK_PORT XIAOMAN_TTS_PORT
