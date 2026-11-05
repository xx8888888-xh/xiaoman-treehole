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

## 3. ② 冷启动 → 首条消息可发送

| 指标（1x 中位数） | 改前 | 改后 |
|---|---|---|
| FCP | 64ms | 44ms |
| DCL | 96ms | 80ms |
| bind ok（输入可用） | 90ms（探针）/127ms（轮询） | 75ms / **97ms** |
| 开场白结束（真正可发） | 1536ms | 1505ms |
| 首条自己消息落地 | 1566ms | 1529ms |

外壳（聊天 UI）在 100ms 内可交互，舞台本来就走 `requestIdleCallback` 分帧挂载（mount ok 153→132ms），因此"先渲染外壳再加载模型"已经具备，无需再拆。>1.5s 的部分全部是开场白流式占用的发送锁（`busy`），属 `app.js` 域，本轮不动；若要压到 1.5s 内，需允许"边聊边发"或把开场白延后到首次交互（已列为越域建议）。

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

## 5. ④ 内存与纹理（200 轮）

| 指标 | 改前 | 改后 |
|---|---|---|
| 堆起→止 | 1.67→2.52MB（+0.85） | 1.69→2.55MB（+0.86） |
| 每 40 轮增量 | 0.07 / 0.04 / 0.07 / 0.05MB | 0.07 / 0.03 / 0.08 / 0.06MB |
| DOM 节点 | 231→272 | 231→272 |
| 纹理字节（start/end） | 9,791,028 / 9,791,028 | 9,791,028 / 9,791,028 |
| `decode()` 调用 | 0（懒解码，首帧同步） | 18（仅挂载时） |
| 200 轮内新增 decode | — | 0（`imgDecode.calls` 恒 18，failed 0） |
| 长任务（1x） | 0 | 0 |

解释：堆的线性增量是"消息数据"本身（200 轮≈600 条记录 ×≈1.4KB），不是泄漏——DOM 恒定、纹理字节恒定、解码次数恒定。`ChatStore` 只保留 300 条，但 `ChatPager.recs` 在会话内不淘汰；要让堆严格平坦必须在 pager 侧按 300 条截断，这会改动 `total`/`older()` 游标语义并牵连 `app.js`，本轮未做（列为剩余项）。

## 6. 风险与回退点

1. **`img.decode()` 不可用/拒绝**：直接跳过预解码，回到原路径；`failed` 计数可观测。
2. **idle 饥饿**：每批有 90ms timeout 兜底，最坏 6 批 ≈540ms；只发生在页面持续繁忙时，且发生在首帧可见之后，不影响输入。
3. **首帧延后**：预解码 wall 9–10ms（云端）；真机低端 WebView 可能 50–100ms，表现为角色出现稍晚，不影响 LCP/输入（LCP 实测未退化）。
4. **lint 自动修复**：`stage.js` / `heartbeat.js` 的 `a && a.b` → `a?.b`、`isFinite` → `Number.isFinite` 等由 biome `--write --unsafe` 生成，已逐块人工复核（均为等价语义，`isFinite` 前已有 `typeof === "number"` 守卫），222 测试全绿。
5. **4x 下残留 56–71ms 块**：与开场白结束同刻，属 `app.js` 收尾，改前同样存在；不在本文件域。

## 7. 复现命令

```bash
# 云端（GitHub Actions，自动在 feat/perf-r3 push 时跑）
gh workflow run perf.yml -f tag=after -f only=""

# 只跑某一项
python scripts/perf_r3.py --only load --out artifacts/perf/load.json

# 测试 / lint（本机）
node --test "web/tests/*.test.js"
cd web && npm run lint
```

## 8. 剩余未解

1. 首条消息真正落地 1.5s：受开场白流式 `busy` 锁限制（`app.js`），需产品侧决策。
2. 堆严格平坦：需 `ChatPager` 按 `ChatStore` 上限淘汰旧记录，并让 `older()` 游标/`total` 语义同步（跨 `app.js`）。
3. 真机 WebView 74ms：云端无法复现，需 MuMu/真机单独跑一轮（本轮按约束不启本地浏览器）。
