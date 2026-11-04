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

## 提交规范（四路通用）
- 小步提交，前缀 `w1:` / `w2:` / `w3:` / `w4:`
- 每个小块独立可测（`node --test` / `pytest` 通过即提交）
- **完成后不要 merge、不要 push 到别人的分支**，由主工作区统一收

## 测试禁令（全局）
- 不启动 `server/tts_server.py` / `edge_tts`，不跑会发声的用例；TTS 只校验 HTTP 与文件生成，或标 `SKIP: muted by user`
- 不在宿主侧起 8901/8902/8903；端到端走 Docker（容器未起时先停下等用户）
- 浏览器一律 headless，且 `--mute-audio`
