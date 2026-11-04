# AGENT_SCOPE · w3 / feat/chat-web

## 负责
- `web/js/chat*`（新增：消息分页 + 只渲染可视页，自研约 150 行，不引库）
- `web/js/live2d*`（音频包络口型；已从 analyser 迁出，可继续打磨）
- `web/js/tts*`（provider 抽象 + Android 系统 TTS；已移除 edge-tts，可继续打磨）
- 对应测试：`web/tests/tts*`、`web/tests/chat*`、`web/tests/live2d*`

## 禁止
- 禁止改 `server/**`
- 禁止改 `web/css/**` 主变量（可读，不要改；确需新增类名先在自己的 JS 里内联限制，或等 w2 合并后协调）
- 禁止改 crisis/risk 相关文件
- 禁止改 `android/**`、`gradle*`、`README*`、`CHANGELOG*`

## 交付目标
1. 长会话分页：只渲染当前可视页，200 轮后 DOM 节点数稳定在常数级
2. 口型：音频包络关键帧（无音频时静止或轻微呼吸），MuMu 上可用
3. TTS：Android 系统 TTS、可选音色/语速档、静音状态持久化、弱网超时先出文字
4. 性能/可靠性相关的前端部分：429「稍等」状态、断网最小集（能看历史/能看角色）、进程被杀后恢复

## 提交规范
- 小步提交，前缀 `w3:`
- **完成后不要 merge、不要 push 到别人的分支**，由主工作区统一收

## 测试禁令（全局）
- 不启动 `server/tts_server.py` / `edge_tts`，不跑会发声的用例；TTS 只校验 HTTP 与文件生成，或标 `SKIP: muted by user`
- 不在宿主侧起 8901/8902/8903；端到端走 Docker（容器未起时先停下等用户）
- 浏览器一律 headless
