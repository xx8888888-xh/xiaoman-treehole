# 小满树洞（treehole）

AI 陪伴 / 情感倾诉小应用：静态前端（Live2D 小满）+ 本地对话服务（内置规则引擎 + OpenAI 兼容代理）+ 可插拔语音服务（默认走设备系统 TTS）。

## 快速开始

三种跑法，从轻到重：**① 纯本地网页** → **② Docker Compose 全栈** → **③ Android APK**。
不想装 Docker 时可用 ②-b 的本地进程方式。

### 方式 ①：纯本地网页（不需要任何服务）

```bash
cd web && python -m http.server 8901
# 浏览器打开 http://127.0.0.1:8901 （手机视口 390×844 最佳）
```

直接双击 `web/index.html`（`file://`）也能看到界面，没有后端时会自动落到**离线规则引擎 + 浏览器语音**；
但部分浏览器对 `file://` 下的 IndexedDB 有限制，**推荐起一个本地静态服务**。
需要 mock 对话 / 真模型时用方式 ② 或 ②-b；语音默认走安卓系统 TTS / 浏览器 `speechSynthesis`，不依赖后端。

### 方式 ②：Docker Compose 全栈（推荐）

```bash
bash scripts/build_assets.sh       # 先同步 web/ → android/.../assets/www 并生成哈希清单（唯一真源）
docker compose up -d --build      # 必须在仓库根目录执行：web 与 assets/www 用相对路径只读挂载
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
| 8903 | tts | 语音服务（默认 `provider=none`，不内置合成后端，由设备系统 TTS 接管） |

容器统一打 label `com.dsh.project=treehole`，可用 `docker ps --filter label=com.dsh.project=treehole` 查看。

### 方式 ②-b：本地进程（不想用 Docker）

```bash
cp .env.example .env      # 按需改成 Kilo / OpenRouter / mock
bash scripts/dev_up.sh    # 幂等启动三服务，日志在 logs/
bash scripts/verify_stack.sh dev   # curl 三端口做一次冒烟
```

### 方式 ③：Android APK（构建 + 安装）

```bash
bash scripts/build_assets.sh      # 先把 web/ 同步进 APK 的 assets（唯一真源）
bash scripts/build_apk.sh         # 手工流水线（aapt2→javac→d8→签名），需 ANDROID_HOME
# 或官方 Gradle 流水线（JDK 17+）：
cd android && ./gradlew assembleDebug     # app/build/outputs/apk/debug/app-debug.apk
cd android && ./gradlew assembleRelease   # 无签名 secrets 时产出 app-release-unsigned.apk

adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

- `build_apk.sh` 的输出是 `android/app/build/outputs/apk/debug/xiaoman-treehole-v050-debug.apk`（debug 签名）。
- 首次 `./gradlew` 会下载 Gradle 8.7（`gradle-wrapper.properties` 已固定版本并带 SHA-256 校验）。
- release 签名材料不进仓库，步骤见 [`docs/RELEASE_SIGNING.md`](docs/RELEASE_SIGNING.md)。

## 配置（`.env`，真实 key 绝不入库）

| 变量 | 说明 |
|------|------|
| `AI_MODE` | `openai`（走真实模型）/ `mock`（本地规则引擎）/ `offline`（纯离线，不联网） |
| `AI_API_BASE` | OpenAI 兼容地址，如 `https://api.kilo.ai/api/gateway` |
| `AI_API_KEY` | 仅 `openrouter` / 自建网关需要；**Kilo 免 Key**。留空时 Windows 下自动从凭据管理器读取 |
| `AI_KEY_CREDENTIAL_TARGET` | 读取通用凭据的 Target 名（默认 Freebuff BYOK 写入的那条），可选 |
| `AI_MODEL` | 默认 `nvidia/nemotron-3.5-lightning:free` |
| `TTS_BASE` / `TTS_VOICE` | 语音服务地址与音色（仅服务端 provider 启用时使用） |
| `XIAOMAN_TTS_PROVIDER` | 服务端语音后端：`none`（默认，回 503）/ `cmd` / `sherpa` |
| `XIAOMAN_TTS_CMD` | `provider=cmd` 时的本地 TTS 命令（文本走 stdin、音频走 stdout） |

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

## 依赖说明与许可

- **不再使用 edge-tts**：它不是开源许可（GPL-3.0），微软也未授权对 Edge 朗读接口做自动化调用，
  且强依赖在线服务、与本项目「离线可用 + 可降级」的定位冲突。仓库代码 / 依赖清单 / 镜像中已无任何
  `edge-tts` 引用与安装层。
- **语音默认走设备本机能力**：安卓用系统 TTS（`AndroidTTS` JS Bridge：`available / speakRate / stop`，
  完成回调 `window.__ttsEnded`），浏览器用 `speechSynthesis`；两者随系统离线可用、不联网、零额外依赖。
- **服务端语音是可选插件**：`server/tts_server.py` 默认 `XIAOMAN_TTS_PROVIDER=none`，
  `/tts` 返回 `503 {"error":"tts_provider_unavailable","provider":"none"}`；前端收到后静默降级，
  文字始终先出，不阻塞对话。需要服务端合成时用环境变量显式开启（不联网下载任何东西）：
  - `XIAOMAN_TTS_PROVIDER=cmd` + `XIAOMAN_TTS_CMD=/path/to/tts-cli`：文本走 stdin、音频走 stdout，
    可把 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（Apache-2.0）的 CLI 包成这个命令；
  - `XIAOMAN_TTS_PROVIDER=sherpa` + 自备 `XIAOMAN_TTS_SHERPA_MODEL` / `..._TOKENS` 本地模型文件
    （可选 `_LEXICON` / `_DATA_DIR` / `_DICT_DIR` / `_SID`）。**仓库不内置、不下载模型**，
    模型许可由使用者自行确认。
- **许可友好**：生产新增运行时依赖保持 **0**（Python 侧只用标准库；前端既有依赖为 MIT）。
  可选后端优先 Apache-2.0 / MIT 实现，明确避免 GPL/AGPL 传染性许可。
- **CI 锁版本**：基础镜像 `python:3.12-slim`、Playwright `1.63.0`、Gradle Wrapper 8.7 均在配置中固定，
  不随 latest 漂移；单测与 e2e 全部离线，不调用任何云 TTS。

## 常见问题（FAQ）

**没有 API key 怎么办？**

- 主通道 Kilo **免 Key**（`AI_API_BASE=https://api.kilo.ai/api/gateway`，`AI_API_KEY` 留空即可）；
- 完全不想联网：`.env` 里设 `AI_MODE=mock`（本地规则引擎）或 `AI_MODE=offline`（纯离线），
  也可以在 App 设置页把模式切到「本地」；
- 只有 `openai` 模式走 OpenRouter / 自建网关时才需要 Key；设置页输入的 Key 只存在页面内存，刷新即失效。

**离线模式是什么？**

`AI_MODE=offline` 时不访问任何模型，只用内置离线规则引擎对话；界面、记忆、提醒、危机话术仍可用，
语音可在设置页切到「本机语音」。

**悬浮窗打不开 / 提示没有权限？**

「显示在其他应用上层」（`SYSTEM_ALERT_WINDOW`）是特殊权限，需在系统设置里手动授权——
点设置页「开启悬浮球」未授权时会跳转到设置页。悬浮球由前台服务承载，Android 8+ 会常驻一条通知，属正常现象。

**语音没有声音？**

1. 服务端默认 `provider=none`（不合成），这是设计行为：语音由安卓系统 TTS / 浏览器 `speechSynthesis` 接管；
   安卓需在系统设置里安装中文语音包；
2. 浏览器有自动播放限制，发一条消息（或点一下页面）之后再播放；
3. 确实需要服务端语音时，用 `XIAOMAN_TTS_PROVIDER=cmd`（或 `sherpa`）显式开启，见「依赖说明与许可」；
   可用 `curl -s http://127.0.0.1:8903/health` 查看当前 provider；
4. 「说话音色 / 语速」修改后从下一条消息开始生效；**音色只在服务端语音真正发声时生效**，
   本机语音使用系统音色（设置页会把"实际发声是谁"直接显示出来）。

**怎么管理提醒 / 查看小本本？**

右上角「记忆」抽屉里：上方可新建、编辑、取消提醒，能看到「即将到来」和「已经提醒过」的记录；
下方「我的小本本」是本地统计（说过的话、连续打卡、深夜次数、笑出声、最近 7 天柱状），
只存在本机、跨会话保留；清除浏览器数据会一并消失。

**我的数据存在哪里？**

对话历史在浏览器/APK 本机的 IndexedDB（库名 `xiaoman_chat`）；记忆、设置、字号、待发消息、风险日志在
`localStorage`。服务端默认不落盘；但 **Docker Compose 会通过 `XIAOMAN_DATA_DIR` 把会话上下文写到宿主机的
`./data/sessions`**（用于多轮上下文），不需要时直接删该目录即可。卸载 App 或清除浏览器数据会一并删除本机记录，
重要内容请先导出。

**怎么导出 / 迁移备份？**

设置页 →「倾诉记录」→ 导出为 JSON 文件；换设备后用「从 JSON 文件导入」，按内容去重合并，
不会覆盖已有记录，也不会上传云端。

**遇到心理危机怎么办？**

全国心理援助热线 **12356**（24 小时），也可以微信搜「12356 心理援助」用文字聊；有立即危险请拨 120/110。
App 命中危机信号时会给出固定关怀话术与热线，不依赖模型自觉。

## 目录结构

```text
treehole/
├─ web/                 前端（index.html + css/js/assets），浏览器与 APK 共用的唯一真源
├─ server/              本地服务：mock_api.py（对话/mock/LLM 代理）、tts_server.py（可插拔 TTS，默认 none）、risk_guard.py（危机分级）
├─ android/             WebView 壳工程（app/src/main/{java,res,assets} + Gradle Wrapper）
├─ scripts/             构建/启动/测试脚本（build_assets.sh、build_apk.sh、dev_up.sh、test_*.py）
├─ docs/                发布与运维文档（RELEASE_SIGNING.md、DEVLOG.md、PROJECT_STATE.md、截图等）
├─ .github/workflows/   CI（ci.yml 快闸门、e2e.yml 浏览器、android.yml 出包、gates.yml 一键全套）
├─ CHANGELOG.md         版本变更记录
├─ ANALYSIS.md          技术与 API 现状分析
├─ LICENSE（MIT）
```

## 测试

```bash
# Python 契约 / 端到端（需先启动本地栈）
python scripts/test_mock_api.py          # 8/8
python scripts/test_memory_recall.py     # 152/152
python scripts/test_onboarding.py        # 9/9（mock 模式）
python scripts/test_lifeline_ui.py       # 5/5
python scripts/test_phase3_e2e.py        # 设置页 → 3 轮真实对话 → 持久化

# 前端单测 + lint（Node 18+；含 api / config / backup / risk 四套）
cd web && npm test && npm run lint

# Android：Gradle 可复现构建自检（JDK 17+）
cd android && ./gradlew --version
cd android && ./gradlew assembleDebug
```

## 版本与发布

当前版本 **v0.5.0（2026-11-04）**：变更记录见 [`CHANGELOG.md`](CHANGELOG.md)，
release 出包与签名步骤见 [`docs/RELEASE_SIGNING.md`](docs/RELEASE_SIGNING.md)。
版本号需在 `android/app/build.gradle.kts`、`scripts/build_apk.sh`、`web/index.html` 三处同步。
