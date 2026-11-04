# Changelog

本文件记录小满树洞的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号采用语义化版本（`主版本.次版本.修订号`）。

版本号单一真源：`android/app/build.gradle.kts` 的 `versionName`/`versionCode`、
`scripts/build_apk.sh` 的 `VER_NAME`/`VER_CODE`、`web/index.html` 关于页文案，三处需同步。

## [0.5.0] - 2026-11-04

### Added

- **危机分级双闸门**：对话风险从「关键词命中」升级为四级分级（`none` / `low` / `medium` / `high`，
  并单独判定「即时性」），客户端 `web/js/risk.js` 与服务端 `server/risk_guard.py` 各跑一遍，
  任一侧判为中/高风险即拦截（模型输出在下发前还会被二次清洗）。
  - 支持否定语境降级（如「我不会做傻事」不会被误判）；
  - 高风险固定话术含 120/110 与全国心理援助热线 **12356**（24 小时），并给出「微信搜 12356 心理援助」文字渠道；
  - 命中词与判定原因留痕（最多 50 条本地风险日志），风险上下文最多延续 3 轮，避免对话立刻「失忆式」回弹；
  - mock / 真实模型两条链路共用同一套分级与话术。
- **倾诉记录备份**：设置页「倾诉记录」可导出为 JSON 文件、也可从 JSON 导入；数据只在本机读写，
  不上传云端。导入时按内容去重合并，重复条目跳过并提示数量。
  APK 内通过原生桥（`AndroidTTS.saveBackup`）写入文件，并用系统文件选择器导入。
- **外观预设 + 三个独立开关**：极简（默认，灰阶图标 / 零动画 / 大字号 / 扁平层级）、
  默认、生动（彩色图标 + 仅 160ms 状态转换）；改任一开关即进入「自定义」并持久化
  `localStorage`，首帧同步应用、刷新不闪回；系统 `prefers-reduced-motion` 默认极简，但可手动覆盖。
- **危机对抗用例 40 条**：20 正例（隐性告别 / 计划性 / 方法询问 / 第三人称）+ 20 反例
  （否定、玩笑、歌词、夸张），客户端 `web/tests/risk_regression.test.js` 与服务端
  `server/tests/test_risk_regression.py` 同源双跑，纳入 CI。
- **无障碍字号**：设置页新增「字号」三档（标准 / 大 +15% / 特大 +30%），选择后持久化并即时生效。
- **语音语速与音色**：设置页新增「语速」（慢一点 / 正常 / 快一点）与「说话音色」（小艺 / 小晓 / 小圆）；
  - 服务端 `POST /tts` 接受 `rate` 档位并做白名单校验，非法值回退 `normal`；
  - 浏览器 `speechSynthesis` 与安卓系统 TTS 同步同一套语速映射；
  - TTS 缓存键纳入 `rate`/`pitch`，换语速不会命中旧音频。
- **待发消息重试条**：上游限流时保留未送达的消息，展示可关闭的重试条，支持一键重发。
- 工程侧：补齐 Gradle Wrapper（Gradle 8.7 + AGP 8.4.1）、`CHANGELOG.md`、
  `docs/RELEASE_SIGNING.md`，README 补充三条启动路径与常见问题。

### Changed

- **移除 edge-tts（许可与离线策略）**：语音改为可插拔 provider（`native` 安卓系统 TTS →
  可选 `server` → `browser` `speechSynthesis` → `none`）。`server/tts_server.py` 不再依赖
  edge-tts，默认 `provider=none` 时 `/tts` 回 `503 {"error":"tts_provider_unavailable"}`，
  前端静默降级、文字始终先出；镜像去掉 edge-tts 安装层，生产新增运行时依赖保持 0，
  可选后端为 `cmd` / `sherpa`（Apache-2.0，模型需自备、不内置不下载）。
- **Live2D 口型改为「音频包络关键帧」**：不再依赖实时 `AnalyserNode`（MuMu 等环境里
  AudioContext 被挂起、RMS 恒 0，嘴一动不动）。改为解码音频后预计算 20Hz RMS 包络，
  播放时按 `audio.currentTime` 取帧并平滑；音频直接播放（不接 WebAudio 图），
  避免 `createMediaElementSource` 在跨域时把声音静音。无音频数据（系统 TTS / 解码失败）
  只做轻微呼吸，不随机张嘴。
- 首屏渲染改为**按需加载**：PIXI + Cubism 运行时（约 900KB）串行懒加载，输入框与聊天流先可用，
  Live2D 舞台随后挂载；加载失败只降级舞台，不影响聊天。
- 安卓 WebView 上渲染分辨率降到 1x、帧率设上限，低端机/模拟器更省电、更流畅。
- 系统 TTS 桥新增 `speakRate(text, rate)`；旧版 APK 无此方法时自动回退固定语速。

### Fixed

- 刷新后开场白重复：记录上一条开场白并做内容去重。
- APK 内导入备份文件无响应：补 `onShowFileChooser` / `onActivityResult` 文件选择回调。
- WebView 内链接触发整页跳转：`shouldOverrideUrlLoading` 只放行应用内导航。
- 舞台初始化失败时不再静默隐藏，改为显示可见的降级占位提示。
- **红队审计 P0**：隐性告别补全（「东西都送人 / 该说的都说完 / 最后一晚 / 以后见不到我 / 不想拖累」等），
  `D1/D2` 原句与 3 条新增隐性表达均命中高危或中危；上游回复若对告别语给出「挺好的 / 安心 / 不打扰」式肯定，
  会被替换为对应级别的安全话术；服务端 `max_tokens` 封顶 1024，客户端 system 被剥离并注入不可覆盖安全规则。
- **红队审计 P1**：发布物统一由 `build_assets.sh` 生成并带 `ASSET_MANIFEST.json`，容器启动前校验哈希；
  默认模式改为在线（原生桥 / 服务端代理），mock 仅兜底；浮窗改用 `appassets` 同源；隐私文案改为如实描述；
  TTS 改 `POST` body 且日志脱敏；补充人格约束与输出侧一致性检查；消息列表超过 80 行时窗口化渲染。

## [0.4.0] - 2026-10-16

### Added

- **原生悬浮窗**：前台服务（`specialUse` 类型）+ `SYSTEM_ALERT_WINDOW` 特殊权限；60dp 悬浮球支持拖动、
  松手贴边吸附、单击展开迷你对话窗；迷你窗复用同一份 Web 前端（`?float=1`），保留记忆、提醒与离线引擎。
- 悬浮球增加 TalkBack 无障碍描述；权限未授权时引导到系统设置页。
- 关系进展感知：相识天数、里程碑、月度回放（基于真实记忆索引，零虚构）。
- 心跳问候情境化：开场白 × 引用句组合，跨会话去重。
- 免费模型限流排队：限流时不静默降级（避免人格偷换），展示排队文案并跨会话注回待发消息。
- 自我叙事统计（「小本本」）+ Q 版悬浮挂件（提醒播报、摆手）。
- 友谊卡片：脱敏聚合数据 + 手绘 Q 版形象 + 分享降级链。
- 危机安全升级：抑郁信号分级转介 + 固定热线话术（12356）。

### Changed

- 安卓壳由纯静态页面加载改为 `WebViewAssetLoader` 同源域加载，CORS 白名单放行 `appassets.androidplatform.net`。

### Fixed

- 悬浮窗在模拟器上的多轮问题：收起按钮误点「关闭」、壁纸色干扰判别、像素坐标偏移等，
  验收脚本改为 `dumpsys window` + 像素差分 + logcat 三通道取证。

## [0.3.0] - 2026-10-05

### Added

- 首个可安装版本：WebView 壳 + 静态前端（Live2D 小满、分条连发、打字延迟、时段问候、留存钩子）。
- 本地三服务：web 静态站 `8901`、mock 对话 `8902`、edge-tts 语音 `8903`。
- 手工 APK 构建脚本 `scripts/build_apk.sh`（aapt2 → javac/ecj → d8 → zipalign → apksigner）。
- GitHub Actions：出包 + KVM 模拟器冒烟 + 截图。
