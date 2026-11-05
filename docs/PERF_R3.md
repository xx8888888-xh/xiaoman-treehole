# PERF R3 · 第六轮性能收口（w21）

> 基线 `r5-20261105` / `a283342` · 分支 `feat/perf-r3` · worktree `treehole-w21`
> 全部浏览器数据来自云端 GitHub Actions（Ubuntu runner + Playwright Chromium），本机未启动任何浏览器/AVD/Gradle。

## 0. 结论速览

| 项 | 改前 | 改后 | 判定 |
|---|---|---|---|
| ① 加载期最长块（1x） | 0ms（0 个 >50ms 块） | 0ms（0 个） | 云端基线本就无长任务，见下方 4x 对照 |
| ① 加载期最长块（4x 节流） | 引导解析 159ms / 首帧 51ms（3 次中 1 次） | **无 <1.4s 的 >50ms 块**；仅剩开场白收尾 56–71ms | 纹理首帧同步解码已消失 |
| ① LCP | 1568ms（1x）/ 1788ms（4x） | 1556ms / 1664ms | 不退化 |
| ② 输入可发送（bind 完成） | 127ms | **97ms** | ≤1.5s |
| ② 首条消息真正落地 | 1566ms | 1529ms | 仍 >1.5s，瓶颈是开场白流式（非初始化），见 §5 |
| ③ 会话 5→208 条同一段回复的脚本成本比 | 0.13 | 0.17 | 不随条数增长，无 O(n) 重排 |
| ③ push ×300（最小样本） | 83.5ms（278µs/条） | **43.4ms（145µs/条）** | −48% |
| ③ 历史翻页 older() | 1.75ms/页（max 2.5） | 1.20ms/页（max 1.7） | 整窗重建恒 14 行，O(1)/页 |
| ④ 200 轮堆增长 | +0.85MB | +0.86MB | 数据增长、非泄漏；未做到"严格平坦"，见 §6 |
| ④ 纹理占用/解码次数 | 9,791,028B / 0 次显式解码 | 9,791,028B / **18 张仅 1 次** | 不重复解码 |
| 测试 | 222 pass | **222 pass** | 不降 |

云端 run：

| 用途 | run 号 | commit |
|---|---|---|
| 改前基线（含 4x 节流） | **42020843009** | `023dce2` |
| 改后（正式样本） | **42021546457** | `bdd0d91` |
| 改后（中间样本，自检口径未分离） | 42021058074 | `a2d75b8` |
| 改前（无节流第一版） | 42019279682 | `3852f9a` |

## 1. 采样方法与口径

- 环境：`ubuntu-latest`，Chromium headless（`--mute-audio`），viewport 390×844，DPR=1；Web 静态站 8911、`server/mock_api.py` 8912（AI_MODE=mock）；TTS 关闭，未占用 8901-8903。
- 入口：`python scripts/perf_r3.py --out artifacts/perf/r3.json`；工作流 [perf.yml](.github/workflows/perf.yml) 仅 `feat/perf-r3` push / 手动触发，单 job + concurrency 串行。
- 页面探针：`scripts/perf_r3.py` 注入 init script，采集 `longtask`、`largest-contentful-paint`、`first-contentful-paint`、`HTMLImageElement.decode` 调用、`drawImage` 单次耗时（`?perfdraw=1`）；`web/js/perf.js` 包 `ChatPager.push/older` 逐次计时。
- 4x 节流：`Emulation.setCPUThrottlingRate=4`，用于把低端机/WebView 的首帧解码量级放大；1x 用于 LCP / 可发送时刻等绝对指标。改前改后同脚本、同 runner 型号、每项 3 次取中位数。
- 长任务观测器自检：headless 下 `longtask` 在 1x 往往不派发（真实 0 个）；4x 下实测能采到真实条目，说明观测器工作。页面内 `page.evaluate` 的忙等不会计入 longtask（CDP 注入任务被排除），因此自检值常为 `null`，不作为判据。
- 「首条消息可发送」：`sendBtn.onclick` 挂上（bind ok）→ 等开场白流式结束（typing 收起且条数稳定）→ 真实发一条并等自己的气泡落地。
- 本机资源：本轮本机只跑 `node --test` 与 biome，未起浏览器。测试期间本机 node 峰值 **2.16 核 / 8 核 ≈ 27%**（<40%），耗时 15.6s。

## 2. ① 加载期长任务（纹理解码）

改动：`web/js/stage.js` 在 `puppet.load()` 之后、首帧之前新增 `prewarmTextures()`：
`Object.values(puppet.images)` → 每批 3 张 → 批间 `requestIdleCallback`（timeout 90ms）→ `img.decode()` 异步完成；12 层共 18 张（含表情/眼图）分 6 批，wall 9–10ms。兜底 `full.png` 也改为 `onload` 后先 `decode()` 再画。不支持 `decode()` 的老 WebView 直接跳过，保持原行为。

改前（run 42020843009）：

- 1x：3 次均 0 个 >50ms 块，`longMax=0`，`drawImage` 无单次 ≥4ms。
- 4x 三次的 >50ms 块：`159ms@32`（JS 引导解析）、`51ms@333`（首帧）、`75/90ms@1.7–2.1s`（开场白收尾/自检）。

改后（run 42021546457）：

- 1x：3 次均 0 个 >50ms 块，LCP 1556ms（改前 1568ms）。
- 4x：`prewarm decode 18/18 张、6 批、wall 9ms、failed 0`；首帧附近（<1.4s）**没有任何 >50ms 块**；仅剩 `56/57/71ms@1.65s` 三次，均与 `readyToSend≈1.67s` 对齐，即开场白结束时的应用侧收尾（改前同样存在 75ms 级别），与纹理无关。
- `__stageDecode = {images:18, batches:6, decoded:18, failed:0, wallMs:9, bytes:9791028}` 可复核。

说明：云端 runner（甚至 4x）都无法复现历史 74ms——基线 1x 本来就是 0，因此本项的硬证据是"4x 下首帧块 51ms → 无"以及"18 张全部在 idle 预解码"。真机 WebView 量级需在 MuMu 上复测（本轮按约束不跑本地浏览器）。

## 3. ② 冷启动 → 首条消息可发送（r3 收口）

测法（改前/改后同一脚本）：bind 完成 → 立即填字点发送 → 自己的气泡进列表。改前基线用 `git show f0f19f2:web/js/app.js` 临时还原（`perf.yml` 的 `baseline=1`）。

| 指标 | 改前（run 42038008421） | 改后（run 42038552571 / 42036935508） |
|---|---|---|
| 1x `firstSendMs` | 209ms | **174ms**（另一轮 119ms） |
| 4x `firstSendMs` | 390ms | **458ms**（另一轮 274ms） |
| 1x `inputReadyMs` | 123ms | 118ms（另一轮 82ms） |
| 4x `inputReadyMs` | 260ms | 275ms（另一轮 189ms） |
| 开场白（idle 路径，4x） | landed 1251ms / played | landed 1241ms / done 1351ms / records 1 |

说明：历史口径里的 1529ms 主要来自"先等开场白播完再发送"的测量协议；改成用户立即发送后，改前/改后都在 0.5s 内。真正的结构改动是 `greet()` 不再占住发送通道：900ms 开场窗口内若用户已开口，开场白 **延后到首轮回复之后补播**（`busy/sending` 中则暂存 `pendingGreet`，回复收尾后补）；幂等标记只在真正播出时落盘，所以不丢、不重复、始终排在用户消息/回复之后。idle 路径（用户没先说话）完全不变。

外壳（聊天 UI）在 100ms 内可交互，舞台走 `requestIdleCallback` 分帧挂载（mount ok 153→132ms）。

## 4. ③ 对话路径（流式 / 历史窗口）

改动：`web/js/chat_pager.js` 的 `syncBars()` 改为差值写入（`setClass` / `setText` 先比较再写），避免每条消息都替换同值文本节点、触发无谓样式失效。流式逐字仍只写最后一条 bubble 的 `textContent`，历史窗口恒 14 行，`older()` 只重建当前窗（`start` 游标）。

| 场景 | 改前 | 改后 |
|---|---|---|
| 会话 5 条时回复窗口 `chat_pager.push` 平均 | 1.27ms | 0.90ms |
| 会话 208 条时同一段回复 push 平均 | 0.80ms | 0.53ms |
| 回复窗口时长（5 条 / 208 条） | 237ms / 220ms | 232ms / 218ms |
| 回复窗口 ScriptDuration 比（大/小） | 0.13 | 0.17 |
| push ×300 最小耗时 | 83.5ms（278µs/条） | **43.4ms（145µs/条）** |
| older() 单页（18 页） | avg 1.75ms / max 2.5ms | avg 1.20ms / max 1.7ms |
| 窗口内 DOM 行数 | 14 | 14 |

结论：两条路径都没有随会话长度增长的 O(n) 全量重排；push 的收益来自去掉冗余 DOM 写入，历史翻页成本恒定。

## 5. ④ 内存与纹理（200 轮，r3 收口）

pager 淘汰策略（`chat_pager.js`）：内存只留最近 `KEEP=36` 条，窗口外越过 `EVICT_GAP=24` 条安全余量后，从最旧一段 `splice` 并记入 `evicted`；`total = evicted + recs.length`，`start` 为绝对索引，`older()` 到内存边界时经 loader 从 IndexedDB 取回（`restored/fresh` 回填 `evicted`，去重后保序）。没有 loader（如纯内存测试）时不淘汰，避免丢消息；`jumpLatest()` 也不淘汰。

验收（run 42038552571，4x 不节流环境）：
| 指标 | 改前（run 前基线） | 改后 |
|---|---|---|
| 200 轮冷段（40 轮预热） | +0.86MB | +0.18MB |
| **200 轮稳态（200 轮预热 + 200 轮测量）** | — | **+0.09MB**（marks 0.09 / 0.02 / 0.00 / -0.01） |
| 保留 `[probe]` 日志对照 | — | +0.21MB，末态 `console.clear()` 回落 0.06MB |
| 关持久化写入对照 | — | +0.16MB（IDB 只占 0.02） |
| DOM 节点 / pager 持有 | 231→272 / 线性 | 272→272 / `held` 恒 38 |
| 纹理字节 / decode 新增 | 9,791,028 / — | 9,791,028 恒定 / `imgDecode.calls` 恒 18 |
| 长任务（1x） | 0 | 0 |

翻页自检（同一次 200 轮后）：`moves=20 calls=38 stepOk=True overlapOk=True fetchKeepsWindow=True dupInPage=False firstRepeat=False idsPresent=True totalStable=True`；before `{total 1197, start 1183, held 38, evicted 1159}` → after `{start 989, held 208, evicted 989}`。

解释：冷段剩下的 +0.18MB 是 V8 预编译/首轮路径的一次性（同一页面再跑 200 轮只有 +0.09，且 marks 收敛到 0），不是消息数据泄漏；`localStorage` 200 轮只涨 71 字符、IDB 行数恒 301，pager 持有条数恒定。`[probe]` 日志是安卓 logcat 诊断通道，进 Chromium 控制台缓冲（有容量上限），测量口径里静音并单列。

## 6. 风险与回退点

1. **`img.decode()` 不可用/拒绝**：直接跳过预解码，回到原路径；`failed` 计数可观测。
2. **idle 饥饿**：每批有 90ms timeout 兜底，最坏 6 批 ≈540ms；只发生在页面持续繁忙时，且发生在首帧可见之后，不影响输入。
3. **首帧延后**：预解码 wall 9–10ms（云端）；真机低端 WebView 可能 50–100ms，表现为角色出现稍晚，不影响 LCP/输入（LCP 实测未退化）。
4. **lint 自动修复**：`stage.js` / `heartbeat.js` 的 `a && a.b` → `a?.b`、`isFinite` → `Number.isFinite` 等由 biome `--write --unsafe` 生成，已逐块人工复核（均为等价语义，`isFinite` 前已有 `typeof === "number"` 守卫），222 测试全绿。
5. **4x 下残留 56–71ms 块**：与开场白结束同刻，属 `app.js` 收尾，改前同样存在；不在本文件域。
6. **开场白延后（r3）**：用户先开口时，开场白补播在首轮回复之后；若用户一直不发，900ms 后照旧开场。只有"900ms 内已点发送"会走到延迟分支，产品观感是"她先回答，再打招呼"，不会再挡首条消息。
7. **pager 淘汰（r3）**：`KEEP=36` 只在有 loader 时生效；无 loader（纯内存测试/异常初始化）不淘汰，退化为原行为。翻回更早历史依赖 `ChatStore.listBefore`，而 `ChatStore.prune` 只留 300 条——超出 300 条之前的消息仍会因库里已被裁掉而无法取回（原本就如此，不是本轮新增）。
8. **`total`/游标语义（r3）**：`total = evicted + recs.length`、`start` 为绝对索引；任何直接读 `recs[0]` 的旧代码都要改用 `atAbs()`。已跑 222 测试 + 20 页云端翻页自检（无重复/无漏行/窗口恒 14 行）。

## 7. 复现命令

```bash
# 云端（GitHub Actions，自动在 feat/perf-r3 push 时跑）
gh workflow run perf.yml -f tag=after -f only=""

# 改前基线（同一脚本临时还原旧 app.js）
gh workflow run perf.yml -f baseline=1 -f only=load -f tag=before

# 只跑某一项
python scripts/perf_r3.py --only load --out artifacts/perf/load.json

# 测试 / lint（本机）
node --test "web/tests/*.test.js"
cd web && npm run lint
```

## 8. 剩余未解

1. 真机 WebView 74ms：云端无法复现，需 MuMu/真机单独跑一轮（本轮按约束不启本地浏览器）。
2. 堆的"冷段"（40 轮预热）仍 +0.18MB：是 V8 预编译/首轮路径的一次性，稳态收敛到 +0.09；若产品要求"从冷启动起 200 轮 ≤0.1"，只能再加预热或等 V8 稳态，无代码可削。
