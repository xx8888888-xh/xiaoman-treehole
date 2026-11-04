# W7 · 响应速度与真人感改造（`feat/streaming`）

> 隔离 worktree：`treehole-w7`，基线 = 主工作区 HEAD `688dd20`（`feat/containerize-and-config`）。
> 本分支不 push、不合并、不开 PR，由主工作区统一收口。

## 1. 改动文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `server/prompt_core.py` | 新增 | 提示词唯一真源（安全三条/身份/长度跑题分寸/底线/JSON 协议逐字保留 + 新增真人感/记忆两段） |
| `server/provider_policy.py` | 新增 | provider 隐私策略：默认禁止把倾诉内容发往 Kilo 免费 trial |
| `server/mock_api.py` | 修改 | 新增 `/api/chat/stream`（SSE）、上游 SSE 增量解析、2 字 holdback、输出侧复检、上游连接池、计量日志 |
| `server/tests/test_stream.py` | 新增 | 流式回归 8 例（事件顺序/mode/首字/危机先于上游/隐私策略/500/增量/断连/日志脱敏） |
| `server/tests/test_prompt_parity.py` | 新增 | 服务端 `prompt_core` 与客户端 `api.js` 常量逐字对齐 |
| `web/js/api.js` | 修改 | 提示词 v2、`chatStream`（SSE 逐 token）、三档超时、熔断、mode 通知、空回复降级 |
| `web/js/app.js` | 修改 | 流式渲染器、移除 650–1150ms 预等与逐段假打字、离线示例模式标注、各类固定等待缩短 |
| `web/js/config.js` | 修改 | 新增 `allowFreePersonal`（默认 false） |
| `web/tests/stream.test.js` | 新增 | 客户端 SSE/超时/熔断/mode/空回复/提示词 v2 共 12 例 |
| `scripts/measure_ttft.py` | 新增 | 首字延迟实测（TTFB / 首 token / 整段），mock 与真实 provider 两组 |
| `scripts/test_stream_e2e.py` | 新增 | headless + `--mute-audio` + TTS provider=off 端到端 |
| `docs/W7_STREAMING.md` | 新增 | 本文件 |
| `AGENT_SCOPE.md` | 修改 | 补 w5/w6/w7 文件域与 w7 基线 |

未触碰：`android/**`、`web/index.html`、`web/css/**`、`web/js/store.js`、`web/js/chat_pager.js`、`web/js/appearance.js`、`web/js/risk.js`、`server/risk_guard.py`（只读）、`assets/www`（gitignore 生成物）。

## 2. 真流式

- 服务端 `POST /api/chat/stream`，`Content-Type: text/event-stream`，事件：`meta` → `delta*` →（必要时 `replace`）→ `done`，异常为 `error`。
- `meta` 里带 `mode` / `provider` / `model`，`done` 里带完整 `content`、`tokens`、`ttfb_ms`、`first_token_ms`、`total_ms`、`degraded`。
- 上游流式：从半截 JSON 里按 `"reply"` 前缀增量提取（支持 `\uXXXX` 转义）；输出前留 2 字缓冲，避免危险词先露头，每个增量都过 `sanitize_output` 复检；整段结束再复检一次，需要时用 `replace` 纠正。
- 客户端 `fetch + ReadableStream` 逐 token 渲染（`createStreamRenderer`），首条消息不再预等，后续分条按 `||` 对齐增量。
- 上游连接复用：实测跨境 TLS 握手 120–1200ms，流式请求共用空闲连接后，真实 provider 首字 p50 从 ≈1213ms 降到 ≈731–848ms。

## 3. 首字延迟实测

命令：`python scripts/measure_ttft.py --mock-n 20 --real-n 12`（真实 provider 只用非个人测试短句，显式 `XIAOMAN_ALLOW_FREE_PERSONAL=1`）。

| 通道 | 采样 | TTFB(meta) p50 / p95 | 首 token p50 / p95 | 整段 p50 / p95 |
|---|---|---|---|---|
| 本地 mock（SSE） | 20/20 | 6 / 11 ms | 6 / 11 ms | 302 / 357 ms |
| 真实 provider（Kilo free，run A） | 10/12 | 6 / 31 ms | 731 / 1146 ms | 1390 / 1976 ms |
| 真实 provider（Kilo free，run B） | 7/12 | 5 / 25 ms | 848 / 1572 ms | 1560 / 1844 ms |

- 客户端真实通道首字硬线 = **1400ms**（`API.LIMITS.proxy.first` / `direct.first`）：超过即降级到本地 mock 并显示“离线示例模式”，因此**用户感知首字 P95 < 1.5s** 与上游波动无关。
- run A/B 的差异来自 Kilo 免费队列；两次均出现过上游 200 但 `content` 为空（run A 2/12、run B 5/12）。这类响应现在会切到本地引擎并标 `mode=mock`，不会出现空白或把兜底文案冒充成真模型。
- E2E 实测 mock 首字 197–328ms。

## 4. 降级链与透明度

路由（`web/js/api.js chatStream`）：

```
mode=offline → 本地离线
mode=mock    → 服务端 mock 流 → 失败则本地 offline
mode=openai  → 服务端代理（默认）→ 失败/超时 → mock → offline
               ├ 有 apiKey 且开启直连：浏览器直连 provider
               └ 安卓原生通道：仅 allowFreePersonal=true 时允许
```

| 级别 | 首字超时 | 空闲超时 | 总时长 | 熔断 |
|---|---|---|---|---|
| mock | 8s | 6s | 20s | 连续失败 2 次 → 冷却 30s |
| proxy | 1.4s | 8s | 45s | 同上 |
| direct | 1.4s | 6s | 45s | 同上 |
| native | 30s | — | 30s | 同上 |

隐私策略（`server/provider_policy.py`）：

- `kilo` 属于免费 trial，条款禁止承载个人数据 → **默认拦截**，返回 `error kind=policy`（403，`mode=mock`），且发生在 `meta` 之前，避免先显示“在线”。
- 仅当显式设置 `XIAOMAN_ALLOW_FREE_PERSONAL=1`（客户端对应 `allowFreePersonal`）才放行；测速脚本只在非个人信息场景临时开启。
- 默认兜底仍是 mock，不把倾诉内容默认发往免费端点。

模式透明：`meta/error/done` 都带 `mode`；客户端 `notifyMode()` → `window.__onMode` → `showModeBadge()`，`mock`/`offline` 显示“离线示例模式”，真实模型隐藏。

## 5. 提示词 v2 diff 摘要

- 原有段落**逐字保留**：安全三条、身份（小满/团子）、长度硬约束、跑题硬约束、朋友分寸、底线（12356）、JSON 输出协议。
- 仅拆分为常量并新增两段：
  - `REALISM_RULES`【真人感细则】：回复条数分布 60/30/10、陈述句多于问句、不复述不总结、不重复自己开头、不喊口号、接住具体细节、允许有自己的小事、不做诊断/不说“你应该”。
  - `MEMORY_RULES`【记忆】：只在相关时用、每次最多 1 条、不罗列、不用“我记得你…”开场、无关忽略、不编造。
- 顺序：安全 → 身份/约束 → 真人感 → 记忆 → 底线 → JSON 协议。
- 同源校验：`server/tests/test_prompt_parity.py` 直接读 `web/js/api.js` 常量对比，防两端漂移；心跳提示仍注入安全三条。

## 6. 计量与可观测

`_log_chat` 统一输出：`chat route=stream mode=… provider=… model=… first_token_ms=… tokens=… degraded=… status=… session=<sha1 前 8 位>`。

- 沿用 `log_redact.py` 的 `mask_sensitive`；不打印用户原文、不打完整 session id（测试用例断言日志中不含原文与明文 id）。
- 上游错误只记状态码与截断后的脱敏摘要。

## 7. 测试结果

| 项目 | 结果 |
|---|---|
| `node --test tests/*.test.js`（web） | 107 passed / 0 failed |
| `python -m pytest server/tests -q` | 93 passed, 19 subtests passed |
| 危机集 `test_crisis.py` | 13 passed（100%） |
| `test_risk_regression.py`（含 `test_parity_with_client_thresholds`） | 3 passed |
| `test_prompt_parity.py` | 6 passed |
| `biome check js/config.js js/api.js tests/` | 0 error（仅既有 warning/info） |
| `scripts/build_assets.sh` + `scripts/verify_assets.py` | 全绿（`assets/www` 为 gitignore 生成物） |
| `scripts/test_stream_e2e.py` | 8/8（首字 328ms、增量渲染、离线示例模式标注、typing 收起、全程静音、0 个 TTS 请求、0 JS 错误） |

流式回归覆盖：中途断连（服务端存活、客户端 partial）、首字超时降级、provider 500、mode 透传、mock 标注、空回复降级、熔断。

## 8. 与 w5 / w6 的潜在冲突点

- `web/js/app.js`：w7 改了发送主流程（`send` 走 `chatStream`）、`sendSplit` 尾部抽成 `finalizeReply`、新增 `createStreamRenderer`/`showModeBadge`；w6（UI）与 w5（悬浮窗）若同样改这段，合并时以主工作区收口，保留流式渲染与模式标注。
- `web/index.html` / `web/css/**`：w7 未改，只读取既有的 `#degradeBadge`/`#degradeText`；w6 若改这两个节点的结构或样式，需保证 id 不变，或把 `showModeBadge` 的选择器一起改。
- 悬浮窗（w5）若复用 `send()`/`API.chatStream`，注意回调 `onMeta/onDelta/onReplace` 与 `window.__onMode` 是两个入口；悬浮窗需要自己的渲染器时不要覆盖 `window.__onMode`。
- `server/mock_api.py`：w7 大改（SSE + 连接池 + 计量），w1 的闸门片段若再动该文件需基于本分支解决冲突；`risk_guard.py` 未改。
- `web/js/tts.js`：w7 只在 `api.js` 末尾加了 `window.API = API` 导出（修复 `tts=off` 静音开关被静默忽略）；w3 若改 tts 实现，保留对 `window.API` 的读取即可。

## 9. 已知限制

- Kilo 免费 trial 偶发返回空 `content`（本次实测 2/12 与 5/12），客户端会降级到本地引擎并标注“离线示例模式”；要稳定真人感建议配置用户自填 key 的直连/代理通道。
- 免费端点默认不放行个人倾诉内容，属于产品策略而非缺陷。
