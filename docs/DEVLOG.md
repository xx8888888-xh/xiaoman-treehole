# DEVLOG · 操作日志（追加式，新条目在顶部）

> 规矩：每完成一步操作/变更，在这里追加一条。格式：`[时间] 动作 → 结果 → 触及文件`

---

[2026-10-05 07:30] ✅ APK构建成功（第5次）：绕过gradle的手工流水线全线打通（aapt2→ecj→d8→zipalign→apksigner），4.8MB已签名验证，包内容完整（模型2.5MB完好版/网页/音频全打入）。gradle路线死因：504会话断裂杀客户端，2核机构建10分钟必跨断裂 → scripts/build_apk.sh, android/app/build/outputs/apk/debug/xiaoman-treehole-v0.3-debug.apk
[2026-10-05 07:28] 构建攻坚记录：javac缺失(JRE-only)→ecj.jar替代；appcompat主题依赖→系统Material；manifest补package与versionCode属性；src路径修正。全部坑已沉淀进build_apk.sh可复跑
[2026-10-05 07:25] ✅ .github/workflows/android-ci.yml 落盘：双job（出包+KVM模拟器冒烟测试），公开仓库免费。模拟器验证路径正式转移到GitHub CD（本地无KVM+会话断裂，用户确认此方案）→ .github/workflows/
[2026-10-05 07:20] ✅ 离线兜底引擎mock_engine.js落地并单测通过（问候/上下文亲密/危机拦截），api.js改三级降级（openai→本地mock→离线引擎），真机无网可跑 → web/js/mock_engine.js

[2026-10-05 07:10] ✅ 修复亲密请求读错情绪bug：LEX加intimate类目+@CTX上下文感知（先骂老板再要抱抱→"拍拍，抱一下"接住；开心语境→团子抗议版），EMO_MAP补intimate→(gentle,Nod)。API级验证PASS+截图PASS → server/mock_api.py
[2026-10-05 07:05] ✅ 三场景截图验收：对话流(分条+钩子+时段问候"早呀呀")/危机(关怀卡+12356热线+表情联动)/戳戳。上一轮的OPTIONS预检怀疑被证伪，真凶是api.js fetch了/v1/chat而mock只认/v1/chat/completions(404) → web/js/api.js
[2026-10-05 06:59] 服务全灭重启（沙箱过夜重启杀进程）。教训：setsid进程在沙箱重启后不存活，每次续工先curl三health

[2026-10-04 22:40] ✅ R3语音链路闭环：edge-tts生成mp3(21KB)→ffmpeg转WAV→ASR回环转写"你好呀，我是小满，今晚也辛苦了"→PASS。注意：ASR仅支持WAV/WebM；SDK接口为 zai.audio.asr.create({file_base64})；ffmpeg系统已内置 → scripts/verify_tts.py, web/assets/audio/selftest.mp3
[2026-10-04 22:32] 双服务启动：mock_api.py(:8902)+tts_server.py(:8903)，health均OK。edge-tts安装遇PEP668，用--break-system-packages解决；服务启动早于库安装，重启后生效（教训：装新库后须重启服务）→ /tmp/mock.log, /tmp/tts.log
[2026-10-04 22:25] 前端代码v1落盘：index.html/style.css/api.js/tts.js/stage.js/app.js（活人感引擎五件套+危机安全层+12356热线）→ web/*
[2026-10-04 22:12] ✅ Live2D结构化手术完成：6表情(happy/sad/gentle/surprised/shy/neutral)+4动作组(Greeting/Nod/Shake/HappyJump)，原件存.orig可回滚，手术报告 → docs/live2d_surgery.md, web/assets/models/hiyori/expressions/
[2026-10-04 22:06] 发现模型无表情文件、仅Idle/TapBody动作组 → 确定"结构化手术"主刀点：自建6表情+动作重映射
[2026-10-04 22:05] Hiyori模型17文件经jsdelivr镜像全部下载成功（官方zip路径已失效，记录在案）→ web/assets/models/hiyori/
[2026-10-04 22:02] GitHub复用调研：pixi-live2d-display(MIT)+PixiJS6(MIT)+Hiyori官方模型(Live2D免费素材许可)+Open-LLM-VTuber(仅参考架构，未复用代码，规避AGPL)+edge-tts → PROJECT_STATE.md §6
[2026-10-04 22:00] 环境检查：无KVM(模拟器降级预案就绪)、Java21✓、磁盘25G✓、内存4G✓、Playwright✓、2核 → PROJECT_STATE.md §5
[2026-10-04 21:46] 建立 backup.sh 备份机制（重大变更前快照） → scripts/backup.sh（沙箱禁chmod，用 bash 直调）
[2026-10-04 21:45] 创建项目目录骨架（web/server/android/docs/backups/scripts） → ok
[2026-10-04 21:45] 任务启动，解析用户需求为 R1-R10 验收清单 → PROJECT_STATE.md 建立并写入架构设计与Mock API契约
- 沙箱教训：heredoc 追加文件会被拦（Permission denied），日志一律用 Edit 工具维护
