# AGENT_SCOPE · 四路并行任务的文件域（合并版）

> 四个 worktree（`treehole-w1` ~ `treehole-w4`）已全部合并回
> `feat/containerize-and-config`。本文件保留四路的原始文件域约定，供后续并行继续使用；
> 合并后的主工作区是唯一真源，新增并行分支请从这里再切。

| worktree | 分支 | 负责 | 禁止 |
|---|---|---|---|
| `treehole-w1` | `feat/crisis` | `web/js/crisis*`、`server/crisis*`、`server/mock_api.py` 闸门片段、危机测试 | CSS、聊天渲染、设置页外观、tts/live2d/stage、android/gradle/README/CHANGELOG |
| `treehole-w2` | `feat/appearance` | `web/css/**`、`web/js/appearance*`、设置页外观区块、外观测试 | `server/**`、crisis/risk、tts/live2d、聊天分页、android/gradle/README/CHANGELOG |
| `treehole-w3` | `feat/chat-web` | `web/js/chat*`（分页/只渲染可视页）、`web/js/live2d*`、`web/js/tts*`、对应测试 | `server/**`、CSS 主变量（可读）、crisis/risk、android/gradle/README/CHANGELOG |
| `treehole-w4` | `feat/security` | `docs/**`、`README*`、`CHANGELOG*`、`gradle*`、`android` 默认模式、`server/` 日志脱敏与隐私文案、导出导入 | crisis/risk、CSS、聊天分页/口型/TTS 实现 |
| `treehole-w9` | `feat/history-paging` | `web/js/store.js`、`web/js/chat_pager.js`、`web/js/app.js`、`web/js/stage.js`（仅纹理选档与挂载时机）、`web/assets/models/hiyori/Hiyori.1024/**` 与 `Hiyori.1024.model3.json`、`web/tests/store.test.js`/`chat_pager.test.js`/`idb_stub.js`、`scripts/test_paging.py` | `server/**`、`android/**`、`web/index.html`、`web/css/**`、`web/js/risk.js`、`web/js/tts.js`、`assets/www`；**不碰 w5/w6/w7 worktree** |

### w9 细则（历史分页 + 首屏显存）
- 目标：`ChatStore.listBefore` 稳定游标接通 `ChatPager` loader（30 → 300 条可回看）；窗口 `PAGE=10 + BUFFER=4` 恒定；Live2D 纹理按 DPR/可视尺寸选 1024/2048，首屏显存 ≤12MB。
- 数据契约：游标为 `{at, id}` 双键（同毫秒不重不漏）；`ChatStore.list/listBefore` 升序返回并带 `id`。
- 测试禁令不变：headless + `--mute-audio` + `TTS provider=off`；不 push/不合并/不开 PR。
- 冲突注意：w6 改 UI/CSS，w7 改流式回复；本分支只动消息数据层、分页窗口与纹理选档，合流时以「最后 k 行」断言为准（见 `ANALYSIS.md` §16.6）。

## w6 · feat/ui-redesign（夜灯 · 暖纸）

| worktree | 分支 | 负责 | 禁止 |
|---|---|---|---|
| `treehole-w6` | `feat/ui-redesign` | `web/css/**`（令牌、组件、状态、无障碍）、`web/index.html` 的结构与 class、`web/js/appearance.js` 的**契约保持**、`web/js/app.js` 中主题/渲染/输入栏接线、`web/js/chat_pager.js` 渲染入参与顶部渐隐、`web/js/stage.js` 的加载/失败/零动画冻结、外观与 E2E 测试的适配 | Android 原生代码、`server/**`、`scripts/**`、`assets/www` 生成物、`treehole-w5` 目录、主工作区、`TTS_MUTED.flag`；不改 `apFont/apMotion/apIcons` 的对外行为与文案 |

- 基线：`feat/containerize-and-config` 当前 HEAD；不 push、不合并、不开 PR。
- 测试：`node --test "web/tests/*.test.js"`、`pytest server/tests`、`biome`、`scripts/verify_assets.py`、`scripts/test_appearance.py` 全绿；浏览器 headless + `--mute-audio`，TTS provider=off。
- 与 w5 潜在冲突：`web/index.html` 悬浮输入行区块、`web/js/app.js` 悬浮窗 UI 区块；合并时以 w5 的功能逻辑为准，只保留本分支的 class/结构改造。

## 提交规范（四路通用）
- 小步提交，前缀 `w1:` / `w2:` / `w3:` / `w4:`
- 每个小块独立可测（`node --test` / `pytest` 通过即提交）
- **完成后不要 merge、不要 push 到别人的分支**，由主工作区统一收

## 测试禁令（全局）
- 不启动 `server/tts_server.py` / `edge_tts`，不跑会发声的用例；TTS 只校验 HTTP 与文件生成，或标 `SKIP: muted by user`
- 不在宿主侧起 8901/8902/8903；端到端走 Docker（容器未起时先停下等用户）
- 浏览器一律 headless，且 `--mute-audio`

## w19 · feat/data-r3（第六轮并行·数据与发布收口）

> 基线：`r5-20261105` / `a283342`。worktree `treehole-w19`，分支 `feat/data-r3`。
> 本轮只做「数据完整性 + 发布物」，不改视觉/布局。

| 范围 | 文件 |
|---|---|
| 数据层 | `web/js/backup.js`、`web/js/stats.js`（仅清空/还原相关）、`web/js/reminders.js`（仅镜像同步相关） |
| 界面接线 | `web/js/app.js`（**只限**消息删改菜单、统计清空入口两段）、`web/js/memory.js`（仅在必要时） |
| 原生 | `android/**`（提醒镜像同步、版本号、签名读取） |
| 服务端 | `server/**`（导出/导入/删改校验、安全话术） |
| 脚本/文档 | `scripts/**`、`docs/SAFETY_COPY.md`、`docs/DATA_R3.md`、`.github/workflows/android-release.yml` |
| 新增测试 | `scripts/test_data_roundtrip.test.js`（真实 JSON 文件往返）、`server/tests/test_data_r3.py` |

**禁止**：`web/css/**`、`web/index.html`、`web/js/stage.js`、`web/js/pet.js`、`web/js/mock_engine.js`、
`web/js/chat_pager.js`、`package.json`、`.github/workflows/ci.yml`、`assets/**`、现有 `android.yml`。

**闸门**：
- `node --test "web/tests/*.test.js"` ≥ **222**（串行）
- `node --test scripts/test_data_roundtrip.test.js`（真实文件 I/O，无 mock）
- `python -m pytest server/tests -q`（串行，基线 153 passed + 19 subtests）
- 本地禁止 headless 浏览器 / AVD / Gradle；APK 只在 `android-release.yml` 云端构建
- 单会话 CPU/GPU/内存 ≤ 整机 30–40%，串行执行，采样写进 `docs/DATA_R3.md`

**交付**：小步 commit（前缀 `w19:`），能推就推；完成后回报五项完成度、往返通过率、APK 体积与签名校验结果、遗留项。

