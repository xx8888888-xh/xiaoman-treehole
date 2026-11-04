# AGENT_SCOPE · w1 / feat/crisis（最高优先）

## 负责
- `web/js/crisis*`（新增模块；可直接把现有 `web/js/risk.js` 的分流逻辑演进/拆分到 crisis 模块）
- `server/crisis*`（新增模块；现有 `server/risk_guard.py` 的闸门逻辑可演进）
- `server/mock_api.py` 中**只与闸门/转介相关的片段**
- 危机相关测试：`web/tests/risk*`、`web/tests/crisis*`、`server/tests/test_risk*`、`server/tests/test_crisis*`
- 对应的 E2E 断言只允许改危机相关段落（脚本其余部分不动）

## 禁止
- 禁止改 `web/css/**`、聊天渲染（`web/js/app.js` 的消息 DOM/分页）、设置页 UI/外观
- 禁止改 `web/js/tts*`、`web/js/live2d*`、`web/js/stage.js`
- 禁止改 `android/**`、`gradle*`、`README*`、`CHANGELOG*`（需要变更记录时在提交正文里写清楚，由主工作区统一整理）

## 交付目标（按优先级）
1. 本地确定性三档分流（低/中/高）+ 上下文过滤 + 多轮累积权重 + **只升不降**
2. 服务端必须过闸再转发（不依赖客户端）；高危档先问安全 + 120/110 + 12356 + 文字渠道
3. 隐性告别、绝望、计划性语句、时间性识别；否定/第三人称/玩笑/引用/歌词区分；"怎么死"最高档
4. 关怀语境粘住 3 轮；输出侧复检（肯定式"挺好的/安心/不打扰"必须替换）
5. 40 条对抗用例（20 正 20 反）双端同源、纳入测试

## 提交规范
- 小步提交，前缀 `w1:`，例如 `w1: 危机词表补隐性告别与时间性`
- 每个小块独立可测（`node --test` / `pytest` 通过即提交）
- **完成后不要 merge、不要 push 到别人的分支**，由主工作区统一收

## 测试禁令（全局）
- 不启动 `server/tts_server.py` / `edge_tts`，不跑会发声的用例；TTS 只校验 HTTP 与文件生成，或标 `SKIP: muted by user`
- 不在宿主侧起 8901/8902/8903；端到端走 Docker（容器未起时先停下等用户）
- 浏览器一律 headless
