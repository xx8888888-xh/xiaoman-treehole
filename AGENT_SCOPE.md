# AGENT_SCOPE · w2 / feat/appearance

## 负责
- `web/css/**`（含 `web/css/style.css`、外观相关样式）
- `web/js/appearance*`（预设 + 开关 + 持久化 + `prefers-reduced-motion` 覆盖）
- 设置页中的**外观区块**（`web/index.html` 里仅外观 section 的 DOM/文案）
- `web/tests/appearance*`、`scripts/test_appearance.py`（仅外观断言）

## 禁止
- 禁止改 `server/**`
- 禁止改 crisis/risk 相关文件（`web/js/risk.js`、`server/risk_guard.py`、`web/tests/risk*`）
- 禁止改 `web/js/tts*`、`web/js/live2d*`、聊天分页逻辑
- 禁止改 `android/**`、`gradle*`、`README*`、`CHANGELOG*`

## 交付目标
1. 三种预设：极简（灰阶图标 / 零动画 / 大字号）、默认、生动
2. 三个独立开关；改任一开关即进入「自定义」；持久化 `localStorage`；首帧同步、刷新不闪回
3. 首次启动默认极简；`prefers-reduced-motion` 优先但可手动覆盖
4. 生动模式仅 120–200ms 状态转换动效；无循环装饰动画、无自动播放、无声
5. 两种模式对比度达 AA、焦点环可见
6. 测试断言：仅极简模式 CSS 动画命中 0

## 提交规范
- 小步提交，前缀 `w2:`
- **完成后不要 merge、不要 push 到别人的分支**，由主工作区统一收

## 测试禁令（全局）
- 不启动 `server/tts_server.py` / `edge_tts`，不跑会发声的用例；TTS 只校验 HTTP 与文件生成，或标 `SKIP: muted by user`
- 不在宿主侧起 8901/8902/8903；端到端走 Docker（容器未起时先停下等用户）
- 浏览器一律 headless
