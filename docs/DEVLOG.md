# DEVLOG · 操作日志（追加式，新条目在顶部）

> 规矩：每完成一步操作/变更，在这里追加一条。格式：`[时间] 动作 → 结果 → 触及文件`

---

[2026-10-06 07:55] ✅ 多AI协同分支制落地（用户指令：另一AI协同开发，各自分支防冲突）：
① 我的工作分支=`zhiqiu/dev`（已推送，从 v0.4 的 main 切出），后续提交全走此分支，不再直接动 main
② workflow 触发放宽到 `zhiqiu/**`，分支首推即触发 CI
③ v0.4 分支 CI 全绿（build-apk ✓ + emulator-smoke ✓，模拟器回归 PASS）——Live2D渲染/表情芯片/交互正常
④ 顺手修 CI 截图：adb input text 弹软键盘遮挡对话区 → keyevent 111 收起（下轮回归验证）
触及：.github/workflows/android-ci.yml；记忆同步登记分支规矩

[2026-10-05 08:40] ✅ v0.4 三大生命线系统上线（用户定调：朋友定位+长期记忆+对话式提醒+心跳）：
① 提示词重构：小满=用户的朋友（去掉"树洞/倾听者"框架——过度限定失活人味），新增 HEARTBEAT_PROMPT（禁"在吗/好久不见"客服腔）+ buildSystemPrompt(记忆注入+时间注入)
② memory.js：localStorage 长期记忆库，2-gram 重叠滑窗检索（非重叠 match 会吞词——debug1h 的坑）+时间衰减+钉子户，双写兼容旧抽屉
③ reminders.js：中文时间解析（八点/十点半/九点一刻/明早/周X/已过时间→明天语义），客户端截获优先（可靠+离线可用），模型 reminders 协议补充
④ heartbeat.js：多重灵性闸门——提醒优先（用户要求的深夜也送）/静默23-8/间隔>2h/日≤4/聊天中不插嘴/25%抖动；离线模板池+记忆追访
⑤ 修复：window挂载（const不挂window导致守卫失效）、typingRow归位进#messages、捕获组索引[2]→[1]、双气泡
⑥ 单测14/14 + UI集成6/6全绿（REMINDER_SET/MEMORY_WRITE/DRAWER_LIST/HEARTBEAT_GEN/REMINDER_FIRE/零JS错误）→ docs/shots/07_memory_reminder_drawer.png
触及：api.js/app.js/index.html/memory.js/reminders.js/heartbeat.js/scripts/test_lifeline_ui.py

[2026-10-05 07:50] ✅✅ R1最终闭环：GitHub KVM模拟器验收通过——APK安装→启动→Live2D渲染(WebGL)→离线对话→表情联动→截图三张全证据。仓库 https://github.com/xx8888888-xh/xiaoman-treehole · CI四轮迭代史：①模拟器job因adb install通配符命中中间产物失败→指定文件名 ②幽灵权限弹窗(Files/Phone)→根因aapt2 link缺target-sdk-version被系统当targetSdk=1史前应用→补全link参数后消失 ③APK变量作用域问题(action逐行执行script)→路径写死 ④✅全绿 → docs/shots/emulator_0*.png
[2026-10-05 07:32] ✅ GitHub CD 全线打通：用户提供的PAT（upload/文件，全程未回显未入库）→建仓xx8888888-xh/xiaoman-treehole(公开)→推送→CI出包artifact。安全措施：密钥扫描、credential store即用即焚、token零落盘零日志
[2026-10-05 07:29] 用户提供GitHub token（回答其"云端CD是否可行"：是，runner自带KVM+SDK，公开仓库免费）

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
