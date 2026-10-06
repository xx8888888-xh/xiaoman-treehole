#!/usr/bin/env bash
# setup_dsh.sh —— 一键固化 DeepSeek Harness(DSH) 运行环境。
# 幂等：可反复执行；已就绪的步骤会自动跳过。
#
# 固化内容：
#   1) Node 24（DSH 0.2.0-rc.2 需 Node >= 24.2）+ 全局 @deepseek-ai/dsh@${DSH_VERSION}
#   2) /app/bin/dsh 包装脚本：固定用 Node24 运行、默认 danger-full-access、注入 OneRouter 凭据
#   3) $DSH_HOME/cordis.patch.yml：onerouter(OpenRouter) 免费模型路由 + 默认模型
#   4) 凭据存放于 /workspace/.secrets/onerouter.key（权限 600，绝不入库）
#   5) 可选插件（modsearch），best-effort
#   6) Python 依赖（edge-tts 供 TTS 服务、playwright + chromium 供 UI 测试）
#   7) AI 长期记忆还原：docs/AI_RULES.md → /workspace/.trae/rules/project_rules.md
#      （仓库外的 .trae/rules 会被沙箱重置清掉，故以仓库内副本为源还原）
#
# 用法：
#   ONEROUTER_API_KEY=sk-or-v1-xxx bash scripts/setup_dsh.sh   # 首次：写入密钥并固化
#   bash scripts/setup_dsh.sh                                  # 复用已存密钥
#   SKIP_PLUGINS=1 bash scripts/setup_dsh.sh                   # 跳过插件安装
set -euo pipefail

DSH_VERSION="${DSH_VERSION:-0.2.0-rc.2}"
NVM_DIR="${NVM_DIR:-/root/.nvm}"
DSH_HOME="${DSH_HOME:-/root/.dsh}"
SECRET_FILE="${SECRET_FILE:-/workspace/.secrets/onerouter.key}"
WRAPPER="/app/bin/dsh"
DEFAULT_FREE_MODEL="${DEFAULT_FREE_MODEL:-nvidia/nemotron-3-ultra-550b-a55b:free}"

log() { printf '[setup_dsh] %s\n' "$*"; }
die() { printf '[setup_dsh] 错误：%s\n' "$*" >&2; exit 1; }

# ---------- 1. Node 24 ----------
export NVM_DIR
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  die "未找到 nvm（$NVM_DIR/nvm.sh）。请先安装 nvm。"
fi
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"
if ! ls -d "$NVM_DIR"/versions/node/v24.* >/dev/null 2>&1; then
  log "安装 Node 24 ..."
  nvm install 24 >/dev/null
fi
NODE_PREFIX="$(ls -d "$NVM_DIR"/versions/node/v24.* 2>/dev/null | sort -V | tail -1)"
[ -n "$NODE_PREFIX" ] || die "Node 24 安装失败"
NODE_BIN="$NODE_PREFIX/bin/node"
log "Node: $("$NODE_BIN" -v)  ($NODE_PREFIX)"

# ---------- 2. 全局安装 dsh ----------
DSH_BIN="$NODE_PREFIX/lib/node_modules/@deepseek-ai/dsh/lib/bin.js"
if [ ! -f "$DSH_BIN" ]; then
  log "安装 @deepseek-ai/dsh@$DSH_VERSION ..."
  "$NODE_PREFIX/bin/npm" i -g \
    --allow-scripts=@deepseek-ai/dsh-subprocess-local,koffi,node-pty,@google/genai,protobufjs \
    "@deepseek-ai/dsh@$DSH_VERSION" >/dev/null
fi
[ -f "$DSH_BIN" ] || die "dsh 安装失败（$DSH_BIN 不存在）"
log "dsh: $("$NODE_BIN" "$DSH_BIN" --version)"

# ---------- 4. 凭据 ----------
mkdir -p "$(dirname "$SECRET_FILE")"
if [ -n "${ONEROUTER_API_KEY:-}" ]; then
  printf '%s\n' "$ONEROUTER_API_KEY" > "$SECRET_FILE"
  log "已写入 OneRouter 密钥 → $SECRET_FILE"
fi
if [ ! -s "$SECRET_FILE" ]; then
  die "缺少 OneRouter 密钥：请用 ONEROUTER_API_KEY=sk-or-... 运行本脚本，或手动写入 $SECRET_FILE"
fi
chmod 600 "$SECRET_FILE"
log "凭据就绪（$(wc -c < "$SECRET_FILE" | tr -d ' ') 字节，权限 600）"

# ---------- 2b. /app/bin/dsh 包装脚本 ----------
mkdir -p "$(dirname "$WRAPPER")"
cat > "$WRAPPER" <<'WRAP'
#!/usr/bin/env bash
# dsh 包装脚本（由 scripts/setup_dsh.sh 幂等生成，勿手改）：
# 固定用 Node24 运行 DSH、默认 danger-full-access、并从工作区密钥文件注入 OneRouter 凭据。
set -euo pipefail
NODE_PREFIX="$(ls -d /root/.nvm/versions/node/v24.* 2>/dev/null | sort -V | tail -1 || true)"
NODE24="$NODE_PREFIX/bin/node"
DSH_BIN="$NODE_PREFIX/lib/node_modules/@deepseek-ai/dsh/lib/bin.js"
if [ -z "$NODE_PREFIX" ] || [ ! -x "$NODE24" ] || [ ! -f "$DSH_BIN" ]; then
  echo "dsh: 运行环境缺失，请先执行 scripts/setup_dsh.sh" >&2
  exit 127
fi
export DSH_PERMISSION_MODE="${DSH_PERMISSION_MODE:-danger-full-access}"
if [ -z "${ONEROUTER_API_KEY:-}" ] && [ -f /workspace/.secrets/onerouter.key ]; then
  ONEROUTER_API_KEY="$(tr -d ' \t\r\n' < /workspace/.secrets/onerouter.key)"
  export ONEROUTER_API_KEY
fi
exec "$NODE24" "$DSH_BIN" "$@"
WRAP
chmod +x "$WRAPPER"
log "包装脚本就绪：$WRAPPER"

# ---------- 3. 用户级覆盖层：provider + 默认模型 ----------
mkdir -p "$DSH_HOME"
cat > "$DSH_HOME/cordis.patch.yml" <<EOF
# DSH 用户级覆盖层（由 scripts/setup_dsh.sh 幂等生成）：
# 接入 OneRouter(OpenRouter) 免费模型路由，并设为默认模型。
# 密钥不写在此处，由环境变量 ONEROUTER_API_KEY 提供（包装脚本注入）。
- id: llm-pi-ai
  config:
    providers:
      onerouter:
        displayName: OneRouter (OpenRouter free)
        apiKeyEnv: ONEROUTER_API_KEY
        api: openai-completions
        baseURL: https://openrouter.ai/api/v1
        reasoning: off
        models:
          - id: "nvidia/nemotron-3-ultra-550b-a55b:free"
            name: Nemotron 3 Ultra 550B (free)
            contextWindow: 1000000
          - id: "nvidia/nemotron-3-super-120b-a12b:free"
            name: Nemotron 3 Super 120B (free)
            contextWindow: 262144
          - id: "dots-studio/dots-3-note-preview:free"
            name: Dots 3 Note Preview (free)
            contextWindow: 512000

- id: agent-default-model
  config:
    provider: onerouter
    model: "$DEFAULT_FREE_MODEL"
EOF
log "覆盖层就绪：$DSH_HOME/cordis.patch.yml（默认模型 $DEFAULT_FREE_MODEL）"

# ---------- 5. 可选插件（best-effort） ----------
# 说明：dsh-our-free-model 已从 npm 下架（404），其“免费模型渠道”职责现由
# 本脚本接入的 OneRouter(OpenRouter) 免费模型承担，故默认只装 modsearch（网页搜索）。
if [ "${SKIP_PLUGINS:-0}" != "1" ]; then
  for pkg in "@liustack/modsearch@5.10.5"; do
    if PATH="$(dirname "$WRAPPER"):$PATH" "$WRAPPER" plugin --profile headless add "$pkg" >/dev/null 2>&1; then
      log "插件已安装：$pkg"
    else
      log "插件跳过（可能已装或环境受限）：$pkg"
    fi
  done
else
  log "SKIP_PLUGINS=1，跳过插件安装"
fi

# ---------- 6. Python 依赖（TTS 服务 + UI 测试） ----------
if [ "${SKIP_PYDEPS:-0}" != "1" ]; then
  if ! python3 -c "import edge_tts, playwright" >/dev/null 2>&1; then
    log "安装 Python 依赖（edge-tts / playwright）..."
    python3 -m pip install --quiet edge-tts playwright >/dev/null 2>&1 \
      || python3 -m pip install --quiet --break-system-packages edge-tts playwright >/dev/null 2>&1 \
      || log "警告：pip 安装失败"
  fi
  python3 -c "import edge_tts" >/dev/null 2>&1 && log "edge-tts OK" || log "警告：edge-tts 仍缺失（8903 将降级）"
  # playwright chromium（幂等：已安装会跳过）
  if python3 -c "import playwright" >/dev/null 2>&1; then
    python3 -m playwright install chromium >/dev/null 2>&1 && log "playwright chromium OK" \
      || log "警告：playwright chromium 安装失败"
    # chromium 运行时系统库（libatk/libxcomposite 等）。缺失时浏览器启动直接失败
    # （报 libatk-1.0.so.0: cannot open shared object file），会把所有 Playwright 测试打挂。
    # 幂等：已装则跳过，避免每次重置都跑一遍 apt。
    if ! ldconfig -p 2>/dev/null | grep -q "libatk-1.0"; then
      log "安装 chromium 系统依赖（apt，首次较慢）..."
      python3 -m playwright install-deps chromium >/dev/null 2>&1 && log "chromium 系统依赖 OK" \
        || log "警告：playwright install-deps 失败（Playwright 测试可能无法启动浏览器）"
    else
      log "chromium 系统依赖 OK（已就绪）"
    fi
  fi
else
  log "SKIP_PYDEPS=1，跳过 Python 依赖"
fi

# ---------- 7. AI 长期记忆还原（仓库内副本 → 仓库外 .trae/rules） ----------
# 沙箱重置会清掉 /workspace/.trae/rules/；docs/AI_RULES.md 随 git 入库，是唯一真源。
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RULES_SRC="$REPO_ROOT/docs/AI_RULES.md"
RULES_DST="/workspace/.trae/rules/project_rules.md"
if [ -f "$RULES_SRC" ]; then
  mkdir -p "$(dirname "$RULES_DST")"
  if cmp -s "$RULES_SRC" "$RULES_DST"; then
    log "AI 长期记忆已就绪（与仓库同源）"
  else
    cp "$RULES_SRC" "$RULES_DST"
    log "已还原 AI 长期记忆 → $RULES_DST"
  fi
else
  log "警告：未找到 $RULES_SRC，跳过长期记忆还原"
fi

# ---------- 自检 ----------
log "自检：dump-config ..."
if ( cd /workspace && "$WRAPPER" --profile headless --dump-config 2>/dev/null | grep -q "onerouter" ); then
  log "OK：onerouter 路由已生效"
else
  die "自检失败：dump-config 未发现 onerouter 路由"
fi
log "完成。现在可直接执行： dsh headless \"你的任务\""
