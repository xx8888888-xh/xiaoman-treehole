# AGENT_SCOPE · w4 / feat/security

## 负责
- `docs/**`（AI_RULES、PROJECT_STATE、ANALYSIS、发布/签名说明等）
- `README*`、`CHANGELOG*`
- `gradle*`（Gradle Wrapper、release 可复现）、`android/**` 的默认模式（APK 默认走原生桥/Kilo，mock 只兜底）
- `server/` 的**日志脱敏**与**隐私文案**（不得把对话原文写进日志/容器日志）
- 导出/导入 JSON（会话数据可迁移）
- 发布准备：版本号与 CHANGELOG、release 签名步骤文档（**不要执行签名**）

## 禁止
- 禁止改危机相关文件（`web/js/risk.js`、`server/risk_guard.py`、`web/tests/risk*`）
- 禁止改 `web/css/**`
- 禁止改聊天分页/口型/TTS 实现（可读，不要改）
- 不要执行 release 签名，不要跑 E2E

## 交付目标
1. 隐私文案如实（去掉"只保存在你手机上"之类不实描述）
2. TTS/对话原文不进日志；日志脱敏
3. 数据导出/导入 JSON 完整可用（不做云同步）
4. 版本号 + CHANGELOG + Gradle Wrapper + release 签名步骤文档
5. README 三条启动路径 + 常见问题
6. 生产环境新增运行时依赖保持 **0**；新依赖只接受 MIT/Apache/BSD

## 提交规范
- 小步提交，前缀 `w4:`
- **完成后不要 merge、不要 push 到别人的分支**，由主工作区统一收

## 测试禁令（全局）
- 不启动 `server/tts_server.py` / `edge_tts`，不跑会发声的用例；TTS 只校验 HTTP 与文件生成，或标 `SKIP: muted by user`
- 不在宿主侧起 8901/8902/8903；端到端走 Docker（容器未起时先停下等用户）
- 浏览器一律 headless
