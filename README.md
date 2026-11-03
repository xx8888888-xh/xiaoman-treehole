# 小满树洞（treehole）

AI 陪伴 / 情感倾诉小应用：静态前端（Live2D 小满）+ 本地对话服务（内置规则引擎 + OpenAI 兼容代理）+ edge-tts 语音服务。

## 快速开始

### 方式 A：Docker（推荐）

```bash
docker compose up -d --build      # 或在 WSL: docker compose -f ~/treehole-stack/compose/docker-compose.yml up -d --build
```

> 如果所在环境的 Docker 容器**没有外网出口**（本机 WSL 的 bridge 被防火墙拦掉 DNS/TCP，表现为 App 只能降级到本地 mock），
> 叠加 host 网络覆盖即可（mock/tts 直接复刻宿主机出网能力，仍只绑回环）：
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.hostnet.yml up -d --build
> ```

三个服务全部映射到 **宿主机回环**：

| 端口 | 服务 | 说明 |
|------|------|------|
| 8901 | web | 前端静态站 |
| 8902 | mock | 对话服务（mock 规则引擎 + LLM 代理） |
| 8903 | tts | 语音合成（edge-tts） |

容器统一打 label `com.dsh.project=treehole`，可用 `docker ps --filter label=com.dsh.project=treehole` 查看。

### 方式 B：本地进程

```bash
cp .env.example .env      # 按需改成 Kilo / OpenRouter / mock
bash scripts/dev_up.sh    # 幂等启动三服务，日志在 logs/
bash scripts/verify_stack.sh dev   # curl 三端口做一次冒烟
```

## 配置（`.env`，真实 key 绝不入库）

| 变量 | 说明 |
|------|------|
| `AI_MODE` | `openai`（走真实模型）/ `mock`（本地规则引擎）/ `offline`（纯离线，不联网） |
| `AI_API_BASE` | OpenAI 兼容地址，如 `https://api.kilo.ai/api/gateway` |
| `AI_API_KEY` | 仅 `openrouter` / 自建网关需要；**Kilo 免 Key**。留空时 Windows 下自动从凭据管理器读取 |
| `AI_KEY_CREDENTIAL_TARGET` | 读取通用凭据的 Target 名（默认 Freebuff BYOK 写入的那条），可选 |
| `AI_MODEL` | 默认 `nvidia/nemotron-3.5-lightning:free` |
| `TTS_BASE` / `TTS_VOICE` | 语音服务地址与音色 |

两条预置通道：

- **主通道（免 Key）**：`AI_API_BASE=https://api.kilo.ai/api/gateway`，`AI_API_KEY` 留空。
- **备选通道（兜底）**：`AI_API_BASE=https://openrouter.ai/api/v1`，Key 运行时从环境变量 / Windows 凭据管理器注入（`AI_API_KEY` 留空即走凭据管理器，见 `server/credentials.py`）。

模型默认开启思维链，因此请求固定 `max_tokens >= 512`（实际 600）并带 `reasoning: {"enabled": false}`，避免 `content` 被 reasoning 吃空返回 `null`。

## 降级与限流（重要）

真实模型 → 本地 mock → 离线规则引擎，三级链路：

1. 上游返回 **429/402**（免费池限流 / 额度耗尽）时，服务端与浏览器各自 **退避 1.5s 重试一次**；
2. 仍失败则降级到本地 mock，再失败降级到离线引擎；
3. 降级原因会记录在日志，并在设置页「测试连接」里显示 HTTP 状态与错误原因。

> **免费池可能限流**：Kilo 免费模型与 OpenRouter 免费额度（约 50 次/天，账户上限 0.01 USD）都可能随时触发 429。遇到限流时功能不会中断（会自动降级），但回复人格会切换到本地规则引擎；稍后重试或换自建 Key 即可。

## 安全注意事项

- Key 只从 **环境变量 / 系统凭据管理器** 读取；`.env` 已在 `.gitignore` 中，`.env.example` 只留空值。
- **容器内读不到 Windows 凭据管理器**（Linux 没有 `CredRead`）。跑在 Docker 里时必须用 `.env` / `-e AI_API_KEY=…` **显式注入**，否则 OpenRouter 通道不可用、只会走 Kilo（免 Key）或降级到本地 mock。`scripts/dev_up.sh` 启动后会打印通道自检结果，缺 Key 时给 ⚠️ 提示。
- 设置页输入的 Key **只存在当前页面内存**，刷新即失效，不写 `localStorage`、不进 IndexedDB。
- 服务端 `/api/config` 只下发 mode/model/base/provider，`AI_API_KEY` 永不外泄。
- CORS 默认白名单：`http://127.0.0.1:8901`（网页前端）、字面量 `null`（旧 `file://` 壳 / 悬浮窗）、
  `https://appassets.androidplatform.net`（APK 的 WebViewAssetLoader 同源域）；可用
  `XIAOMAN_ALLOW_ORIGINS` 覆盖。音频接口 `/tts` 是公开资源，固定回 `Access-Control-Allow-Origin: *`
  （跨域 `<audio>` + WebAudio 取音量包络所必需；无此头浏览器会把音频静音）。

## 测试

```bash
# Python 契约 / 端到端（需先启动本地栈）
python scripts/test_mock_api.py          # 8/8
python scripts/test_memory_recall.py     # 152/152
python scripts/test_onboarding.py        # 9/9（mock 模式）
python scripts/test_lifeline_ui.py       # 5/5
python scripts/test_phase3_e2e.py        # 设置页 → 3 轮真实对话 → 持久化

# 前端单测 + lint（Node 18+）
cd web && npm test && npm run lint
```
