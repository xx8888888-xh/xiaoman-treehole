# 审计缺陷修复说明 & 代码改动说明（AUDIT REMEDIATION）

> 本文件是「小满树洞」2026-10-05/06 全量代码审计（148 条缺陷）的**修复与改动总账**，随分支 `fix/audit-remediation-20261005` 一并入库，供评审与回溯。
>
> - 分支：`fix/audit-remediation-20261005`（首轮）、`fix/remediation-r2-20261006`（第二轮：本轮遗留项清零 + 固化）
> - 修复提交：`1b8541e`（全量修复，60 文件，+3716 / −1021）、`86426d0`（推送脚本 + DEVLOG）、文档 + 退出动画修复 + APK 去跟踪；**第二轮**（本分支）：清零 4 项遗留（android D3/D9/D22 + scripts dev_down 字面量）、把 AI 长期记忆与固化清单入库
> - 审计范围：`server/`、`web/js/`、`web/`（UI）、`android/` + CI、`scripts/`
> - 验证：本地三服务（8901 静态站 / 8902 mock_api / 8903 tts_server）实跑，见 §八

---

## 一、总览

| 模块 | 审计条数 | 已修 | 部分采纳 | 遗留 | 主要风险面 |
|---|---:|---:|---:|---:|---|
| server（mock_api / tts_server） | 15 | 12 | 3 | 0 | 安全兜底、并发、错误脱敏 |
| web/js（前端逻辑） | 28 | 28 | 0（附加建议 3 条未采纳） | 0 | XSS、发送并发自锁、提醒丢失 |
| web UI（index.html / css） | 25 | 23 | 2 | 0 | 可访问性、对比度、降级 |
| android（壳 + 构建 + CI） | 26 | 19 | 6 | 1 | 编译阻断、assets 漂移、签名 |
| scripts（构建 / 测试 / 运维） | 54 | 54 | 0 | 0 | 门禁失效、他机硬编码、环境固化 |
| **合计** | **148** | **136** | **11** | **1** | — |

**最高优先级、会直接阻断构建/运行或造成安全问题的项**（本轮重点）：

1. **前端 XSS**（web/js #1、UI D1）：模型回复/记忆/提醒文本经 `innerHTML` 注入 → 任意脚本可读全部 `localStorage`。
2. **发送自锁死锁**（web/js #5）：`send()` 内嵌调用 `sendSplit()` 两次争抢同一把锁 → 用户发出消息后**永远不会收到回复、发送键永久禁用**（已用 Playwright 复现）。
3. **提醒被静默吞掉**（web/js #2）：`due()` 先置 `done`，投递时遇 `busy` 直接 return → 用户亲口定的提醒被标记完成却**永不到达**。
4. **PIXI 顶层初始化崩站**（web/js #3、UI D25）：vendor 未就绪时模块顶层 `new PIXI.Application` 抛错，整个 `App.init` 中断，只剩一个死输入框。
5. **Android 编译阻断**（android D1/D2/D17）：同包同名 `MainActivity.kt`+`.java`、Manifest `package` 与 AGP 8.4.1 冲突、残留 Kotlin 插件/依赖 → APK 出不来。
6. **`assets/www` 与 `web/` 漂移**（android D4）：APK 打包的前端是**旧代码**，所有 web 修复都不会进 APK。
7. **测试门禁全线失效**（scripts #33/#38/#48/#50/#54）：多个测试脚本「打印 FAIL 仍 `exit 0`」，且未接入 CI；`verify_tts.py`/`test_freemodels.py` 写死了**他机绝对路径**。
8. **环境不可复现**（scripts）：沙箱重置后 Node24/dsh/Python 依赖全丢，本轮新增一键固化脚本 `setup_dsh.sh`。

---

## 二、验证与复现（怎么核对）

```bash
cd /workspace/xiaoman-treehole
bash scripts/setup_dsh.sh        # 环境固化（幂等）：Node24 + dsh + Python 依赖 + chromium
bash scripts/dev_up.sh           # 起三服务：8902 mock / 8903 tts / 8901 web
python3 scripts/test_mock_api.py # 8/8（连跑两次验幂等）
python3 scripts/test_lifeline_ui.py
python3 scripts/test_e2e.py
python3 scripts/verify_tts.py
bash scripts/dev_down.sh
```

静态检查（全绿）：

```bash
for f in web/js/*.js; do node --check "$f"; done
for f in scripts/*.sh; do bash -n "$f"; done
python3 -m py_compile server/*.py scripts/*.py
python3 -c "import yaml;yaml.safe_load(open('.github/workflows/android-ci.yml'))"
```

---

## 三、server（后端 mock_api / tts_server）

审计 15 条，全部有对应改动；其中 #1、#2、#7 为**部分采纳**（专业分级量表/模型判别、记忆值白名单、Origin 白名单与鉴权未落地）。

### 缺陷与修复清单

| # | 严重 | 位置(文件:行) | 问题 | 修复 / 代码改动 | 验证 |
|---|---|---|---|---|---|
| 1 | 高 | server/mock_api.py:40-45, 216-239 | 危机拦截只看最后一条 user 消息且词表窄，"跳楼/结束自己/煤气/遗书/不想醒来"等不命中，漏拦后直接走娱乐模板 | `CRISIS_RE` 扩充同义词（跳楼、结束自己、煤气、遗书、不想醒来、去死、寻短见、解脱、不想活着、活着没劲、死了算了）；`make_reply` 改为遍历 `all_user_texts` 扫描全部 user 历史命中即拦。**部分改**：未接入专业分级量表/模型判别 | test_mock_api t1「危机拦截」PASS（8/8） |
| 2 | 高 | server/mock_api.py:76, 82-94 | 昵称正则 group2 无词边界，`我叫小明今天很开心`吞成"小明今天开心"，污染长期记忆 | 昵称正则字符数收窄为 `{1,4}` 并加后向断言 `(?=\b\|[，。！？,.!?\s]\|$)`。**部分改**：未做候选值白名单校验 | test_mock_api t3「记忆提取」PASS（8/8×2） |
| 3 | 中 | server/mock_api.py:77, 88-89 | "老板"记忆取值恒为 group2（"老板/领导/上司"字面），group3 姓名从未使用 | `extract_memory` 中 `key=="老板"` 时改取 `m.group(3).strip() or m.group(2).strip()` | 静态检查 |
| 4 | 中 | server/mock_api.py:247-251 | `extract_memory(user_text, {})` 传空 old、结果不落库，跨轮记忆实际不存在 | `_State` 每会话新增 `memories`；`make_reply` 读取 `_STATE.get_memories(session_id)` 传入，有更新时 `update_memories` 写回 | test_mock_api t3（改用唯一 `session_id`），8/8×2 |
| 5 | 中 | server/mock_api.py:161-209 | `_STATE` 进程级全局单例、无会话隔离，多用户互相覆盖 `last_topic` | `_State` 重构为 `_sessions` 字典（按 `session_id`）+ `threading.Lock` + TTL 清理（>3600s，每 300s 清）；`session_id` 取自 body 或 `X-Session-ID` 头 | test_mock_api 8/8×2（隔离+幂等） |
| 6 | 中 | server/mock_api.py:24, 36, 306-312, 347 | 单线程 `HTTPServer` + 请求内 sleep，Content-Length 无上限，慢请求阻塞全部连接、超大 body 撑爆内存 | 换 `ThreadingHTTPServer`；新增 `MAX_BODY_SIZE=64KB`，超限回 413；`int()` 包 try 回 400、负值/超限回 413 | 静态检查 |
| 7 | 中 | server/mock_api.py:279-282, 294-296, 35, 346 | CORS 全开（Headers/Methods `*`）、绑定 0.0.0.0、无鉴权，局域网任意页面可跨域读取隐私语料 | **部分改**：Allow-Headers 收敛为 `Content-Type, Authorization`、Methods 收敛为 `POST, GET, OPTIONS`；`BIND_HOST` 改为环境变量 `XIAOMAN_BIND_HOST`（默认仍 `0.0.0.0`）。**未改**：Origin 仍 `*`、无 token 鉴权 | 静态检查 |
| 8 | 中 | server/tts_server.py:197 | `asyncio.get_event_loop().run_until_complete(...)` + 死代码 `if True else b""`（3.10+ 告警 / 3.12+ 报错） | 改 `asyncio.run(_get_or_generate_audio(text, voice))`，删除 `if True else b""` | verify_tts.py mp3 PASS |
| 9 | 中 | server/tts_server.py:56-136 | TTS 无超时、无缓存，上游挂起无限阻塞，相同文本反复回源易触发限流 | 新增磁盘缓存（key=`sha1(text\|voice\|rate\|pitch)`，LRU 200 条、TTL 7 天、原子写入）+ `asyncio.Lock` single-flight 合并同 key 并发；`asyncio.wait_for(..., timeout=10.0)` | verify_tts.py mp3 PASS |
| 10 | 中 | server/tts_server.py:46-54, 192-193 | `voice` 无白名单校验，任意字符串传入上游触发异常并以 500 回显，可枚举探测 | 新增 `ALLOWED_VOICES`（7 个 zh-CN 音色 frozenset），非法直接 `_error(400, invalid voice)` | 静态检查 |
| 11 | 中 | server/mock_api.py:326-331; server/tts_server.py:205-210 | `str(e)` 原样返回客户端，泄露文件路径/代理 URL/库版本等内部实现 | mock：`JSONDecodeError`→400 `invalid JSON`，其余 `logger.exception`+500 `internal server error`；tts：`RuntimeError`→500 `tts synthesis failed`，其余 500 通用文案；新增 `_error()` 统一出口，细节仅入日志 | 静态检查 |
| 12 | 中 | server/mock_api.py:60-71, 234-239 | 危机分支固定话术每轮一字不差，体验机械、易被识破 | 新增 `CRISIS_SCRIPTS` 3 套变体；`increment_crisis(session_id)` 计数后按轮次 `min(count-1, len-1)` 选变体（`CRISIS_SCRIPT` 保留为第 0 套兼容旧测试） | test_mock_api t1 仍含 12356 PASS |
| 13 | 低 | server/mock_api.py:17-24 | 模块级 `random.seed(int(time.time()))`，同秒启动随机序列一致、测试不可复现 | 删除模块级 `random.seed(...)`，改用系统默认随机源 | 静态检查 |
| 14 | 低 | server/mock_api.py:271-273; server/tts_server.py:140-141 | `log_message` 被静默吞掉，无访问/错误日志，排障困难 | 两服务新增 `logging.basicConfig` 与 logger；`log_message` 改 `logger.info` 记录地址/时间/请求行，关键路径补 debug/error | 静态检查 |
| 15 | 低 | server/tts_server.py:170-181; server/mock_api.py:302, 337 | 404/路径归一化不一致（tts 用 HTML `send_error`、`/health/` 尾斜杠不归一） | 两服务统一 JSON 错误（`_error`/`_json` 取代 `send_error`）；tts 用 `urlparse().path.rstrip("/")`、mock 用 `self.path.rstrip("/")` | test_mock_api t6「404 JSON」、t7「204 预检」PASS |

### 代码改动说明（按文件）

- `server/mock_api.py` → 加宽危机词表并全历史扫描；记忆提取加词边界、"老板"取姓名 group3、按 `session_id` 读写记忆；`_STATE` 重构为带锁/TTL 的多会话状态；换 `ThreadingHTTPServer` + 64KB body 上限 + 统一 JSON 错误（不再回显 `str(e)`）+ 移除 `random.seed`；危机话术 3 套变体。
- `server/tts_server.py` → 新增 voice 白名单（非法 400）、sha1 磁盘缓存 + single-flight 并发合并 + 10s 超时；`asyncio.run` 取代 `get_event_loop().run_until_complete` 并删死代码；错误统一 JSON 且不回显异常细节、路径尾斜杠归一。

### 小结

15 条全部有改动：mock_api 补齐危机全历史扫描与话术变体、记忆边界/"老板"取值/按会话落库、多会话隔离、线程化与请求体上限、错误脱敏与日志；tts_server 补齐超时、磁盘缓存 + single-flight、voice 白名单与错误脱敏。**遗留风险**：危机专业分级、记忆值白名单、CORS Origin 白名单/鉴权未落地。

---

## 四、web/js（前端逻辑）

审计 28 条，核心缺陷全部消除；#16/#19/#27 的**附加建议**未采纳（核心缺陷已修）。

### 缺陷与修复清单

| # | 严重 | 位置(文件:行) | 问题 | 修复 / 代码改动 | 验证 |
|---|---|---|---|---|---|
| 1 | 高 | app.js:49,52,56,74,85 | addMsg/renderMem 用 innerHTML 拼接模型回复、记忆键值、提醒文本 → 远程输出含 `<img onerror>` 即 XSS | addMsg 三态（危机卡/system-tip/普通）与 renderMem（记忆 chips + 提醒清单 + 取消按钮）、showHook 全部改 `createElement` + `textContent`/`append`，弃用 innerHTML 拼接 | Playwright XSS 回归：注入节点 0、无 pageerror |
| 2 | 高 | app.js:313 + heartbeat.js:109 | due() 先置 done，pushFn 回调 `if(busy)return` 静默丢弃 → 提醒被标记完成却永不到达 | reminders 新增 `markDone(ids)`，`due()` 只返回到期项不置 done；heartbeat.tick 逐条 `await pushFn`，成功才 markDone；app.js 对 `kind==="reminder"` 遇 busy 抛错触发重投 | test_lifeline_ui REMINDER_FIRE PASS（5/5） |
| 3 | 高 | stage.js:13 | 模块顶层 `new PIXI.Application`，vendor 未就绪即抛异常令整个 IIFE 失败 → App.init 中断，只剩死输入框 | PIXI.Application 移入 `mount()` 内懒加载 + try/catch；失败渲染 `.stage-fallback` 可见占位，跳过模型/事件，主流程不受影响 | Playwright 舞台降级：拦截 vendor → 对话照常、无 pageerror |
| 4 | 中 | api.js:117-121, 90 | auto 每条先打 `127.0.0.1:8902` 再打 openai；base 为空时 fetch 相对路径打到页面自身 → 必败往返、无超时可挂 30s | 新增 `fetchWithTimeout(url,opt,5000)`（AbortController 5s）；base 为空时直接 throw 跳过；auto 改「有 apiKey 先 openai → mock → 离线引擎」，每级 try 内降级 | 静态检查；test_e2e ②/④ |
| 5 | 中 | app.js:172-180 | sendSplit 无 busy 互斥，greet/心跳/poke/hook 直调 → 与用户发送并发，气泡交错、history 乱序 | 新增**可重入发送锁** `withSendLock`（`lockDepth` 深度计数：外层 `while(sending)` 排队占锁、嵌套同链放行、finally 释放并唤醒 `sendQueue`），send 与 sendSplit 全程包锁 | test_e2e ②；曾用 Playwright 复现死锁后修复 |
| 6 | 中 | heartbeat.js:110 | 多条提醒 pushFn 未 await、未串行 → 并发 sendSplit 顺序错乱 | tick 内 `for (const r of fired)` 逐条 `await pushFn`，成功即 markDone、失败留下轮重试 | test_lifeline_ui REMINDER_FIRE |
| 7 | 中 | heartbeat.js:106,144 | setInterval 驱动 async tick，generate 内无超时 → 上轮未返回即重入，日限/2h 闸门被绕过 | tick 首部 `if(!pushFn||running) return;`，`running=true` … `finally { running=false }` 串行防重入 | 静态检查；test_lifeline_ui |
| 8 | 中 | heartbeat.js:81 | 仅 `mode==="openai" && apiKey` 才走模型，默认 auto 配置被跳过 | 门槛改 `if (cfg.apiKey && cfg.mode!=="local")`，auto 只要配了 key 也走模型生成 | test_lifeline_ui HEARTBEAT_GEN PASS |
| 9 | 中 | tts.js:47-54 | playWithLipSync 异步失败不抛错，外层 catch 永不触发 → server TTS 挂了却不降级、静默无声 | playWithLipSync 增 `onFail` 参数；`audio.onerror → settle(true) → onFail`；server 路 `onFail` 显式调 `speakBrowser` 降级；新增 `settled` 防重复回调 | test_e2e ③/④：TTS 请求落 8903 |
| 10 | 中 | tts.js:61,74 | Android/browser 假包络 setInterval 与 stop()/新一次 speak() 无关 → 多 interval 叠加、口型乱抖、onEnd 重复 | 全局唯一 `fakeTimer`/`fakeStopTimer` + `clearFake()`；`speak()` 开头先 `stop()`；browser/Android 各自 clearFake 后起定时器 | 静态检查 |
| 11 | 中 | tts.js:25-29 | AudioContext 在 play().then 内创建，非用户手势可能 suspended → analyser 全 0、语音在播嘴不动 | 创建后 `if(ctx.state==="suspended") ctx.resume().catch(()=>{})`；分析失败仍 `onFrame(-1)` 走随机兜底 | 静态检查 |
| 12 | 中 | stage.js:58 | `motionManager.on("motionFinish",()=>{})` 空实现且 motManager 未必是 EventEmitter → 抛错被吞成"Live2D 加载失败" | 直接删除该行 | Playwright 舞台降级 / test_e2e |
| 13 | 中 | app.js:160-161 | data.crisis 字段全程未消费 → 模型识别的危机只出普通气泡，热线卡不显示 | sendSplit 开头判 `if (data.crisis)` → `addMsg(CARE_SCRIPT,"them",{crisis:true})` + `Stage.setEmotion("gentle")` + history 补 at | test_e2e ⑥ 危机热线 12356 PASS |
| 14 | 中 | reminders.js:82 | 顺延判定只排除 `明天\|后天\|周\|星期\|YYYY-`，"今天/今晚/X月X日"未排除 → 过点变明天/跨年误判 | 重构 parseTime/_mk，新增 `explicitDay`/`noYear` 标志：显式日词一律不顺延，仅"裸时刻"已过顺延次日；无年份 X月X日 已过补到明年 | test_lifeline_ui REMINDER_SET |
| 15 | 中 | reminders.js:41-45 | 周X 同日强制下一周，且相对日 delta 与周X 偏移叠加 → "明天周五"双跳 8 天 | 周X 与相对日互斥（`relMatched`）；`offset=(target-today+7)%7` 同日取当天；`sameWeekday` 且时刻已过才 `+7` 天 | 静态检查 |
| 16 | 中 | memory.js:41 + app.js:188 | 同键新值 append、旧抽屉仅 merge → 改昵称后旧昵称仍以 pin 永远注入，人格记忆矛盾 | memory.js 新增 `removeByKey(key)`，`addUpdates` 先 removeByKey 再 add（同键覆盖）并跳过 `null/""`；app.js 旧抽屉保持 merge。**附加建议"保留旧值为 history"未采纳** | test_e2e ⑦/⑧ 抽屉含"阿秋" PASS |
| 17 | 中 | heartbeat.js:129-131 | historyFn 被调两次；危机路径 push 的 assistant 无 at → `last.at\|\|0=0` 使"正在聊天"闸门失效 | tick 内 `const recent = historyFn?historyFn(1):[]` 只调一次；app.js 危机分支与 sendSplit 各 `history.push` 均补 `at:Date.now()` | test_e2e ⑥；静态检查 |
| 18 | 中 | app.js:136-141 | remindCap `(.{1,50})` 贪婪 + 清洗正则连"一下/哈"一并剥除 → 文案退化 | 正则改 `(?:提醒我\|叫我\|记得让我\|别忘了让我)\s*(.+)`；拆三段清洗：先时间词、再日期/周词、最后语气词 `(一下\|哈\|吧\|呢\|嘛\|啦)` | test_lifeline_ui REMINDER_SET |
| 19 | 中 | app.js:241 | 主动 ping 走 sendSplit 而非 send：不置 busy、失败无捕获、之后不 armIdle → 静默 150s 仅靠下次 send 触发 | `armIdle` 改 async：busy 时重排、`try/catch` 捕获、结束后再 `armIdle()` 恢复 60s/150s 节奏。**附加建议"ping 复用 busy"未采纳**（并发防护由 withSendLock 承担） | test_lifeline_ui HEARTBEAT_GEN |
| 20 | 中 | tts.js:64 | AndroidTTS 估算 `max(2500, 260ms×字数)` 偏长 → 说完嘴还张 3s | 估算改 `max(1800, 字数×190)`ms，并纳入 `fakeStopTimer` 统一清理 | 静态检查 |
| 21 | 低 | api.js:66-71 | parseStructured 兜底 `reply:text`，非 JSON 空/null/截断原样进气泡 | 兜底前过滤空串及 `/^(null\|undefined\|\[\]\|\{\})$/i`，命中返回默认话术"嗯……我好像走神了…" | 静态检查 |
| 22 | 低 | mock_engine.js:81 | 昵称正则 `我[叫是]` 把"我是程序员"存成昵称 | 改 `(我叫\|叫我\|你可以叫我)\s*([一-龥A-Za-z]{1,4})(?=\b\|[，。！？,.!?\s]\|$)`，去掉"我是"、限长 4 且要求词边界；宠物正则同步收窄 | test_e2e ⑦；test_mock_api 8/8 |
| 23 | 低 | mock_engine.js:10 vs app.js:15 | 两份危机正则词条不一致 → 同一句判定不同，安全网缝隙 | app.js 与 mock_engine.js 的 `CRISIS_RE` 扩为同一集合，并同步 `mock_api.py` | test_e2e ⑥；test_mock_api 8/8 |
| 24 | 低 | heartbeat.js:115 | 提醒投递也写 lastAt 但不计 count → 一条提醒挤占 2 小时问候间隔 | 提醒分支删除 `setState({lastAt})`；lastAt 仅在闲聊 pushFn 成功后与 count 一并更新 | 静态检查 |
| 25 | 低 | memory.js:71,79-85 | 注释"每周衰减20%"实为 0.03/天；recall 每次 renderContext 都回写 localStorage | 注释订正为"约 3%/天（≈21%/周），下限 0.2"；hits 回写新增 `lastHitsSave` 5s 节流后才 save | 静态检查 |
| 26 | 低 | app.js:123,157 | history 只增不减且携带 at 整体上传 → 长会话 payload 线性膨胀 | 引入 `MAX_HISTORY=30` 滑动窗口；api.js 新增 `toMessages()` 上传前只保留 role/content（剥离 at） | 静态检查；test_e2e ② |
| 27 | 低 | app.js:204-206 | onEnd 取 last 后未使用；opts.voice 永不传入 → "🔊 语音已播" meta 是死代码 | sendSplit 用 addMsg 返回的行引用 `lastRow`，onEnd 里真正为其 `.bubble` 追加 meta（避开 typingRow）。**附加建议"opts.voice 传参"未采纳** | 静态检查 |
| 28 | 低 | stage.js:28,90 | resize 监听无销毁；expression 索引 0-5 硬编码依赖模型顺序 | 保存 `resizeHandler` 并新增 `unmount()`（移除 resize 监听 + 停 ticker + `app.destroy(true)`）；setEmotion 改用 `model.expressionManager.expressionNames.indexOf(name)` 查找，找不到才回退旧索引表 | Playwright 舞台降级；静态检查 |

### 代码改动说明（按文件）

- `web/js/api.js` → 新增 `fetchWithTimeout`（AbortController 5s）；base 为空直接抛错跳过；auto 顺序改「有 key 先 openai → mock → 离线引擎」；新增 `toMessages` 上传剥离 at；parseStructured 过滤 null/截断并给默认话术。
- `web/js/app.js` → XSS 收口（addMsg/renderMem/showHook 改 createElement+textContent）；可重入发送锁 `withSendLock`；history 补 at + `MAX_HISTORY=30`；消费 `data.crisis`；提醒文案提取与清洗收窄；armIdle 支持重排/异常保护；pushFn 对 reminder busy 抛错重投；init 分区 try/catch 降级；抽屉/弹窗 Esc+Tab 焦点陷阱与焦点归还。
- `web/js/heartbeat.js` → 模型门槛改 `apiKey && mode!=="local"`；tick 加 `running` 防重入并逐条 await pushFn，成功才 markDone；提醒不再写 lastAt；historyFn 只调一次。
- `web/js/memory.js` → 新增 `removeByKey`，`addUpdates` 同键覆盖并跳过空值；recall 的 hits 回写 5s 节流；衰减注释订正为 3%/天。
- `web/js/mock_engine.js` → 危机词表与 app.js/mock_api.py 统一扩集；昵称正则去"我是"、限长 4 且加词边界；宠物正则收窄。
- `web/js/reminders.js` → parseTime/_mk 重构 `explicitDay`/`noYear`/`sameWeekday`，周X 与相对日互斥；`due()` 不再置 done，新增并导出 `markDone`。
- `web/js/stage.js` → PIXI 懒加载 + `.stage-fallback` 可见兜底；删除 motionManager 空监听；`resizeHandler` + `unmount()`；setEmotion 按 expression name 查找。
- `web/js/tts.js` → 全局 `clearFake`/`fakeTimer`/`fakeStopTimer`，speak 先 stop；playWithLipSync 增 onFail + audio.onerror 降级、ctx.resume；Android 估算改 190ms/字。

### 小结

8 个文件均有实质改动，28 条逐条可对应到 diff。高危三项（XSS、提醒被 busy 吞、PIXI 顶层初始化崩站）已修复并有 Playwright/端到端证据。

---

## 五、web UI（index.html / style.css）

审计 25 条，已修 23 条、部分改 2 条（D18 / D22），无遗留。
（说明：D21 在文档整理时核验出「退出动画选择器不匹配、实际不生效」的真实缺陷，本轮已修复，见 D21 行；D22 的 `.drawer-mask` 类被保留但 JS 选择器仍有效，属命名冗余而非功能缺陷。）

### 缺陷与修复清单

| # | 严重 | 位置(文件:行) | 问题 | 修复 / 代码改动 | 验证 |
|---|---|---|---|---|---|
| D1 | 高 | index.html:1–9；app.js:49/52/56/74 | `innerHTML` 无转义注入模型/记忆数据，且页面无 CSP（存储型 XSS） | `index.html:5` 新增 CSP meta（default-src 'self' 等）；`app.js` 的 addMsg/renderMem/提醒渲染全部改 `createElement`+`textContent`，`innerHTML` 仅剩清空用 | Playwright 注入回归 0 节点；静态检查 |
| D2 | 中 | index.html:5 | `maximum-scale=1.0, user-scalable=no` 禁用缩放（违 WCAG 1.4.4） | viewport 改 `width=device-width, initial-scale=1.0, viewport-fit=cover`，删两个禁缩放属性 | 静态检查 |
| D3 | 中 | style.css:123（配合 97） | `.field` 一族 `outline:none` 却无 `:focus` 补偿，焦点不可见 | 新增 `:focus-visible` 环（粉描边+外环），覆盖 `.field`、`.icon-btn`、`.send-btn`、`.ghost-btn`、`.hook-chip`、`.r-cancel`、`#stage` | 静态检查 |
| D4 | 中 | index.html:65/78；app.js:286–298 | 浮层无 `role=dialog`/`aria-modal`/标题、无焦点管理、无 Esc、不归还焦点 | index.html 加 `role="dialog" aria-modal="true" aria-labelledby`，标题升级 `<h2>`；app.js 新增 `openPanel/closePanel/onKeydown`：打开聚焦首个可聚焦元素、Tab 循环、Esc 关闭、关闭后归还触发按钮 | Playwright 焦点入面板/Esc/Tab ✓ |
| D5 | 中 | index.html:45/107 | 消息流与 toast 无 live region，读屏收不到更新 | `#messages` 加 `role="log" aria-live="polite" aria-relevant="additions"`；`#toast` 加 `role="status" aria-live="polite"` | 静态检查 |
| D6 | 中 | index.html:57 | `#textInput` 无可关联标签；`#cfgApi` 未声明类型 | `#textInput` 加 `aria-label` 与 `enterkeyhint="send"`；`#cfgApi` 补 `type="url" inputmode="url"` | 静态检查 |
| D7 | 中 | style.css:15/92/57/64/129 | 次要文字/钩子芯片/图标/热线对比度不达标 | `--ink-light` 改 `#7A6A6F`；`.hook-chip` 文字 `#2F6F5C`、底色不透明度 .16→.22；`.icon-btn` 改 `#D4607A`；`.mic` 改 `#357A64`；`.hotline` 改 `#B4531B` | 静态检查（按审计建议值落地） |
| D8 | 中 | style.css:117；app.js:84–86 | `.chip` 无 `display:flex`，提醒行 `justify-content` 失效 | `.chip` 加 `display:inline-flex; align-items:center; gap:8px`，新增 `.chip.row`；app.js 提醒条目改用类 `chip row` | 静态检查 |
| D9 | 中 | style.css:109 | 抽屉无高度上限，"清空记忆"条目多时不可达 | `.drawer-panel` 加 `max-height:min(86vh,640px); overflow-y:auto`；`.modal-panel` 加 `max-height:90vh; overflow-y:auto` | 静态检查 |
| D10 | 中 | app.js:282–284 | Enter 未处理 IME 合成态；busy 时先清空导致丢字 | keydown 加 `!e.isComposing && e.keyCode!==229`；抽出 `submitText()`：busy 时 toast 且**不清空**，仅在真正接受后才清空 | 静态检查；node --check OK |
| D11 | 低 | index.html:71 | 孤儿节点 `#memoryList`（HTML 有、JS/CSS 均无用） | 删除该行 | 静态检查 |
| D12 | 低 | style.css:80 | 死样式 `.bubble.safety`（JS 无该类） | 删除该规则 | 静态检查 |
| D13 | 低 | style.css:79；app.js:55/204–206 | `.meta` 永不渲染 + `querySelector` 恒 null 的死代码 | sendSplit 用 `lastRow` 保存行引用，TTS `onEnd` 向其 `.bubble` 注入"🔊 语音已播" | 静态检查 |
| D14 | 低 | style.css:96 | `backdrop-filter` 缺 `-webkit-` 前缀 | 输入栏改 `-webkit-backdrop-filter` + `backdrop-filter` 双写 | 静态检查 |
| D15 | 低 | style.css:24/32 | 未用 `dvh`，动态工具栏下底部留白 | `html, body` 改 `height:100%; height:100dvh;`（渐进增强） | 静态检查 |
| D16 | 低 | style.css:61 | 矮视口/横屏下舞台 240px 硬约束挤压聊天区 | 新增 `@media (max-height:480px)`：`#stage` 改 `flex-basis:34%; min-height:110px` | 静态检查 |
| D17 | 低 | style.css:37,39,41,42,54,85,130 | 所有循环动画无 `prefers-reduced-motion` 降级 | 末尾新增 `@media (prefers-reduced-motion:reduce)` 压短动画/过渡 | 静态检查 |
| D18 | 低 | style.css:35/63/106/107 等 | `inset`/flex `gap`/`anywhere`/`scroll-behavior` 旧 WebKit 退化 | **部分改**：关键 `inset:0` 已改四向 `top/right/bottom/left:0`；`gap` 未补 margin 兜底、`overflow-wrap:anywhere` 与 `scroll-behavior:smooth` 未改 | 静态检查 |
| D19 | 低 | index.html:4–8/25 | 缺 `theme-color`、favicon、语义标题 | 加 `theme-color` 与内联 SVG favicon；`.brand` `<div>`→`<h1>`；抽屉/弹窗标题升级 `<h2>` | 静态检查 |
| D20 | 低 | style.css:66 | `.stage-mood{transition:all}` 隐式过渡 | 改 `transition: transform .3s, color .3s` | 静态检查 |
| D21 | 低 | style.css:119–122；app.js:405/409 | 面板退出动画：CSS 选择器落在子元素（`.drawer-panel.is-closing`），而 JS 把 `is-closing` 加在**根节点**（`.drawer`/`.modal`）→ 动画实际不命中、未生效 | **本轮修复**：选择器改为「根节点 `.is-closing` 的后代」——`.drawer.is-closing .drawer-mask` / `.drawer.is-closing .drawer-panel` / `.modal.is-closing .modal-panel`，与 app.js 加类节点对齐 | 静态检查（选择器与 JS 加类节点已一致）+ 同步 `assets/www` |
| D22 | 低 | index.html:79；app.js:288,298 | 弹窗内复用 `.drawer-mask` 语义不符 | **部分改**：新增通用类 `.overlay`，两处 mask 改 `class="drawer-mask overlay"`，CSS 合并共享规则；**未删** `drawer-mask`（JS 仍按 `.drawer-mask` 选择，类保留故功能不受影响，属命名冗余） | 静态检查 |
| D23 | 低 | app.js:86；style.css 无 | JS 创建 `.r-cancel` 无 CSS 规则、样式写死行内 | 新增 `.r-cancel` 规则及 `:focus-visible`；app.js 去行内 style、改用类名并加 `aria-label="取消提醒"` | 静态检查 |
| D24 | 低 | style.css:57/101；index.html:37 | 触控目标偏小；舞台交互不可键盘访问 | `.icon-btn` 38→44px、`.send-btn` 42→44px；`#stage` 加 `tabindex="0" role="button" aria-label`，app.js 绑 keydown：Enter/空格 → 摸头 | Playwright `#stage` 回车触发 ✓ |
| D25 | 低 | index.html:110–112；stage.js:13；app.js:305–320 | 舞台初始化失败静默降级、无可见反馈，且拖垮问候 | stage.js `mount()` 加 try/catch 插入 `.stage-fallback` 占位；vendor 脚本加 `onerror`；app.js `init()` 把 bind/Stage/问候/心跳拆独立 try | Playwright 拦截 vendor → 对话照常、无 pageerror |

### 代码改动说明（按文件）

- `web/index.html` → 加 CSP、`theme-color`、内联 favicon；viewport 放开缩放；标题升级 `<h1>/<h2>`；浮层加 `role=dialog/aria-modal/aria-labelledby`，消息流 `role=log`、toast `role=status`；`#textInput` 加 `aria-label`，`#cfgApi` 改 `type=url`，`#stage` 加 `tabindex/role/aria-label`；删 `#memoryList`；vendor 脚本加 `onerror`。
- `web/css/style.css` → 提对比度；补 `:focus-visible` 焦点环；`.chip` 改 inline-flex + `.chip.row`；抽屉/弹窗加 `max-height+overflow`；`inset` 改四向、`-webkit-backdrop-filter`、`transition` 去 `all`；新增 `.r-cancel`、`.stage-fallback`、`.overlay`、退出关键帧（D21 修为后代选择器）、`max-height:480px` 与 `prefers-reduced-motion` 媒体查询。
- `web/js/stage.js` → PIXI 懒加载 + 可见兜底占位；setEmotion 按 `expressionNames` 查找；新增 `unmount()`；移除无效 `motionFinish` 空绑定。

### 小结

25 条中 23 条已落实，2 条为部分改（D18 只改关键 `inset`；D22 未删旧类，功能不受影响）。D1（XSS+CSP）、D3/D4（可访问性）、D7（对比度）、D9（抽屉可滚动）、D21（退出动画）、D24（舞台键盘化）、D25（降级兜底）为本模块重点。

---

## 六、android（WebView 壳 + 构建 + CI）

审计 26 条，已修 19 条、部分采纳 6 条、遗留 1 条（**Gradle Wrapper 未补**，D15）。其中 D3/D9/D22 于第二轮 `fix/remediation-r2-20261006` 由「部分」转「已修」。

### 缺陷与修复清单

| # | 严重 | 位置(文件:行) | 问题 | 修复 / 代码改动 | 验证 |
|---|---|---|---|---|---|
| D1 | 高 | `MainActivity.java:18` / `MainActivity.kt:18` | 同包同名双 Activity，Gradle 必触发 `Duplicate class`；两份行为不一致 | 删除 `MainActivity.kt`；Java 版回填 .kt 的 `setLanguage` + 完成回调，成为唯一实现 | 工作区已无 .kt；仅编译 `.java` |
| D2 | 高 | `AndroidManifest.xml:3` | Manifest `package` 与 AGP 8.4.1 冲突 | 删除 `package`/`versionCode`/`versionName`，命名空间由 `build.gradle.kts` 的 `namespace` 提供 | XML 解析通过 |
| D3 | 高 | `AndroidManifest.xml:4-5` vs `build.gradle.kts:14-15` | versionCode 双源（3 vs 1） | Manifest 版本属性已删；`build_apk.sh` 参数化 `VER_CODE=3`；**第二轮** Gradle DSL `versionCode` 1→3、`versionName` 对齐 `0.3.0`，两源一致 | 静态检查：grep 两处均为 3 |
| D4 | 高 | `android-ci.yml:31-37`；`README.md:8` | `assets/www` 与 `web/` 漂移，CI 出包前不同步 | 新增 CI 步骤 `Sync web assets into android assets/www`（build 与 release 均先跑）；`scripts/build_assets.sh` 重写同步逻辑 | `diff -rq web assets/www` 已对齐（仅剩有意排除的 `package*.json`） |
| D5 | 高 | `MainActivity.java:44` | WebView 远程调试无条件开启 | 仅当 `(flags & FLAG_DEBUGGABLE)!=0` 时才 `setWebContentsDebuggingEnabled(true)` | 静态检查 |
| D6 | 高 | `android-ci.yml` + `build_apk.sh:48-56` | 签名密钥明文入库且每次重建 | CI 新增 secrets 注入（`ANDROID_KEYSTORE_B64` 等）并解码 keystore；但手工 debug 路径仍新建 keystore、`KS_PASS` 默认明文 → **部分采纳** | 静态检查 |
| D7 | 高 | `build.gradle.kts:17-21` | release 无签名、无混淆 | release 增 `isMinifyEnabled/isShrinkResources/proguardFiles` + 新增 `proguard-rules.pro` + `signingConfigs.release`（读环境变量）；CI 新增 `assemble-release` job 并 `apksigner verify` | YAML / 静态检查 |
| D8 | 中 | `MainActivity.java:29` | TTS 未设中文，`available()` 误报 | init 回调加 `setLanguage(Locale.SIMPLIFIED_CHINESE)` 并校验语言可用性，不支持则 `ttsReady=false` | 静态检查 |
| D9 | 中 | `MainActivity.java:50-69`；`web/js/tts.js` | 桥接无"说完"回调，靠估算 | 原生端 `UtteranceProgressListener.onDone/onError` → `evaluateJavascript("window.__ttsEnded()")`；**第二轮**前端在 `tts.js` 订阅 `window.__ttsEnded`：`speakAndroid` 注册完成回调 + `settled` 去重（原生回调与 190ms/字估算兜底只结算一次），`stop()` 清空回调防串台 | Playwright 桩测（§八）：原生回调 onEnd 恰一次、重复 `__ttsEnded()` 去重、兜底路径亦一次、pageerror 0 |
| D10 | 中 | `android-ci.yml:45-63` | emulator job 重建，测的不是发布包 | 改为 `actions/download-artifact@v4` 下载 `xiaoman-treehole-apk`，删除重复构建步骤 | YAML 结构 |
| D11 | 中 | `android-ci.yml:71` | APK 文件名写死 | 改 `APK_PATH=$(ls …/debug/*.apk \| head -1)` 动态取包 | YAML 结构 |
| D12 | 中 | `android-ci.yml:73-74,77` | 对未声明权限 `pm grant` + 盲点权限页 | 删除 `READ_PHONE_STATE/WRITE_EXTERNAL_STORAGE` 的 `pm grant` 及"权限页 CONTINUE" tap | YAML 结构 |
| D13 | 中 | `android-ci.yml:77-89` | 冒烟无断言 + 坐标/输入脆弱 | 加 `pidof` 进程断言（失败 exit 1）、`logcat` FATAL/Exception 断言、`uiautomator dump` 文本检查（仅 WARN）、`profile: pixel_4`、`input text` 用 `%s`；坐标仍写死 → **部分采纳** | YAML 结构 |
| D14 | 中 | `android-ci.yml:91-95` | 截图上传无 `if: always()` | 截图上传加 `if: always()` + `if-no-files-found: ignore` | YAML 结构 |
| D15 | 中 | `android/`（无 `gradlew`） | 缺 Gradle Wrapper，Gradle 配置从未被 CI 验证 | **未采纳（遗留）**：`ls android/gradlew*` 仍无；README 改为推荐手工流水线、把 Gradle 降级为可选。→ 两条构建链（手工 `build_apk.sh` vs Gradle）仍未统一 | 静态检查：Wrapper 仍缺失 |
| D16 | 中 | `AndroidManifest.xml:13` | 全局放行明文，默认后端 `127.0.0.1` | 移除 `usesCleartextTraffic`；新增 `network_security_config.xml`（仅 `10.0.2.2/127.0.0.1/localhost` 放行，`base-config` 禁明文）。默认 `apiBase/ttsBase` 仍 `127.0.0.1` → **部分采纳** | XML 解析；静态检查 |
| D17 | 中 | `app/build.gradle.kts:32-35` | 依赖只服务不生效的 Kotlin 版 | 删除 `appcompat`/`core-ktx` 依赖与 `kotlin.android` 插件（app 与根 build.gradle.kts 均删） | 静态检查 |
| D18 | 中 | 根 `小满树洞-v0.3-debug.apk` | 5.3MB APK 入库 | `.gitignore` 增 `*.apk`/`*.aab`；**本轮补**：`git rm --cached` 取消对根 APK 的跟踪（文件保留在磁盘、已被 ignore） | `git ls-files "*.apk"` 现为空 |
| D19 | 低 | `AndroidManifest.xml:20` | `configChanges` 覆盖不全 | 补 `screenLayout\|smallestScreenSize\|density\|uiMode\|locale\|fontScale` | XML 解析 |
| D20 | 低 | `MainActivity.java:41-42` | 未实现 `onRenderProcessGone` | `WebViewClient` 覆写 `onRenderProcessGone` → 记录日志、`recreateWebView()` 重建、返回 `true` | 静态检查 |
| D21 | 低 | `MainActivity.java:72-74` | `onBackPressed` 已废弃，未适配预测式返回 | **部分采纳**：本项目是零依赖纯 Java 壳（手工 ecj，不引 androidx），故不迁移 `OnBackPressedDispatcher`；保留覆写并加 `@SuppressWarnings("deprecation")`，行为等价 | 静态检查 |
| D22 | 低 | assets 无用文件进 APK | `docs/` 截图与 `.orig` 被打包 | `build_assets.sh` 增 `--exclude='assets/audio/selftest.*'` 并删除两个 selftest 音频；**第二轮**补 `--exclude='docs/shots'`、`--exclude='*.orig'` 并重跑同步 | 同步后 `find assets/www -name '*.orig'` 为空、`assets/www/docs/shots` 不存在；`ASSETS_SYNCED 5.8M` |
| D23 | 低 | `android-ci.yml` 全局 | 吞错、无权限约束、无缓存、无 PR 触发 | 加 `permissions: contents: read`、`concurrency`、`pull_request` 触发、`actions/cache`（Gradle/SDK）、去掉 sdkmanager 的 `\|\| true` | YAML 结构 |
| D24 | 低 | `android-ci.yml:39-43` | artifact 缺保护参数 | Debug/Release artifact 均加 `if-no-files-found: error` + `retention-days` | YAML 结构 |
| D25 | 低 | `MainActivity.java:34-46` | WebView 安全/状态未显式固化 | 显式 `setAllowFileAccessFromFileURLs(false)`/`setAllowUniversalAccessFromFileURLs(false)`/`setAllowContentAccess(false)`/`setGeolocationEnabled(false)`；新增 `onSaveInstanceState`→`saveState` 与 `restoreState` | 静态检查 |
| D26 | 低 | `gradle.properties:1-4` | Gradle 参数为沙箱刻意限流 | `gradle.properties` 加注释说明并保留沙箱值；CI `GRADLE_OPTS=-Dorg.gradle.workers.max=4 -Xmx2g` 覆盖 → **部分采纳（值未改，CI 覆盖）** | 静态检查 |

### 代码改动说明（按文件）

- `android/app/src/main/java/com/xiaoman/treehole/MainActivity.java` → TTS 中文语言校验 + `UtteranceProgressListener` 完成回调；显式关闭 fileURL/universal/content 访问、`onRenderProcessGone` 重建、debug-only 调试开关、`onSaveInstanceState/restoreState`；保留覆写 `onBackPressed`（零依赖）。
- `android/app/src/main/java/com/xiaoman/treehole/MainActivity.kt` → 整文件删除（消除同包同名 `Duplicate class`）。
- `android/app/src/main/AndroidManifest.xml` → 删 `package`/`versionCode`/`versionName`/`usesCleartextTraffic`；加 `networkSecurityConfig` 与 `tools:targetApi`；`configChanges` 补全。
- `android/app/src/main/res/xml/network_security_config.xml` → 新增：仅 `10.0.2.2/127.0.0.1/localhost` 放行明文，`base-config` 禁明文（注释说明 `<domain>` 不支持 CIDR）。
- `android/app/build.gradle.kts` → 去 Kotlin 插件与 `appcompat/core-ktx` 依赖；release 开混淆/资源压缩、加 `signingConfigs.release`（读环境变量）。
- `android/app/proguard-rules.pro` → 新增：keep `Bridge` 的 `@JavascriptInterface`、`android.webkit.**`、`android.speech.tts.**` 及注解/枚举。
- `android/build.gradle.kts` → 删除 `org.jetbrains.kotlin.android` 插件声明。
- `android/gradle.properties` → 增沙箱限流说明注释（值未改）。
- `android/README.md` → 生效实现更正为 `MainActivity.java`；构建步骤改为 `build_assets.sh` → `build_apk.sh`，Gradle 列为可选。
- `android/app/src/main/assets/www/**` → 整体同步对齐 `web/`；删除 `assets/audio/selftest.{mp3,wav}`。
- `.github/workflows/android-ci.yml` → 加 `permissions`/`concurrency`/`pull_request`/`actions/cache` 与 secrets；新增 assets 同步步骤；新增 `assemble-release` job；`emulator-smoke` 改为下载 artifact + 加断言 + `if: always()` 截图；新增 `web-tests` 门禁 job。
- `scripts/build_assets.sh` → 源/目标目录不存在时显式报错退出；tar 排除增 `selftest.*`。
- `scripts/build_apk.sh` → 版本参数化（`VER_CODE/VER_NAME`）；强制 `ANDROID_HOME`；ecj 回落缓存 + SHA-256 校验；编译错误不再被吞；`d8` 改 `find -print0 | xargs -0`。

### 小结

两条**构建阻断项**已拆解：`MainActivity.kt` 删除消除同包同名冲突（D1/D17），Manifest 移除 `package` 使 AGP 8.4.1 不再因命名空间冲突报错（D2）；**assets 漂移（D4）**经 CI 强制同步 + `diff` 复核已对齐（修复前 APK 内置的过期前端有 TTS 打错端口、清记忆漏清等）。**第二轮**再补齐三处原「部分采纳」：versionCode 两源归一为 3（D3）、前端订阅原生 TTS 完成回调 `window.__ttsEnded`（D9）、assets 同步排除 `docs/shots` 与 `*.orig`（D22）。**遗留**：Gradle Wrapper 未补（D15）；**部分采纳**余下集中在密钥治理（D6）、冒烟断言强度（D13）、默认后端明文值（D16）。

---

## 七、scripts（构建 / 测试 / 运维脚本）

审计 54 条，**全部闭环**。其中 #54（测试未接入 CI）原被记为未完成，已由本轮在 `.github/workflows/android-ci.yml` 新增 `web-tests` job 补齐。

### 缺陷与修复清单

| # | 严重 | 位置(文件:行) | 问题 | 修复 / 代码改动 | 验证 |
|---|---|---|---|---|---|
| 1 | 高 | backup.sh:8 | tar 错误被 `2>/dev/null \|\| true` 吞掉且恒打印 BACKUP_OK | 去掉屏蔽与 `\|\| true`；`set -e` 下 tar 失败即中断；成功后 `[ -s "$OUT" ]` 校验产物非空否则 exit 1 | bash -n |
| 2 | 低 | backup.sh:4,7 | LABEL 未清洗、未建 backups 目录 | `LABEL=${LABEL//[^A-Za-z0-9._-]/_}` 清洗；先 `mkdir -p backups` | 静态检查 |
| 3 | 中 | backup.sh:7-8 | backups/ 未 gitignore，10MB+ 归档入库 | `.gitignore` 新增 `backups/`（含 `*.apk`/`*.aab`） | 静态检查 |
| 4 | 高 | build_apk.sh:9 | SDK 缺省指向他机 `/home/z/android-sdk`，本机必挂 | 删除回落；`: "${ANDROID_HOME:?请设置…}"` 未设即可诊断报错并非零退出 | 静态检查 |
| 5 | 中高 | build_apk.sh:34-35 | ECJ 缺省他机路径 + 从镜像下载无校验 | ECJ 缓存到 `$ROOT/.cache/ecj-3.33.0.jar`；下载后 `sha256sum` 强制比对，不符即删并退出 | 静态检查 |
| 6 | 中 | build_apk.sh:32,36 | 编译错误被 `\| rg … \|\| true` 吞掉且隐式依赖 rg | `set -euo pipefail`；用 `PIPESTATUS[0]` 判退出码，失败打印日志并 exit | 静态检查 |
| 7 | 低 | build_apk.sh:40 | `$(find … '*.class')` 未引号，word-splitting/ARG_MAX | 改 `find … -print0 \| xargs -0 d8 …` | 静态检查 |
| 8 | 低 | build_apk.sh:25,56 | 版本号三处独立硬编码易漂移 | 顶部 `VER_CODE/VER_NAME` 单一变量，aapt2 与输出名同源 | 静态检查 |
| 9 | 低(安全) | build_apk.sh:51-55 | keystore 密码明文写死并随仓库公开 | `KS_PASS="${KS_PASS:-xiaoman2026}"` 支持环境变量覆盖并注明仅 debug | 静态检查 |
| 10 | 低 | build_assets.sh:4-5 | cd 失败无错误信息直接中止 | `cd … \|\| { echo "❌ 目录不存在"; exit 1; }` | 静态检查 |
| 11 | 中 | build_assets.sh:7 | 同步含 selftest 测试产物并随 APK 分发 | tar 增加 `--exclude='assets/audio/selftest.*'` | 静态检查 |
| 12 | 低 | dev_up.sh:38-41 | `launch()` 定义后从未调用 | `ensure_up` 改为调用 `launch()`，消除死代码 | 静态检查 |
| 13 | 中 | dev_up.sh:63-66 | 幂等只看端口监听，不校验监听者身份 | 端口已监听时用 `/health`（mock/tts）或 `/index.html` 指纹校验，不匹配即报「端口被陌生进程占用」并非零返回 | 静态检查 |
| 14 | 中 | dev_up.sh:67 | 后台服务不记 PID，停止只能靠文本匹配 | 启动写 `/tmp/xiaoman_<svc>.pid`；dev_down 优先按 PID（SIGTERM→SIGKILL），pkill 仅兜底 | 静态检查 |
| 15 | 低 | dev_up.sh:40,67 | 日志追加固定 /tmp、新旧混排且全局可预测 | 日志改仓库 `logs/`（已 gitignore）；启动前 `: > "$log"` 截断 | 静态检查 |
| 16 | 中 | dev_down.sh:11,22-24 | `pkill -f` 模式宽且与启动命令文本耦合 | PID 文件优先；兜底模式收窄为 `mock_api\.py`/`tts_server\.py`/`http\.server $PORT_WEB`；**第二轮**端口与描述全部取自 `env.sh` 变量，去掉 `8901` 字面量 | 静态检查 + `bash -n` |
| 17 | 低 | dev_down.sh:26-27 | 停止后不复检端口、恒 exit 0 | 逐端口 `/dev/tcp`+curl 复检，仍有占用则列出并非零退出 | 静态检查 |
| 18 | 高 | fetch_model.py:11,35 | 依赖不存在的 `/tmp/hiyori_test.json`，脚本非自包含 | 模型清单内置为脚本内 `MANIFEST` 常量，移除外部临时文件依赖 | grep 无 `/tmp/hiyori_test` |
| 19 | 高 | fetch_model.py:34-35 | 无条件覆盖 model3.json，冲掉 surgery 手术成果 | 仅当不存在或 `--force` 才写；`.orig` 缺失才备份；写入前 diff 提示 | 静态检查 |
| 20 | 中 | fetch_model.py:13 | 直接下标取值，字段缺失/None 即 KeyError 或拼出 /None | 改 `fr.get(k)` + 过滤空值，缺失时告警跳过 | 静态检查 |
| 21 | 中 | fetch_model.py:29 | `urlretrieve` 无超时；全失败仍 exit 0 | `download_with_timeout(timeout=30)`；记录 fail，末尾 `if fail: sys.exit(1)` | 静态检查 |
| 22 | 中 | refetch_texture.py:23-36 | 全镜像失败只打印 DONE、exit 0 静默失败 | 结束时无 SAVED 则打印「所有镜像均失败」并 `sys.exit(1)` | 实测 SAVED/PASS |
| 23 | 低 | refetch_texture.py:14-21 | 判损仅黑像素启发式，无基准 | 固化 `EXPECTED_SIZE` + 真实 `EXPECTED_SHA256`，尺寸+SHA 双校验 | 实测 PASS |
| 24 | 低 | refetch_texture.py:17 | `list(getdata())` 物化约 400 万元素 | 改 `img.tobytes()` 分块采样（每 10 像素采 1） | 实测 PASS |
| 25 | 低 | refetch_texture.py:15 | PIL 延迟导入，缺包时下载完才报错 | `from PIL import Image` 提至模块顶部快速失败 | 静态检查 |
| 26 | 低 | shot.py:48 | `page.screenshot(...) if False else None` 残留 | 删除该行 | 静态检查 |
| 27 | 中 | shot.py:4-7,43,58 | docstring 漏 crisis；未知步骤仍打印 SHOT_OK 假成功 | docstring 补 crisis；新增 `else: 未知步骤 + sys.exit(1)` | 静态检查 |
| 28 | 中 | shot.py:15,25,31-37 | 端口硬编码 + 固定 sleep 不探测就绪 | 先 `wait_ready(URL)`；改 `domcontentloaded`+`wait_for_selector`；端口来自 env.sh | 静态检查 |
| 29 | 中 | shot.py:50-55 | console/pageerror 仅打印、恒 exit 0 | 统计 errors，`SHOT_STRICT=1` 时非空即 `sys.exit(1)`（默认仅打印） | 静态检查 |
| 30 | 高 | surgery.py:55 | 每次运行用当前 model3 覆盖 `.orig`，摧毁回滚备份 | 仅当 `.orig` 不存在才备份，否则以原件重放（幂等），始终以 `.orig` 为输入 | 实测：连跑两次 `.orig` 哈希恒等 |
| 31 | 中 | surgery.py:61-67 | Motions 取值 KeyError / 空列表 ZeroDivision·IndexError | `fr.get("Motions",{})` + 过滤空值；缺失时打印明确错误并 `return 1` | 静态检查 |
| 32 | 低 | surgery.py:76-109 | 报告 before 段为静态文案；模块级执行无 main 守卫 | before 数据从 `.orig` 实测读取；加 `if __name__=="__main__"` 守卫 | 静态检查 |
| 33 | 高 | test_e2e.py:32-81 | 打印 PASS/FAIL 但从不非零退出，门禁失效 | 收集 `results`，任一 FAIL → `sys.exit(1)`，末尾 `[SUMMARY]` | 实测 5/5；负向 EXIT=1 |
| 34 | 中 | test_e2e.py:7 | `SHOTS` 相对 cwd，与 shot.py 不一致 | `SHOTS=normpath(join(dirname(__file__),"..","docs","shots"))` | 静态检查 |
| 35 | 中 | test_e2e.py:6,23-30 | 端口硬编码、networkidle、多处固定 sleep | 先 `wait_ready`；`domcontentloaded`+条件轮询；端口来自 env.sh | 静态检查 |
| 36 | 中 | test_e2e.py:65,77 | 直取页面全局，异常即抛且浏览器泄漏 | `try/finally: await b.close()`；缺失返回 None，断言判 FAIL 不抛 traceback | 静态检查 |
| 37 | 低 | test_e2e.py:32 | 关键词启发式判定，文案微调即误报 | ② 改结构断言（`.msg-row.them`≥1 且文本>8），安慰词仅 INFO | 实测 5/5 |
| 38 | 高 | test_freemodels.py:9 | 密钥读他机绝对路径，本机必 FileNotFoundError | 移除他机路径；`ONEROUTER_API_KEY` 优先、`ONEROUTER_KEY_FILE`（默认 `/workspace/.secrets/onerouter.key`）兜底；缺失 `sys.exit(2)` | 静态检查 |
| 39 | 中 | test_freemodels.py:66-68 | 客服腔检测只在 JSON 解析失败分支，正常输出恒 False | 移入 `judge()` 开头，对原始 content 始终检测 | 静态检查 |
| 40 | 低 | test_freemodels.py:59 | `lstrip("json")` 按字符集剥离，变体不健壮 | 改正则 `re.sub(r"^```(?:json)?\s*\|\s*```$", "", s, re.I)` | 静态检查 |
| 41 | 中 | test_freemodels.py:21-26 | 模型 ID 硬编码，失败结果仍 exit 0 | 清单可经 `FREEMODEL_MODELS` 覆盖；错误占比超 `FREEMODEL_MAX_ERR_RATIO`(0.5) 则 `return 1` | 静态检查 |
| 42 | 低 | test_freemodels.py:7,82 | `sys` 未用 + 固定 sleep(10) 盲等慢 | sys 用于退出码；429 优先按 `Retry-After` 退避，间隔可配 `FREEMODEL_DELAY` | 静态检查 |
| 43 | 中 | test_lifeline_ui.py:46-48 | 派发 `xiaoman-test-ping` 事件，全仓无监听器（假步骤） | 删除该无效事件派发 | 实测 5/5 |
| 44 | 中 | test_lifeline_ui.py:51-63 | 第 5 步手工注入 DOM，真实 pushFn 链路未覆盖 | 保留视觉截图，明确注释「非真实 pushFn 链路功能断言」并打印标注 | 实测：标注输出 |
| 45 | 低 | test_lifeline_ui.py:68-76 | 第 6 步依赖前步、空列表必误报；篡改生产 localStorage | 先读 `list.length`，为 0 则 SKIP；用 Playwright 默认临时 context | 实测 5/5 |
| 46 | 低 | test_lifeline_ui.py:3,8,9,39,65 | 未用 import、函数体内 import、相对 cwd | import 提顶部；SHOTS 改 `__file__` 定位；加 `sys.exit` 门禁；端口来自 env.sh | 静态检查 |
| 47 | 低 | test_mock_api.py:48-50 | 「不含 了/只」脆断言，合法值误判 | t3 改正则 `fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9]{1,10}")` 且无残留量词；改用唯一 `session_id` 保幂等 | 实测 8/8×2 幂等 |
| 48 | 高 | verify_tts.py:33-34 | ASR 脚本写他机 `/home/z/my-project`，本机必失败 | 写 `tempfile.mkdtemp()` 并以其为 cwd 执行 node，`finally` 清理；脚本内已无 `/home/z` | grep 0 命中 |
| 49 | 低 | verify_tts.py:9,15,21 | 缺显式 import（靠间接）、`subprocess` 重复导入、sys 未用 | 顶部统一 import；删 `subprocess as sp` 重复；sys 用于退出码 | 静态检查 |
| 50 | 高 | verify_tts.py:19,43 | 体积不足不中断、所有 FAIL 仅打印恒 exit 0 | 统一 `fail(step,reason)`→`[FAIL]`+`sys.exit(1)`；体积/ffmpeg/ASR 失败均非零 | 实测：负向 EXIT=1 |
| 51 | 中 | verify_tts.py:11-18,23-25 | 产物写 `web/assets/audio` 且已被 git 追踪、随 APK 分发 | 产物迁仓库 `.cache/audio/selftest.*`；`.gitignore` 加 `.cache/` 与 `selftest*.wav`；旧产物已从 git 删除 | 实测：web/assets 时间戳未变 |
| 52 | 低 | verify_tts.py:16,24 | urlopen 无捕获、ffmpeg `check=True`、node 不检 rc 裸 traceback | 全环节 try/except，统一输出 `[FAIL] <环节>: <原因>` 并非零退出 | 实测：负向 EXIT=1 |
| 53 | 低 | 横向（7 文件端口） | 8901/8902/8903 在 7 个文件重复硬编码 | 新增 `scripts/env.sh` 单一真源（`XIAOMAN_*` 支持环境变量覆盖），bash source / Python 经 bash source 读取 | bash -n / 静态检查 |
| 54 | 中 | 链路（CI 未接入） | test_e2e/lifeline/verify_tts 未接入 CI，叠加恒 exit 0 无门禁 | `.github/workflows/android-ci.yml` 新增 `web-tests` job：`dev_up.sh` → `test_mock_api`×2/`test_lifeline_ui`/`test_e2e`/`verify_tts`（`set -e`）→ `dev_down.sh`，失败即红，`if: always()` 上传 logs/ 与 docs/shots/ | CI YAML 解析；本地同序复跑 8/8×2、5/5、5/5、mp3 PASS |

### 代码改动说明（按文件）

- `scripts/env.sh`（新增）→ 端口集中配置唯一真源，定义 `XIAOMAN_HOST/WEB_PORT/MOCK_PORT/TTS_PORT`，`${VAR:-默认}` 保留外部覆盖。
- `scripts/dev_up.sh`（新增）→ 一键起三服务：端口探测 + `/health` 指纹幂等、PID 文件、日志入 `logs/`、`bash -c 'cd "$1" && shift && exec "$@"'` 位置参数安全传参、15s 健康检查失败 exit 1。
- `scripts/dev_down.sh`（新增）→ PID 优先（SIGTERM→SIGKILL）停止，`pkill -f` 收窄兜底，逐端口复检残留则 exit 1。
- `scripts/setup_dsh.sh`（新增）→ 幂等固化 Node24 + dsh + `/app/bin/dsh` 包装脚本 + `cordis.patch.yml` + 插件 + Python 依赖（edge-tts/playwright/chromium 系统库）+ 凭据，末尾 dump-config 自检。
- `scripts/backup.sh` → tar 失败不再被吞、产物存在性校验、标签清洗、先建 backups。
- `scripts/build_apk.sh` → ANDROID_HOME 强制、ECJ 缓存+SHA 校验、PIPESTATUS 检查编译错误、`find -print0 | xargs -0`、版本单一变量、KS_PASS 可覆盖。
- `scripts/build_assets.sh` → cd 失败明确报错、排除 selftest 测试产物。
- `scripts/fetch_model.py` → 内置 `MANIFEST` 去 `/tmp` 依赖、条件写 model3.json、`.get` 防御、带超时下载且失败 exit 1。
- `scripts/refetch_texture.py` → 尺寸+SHA-256 双校验、`tobytes()` 分块采样、PIL 顶部导入、全镜像失败 exit 1。
- `scripts/shot.py` → 删死代码、补 crisis/未知步骤 exit、就绪探测+条件等待、`SHOT_STRICT` 可选门禁。
- `scripts/surgery.py` → `.orig` 仅首次备份且始终以其为输入、动作组缺失明确报错返回 1、before 实测读取、加 main 守卫。
- `scripts/test_e2e.py`（新增）→ 检查点收集 + FAIL exit 1、`__file__` 截图路径、`wait_ready`/条件轮询、`try/finally` 关浏览器、② 改结构断言。
- `scripts/test_lifeline_ui.py` → 退出码门禁、删无效事件、第 5 步标注非功能断言、第 6 步空列表 SKIP、import/路径清理。
- `scripts/test_mock_api.py`（新增）→ 8 用例契约测试、唯一 `session_id` 保幂等、t3 正则断言。
- `scripts/verify_tts.py` → `tempfile` 替代他机路径、缺 SDK 时 `[SKIP]`+exit 0、四类真实失败 exit 1、显式 import、产物迁 `.cache/`。
- `scripts/test_freemodels.py` → 密钥改环境变量/标准密钥文件、客服腔始终检测、正则剥围栏、模型清单外置+错误率门禁、`Retry-After` 退避。
- `.gitignore` → 新增 `backups/`、`*.apk`/`*.aab`、`__pycache__/`/`*.pyc`、`.cache/`、`web/assets/audio/selftest*.{mp3,wav}`、`logs/`。

### 小结

54 条全部闭环；唯一曾记未完成的 #54（CI 未接入）已由 `web-tests` job 补齐。**第二轮**清掉 `dev_down.sh` 内嵌 `8901` 字面量（改取 `env.sh` 变量）。**遗留**：Android 审计 D15 仍无 Gradle Wrapper（非 scripts 范围）；`shot.py` 的 pageerror 默认仅打印（`SHOT_STRICT=1` 才门禁），属非阻断的刻意取舍。

---

## 八、验证证据汇总

| 项 | 命令 / 方式 | 结果 |
|---|---|---|
| 契约测试（幂等） | `python3 scripts/test_mock_api.py` ×2 | **8/8、8/8**，exit 0 |
| 端到端 | `python3 scripts/test_e2e.py` | **5/5**，exit 0 |
| UI 集成 | `python3 scripts/test_lifeline_ui.py` | **5/5**，exit 0 |
| TTS 链路 | `python3 scripts/verify_tts.py` | mp3 **21168B PASS**；ASR 因缺 `z-ai-web-dev-sdk` 按契约 `[SKIP]`，exit 0 |
| 负向门禁 | 临时副本强制失败（改端口/断言） | `test_e2e`/`test_lifeline_ui`/`verify_tts`/`test_mock_api` 均 **exit 1** |
| XSS 回归 | Playwright 注入 `<img onerror>` | 注入节点 0、无 pageerror |
| 可访问性 | Playwright：抽屉焦点入面板 / Esc 关闭并归还焦点 / Tab 循环 / `#stage` 回车摸头 | 全通过，pageerror 0 |
| 舞台降级 | Playwright 拦截 `js/vendor/**` | 对话照常、无 pageerror |
| 静态检查 | `node --check`（8 js）/`bash -n`（7 sh）/`py_compile`（server+scripts）/XML/YAML 解析 | 全绿 |
| 环境固化 | `bash scripts/setup_dsh.sh` | exit 0（Node24 + dsh + 依赖 + 自检） |

**第二轮 `fix/remediation-r2-20261006` 追加证据**（同一套三服务实跑）：

| 项 | 命令 / 方式 | 结果 |
|---|---|---|
| 回归复跑 | `test_mock_api` / `test_lifeline_ui` / `test_e2e` | **8/8 · 5/5 · 5/5**，均 exit 0 |
| TTS 完成回调订阅（D9） | Playwright 桩测：注入 `AndroidTTS` 桩 + 触发 `window.__ttsEnded()` | `typeof __ttsEnded === "function"`；原生回调 onEnd **恰 1 次**、重复调用**去重**、无回调时估算兜底**恰 1 次**；pageerror 0 |
| assets 排除（D22） | `bash scripts/build_assets.sh` 后 `find`/`ls` | assets/www 内 `*.orig` 与 `docs/shots` 均不存在；`ASSETS_SYNCED 5.8M` |
| 版本双源归一（D3） | grep `versionCode`（Gradle vs build_apk.sh） | 均为 **3** |
| 环境固化 + 记忆还原 | 先移除 `/workspace/.trae/rules/` 再 `bash scripts/setup_dsh.sh` | exit 0；`cmp docs/AI_RULES.md /workspace/.trae/rules/project_rules.md` **一致** |
| 静态检查 | `node --check`（8 js）/`bash -n`（8 sh）/`py_compile`/XML/YAML | 全绿 |

---

## 九、遗留与未采纳项（如实记录）

> 处置口径：**可低风险修复的项都已修**（第二轮清零 4 项）；下列为**有意不修**（附理由）或**需外部条件/决策**的项。不修不是遗漏，是决策。

### 9.1 第二轮已清零（原遗留/部分项）

| 原项 | 处置 |
|---|---|
| android D3 versionCode 双源（Gradle 1 vs 手工 3） | 已修：Gradle `versionCode` 1→3，与 `build_apk.sh` 同源 |
| android D9 前端未订阅 `window.__ttsEnded` | 已修：`tts.js` 订阅原生完成回调并去重 |
| android D22 `docs/shots`、`*.orig` 进 APK | 已修：`build_assets.sh` 增排除并重跑同步 |
| scripts `dev_down.sh` 内嵌 `8901` 字面量 | 已修：端口/描述改取 `env.sh` 变量 |

### 9.2 有意不修及理由（助手决策，2026-10-06）

| 模块 | 项 | 不修理由 | 触发条件 |
|---|---|---|---|
| android | **D15 Gradle Wrapper 未补** | 手工 `build_apk.sh` 链路已验证可用；补 Wrapper 需下载 Gradle 发行包（体积/网络）并统一两条构建链，属架构决策，当前收益不抵风险 | 需走 Gradle/CI 标准构建时 |
| android | D6 debug 签名口令默认明文、每次重建 keystore | 仅 debug 用途，且已支持 `KS_PASS` 环境变量覆盖；release 走 CI secrets，无真实暴露面 | 若要对外分发 debug 包 |
| android | D16 默认后端仍 `127.0.0.1` | 真机联调需**用户的具体 LAN IP**（`network_security_config.xml` 要白名单该 IP），无法凭空生成；默认离线引擎可兜底 | 用户提供联调网段/IP |
| android | D13 冒烟坐标写死 / D21 `onBackPressed` 未迁移 / D26 gradle.properties 沙箱值 | D13 需真机反复校准；D21 迁移 `OnBackPressedDispatcher` 需引 androidx，违背"零依赖纯 Java 壳"约束；D26 值系沙箱刻意限流且 CI 已覆盖 | 非阻断，按需再议 |
| server | #1 危机专业分级量表/模型判别 | 需**临床/产品侧的分级标准与模型选型**（专业决策，非工程可拍板）；mock 仅离线兜底，线上主路径是真 LLM + 前端危机层 | 产品给出分级量表/合规要求 |
| server | #2 记忆值白名单 | mock 端记忆仅本地调试用途，真实记忆由前端 `memory.js` 管理（已有同键覆盖/空值跳过），加白名单收益低 | mock 若用于对外演示 |
| server | #7 CORS Origin 仍 `*`、无 token 鉴权 | 仅绑定本地/局域网的开发 mock；收紧 Origin 会打断本地联调与 Playwright 测试，需先定鉴权方案 | 若要暴露到不可信网络 |
| web/js | #16/#19/#27 附加建议（旧值保留为 history / ping 复用 busy / opts.voice 传参） | 均为"锦上添花"：核心缺陷已修；#16 反会引入历史矛盾、#19 的并发防护已由 `withSendLock` 承担 | 出现对应体验问题时 |
| web UI | D18 `gap`/`overflow-wrap:anywhere`/`scroll-behavior` 旧 WebKit 回退 | 目标运行时（现代 Android WebView / Chromium）均原生支持，补 margin 兜底反有布局回归风险，属低价值改动 | 需兼容极旧 WebView |
| scripts | `shot.py` pageerror 默认仅打印 | 设计取舍：截图工具默认不因页面告警中断，`SHOT_STRICT=1` 可开门禁 | 无 |

---

## 十、改动规模

- 提交 `1b8541e`：**60 files changed, +3716 / −1021**（含 `web/` 与 `android/.../assets/www/` 双份同步）。
- 提交 `86426d0`：新增 `scripts/git_push.sh` + DEVLOG。
- 本文件所在提交：新增 `docs/AUDIT_REMEDIATION.md`、修复 UI D21 退出动画选择器（`web/css/style.css` + 同步 `assets/www`）、取消根 APK 跟踪（`小满树洞-v0.3-debug.apk`）。

### 第二轮 `fix/remediation-r2-20261006`（本轮，多笔原子提交）

| 提交主题 | 触及文件 |
|---|---|
| fix(android): versionCode 两源归一 3 | `android/app/build.gradle.kts` |
| fix(android): assets 排除 docs/shots 与 *.orig + 重同步 | `scripts/build_assets.sh`、`android/.../assets/www/**` |
| fix(web): 前端订阅原生 TTS 完成回调 `__ttsEnded` | `web/js/tts.js`、`android/.../assets/www/js/tts.js` |
| fix(scripts): dev_down 端口参数化（去 8901 字面量） | `scripts/dev_down.sh` |
| chore(solidify): AI 长期记忆入库 + 重置自还原 | `docs/AI_RULES.md`（新增）、`scripts/setup_dsh.sh` |
| docs: 总账/日志/状态同步 | `docs/AUDIT_REMEDIATION.md`、`docs/DEVLOG.md`、`docs/PROJECT_STATE.md` |

---

## 十一、固化与文档维护（长期机制）

- **AI 长期记忆入库**：`docs/AI_RULES.md` 是 `/workspace/.trae/rules/project_rules.md` 的仓库内同源副本（沙箱重置会清掉仓库外 `.trae/rules/`）；`scripts/setup_dsh.sh` 每次运行都会把它还原到该路径（已实测：移除后重跑脚本，`cmp` 一致）。
- **变更留痕三件套**（详见 `docs/AI_RULES.md` 铁律 7）：原子提交 + `docs/DEVLOG.md` 追加 + 总账/状态更新，且**必须推云端**才算留痕。
- **文档维护**：每次会话收尾核对三件套、订正过期数字与遗留清单；文档索引见 `docs/AI_RULES.md` §7.2。
- **重置恢复**：`bash scripts/setup_dsh.sh` 一条命令恢复环境 + 记忆；凭据（`.secrets/`）需用户重新提供。见 `docs/AI_RULES.md` §7.4。
