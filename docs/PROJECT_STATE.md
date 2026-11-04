# PROJECT_STATE · 小满树洞（Xiaoman Treehole）

> 本文档是项目的**唯一权威状态源**，持续更新。任何人（或 AI）接手项目，先完整读完文件即可掌握全局。
> 最后更新：2026-11-04（w4 安全/发布）｜ 当前版本：v0.5.0 ｜ 状态：**安全审计收口 + 发布物单一真源 + 默认在线** ｜ 工作分支：`feat/security`；`main` 为稳定线。

## 0. 2026-11-04 当前事实（优先于下文历史快照）

> 下文 §1–§12 是 2026-10-06 的历史快照，其中的版本号、分支、缺陷计数、`treehole-app/` 路径、
> 「无 KVM / DSH 沙箱」等描述**已过期**。现状以本节、`README.md`、`CHANGELOG.md` 为准。

| 主题 | 当前事实（2026-11-04） |
|---|---|
| 版本 / 分支 | v0.5.0；`feat/security`（含 w4 安全与发布整改），`main` 稳定线 |
| 启动路径 | ① 本地网页：`bash scripts/build_assets.sh` 后 `python -m http.server`；② Docker Compose：`docker compose up -d --build`（WSL bridge 无法出网时叠加 `docker-compose.hostnet.yml`）；③ APK：`bash scripts/build_apk.sh`。详见 README「快速开始」 |
| 默认对话模式 | **在线优先**：安卓走原生桥直连免费模型，浏览器走服务端代理；`mock` 仅作兜底，`offline` 可手动选。设置页显示「当前实际模式」，自动降级会如实标注 |
| 发布物真源 | 唯一真源是仓库 `web/`；`scripts/build_assets.sh` 生成 `android/app/src/main/assets/www` + `ASSET_MANIFEST.json`。compose 启动前跑 `verify_assets.py`（对齐 `web/`）与 `verify_manifest.py`（对齐清单），**不一致直接拒绝启动** |
| CORS | **不是 `*`**：`server/xm_config.py` 默认白名单 `http://127.0.0.1:8901`、字面量 `null`（旧 file:// 壳）、`https://appassets.androidplatform.net`；可用 `XIAOMAN_ALLOW_ORIGINS` 追加。音频 `/tts` 是公开资源，固定回 `Access-Control-Allow-Origin: *` |
| 监听地址 | 默认 `127.0.0.1`；仅在容器内注入 `XIAOMAN_BIND_HOST=0.0.0.0`，再由 compose 端口映射回宿主机回环，不直接暴露局域网 |
| WSL host 网络 | 仅在 bridge 无外网出口（DNS/TCP 被防火墙拦）时使用 `docker-compose.hostnet.yml` 覆盖；仍只绑回环，不是默认部署方式 |
| 凭据边界 | Key 只从**环境变量 / Windows 凭据管理器**读取，不写前端、不入镜像、不落日志；**容器内读不到 Windows 凭据管理器**，Docker 部署必须用 `.env` / `-e AI_API_KEY=…` 显式注入 |
| 语音 | 默认设备系统 TTS / 浏览器 `speechSynthesis`；服务端语音是可选插件，默认 `provider=none`，仓库无 edge-tts |
| 日志 | TTS 原文只走 POST body，日志统一经 `server/log_redact.py` 脱敏（查询串、长文本、疑似密钥、手机号） |
| 功能落实（2026-11-05） | 分支 `feat/feature-completeness`（基线 61384a0，**未合并**）：43 项承诺功能盘点见 `docs/FEATURE_MATRIX.md`；已补齐设置开关真实生效（语音链跟随设置）、提醒 UI 闭环、小本本统计、首次空状态、错误边界提示；验证 166 node / 22 appearance / 60 e2e / 43 feature-loop |

---

## 0.1 项目一句话

安卓端 AI 树洞/倾听陪伴应用「小满树洞」：Live2D 萌系角色 + 活人感对话 + 语音回复，角色名"小满"（26岁新媒体运营，养橘猫团子）。开源（MIT）。

## 1. 完成度总览（对照验收清单）

| # | 需求 | 状态 | 证据 |
|---|------|------|------|
| R1 | 安卓跑通 | ✅ **KVM模拟器验收通过**（云端CI实机截图）+ APK 4.8MB已签名 | docs/shots/emulator_0*.png；repo: github.com/xx8888888-xh/xiaoman-treehole |
| R2 | 文字聊天 | ✅ 分条连发+打字延迟+时段问候+留存钩子 | docs/shots/03_chat.png |
| R3 | 语音回复 | ✅ 可插拔 provider：安卓系统 TTS 优先 / 浏览器 speechSynthesis 兜底 / 服务端默认 none | scripts/verify_tts.py输出；web/js/tts.js |
| R4 | 萌系UI | ✅ 首轮截图审查通过（角色完整渲染+手绘点缀） | docs/shots/01_base.png |
| R5 | Live2D手术 | ✅ 6表情+4动作组，可回滚 | docs/live2d_surgery.md |
| R6 | 活人感 | ✅ 五件套+危机安全层（12356） | docs/shots/04_intimate.png, 06_crisis.png |
| R7 | 最大复用 | ✅ 清单见§6 | — |
| R8 | Mock API | ✅ 我充当（:8902）+离线引擎兜底 | server/mock_api.py; web/js/mock_engine.js |
| R9 | 开源 | ✅ MIT（LICENSE待加，见迭代清单） | — |
| R10 | 用户视角分析 | ✅ 首版自评+迭代清单 | docs/USER_REVIEW.md |

## 2. 当前状态快照（接手先读）

- **Web 核心**：浏览器打开 web/index.html 即完整体验（需起 server/mock_api.py + server/tts_server.py；不起也行，离线引擎兜底）
- **本地部署**：`bash scripts/dev_up.sh` 一键起三服务（幂等，可重复执行），`bash scripts/dev_down.sh` 一键停
- **APK**：v0.5.0，产物在 `android/app/build/outputs/apk/debug/`（`bash scripts/build_apk.sh` 重新生成）
- **模拟器**：本地无 KVM 不可行；GitHub Actions runner 自带 KVM，推仓后自动出包+模拟器截图（workflow 已写好）
- **明天接真 API**：设置页（右上齿轮）→ 模式切 openai → 填 base URL + key + 模型名即可；提示词与结构化协议已内置（web/js/api.js）

## 3. 三分钟跑起来（2026-11-04 更新）

```bash
# 唯一真源 → 发布物（先做一次；之后每次改 web/ 都要重跑）
bash scripts/build_assets.sh

# 方式 ①：纯本地网页（最轻）
bash scripts/dev_up.sh            # 起 8901 静态站 / 8902 mock / 8903 tts（默认 provider=none）
bash scripts/dev_down.sh          # 一键停

# 方式 ②：Docker Compose（推荐）
docker compose up -d --build

# 方式 ③：APK
bash scripts/build_apk.sh         # 需 ANDROID_HOME；版本号在 build.gradle.kts / build_apk.sh
```

完整说明与常见问题（含 WSL hostnet、容器凭据边界）见仓库根目录 `README.md`。

## 4. 关键约束（用户明示）

1. **活文档**：每步操作、每次代码变更都要记录进 DEVLOG.md；本文档不断更新，保证随时可交接。
2. **备份先行**：每次重大代码变更前，先做备份（scripts/backup.sh，产物进 backups/）。
3. 全程自主决策，不问用户；任务完成前不停。
4. 沙箱资源有限（无 GPU，内存紧张），所有重活走"后台+轮询"。
5. **变更留痕 + 固化（2026-10-06 新增）**：每笔改动走「原子提交 + DEVLOG 追加 + 总账/状态更新」三件套，且必须推云端；规则与记忆固化见 [`docs/AI_RULES.md`](file:///workspace/xiaoman-treehole/docs/AI_RULES.md) 铁律 7 —— 沙箱重置后 `bash scripts/setup_dsh.sh` 一条命令还原环境与长期记忆（`docs/AI_RULES.md` → `/workspace/.trae/rules/project_rules.md`）。

### 执行者优先级（成本铁律）
- **DeepSeek Harness (dsh) 是免费的，必须优先承担实际执行工作**：基础代码编写、函数实现、文件生成、样板代码、批量重构、单测/脚本编写、文档草拟、数据整理。
- 助手（付费模型）只做 DSH 做不了的事：需求解析、架构设计、接口契约、任务拆解、验收标准制定、代码评审、疑难 bug 定位、最终集成。
- 下发 DSH 的任务必须是契约明确的小块（精确到文件、行、替换内容），否则先由助手拆解。
- 大量基础任务走多进程并行：多个 `dsh --profile headless` 同时跑。
- **每次开始本项目任务，第一步必须先 `git pull --rebase origin main`**，因为还有另一条并行开发线在改动云端。

> **2026-10-05 实测校准（重要，修正此前误判）**：
> DSH（配合 `/app/bin/dsh` 包装脚本，已默认 `DSH_PERMISSION_MODE=danger-full-access`）**文件工具与 shell/bash 工具均可用**——能写代码、跑测试、跑 git、起服务。
> 此前"DSH 的 shell 不可用"是**误判**：默认 `workspace-write` 审批=ask，headless 无审批通道 → bash 被 fail-closed，被错当成环境残缺。
> 正确分工：**写代码/写脚本/写文档/跑测试/git 等执行类杂活一律优先派给 DSH；助手只做规划、契约、验收与疑难定位。**
> 另：`dsh` 在非交互 shell 默认因 Node 版本静默失效，已由包装脚本固定 Node 24 修复。

## 5. 架构（当前设计）

```
┌─────────────── Android APK（WebView 壳，待定 Capacitor/原生） ───────────────┐
│                                                                             │
│  web/ 前端核心（同一份代码，浏览器=我们的"模拟器"）                            │
│  ├─ index.html        萌系 UI：Live2D 舞台(上) + 聊天流(下) + 输入栏          │
│  ├─ js/live2d.js      PixiJS + pixi-live2d-display；情绪→表情/动作；口型同步  │
│  ├─ js/app.js         活人感引擎：分条发送/打字延迟/主动开口/记忆/留存钩子     │
│  ├─ js/api.js         LLM 适配器：MOCK ⇄ OPENAI 兼容（真 API 明天接入）       │
│  └─ js/tts.js         TTS 适配器：native(系统TTS) / server(可选) / browser / none  │
│                                                                             │
│  server/（本地/局域网跑，安卓壳内也可打包）                                    │
│  ├─ mock_api.py       我充当的 API：情绪识别+回复模板+记忆+结构化输出          │
│  └─ tts_server.py     可插拔 TTS（默认 provider=none，不内置模型/不联网）      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 结构化 API 契约（明天接真 API 就按这个来）

请求：`POST /v1/chat/completions`，OpenAI 兼容字段。
响应 `choices[0].message.content` 必须是 JSON 字符串：

```json
{
  "reply": "回复正文（可以含「|」表示建议分条）",
  "emotion": "happy|comfort|sad|surprised|neutral|worried",
  "motion": "idle|tap|shake|greeting",
  "tts": true,
  "memory_write": {"key": "值"},
  "hook": "留存钩子可选文案，为空则无"
}
```

安全层（危机干预）在前端+Mock API 双侧实现，不依赖大模型自觉：
触发词命中 → 固定温暖话术 + 全国心理援助热线 **12356** + Live2D 切 worried 表情。

## 6. 目录结构

```
treehole-app/
├─ PROJECT_STATE.md      ← 本文件
├─ docs/                 ← DEVLOG.md（操作日志）、设计稿、用户视角分析
├─ backups/              ← 重大变更前的快照 tar
├─ scripts/              ← backup.sh 等工具脚本
├─ web/                  ← 前端核心（index.html + css/ + js/ + assets/）
├─ server/               ← mock_api.py + tts_server.py
└─ android/              ← 安卓壳工程（阶段二）
```

## 7. 环境事实（持续补充）

- 沙箱：无 GPU、内存约 4GB、网络可用（npm/pip/curl 已验证可用）
- 已有：Node 24、Python3、Playwright（Chromium）、z-ai SDK
- 待确认：/dev/kvm（决定模拟器可行性）、Java（决定 Gradle 打包）、磁盘余量

## 8. 复用清单（R7，持续登记）

| 来源 | 用途 | 许可 |
|------|------|------|
| pixi-live2d-display (guansss) | Live2D 渲染 | MIT |
| PixiJS | 渲染底层 | MIT |
| Live2D 官方免费示例模型 | 角色模型（将做结构化改造） | Live2D 免费素材许可 |
| 系统 TTS / speechSynthesis | 语音默认路径 | 设备自带，离线可用，零额外依赖 |
| Open-LLM-VTuber (t41372) | 架构参考：情绪标签驱动 Live2D | AGPL-3.0（仅参考思路，不复代码） |
| 前几日"小满"测试集与盲测结论 | 人设、活人感规则、安全层话术 | 自产 |

## 9. 测试记录

| 日期 | 项目 | 结果 |
|------|------|------|
| 10-04 | TTS→ASR 回环 | PASS（"你好呀，我是小满…"转写吻合） |
| 10-04 | Live2D 渲染 | 首次贴图损坏（jsdelivr截断）→gcore镜像修复→完整渲染 |
| 10-05 | 对话流截图 | PASS（分条/钩子/时段问候） |
| 10-05 | 危机场景 | PASS（关怀卡+12356+表情联动） |
| 10-05 | 亲密请求上下文 | PASS（@CTX接住情绪） |
| 10-05 | APK 构建+签名 | PASS（4.8MB，apksigner verify通过） |
| 10-05 | 模拟器 | 本地不可行（无KVM+会话断裂）→ GitHub CI 接管 |
| 10-05 | 本地部署三服务（8901/8902/8903）+ 5 处缺陷修复 | 全部实测验证（DEVLOG 顶条） |
| 10-05 | Mock 契约测试（scripts/test_mock_api.py） | **8/8 passed** |
| 10-05 | UI 集成（记忆/提醒/心跳，scripts/test_lifeline_ui.py） | **5/5 PASS**，控制台错误 0 |
| 10-05 | 端到端实测（浏览器真跑） | **11 项全通过**：TTS 打到 8903、音频 200/`audio/mpeg`、危机拦截含 12356、记忆写入与清空、零控制台错误、无 HTTP≥400（仅 favicon 404，非缺陷） |
| 10-06 | 第二轮回归（三服务实跑） | Mock **8/8** · Lifeline UI **5/5** · E2E **5/5**，均 exit 0 |
| 10-06 | D9 原生 TTS 完成回调订阅（Playwright 桩测） | PASS：`__ttsEnded` 触发 onEnd 恰 1 次、重复调用去重、无回调时兜底恰 1 次、pageerror 0 |
| 10-06 | 沙箱重置自还原 | PASS：移除 `.trae/rules` 后跑 `setup_dsh.sh`，长期记忆还原且 `cmp` 一致（exit 0） |

## 10. 已知问题 / 风险

- 沙箱无 KVM 的话，Android 模拟器将极慢甚至不可行 → 降级方案：APK 构建 + Chromium 移动视口仿真截图 + 交付用户真机安装
- Gradle/SDK 下载量大，内存紧张时用 --no-daemon + 限制 JVM 堆

## 11. 交接指南（给下一个接手者）

1. 读 §1 完成度与 §4 约束 → 2. 读 docs/DEVLOG.md 最新 20 条 → 3. 按 §3 三分钟跑起来 → 4. 接真 API：设置页切 openai 模式（提示词在 web/js/api.js 的 SYSTEM_PROMPT）→ 5. 模拟器验证：git push 后看 Actions（.github/workflows/android-ci.yml）。

## 12. 交付物索引

| 交付物 | 路径 |
|---|---|
| APK（可装真机） | android/app/build/outputs/apk/debug/xiaoman-treehole-v0.3-debug.apk |
| Web 源码 | web/ |
| 手工构建脚本 | scripts/build_apk.sh（aapt2→ecj→d8→签名，可复跑） |
| CI/CD | .github/workflows/android-ci.yml |
| UI 截图 | docs/shots/01~06.png |
| 手术报告 | docs/live2d_surgery.md |
| 用户视角自评 | docs/USER_REVIEW.md |
| 操作日志 | docs/DEVLOG.md |
| 审计修复总账（148 条逐条） | docs/AUDIT_REMEDIATION.md |
| AI 长期记忆 / 项目规则（仓库内同源） | docs/AI_RULES.md |
| 备份 | backups/（3个快照，含模型原始态与两次重大变更前态） |
