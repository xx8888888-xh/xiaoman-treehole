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
| `treehole-w5` | `feat/floating` | 悬浮窗相关（前端浮层与原生入口，按该工作区实际改动为准） | `server/**`、CSS 主变量、crisis/risk、聊天分页 |
| `treehole-w6` | `feat/ui` | UI 视觉与交互（`web/index.html`、`web/css/**`、外观/分页等，按该工作区实际改动为准） | `server/**`、crisis/risk 闸门 |
| `treehole-w7` | `feat/streaming` | `server/**`（`risk_guard.py` 只读）、`web/js/api.js`、`web/js/app.js`、`web/js/config.js`、`web/tests/`、`scripts/measure_ttft.py`、`scripts/test_stream_e2e.py`、`docs/W7_STREAMING.md` | `android/**`、`web/index.html`、`web/css/**`、`web/js/store.js`、`web/js/chat_pager.js`、`web/js/appearance.js`、`web/js/risk.js`、`server/risk_guard.py`（闸门只读，不得削弱）、`assets/www` 生成物 |

### w7（`feat/streaming`）基线
- 基线：主工作区 HEAD `688dd20`（`feat/containerize-and-config`）
- 目标：真流式 SSE 首字延迟、模式透明、降级链、提示词 v2、计量可观测
- 与 w5/w6 的冲突面：`web/js/app.js`（流式渲染接线）与 `web/index.html`/CSS（w6）、悬浮窗入口（w5），合并时以主工作区收口为准


## 提交规范（四路通用）
- 小步提交，前缀 `w1:` / `w2:` / `w3:` / `w4:`
- 每个小块独立可测（`node --test` / `pytest` 通过即提交）
- **完成后不要 merge、不要 push 到别人的分支**，由主工作区统一收

## 测试禁令（全局）
- 不启动 `server/tts_server.py` / `edge_tts`，不跑会发声的用例；TTS 只校验 HTTP 与文件生成，或标 `SKIP: muted by user`
- 不在宿主侧起 8901/8902/8903；端到端走 Docker（容器未起时先停下等用户）
- 浏览器一律 headless，且 `--mute-audio`
