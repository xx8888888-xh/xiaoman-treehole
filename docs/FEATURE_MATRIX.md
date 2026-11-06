# 功能落实矩阵（FEATURE_MATRIX）

> 基线：`61384a0`（feat/containerize-and-config）
> 审计范围：`README.md` 承诺、`ANALYSIS.md` §16 遗留、`docs/AI_RULES.md` 约束、`web/index.html` 现有入口
> 本轮分支：`feat/feature-completeness`（worktree `treehole-w12`）
> 结论先行：共盘 **43 项**，审计时 **已实现 24 / 半实现 9 / 只有 UI 无逻辑或只有逻辑无 UI 3 / 完全缺失 7**；
> 本轮**补齐 13 项**、**补强 2 项**（500 提示、错误可操作性），遗留 5 项（均注明原因，不含禁止改动域）。

## 1. 总览

| 域 | 项数 | 已实现 | 半实现 | 只有一半（UI/逻辑缺一边） | 完全缺失 |
|---|---|---|---|---|---|
| 对话与模型降级 | 9 | 7 | 1 | 0 | 1 |
| 语音 | 5 | 3 | 1 | 1 | 0 |
| 提醒 | 9 | 4 | 2 | 1 | 2 |
| 统计 / 打卡 | 7 | 2 | 2 | 0 | 3 |
| 首次使用 / 可用性 | 4 | 1 | 0 | 0 | 3 |
| 其他（外观/危机/备份/悬浮窗等） | 9 | 7 | 1 | 0 | 1 |
| **合计** | **43** | **24** | **7** | **2** | **10** |

> 注：上表「只有一半」指功能两边各缺一边（如开关存在但无入口、UI 存在但不生效）。
> 其中「完全缺失」的 10 项里有 3 项为统计 UI/按天数据/连续打卡，2 项为提醒 UI 新建/编辑，3 项为首次引导/空态/错误重试，
> 其余为 500 全通道提示、免费通道开关入口。

## 2. 逐项盘点与结论

状态图例：✅ 已实现 · 🟡 半实现（能用但不闭环） · 🧩 只有 UI 或只有逻辑 · ❌ 缺失
「本轮」列：🔧 已补齐 · ➕ 补强 · ⏸ 遗留

### 2.1 对话与模型降级

| # | 功能 | 来源 | 审计状态 | 证据 / 缺口 | 本轮 |
|---|---|---|---|---|---|
| 1 | 三级降级：直连 → 代理 → mock → 离线 | README「三级链路」 | ✅ | `web/js/api.js#chatStream`；e2e ⑦ 断网/限流 | — |
| 2 | 流式 SSE + 分条 + 打字节奏 | README / ANALYSIS §12 | ✅ | `chatStream` + `sendSplit`；e2e ⑥ | — |
| 3 | 离线规则引擎（记忆/情绪/危机可用） | README FAQ | ✅ | `mock_engine.js`；e2e ① | — |
| 4 | 429/402 退避重试 + 排队 | README「三级链路 1」 | ✅ | 1.5s 重试 + `pending`；e2e ⑦ | ➕ 新增 429 入队 UI 断言 |
| 5 | 断网/失败消息不丢（待发队列） | README / ANALYSIS §16 | ✅ | `pendingBar` + 自动补发；e2e ⑦ | — |
| 6 | 后端 5xx 的用户可理解提示 | 用户诉求「像正规软件」 | 🟡 | 旧版只显示「离线示例模式」徽标，不知道是 500；全通道失败时才有兜底文案 | ➕ 新增 `errors.js`：500→「服务端出小差了（500）」并在全失败时 toast；降级徽标保留 |
| 7 | 模型坏 JSON 的兜底 | ANALYSIS P1 | ✅ | `parseStructured` 失败 → 兜底回复；e2e ⑧ | ➕ 归类为 parse，不要求重试 |
| 8 | 自填 Key / 自定义模型 / 测试连接 | README 配置 | ✅ | `testConnection` 显示 HTTP 状态与原因 | — |
| 9 | 桌面代理与安卓免费通道的隐私闸门 | ANALYSIS W7 | 🧩 | 逻辑有（`allowFreePersonal`），**设置页没有入口**，用户无法授权 | 🔧 设置页新增「允许免费通道」开关 + `routePlan` 断言 |

### 2.2 语音

| # | 功能 | 来源 | 审计状态 | 证据 / 缺口 | 本轮 |
|---|---|---|---|---|---|
| 10 | 设置页「服务端语音」真的优先生效 | README / 设置页文案 | 🟡 | **缺陷**：`providerChain` 固定 `native → server → browser`，选服务端也先本机，文案与行为不符 | 🔧 链路改为跟随选择：server 优先 / browser 优先 / off 静音；`config.describeTts` 与运行时同源 |
| 11 | 音色设置有效性与诚实提示 | README FAQ 4 | 🟡 | 音色只对服务端合成有效；旧 UI 无任何说明 | 🔧 设置页实时显示「实际发声是谁」，并标注音色是否生效 |
| 12 | 语速设置 | README | ✅ | `speakRate(rate)` 对 native/browser/server 均生效 | — |
| 13 | 关闭语音（只显示文字） | README | ✅ | `tts=off → ["none"]`；e2e ⑩ 静音 | — |
| 14 | TTS 不可用时安静提示 | ANALYSIS P1-8 | ✅ | `voiceTip`，不弹窗、不出声；提示条 8s 后自动收起。失败日志写 `sessionStorage["xiaoman_voice_fail_log"]`，**上限 20 条，写满后挤掉最早记录**（真机排查时早期失败可能已不在日志里） | — |

### 2.3 提醒

| # | 功能 | 来源 | 审计状态 | 证据 / 缺口 | 本轮 |
|---|---|---|---|---|---|
| 15 | 对话式创建 / 查看 / 取消 | README FAQ | ✅ | `Reminders.parseTime` + 对话指令；e2e ④ | — |
| 16 | UI 新建提醒 | 用户诉求「功能都落实」 | ❌ | 旧抽屉只列已有提醒，没有新建表单 | 🔧 记忆抽屉新增「文案 + 时间 + 加上提醒」表单 |
| 17 | UI 编辑提醒 | 同上 | ❌ | 只能取消重建 | 🔧 每行「改」→ 预填表单 → 保存修改（`Reminders.update`） |
| 18 | UI 取消 / 删除 | 旧 UI | ✅ | `.r-cancel`；保留 | — |
| 19 | 查看「即将到来」 | 用户诉求 | 🟡 | 旧列表未排序、无分组 | 🔧 按时间升序 + 「即将到来（N）」分组 |
| 20 | 触发后的提醒记录 | 用户诉求 | ❌ | `done` 只有布尔，UI 完全看不到 | 🔧 `doneAt` 留痕 + 「已经提醒过」列表（最近 10 条）+ 清空 |
| 21 | 后台闹钟（Android） | w8 已完成 | ✅ | 不改 `android/**`；本轮只补 Web UI 闭环 | — |
| 22 | 提醒持久化 | README 数据说明 | ✅ | localStorage `xiaoman_reminders_v1`；node 单测 | — |
| 23 | 重复提醒节流（心跳 25% / 10min 静默） | ANALYSIS P1-2 | ✅ | `heartbeat.js`；本轮回归 | — |
| 24 | 主动问候开关 | 用户诉求「新元素可关闭」 | 🧩 | `cfg.heartbeatOn` 逻辑存在，**设置页无入口** | 🔧 设置页「陪伴方式」开关；node 断言 tick 真的停/发 |
| 25 | 提醒的备份/导出 | 数据处置权 | 🟡 | 备份只含消息/记忆/危机，不含提醒 | ✅ w16：备份 v3 纳入提醒（id/文案/时刻/完成态），round-trip 逐项核对 |

### 2.4 统计 / 打卡

| # | 功能 | 来源 | 审计状态 | 证据 / 缺口 | 本轮 |
|---|---|---|---|---|---|
| 26 | 统计数据结构（条数/笑/深夜/天数/emoji） | README P2-5 | ✅ | `stats.js`；旧版仅内部心跳使用 | — |
| 27 | 统计 UI / 图表 | README P2-5 承诺「小本本」 | ❌ | 数据有，界面**完全没有** | 🔧 记忆抽屉「我的小本本」：4 个数字 + 最近 7 天柱状（纯 CSS，零动画） |
| 28 | 按天数据（不重复计/不漏计） | 用户诉求「图表不能因日期边界出错」 | ❌ | 旧版只存「有哪几天」，没有每天条数 | 🔧 `perDay`（保留 90 天）+ `daily(n)` 补零窗口 |
| 29 | 日期边界正确 | 同上 | 🟡 | `days` 用 `toDateString`，但无按天计数；跨零点没有单测 | 🔧 `dayKey()` 本地 `YYYY-MM-DD`（不用 UTC `toISOString`）+ 23:59/00:01 单测 |
| 30 | 连续打卡 | 「打卡/统计」诉求 | ❌ | 无 | 🔧 `streak()`：今天没开口时从昨天算，不清零 |
| 31 | 跨会话保留 | 用户诉求 | ✅ | localStorage；本轮刷新回归断言 | ✅ 已证 |
| 32 | 清空统计入口 | 数据处置权 | 🟡 | `Stats.clear()` 存在，无 UI | ✅ w16：小本本底部「清空」+ 5 秒二次确认，清后 localStorage/界面同步归零 |

### 2.5 首次使用 / 可用性 / 错误边界

| # | 功能 | 来源 | 审计状态 | 证据 / 缺口 | 本轮 |
|---|---|---|---|---|---|
| 33 | 新用户引导 | 用户诉求「让人使用顺滑」 | ❌ | 只有一句问候，无引导 | 🔧 空状态卡片 + 三个快捷入口（说话/定提醒/看记忆），发出第一条后自动收起 |
| 34 | 聊天区空状态 | 用户诉求 | ❌ | 空聊天区只有打字占位 | 🔧 `#emptyState`（只在没有用户消息时出现） |
| 35 | 出错可操作重试 | 用户诉求「不能白屏卡死」 | 🟡 | 只有待发队列能重试；记录读取失败无入口 | 🔧 `noticeBar` + 重试按钮（IndexedDB 读失败可直接重读） |
| 36 | localStorage 满提示 | 用户诉求 | ❌ | 旧版 `catch {}` 静默吞掉 | 🔧 `__storageFull` 统一提示一次 + 说明「先备份再清理」 |
| 37 | IndexedDB 被清空/不可用 | 用户诉求 | 🟡 | 静默失败，用户以为记录还在 | 🔧 明确提示「本机记录暂时读不出来 + 重试」；聊天不阻塞 |

### 2.6 其他承诺功能

| # | 功能 | 来源 | 审计状态 | 本轮 |
|---|---|---|---|---|
| 38 | 记忆抽屉 / 清空 | README | ✅ | — |
| 39 | 外观预设 / 字号 / 主题 / 零动画 | ANALYSIS / AI_RULES | ✅ | 新 UI 全部复用现有 CSS 变量、无动画；`test_appearance.py 22/22` |
| 40 | 悬浮窗 / 桌宠（Android） | README FAQ | ✅ | 属 `android/**` 与 `float_mode.js`，按约束不改；另一会话负责 |
| 41 | 危机识别分级 / 热线 / 记录导出 | README | ✅ | `risk.js` 禁止改；e2e ⑦n 全过 |
| 42 | 友谊卡片 / 关系里程碑 / 时间线 | README | ✅ | — |
| 43 | 备份导出/导入（消息/记忆/危机/提醒/统计/服务端会话） | README | ✅ | ✅ w16 schema v3：新增提醒、小本本、服务端会话（状态+落盘历史+w11 记忆槽）；v2 老备份仍可导入 |
| 44 | 单条消息删除 / 编辑后重发 | 用户诉求（数据处置权） | ❌ | 原来只能整库清空 | ✅ w16：悬停「⋯」/长按菜单（动态 DOM）；删除五处同步（本机库/内存/分页/统计/记忆+槽/服务端），删用户消息级联清其回复；编辑就地改文案后重发 |

## 3. 本轮「开关 → 行为」证据

| 开关 | 行为落点 | 断言 |
|---|---|---|
| 对话模式（离线/mock/在线） | `API.routePlan()` | `web/tests/settings_behavior.test.js`：offline/mock/openai × 桌面/安卓，逐项断言 |
| 自填 Key | `API.routePlan()` | 有 Key → `direct`，与免费开关无关 |
| 允许免费通道 | `API.routePlan()` | 安卓下 false → `[]`（不走在线），true → `["native"]`；`scripts/test_feature_loop.py` 在真实页面再次断言 |
| 主动问候 | `Heartbeat.tick()` 现读配置 | 同文件：关 → 0 次推送；开 → 同一 tick 推送 1 条；浏览器侧断言配置落盘 |
| 服务端/本机/关闭语音 | `TTS.chain()` + `XMConfig.describeTts()` | node 断言三条链路；浏览器断言 `TTS.chain` 与设置页提示文案一致 |
| 语速 / 音色 | `TTS.speak` / 服务端 URL 参数 | 音色仅在服务端实际发声时标注生效；语速三条后端均生效（原有 e2e） |

## 4. 遗留项（含原因，不含禁止改动域）

| 项 | 原因 / 影响 | 建议 |
|---|---|---|
| Android APK 重打包 / 真机有声 | 约束禁止改 `android/**`，且全局静音 | 主合并后由打包流程验证 |
| 中危第三句热线文案 | ANALYSIS §16 待产品决策，非代码缺失 | 等产品确认 |

> w16 已收口：提醒/统计/服务端记忆槽进备份（#25/#43）、单条消息删除与编辑重发（#44）、统计清空入口（#32）。

## 5. 本轮验证结果

| 门槛 | 结果 |
|---|---|
| `node --test tests/*.test.js` | **166/166 PASS**（基线 139 + 新增 27） |
| `npx biome check .` | **0 error / 0 warning**（exit 0） |
| `scripts/verify_assets.py` | **PASS**（55 files / 7,325,185 bytes；用与 `asset_excludes.sh` 同口径的临时发布副本校验） |
| `scripts/test_appearance.py` | **22/22 PASS** |
| `scripts/test_e2e.py` | **60/60 PASS**（TTS provider=none 的 503 为预期静音） |
| `scripts/test_feature_loop.py`（新增） | **43/43 PASS**（首次引导、提醒 CRUD+历史、小本本跨会话、开关→行为、429/500/存储满/IDB） |
| `pytest` | 仓库无 pytest 风格用例；`pytest scripts` 会被既有 `test_freemodels.py` 的 `sys.exit(2)`（缺 Key）中断，属基线问题，本轮未改 |

> 运行环境：静态站与 mock/TTS 起在 **9021/9022/9023**（避开共享 8901-8903），
> TTS `provider=none`、Playwright `headless + --mute-audio`，全程静音。

### 5.1 w16 数据完整性收口轮（2026-11-05，分支 `feat/data-completeness`）

| 门槛 | 结果 | 跑在哪 |
|---|---|---|
| `pytest server/tests` | **153 passed + 19 subtests**（含新增 `test_session_io.py` 8 项） | 本地 |
| `node --test tests/*.test.js` | **192/192 PASS**（基线 181 + 新增 11：backup_roundtrip 6、store 1、reminders 2、stats 2） | 本地 |
| `biome check`（白名单加 errors/app/chat_pager/pet） | **0 error**（仅 warning/info；`pet.js` 只改 `package.json` 白名单，未动源码） | 本地 |
| `scripts/test_roundtrip.py`（新增） | **22/22 PASS**：导出→清空→导入，提醒时刻/统计计数/服务端记忆槽/历史逐项一致 | 本地 |
| `scripts/test_msg_edit.py`（新增） | **26/26 PASS**：菜单/二次确认删除/级联清回复/编辑重发/统计清空 | 本地 |
| `scripts/test_e2e.py` 60、`test_feature_loop.py` 43、`test_live2d_original.py` 24、`test_raf_budget.py` 8、`verify_assets.py` | 本地未跑（负载纪律：交给云端 Actions，push 分支后 `gh run list/watch` 查看） | 云端 CI |
