# DECISIONS · 本轮效率整改授权清单

> 规则：测试脚本、局部断言、文档由执行方自行决定并记录于此；
> **放宽断言 / 改产品行为 / 动 CI 门禁** 必须先回报确认。

## 自主决定（已执行）

| 项 | 内容 | 理由 |
| --- | --- | --- |
| 测试脚本 | `scripts/emulator_e2e_walk.py` ⑧ 增加布局契约：提示条出现前（竖屏+横屏各一次）与出现后（竖屏+横屏）都断言输入栏在视口内；新增截图 `e2e_08_landscape_before.png`、`e2e_08_fail_500_landscape.png`；转屏后重算 WebView 偏移再取点击坐标 | 覆盖"提示条不能把输入框顶出视口"，同一条 AVD 会话内跑完 |
| 局部断言 | ⑧ 重发断言改用 `ChatPager.stats().total`（DOM 行被虚拟化封顶），并等 `busy=false` 后取 total | 原断言误判，不改产品 |
| 局部断言 | ⑧ 失败态只在断言"提示条 vs 输入栏/voiceTip 相对位置"，不新增视觉硬门 | 避免把渲染差异误报成失败 |
| 文档 | 本文件；`docs/` 中日志 20 条上限说明（上一轮已写） | 记录决策 |

## 已做的产品行为变更（需你确认）

| 提交 | 内容 | 起因 |
| --- | --- | --- |
| `b872bb2` | `web/js/app.js` send catch 顶部：`XMErrors.isQuota(e)` 时不再弹"没送出去"，保留存储备份/清理提示 | R7 的失败提示条会盖掉存储满提示，`feature_loop · 存储满` 变红 |
| `881e637`+`9842d1c` | `web/js/api.js`：配置落盘失败（配额满/隐私模式）时把配置留在内存，`loadCfg` 仅在落盘失败时用内存兜底；写成功即清空，平时仍以 localStorage 为准 | 存储满时 cfg 落盘失败退回默认 `openai`，服务端报 `mode=mock` 却每句都走代理抛 config，失败提示条盖掉存储提示 |

CI 门禁（`ci.yml` / `e2e.yml` / `android-e2e.yml` 的 job 与判定）未改动。

## 待办（未动）

- `web-tests` 的 `⑦e-4 月度回放(真实记忆索引)`：既有失败，明令不改。

---

# DECISIONS · 2026-11-07 第二轮（横屏布局 / 存储满 / greet / 时间炸弹）

用户明确指令（四项）：修横屏输入栏被顶出视口（硬线 `inputBarBottom ≤ viewportH − 8`，横竖屏同一把尺）、
存储满内存兜底批准但必须一次提示「先在内存里，重启会丢」、⑧ greet 时红时绿查根因、全仓硬编码日期改相对并加守卫。

## 1. 横屏布局（用户批准修法）

- 提示条出现时压缩聊天区：`showNotice/hideNotice` 维护 `#chatArea.has-notice`；
  `@media (orientation:landscape) and (max-height:420px)` 下 `#chatArea.has-notice #chatScroll { min-height:0 }`。
  **"横屏 chatH 保 88" 只在无提示条时成立**，提示条占用高度时才让出——没有删 88 规则。
- `.inputbar` 增加 `margin-bottom: var(--space-3)`（12px）：云端竖屏实测 inputbar 底边 = 视口底边（751.3/751），
  任何浮层误差都会越过硬线；抬高 12px 后 `b=739.3 ≤ 743`。
- 走查断言收紧（不是放宽）：`inputbar_problems` 从 `b > ih+0.5` 改成 `b > ih-8`（横竖屏都跑）；
  横屏提示条出现后额外断言输入栏不后移（>2px 判红），上移超过 40px 也判红（"位置不动或小幅上移"）。
- 保留「再发一次」按钮 82.2×34 命中检查（`fail_layout_problems` 的 `hitAtCenter` 在横屏同一网关内照跑）。
- 云端 AVD 实测（run `42373201520`，`782×363`）：提示条出现时 `inputbar.t/b = 240.3/319.3`
  （与提示条前 `319.3` 完全一致，未后移），硬线 `ih-8 = 355` 通过；`notice.b=204.3`、`retry 82.2×34 hitAtCenter=true`；
  竖屏 `inputbar.b=739.3 ≤ 743`。`#pendingBar` 在短横屏提示条期间 `display:none`（与提示条的"再发一次"语义重复）。

## 2. 存储满内存兜底提示（用户批准产品行为）

- `app.js` 新增 `window.__cfgMemFallback`：落盘失败时第一次弹 toast + 提示条
  「本机存储满了：设置我先记在内存里，重启会丢。」；后续调用只计数、不再弹（`__cfgMemFallbackCount() === 1`）。
- `__storageFull` 在配置已转内存后，后续存储提示追加「设置我先记在内存里，重启会丢。」，
  保证最后一条可见提示仍含"重启会丢"（不是静默假成功）。
- `feature_loop · 存储满` 新增断言：计数正好 1、文案含"内存"/"重启会丢"、`重启会丢` 只出现一次、再发一次后计数仍为 1。

## 3. 时间炸弹守卫（用户明确要求加门禁）

- 新增 `scripts/check_time_bombs.py`：扫描 `scripts/test_*.py`、`scripts/emulator_*.py`、
  `ui_tour.py / ux_walkthrough.py / shot.py` 里的 `YYYY-MM-DD` 字面量（注释行跳过），命中即 exit 1。
- 例外（写入脚本 docstring）：`test_memory_recall.py` 与 `web/tests/**`——纯单元测试，时间全部显式注入
  （`new Date('...')`），不读真实时钟，不是时间炸弹。
- `ci.yml` python job 增加一步 `python scripts/check_time_bombs.py`（用户明确要求"测试里写死绝对日期即红"）。
- `test_roundtrip.py` 的 `at_local` 已改为"明天 08:00"（`datetime.now() + timedelta(days=1)`）。

## 5. 抓网页命令约束（用户 2026-11-07 追加，立即生效）

- 所有 PowerShell 抓网页一律 `Invoke-WebRequest -UseBasicParsing`，或直接用 `curl.exe`；
  禁止不带 `-UseBasicParsing`（PowerShell 5.1 会弹「分析页面时可能会运行网页中的脚本代码」确认框，卡会话）。
- 自查本轮：仅两次 `Invoke-WebRequest`（`http://127.0.0.1:8901/index.html` 与 `http://127.0.0.1:8902/health` 健康检查），
  均带 `-UseBasicParsing`；`scripts/**` 与探针目录内无 `Invoke-WebRequest` 调用，无需改脚本。
- 后续探针/巡检脚本统一 `curl.exe` 或 `-UseBasicParsing`，写入脚本头注释。

## 6. ⑧ 再访 greet 复诊

- 仅加诊断、不改断言：`test_onboarding.py` 在 ⑧ 失败时打印 reload 之后的 `[probe] greet*` /
  `history restored` / `GREET_FAIL` 控制台行，以及 `xiaoman_greet_day`、today、`xiaoman_memory`、pending 数快照。
- **判定：不是随机问候分支，也不是漏称呼；是旧 ⑧ 断言的场景错误 + 弱判据。**
  - `GreetDay.decide(lastDay, todayKey, hasPending)` 是纯函数、无随机；`greet()` 文案全部由模板拼接，
    称呼/大事读 `xiaoman_memory`（profile），同一 profile 输出恒定。
  - 红 run（`9842d1c`，旧断言）在同一天 reload 后要求全文出现"上次"：同日重入按设计
    `plan.play=false`（只播"话没丢"），开场根本不发；若某条聊天回复碰巧含"上次"→ 误绿（假绿）。
    该红日志收尾文本里既无"对了，你上次说"也无"上次"。
  - 已在 `a6fe3aa` 修正：reload 前把 `xiaoman_greet_day` 回拨一天（还原"隔天再访"），
    判据收紧为"对了，你上次说"（不再接受聊天回复里的散字）。
  - 修复后连续 8 次云端 onboarding 全绿（含本次 6 连跑 42373319777 / 42373403183 / 42373487274 /
    42373572492 / 42373668121 / 42373757702），⑧ 输出稳定为
    `阿秋，中午好呀，吃饭了没 … 对了，你上次说下周要考试——怎么样啦？我一直记着呢`。

---

# DECISIONS · 2026-11-07 语音可断言线

本轮授权清单（用户明确：测试脚本、断言实现、文档由 AI 自行决定，写入本文件）。
只有「放宽断言」和「改产品降级行为」必须回去问；本轮两类都没有发生。

## 1. 失败日志结构化（voice_tip.js）

- 旧 schema：`{at, reason}`，reason 只有 `play_failed` / `start_failed`，长会话写满 20 条也只能知道「失败过 20 次」。
- 新 schema 追加可选 `detail`，白名单字段：
  `provider | error | textLen | bucket | textHash | elapsedMs | timedOut | httpStatus | attempts[]`
- **隐私红线（P1-5）不变**：不落对话原文、不落堆栈。文本只保留长度、长度分桶、FNV-1a 哈希（用于识别"同一句反复失败"）。
- `detail` 缺省时条目仍是 `{at, reason}`，旧读端 / 旧测试不受影响。
- 新增纯函数 `VoiceTip.stats(log)`：按 reason / error / 长度桶 / provider / 超时 / 重复文本哈希聚合，供排障与 e2e 产物使用。
- 长度分桶阈值：`≤20 short`，`≤60 mid`，`>60 long`（与语音回复实际长度分布对齐）。

## 2. 失败原因采样口径（tts.js）

只加诊断，不改任何播放/降级路径：

- 原因枚举：`off / muted / empty_text / no_provider / bridge_rejected / browser_unavailable /
  http_<status> / network_error / start_timeout / audio_error / play_rejected / browser_error / speak_error`
- `lastFailure.error` 保留**首个**败因（最接近根因的一次），`attempts[]` 保留最多 4 跳（含 provider / httpStatus / timedOut / elapsedMs）。
- `lastFailure` 只作探针与失败日志输入；`speak` / `speakSafe` 的返回值与 `onEnd/onSilent` 契约未变。

## 3. 离线断言实现（node 层，模拟器只做最终确认）

- `web/tests/voice_probe.js`：
  - `buildWav`：可控假 TTS，按文本时长生成 16-bit PCM（可造静音/截短）。
  - `decodeWav` / `analyze`：解出 RMS、峰值、静音占比、首尾静音、有声时长（等价于浏览器 decodeAudioData 的 PCM）。
  - `judge`：`nonzero` + `durationOk` + `truncated`。
- 阈值（与 `TTS._speechPlan` 同一文本时间线，语速 1.02）：
  - `durationOk = 0.45×expected ≤ duration ≤ 2.6×expected`
  - `truncated = duration < 0.45×expected` 或（有声段 < 55% 且尾部静音 > 30%）
  - `nonzero = rms ≥ 0.02 且 peak ≥ 0.05`
- 这些是新增断言，没有放宽任何既有断言。
- 无 TTS 环境（无 AndroidTTS / 无 speechSynthesis / 无服务端）必须判 `spoke=false`，并验证明细与失败日志照写；**不以 skip 计为通过**。

## 4. verify_tts.py：去掉「无 TTS = SKIP 通过」

- 旧行为：服务端 503 `tts_provider_unavailable` → 打印 `[SKIP]` 并 `return 0`。
- 新行为：503 时改跑离线假 TTS 的 PCM 断言（非静音 / 时长合理 / 未截断），并用 silent/short 两个负例验证判据真的会判红；任一不满足则 `exit 1`。
- 真实服务端音频路径在 ASR 之前也先做 PCM 断言（体积够 ≠ 有声音）。
- ASR 依赖缺失仍是 `[SKIP]`，但只在 PCM 断言已通过之后；退出码语义写明在脚本 docstring。

## 5. e2e 产物

`scripts/emulator_e2e_walk.py` 的 `07_voice_tip` 探针在原有 `logs/last` 之外，追加最近 5 条完整条目与 `stats`，让"失败在哪"进入 artifact，而不是只剩总数。

---

# DECISIONS · 2026-11-07 第三轮（键盘/字体/转屏 / 存储反例 / 夹具收口）

## 7. 布局矩阵（新增 scripts/test_layout_matrix.py，本地窗口内实测 42/42）

浏览器实测（414/782 宽，失败提示条 + 语音提示 + 排队条最坏叠层；硬线 inputbar.b <= ih-8）：

| 场景 | 修前 | 修后 | 硬线 |
| --- | --- | -- | --- |
| 窄屏 h=380 | — | b=368 | 372 |
| 窄屏 h=364 | b=406.2（超 50.2） | b=352 | 356 |
| 窄屏 h=340 | — | b=328 | 332 |
| 窄屏 h=284 | b=406.2（超 130.2） | b=272 | 276 |
| 横屏 782×280 | b=277.3（超 5.3） | b=236 | 272 |
| 横屏 782×300 | — | b=256 | 292 |
| 横屏 782×260/240 | — | b=248/228；状态条按既有设计让位 | 252/232 |
| 字体 large(1.125)/系统 1.3× | — | b=319，retry 可点 | 355 |
| 竖→横 12 帧瞬时 | — | 最差 over=-4（全程不出视口） | 0 |

- 根因 1：#stage 是 lex:0 0 auto（min-height 158），键盘把高度压短时不让位，溢出全部转嫁输入栏。
- 根因 2：软键盘把高度压到小于宽度后，CSS orientation 会翻成 landscape（414×364 判 landscape），
  按 orientation: portrait 写的规则不会命中。
- 修法（web/css/style.css，只加两条媒体查询）：
  max-width:560px and max-height:480px → #app #stage 压到 clamp(72px,20vh,158px) 且允许收缩；
  max-height:340px 同一范围 → #stage 整体让位。
  横屏 max-height:300px → 隐藏 .topbar（32px，唯一可让的非操作元素）。
- 既有横屏修复未回退：782×363 基线 inputbar.b=319（云端 AVD 实测 319.3）。
- 测试脚本处理：失败时的 toast（2200ms）会压在提示条「重试」上，测量前等 toast 收掉；
  ≤260px 横屏按既有设计不要求提示条/重试存在，只锁"状态条让位 + 输入栏在视口内"。

## 8. 存储满反例

	est_feature_loop.py 新增正常写盘上下文：零兜底计数、无"存储满/重启会丢"提示、配置确实写进
localStorage、刷新后仍在。本地 50/50 PASS。
存储满块补：若启动时没有自动写配置（已有配置/换端口），就显式调用一次 API.saveCfg 触发落盘失败，
避免测试依赖"启动自动保存"这一实现细节。

## 9. 夹具收口（不留可执行的危险脚本）

- 仓库内 .ps1 只有 4 个：scripts/local/{avd_local,emulator_watchdog,emu_timewindow}.ps1、scripts/gate.ps1；
  .github/workflows/* 对它们 **0 引用**（grep 验证），全部只能在宿主手动跑。
- scripts/local/emu_timewindow.ps1 补入库：此前未跟踪，缺失时两个脚本会失去时间窗判断。
- emulator_watchdog.ps1 加 **fail closed**：时间窗模块找不到就写日志并 exit 2，
  防止 limitOff 为空后在免限窗口（21:00-06:00）按 30% 误杀满负荷模拟器。
- 窗口判定实测：22:00 -> LIMIT=off、14:00 -> LIMIT=on、 6:00 -> LIMIT=on；两个 ps1 语法解析 OK。
- 本机临时诊断 .ps1（tools 下 4 个：fetch_shots/gh_api/r5_dispatch/sample_suite）已改名为 .ps1.disabled。

## 10. 软键盘压高度会让 CSS `orientation` 翻成 landscape（最容易再踩）

**现象**：同一台竖屏手机，键盘弹起后视口 414×364，`@media (orientation: portrait)` 的规则**一条都不命中**。

**原因**：`orientation` 媒体特性看的是 **viewport 宽高比**，不是设备物理方向。键盘把 `innerHeight`
压到小于 `innerWidth` 时，浏览器就把这个视口判成 landscape。于是所有按 `portrait` 写的键盘期修复
（压缩舞台、收顶栏等）集体失效，输入栏照旧被顶出视口。

**实测（本机 headless Chromium，失败提示条 + 语音提示 + 排队条最坏叠层）**：
- 414×364：`orientation: portrait` 命中 0 条 → `inputbar.b=406.2`，硬线 356，**超 50.2px**；
- 改成 `(max-width: 560px) and (max-height: 480px)` 后同条件 `inputbar.b=352`（硬线内）；
- 414×284：修前 `406.2`（超 130.2），修后 `272`（硬线 276）。

**以后怎么写**：键盘期布局判定**不要用 `orientation`**，用宽/高阈值组合
（`max-width` + `max-height`）或 JS 读 `innerWidth/innerHeight` 比值；确需方向语义时也得同时
覆盖"竖屏设备 + 键盘把高度压短"这一档。`scripts/test_layout_matrix.py` 的竖屏 860/584/484/380/364/340/284
七档就是这条的回归网。

## 11. WebView 83 不支持 `:has()`，混在选择器列表里会让整条规则作废

**现象**：布局修复在桌面 Chromium 全绿，但 App 内 WebView 83 上等于没写，输入栏照样溢出。

**原因**：老 WebView 不认识 `:has()`。当它出现在**选择器列表**里时，按 CSS 规范整条规则
（包括列表里那条本来合法的 `#stage`）都会被判无效并丢弃——不是"只丢带 `:has` 的那半条"。

```css
/* 错：WebView 83 上两条一起失效 */
#stage, #app:has(#emptyState.hidden) #stage { ... }
/* 对：拆成独立规则，带 :has 的整条被丢，普通那条照常生效 */
#stage { ... }
#app:has(#emptyState.hidden) #stage { ... }
```

**实测**：`04efed4` 拆分前，App 内同条件回到溢出态；拆分后 782×363 基线 `inputbar.b=319.3`
（AVD 终验），本地矩阵 42/42。**守卫**：`web/tests/appearance.test.js` 断言 CSS 里不出现
`#stage,\s*#app:has` 这类混写，并要求无条件规则与 `:has` 规则各存在；`node --test` 20/20。

**同日复查（同类问题第二次出现）**：舞台占位层也有混写——
`#stage .stage-canvas ~ .stage-skeleton, #stage:has(...) ..., #stage.is-fallback ... { display:none }`。
旧内核整条作废，兄弟选择器和 `is-fallback` 兜底一起失效，占位层会一直盖在画布上。
已拆成两条（`#stage:has(...)` 单独一条）。守卫升级为**全站规则扫描**：
把 CSS 按规则切开，任何"含 `:has(` 且含 `,`"的选择器列表即判红，不再只盯一条具体规则。
实测：修前混写列表 1 处 → 修后 0 处；模拟 WebView 83（删掉所有含 `:has` 的规则）后，
占位层 `display=none` 仍由兄弟选择器兜住；`node --test` **21/21 PASS**。

## 12. `test_layout_matrix` 纳入云端默认矩阵（用户批准）

- `.github/workflows/e2e.yml`：`ui_scripts=all` 追加 `test_layout_matrix`（默认 9 套），
  `ui-suites` 的 `case` 增加对应分派，截图目录走 `XIAOMAN_LAYOUT_SHOTS="$OUT/layout"`。
- **场景数**：进 CI 时 42 条断言（竖屏 7 档 ×2、横屏 6 档、字体 2 档、转屏 12 帧采样、基线与收尾），
  没有为跑快去删场景。用户随后批准**新增**"键盘收起"两条断言（分竖屏/横屏两组各 1 条 check，
  共 4 条），现为 **46 条**，原有 42 条一条未删。
- **时长**：本地单次 **14.6s**（42 条时；46 条时 **18.2s**，`ELAPSED_SEC=18.2 EXIT=0`），
  远低于 3 分钟，因此**不拆片**；云端 `ui-test_layout_matrix` 单 job **68s**
  （run `42382188323`，14:34:02Z→14:35:10Z），同样不拆片。CSS 第二轮改动后复跑
  run `42383448800`：**RESULT PASS 42/42**，job 71s（14:50:18Z→14:51:29Z）。

## 13. 同类扫描第二轮（矩阵外 15 档；42 条矩阵未改，仍 42/42）

用独立探针（不写进仓库、不改 42 条断言）补测了 42 条没覆盖的档位，**15/15 PASS**：

| 场景 | 修前 | 修后 | 硬线 |
| --- | --- | --- | --- |
| 414×484 + 系统字体 1.3× | b=499.9（超 23.9） | b=472 | 476 |
| 360×364（窄屏竖版键盘态，有提示条 + 排队条） | b=398.3（超 42.3） | b=352 | 356 |
| 667×320（横屏分栏，窄列） | b=456.2（超 144.2） | b=298.4 | 312 |
| 667×280 | b=388.2（超 116.2） | b=236 | 272 |
| 568×320 | b=371.8（超 59.8） | b=276 | 312 |
| 568×280 / 568×240 / 640×400 / 600×400 / 641×320 | — | 236 / 228 / 356 / 356 / 298.4 | 均通过 |

三处根因与修法（`web/css/style.css`）：
1. **窄屏键盘阈值 480 → 520**：字体 1.3× 时提示条/语音条变高，414×484 的 b=499.9
   需要 ih≥507.9 才放得下，480 阈值刚好漏掉 481–508 这一段。
2. **竖屏键盘期排队条让位**：360×364 判 portrait，横屏 ≤420 的"提示条出现即收排队条"
   不命中；补 `(max-width:560px) and (max-height:480px) #chatArea.has-notice #pendingBar{display:none}`，
   只在提示条出现时收起（无提示条仍显示），省 52.2px。
3. **横屏分栏左列不再写死 320px**：568–667 宽时右列被压到 182–281px，提示条文字被迫
   换行到 242/157.7px 高。改为按 40% 比例分配（≥900 宽屏 40% 已 ≥360px，桌面不变）。
   560–640 宽的横屏键盘态原先整列隐藏舞台（用户指出"角色消失是可见回退"），
   现改为**缩小版舞台** `clamp(120px, 22%, 160px)`：560 宽时右列仍有 374px，
   提示条 1 行 73.3px；560/568/600/640 × 240–480 共 32 档全绿，含字体 1.3×（最紧 560×320：b=311.5，硬线 312）。
   注：`≤340px` 舞台整体让位的旧规则收窄为 `max-width: 559px`，避免 560 宽被误伤。

回归：既有横屏基线 782×363 仍 `b=319`（AVD 319.3），`test_layout_matrix` **46/46 PASS**，
`npm test`（web）**307/307 PASS**。以上均属同一条"输入栏不得出视口"的修复族，
未放宽任何断言、未动 CI 门禁；布局让位规则属产品观感变化，已逐条报给用户裁定。

## 14. 键盘收起：舞台立刻恢复 + scrollTop 不许跳（用户裁定后的两条新断言）

- **舞台恢复**：`414×364 → 414×860`，收起后 60ms 内量到 `display=block`、高度 `72.8 → 197.8`；
  `782×300 → 782×363` 为 `268 → 263`（均 ≥158）。因为 560–640 档已改成缩舞台、不再隐藏，
  收起路径没有"先 display:none 再回来"的中间态。
- **scrollTop 跳变（真 bug，已修）**：修前 `782×300→363` 时 `scrollTop 120 → 1599`，一路被拽到底。
  根因在 `web/js/app.js` 的 `ResizeObserver`：条件写成 `lastGap <= 60 || shrunk`，
  而键盘收起时顶栏回归会让聊天容器反而变矮（clientHeight 59→54），被误判成"键盘弹起"。
  改为只在**视口自身变矮**（`nextVh < vh`）时才无条件贴底；修后同一组 `120 → 120`。
- 断言落在 `scripts/test_layout_matrix.py` §6：先把消息列表拉长（25 条克隆）保证两侧都有滚动余量，
  再置 `scrollTop=120`，收起后 60ms 采样；42 → **46 条**，本地 18.2s。

## 15. 回到底部浮钮（`docs/UI_R2.md` §三-4 遗留项落地）

- **改前观感**：上翻历史到中段时，`pinned=false`，唯一提示是列表末端的底栏「↓ N 条新消息」——
  它随内容滚出视口；此刻来新消息，屏幕上没有任何回底入口（改前截图实测 `jumpExists:false`）。
- **改后**：`#chatArea` 内、`#chatScroll` 之后放 0 高 `.jump-anchor`，44px 圆钮 `.jump-bottom`
  绝对定位在锚点上方 8px。锚点在文档流里就位于"对话区底边"（状态条/输入栏之上），
  所以无论下面叠着提示条、排队条、语音提示还是只有输入栏，浮钮都落在它们上沿；
  矩阵用矩形相交面积断言 `overlapInputbar/Notice/Pending/Voice/Retry` 全为 0。
- **显隐**：由 pager 的 `pinned` 驱动（用户上滚即显示，回底即收起），不依赖 `is-scrolled`
  这类"滚了 4px 就算"的弱条件；条数写进 `aria-label`（`回到最新消息（N 条新消息）`）。
- **平滑滚动防闪**：`scroll-behavior:smooth` 下回底途中会连续触发 scroll，若照常按位置判定，
  `pinned` 被中间帧改回 false、浮钮闪一下。`jumpLatest()` 给 800ms 程序化窗口，
  期间只等"真正贴底"再确认。
- **边角**：横屏 ≤260px 时输入栏是 `position:absolute` 浮在列底，浮钮没有"不压输入栏"的位置，
  随状态条一起让位（`.jump-anchor{display:none}`）；其余档位不受影响。
- **顺带修的真 bug（计数语义）**：`syncBars()` 的已渲染位置原为 `min(total, start+VIEW)`，
  短会话（≤VIEW）上翻后新消息进不了窗口却会被算作"已渲染"，条数恒为 0——
  底栏与浮钮都不提示。改为 `start + rendered.length`（真实挂到 DOM 的条数），
  短会话上翻后来 1 条新消息现在显示「↓ 1 条新消息」/「（1 条新消息）」。
- **验证**：`scripts/test_layout_matrix.py` 46 → **53 条**（新增 §7：上翻可见、中心可命中、
  ≥44px 触控、五类控件零相交、新消息带条数、点击贴底 gap=0、点击后收起），本地全绿；
  `web/tests/chat_pager.test.js` 新增 2 条（浮钮时序 + 短会话计数），`npm test` 307 → **309/309**。

## 16. 加载骨架 + 流式逐字（`docs/UI_R2.md` §6.4 遗留项落地）

### 16.1 骨架：静态三条线，不做 shimmer

- **改前**：`loader` 挂起时 `.window-tip` 仍是一行「正在翻出更早的消息…」，没有 `aria-busy`，
  屏幕和读屏都只能靠文案推断；`active` 状态没有任何占位结构。
- **改后**：`ChatPager.syncBars()` 的 busy 分支调用 `sketch()`，在同一个 40px 浮条里放
  3 条 `span.sk-line`（145.2 / 107.4 / 75.5px，高 3px），Node 只建一次（`__sk` 缓存），
  切回 idle 走 `clearKids()` 恢复文案。`aria-busy=true` + `aria-label="正在翻出更早的消息"`。
- **为什么不做 shimmer**：产品硬约束是「静止零动画」（`appearance.test.js` 只允许登记过的
  一次性 keyframes），无限循环的脉冲既违反约束，也会在低端 WebView 上持续唤醒合成器；
  三条静态线 + 可读语义已经表达"正在取更早的消息"。
- **为什么宽度/高度要钉死**：浮条高度必须和 idle 一致（实测 40 → 40px、顶边 352 → 352），
  否则切 busy 的瞬间会把下面 10 条消息整体推一下，正好落在「用户刚滚到顶」的时刻。

### 16.2 流式逐字：与网络到达解耦的展示节奏（`web/js/stream_typer.js`）

- **改前**：`paint()` 每次把累积文本整段写入 `bubble`。服务端/代理一次吐整段时，
  屏幕上是「0 → 97 字」一帧完成（探针 2 次更新、单帧 95 字、388ms），读起来像闪屏。
- **改后**：`StreamTyper` 只负责「这一刻该显示到第几个字」，不碰 DOM，可注入假时钟做单测。
  - 短回复 `≤20 字`：`speedFor()` 返回 `Infinity`，整段落屏——短句本来就是一句话，
    拆成 3 字一顿反而更假；网络分块到达的节奏原样保留（11 字实测 4 次更新）。
  - 长回复：`cps = min(400, 170 + 0.6 × len)`，97 字约 228cps；实测 26 帧、单帧 ≤4 字、
    494ms 补齐，和网络块到达时间基本重合，不会"先到先灌"。
  - 切后台回来：单帧计时封顶 `MAX_DT=120ms`，不会把暂停期间的欠账一次性补进一帧。
- **收尾**：流结束时 `api.js` 的最后 2 字是缓冲的，`replace(full)` 若直接 `flush()` 会有
  一次 29 字的跳变。新增 `finishSoon(160)`：按剩余欠账算补速（上限 `2×MAX_CPS`），
  在 160ms 窗口内播完，绝不瞬移半段；降级/危机改写仍用 `flush()` 立即定稿（TTS 不等动画）。
- **行入场**：新行打 `data-fresh=1`，CSS `msg-in` 200ms；260ms 后由 `app.js` 摘掉标记。
  整窗重建（窗口未满时的 `replaceChildren`）只给真正的新行播一次，历史行不重播；
  极简模式 `animation:none`，矩阵同时断言 `rowH>0`（不播动画 ≠ 不可见）。

### 16.3 滚动：四条必须同时成立的保护

流式每帧都在长高，滚动位置只要有一处"顺手写成 `scrollTop = scrollHeight`"就会出事：

1. **关闭浏览器滚动锚定**：`#chatScroll { overflow-anchor:none }`。Chrome 在内容长高时会
   自动改 `scrollTop` 把锚点拉回视野；实测上翻后 `1355 → 1409 → 1627`，全程没有一行 JS
   写 `scrollTop`，也没有 `scrollIntoView` 调用。所有滚动位置本来都由 JS 显式管理，锚定必须关。
2. **贴底用 `instant`**：`stickBottom()` 与 pager 的 `pinBottom()` 统一
   `scrollTo({top: scrollHeight, behavior:"instant"})`。CSS 里有 `scroll-behavior:smooth`，
   逐帧贴底会变成"永远追不上的缓动"，流式结束停在离底 200+px，下一帧又被当成"用户在看历史"。
3. **贴底门控当场算 gap**：`MutationObserver` 原来读缓存的 `lastGap`，程序化贴底后缓存
   仍是 ≤60，一次 `scrollIntoView` 就能把上翻的用户拽到底；改为每次回调现算 `gap()`。
4. **句柄重建后重绑**：`ChatPager.render()` 整页重建节点后，流式句柄的 `el` 仍指向已脱离
   文档的旧节点，`paint()` 的 `parentElement === #messages` 守卫会静默停写（实测短回复停在
   9 字）。用 `handleOf`（WeakMap，`rec → handle`）在 `render()` 里把 `h.el = node` 重绑。

### 16.4 验证（本轮未放宽任何阈值）

- 探针（同一 SSE 脚本、414×860、改前快照跑在独立静态服务上）：
  长回复 **2 帧 / 单帧 95 字 / 388ms → 26 帧 / 单帧 4 字 / 494ms**；
  短回复 **4 帧 / 11 字 → 4 帧 / 11 字**（网络分块节奏不变，末尾 2 字由 `finishSoon` 补齐）。
- `scripts/test_layout_matrix.py` **53 → 80 条**：§8 骨架 9 条（线数/递减宽度/线高/静态/
  aria/高度不推挤/切回可逆），§9 流式 18 条（短句整段、长句帧数与单帧上限、350–1400ms 补齐、
  短/长节奏差、`data-fresh` 生命周期、`msg-in`、极简零动画、贴底跟随、**流式中上翻不被拽回**、
  语音提示不抢滚动、点浮钮回底）。原 53 条一条未删，**80/80 PASS**。
- `web/` Node 单测 **309 → 320/320**：新增 `finishSoon` 收尾节奏与「整页重建后句柄重绑」；
  Biome 0 error，`stream_typer.js` 新文件 0 warning。


