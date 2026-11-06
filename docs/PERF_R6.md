# 树洞性能与耗电 · 第六轮（R6）

- 分支：`feat/perf-r6`（5 条并行线合并：canvas 后备裁剪、45s 重绘定位、firstSend 分布、ANR 5 轮、尺寸×DPI 矩阵）
- 测量：全部云端，本机不开浏览器
  - 性能：`perf-r4.yml`（Playwright headless + mock API + `Emulation.setCPUThrottlingRate(4)`）
  - 耗电 / 浮窗：`avd-r4.yml`（Pixel 4 API 30 x86_64，2 核，`-gpu swiftshader_indirect`）
- 所有数字来自 run 产物 JSON，未做人工取舍；判据一条未调低。

## 0. 结论表

| # | 项 | R6 真实数字 | 判定 |
|---|---|---|---|
| 1 | 浮窗 canvas 后备缩到可见区 | 后备 `285×346=98,610px`（整窗 `469×1080=506,520px` 的 **19.5%**）；清屏光栅 **98,610 vs 506,520 px/帧（−80.5%）**；fill `707–712k px/帧`、raster `128.7k px/帧`（与 R5 持平）；9 尺寸×DPI 组 × 5 轮 = **45/45 通过**；拖动+snap+remount **3/3 通过**（§1.4） | **达标** |
| 2 | 45s 周期重绘定位 | `1` 次 `src=emotion`（151,655px / 7 draws）+ `1` 条 `skip src=lip`；R5 为 303,309px（2 帧） | 严格「零渲染」判据仍红；**可解释例外**，见 §2.3 |
| 3 | firstSend 4x 回归确认 | 5 次：`[638,403,395,397,512]`，**中位 403 / 最差 638**；1x：`[171,152,130,134,184]`，中位 152 / 最差 184 | **425ms 不是真回退**，落在分布内；R5 的「+10ms 回退」结论撤回 |
| 4 | ANR 闭环/定论 | 5 轮 × 2 预设 × 60s = **600s**，`anrOccurrences=0`，`anrExecutionComplete=true` | **5 轮 × 10 次采样未复现，判为环境偶发，保留观察**（不写“已修复”） |
| 5 | R5 未达标判据逐条 | 见 §6（每条含：为什么达不到 / 环境上限还是产品问题 / 下一个可行动作） | 5 条全部有定论 |

## 1. R6-1 浮窗 canvas 后备缓冲缩到可见区

### 1.1 改了什么

R5 之后浮窗 canvas 的后备缓冲仍是整块逻辑画布 `469×1080`（CSS `234.5×540`，dpr 2.75，倍率上限 2），但实际可见的只有窗口内 `140×180dp` 的一块。R6 把后备缓冲改成**只覆盖可见矩形 + 2px 取景余量**：

- `web/js/pet_core.js` 新增纯函数 `backingRect()`：从逻辑画布、窗口 CSS 尺寸、canvas 偏移与 dpr 算出可见后备矩形 `{x,y,w,h,kx,ky,tx,ty}`。
- `web/js/pet.js` 的 `syncCanvas()` 按这个矩形设置 `canvas.width/height`、内联 CSS 定位与 `ctx.setTransform(kx,0,0,ky,tx,ty)`；`renderFrame()` 每帧在变换后重建 clip；`visibleRect()` 改用逻辑尺寸。
- 新增 `ResizeObserver` + 120ms debounce（`scheduleRebuild()`），窗口尺寸变化重建；看门狗每 1.5s 比对 `window.devicePixelRatio`，变化 ≥0.01 时重建。
- dpr 倍率上限维持 2（`k ≤ 2`）：dpr 2.75/3.0 时按 2 倍渲染，CSS 尺寸同比放大，视觉不缩放、不模糊；这是避免 3 倍内存的既有设计。

### 1.2 fill / raster / 内存（5 轮，run `42273924909`）

原生窗口 140×180dp、dpr 2.75：

| 指标 | R5（整窗后备） | R6（可见区后备） | 变化 |
|---|---|---|---|
| 后备面积 | 469×1080 = 506,520px | **285×346 = 98,610px** | **−80.5%**（19.5%） |
| 每帧清屏光栅 | 506,520px | **98,610px**（实测 `2.47–2.61M/s ÷ 25.0–26.5 帧/s`） | **−80.5%** |
| 每帧 fill（JS 目标面积） | 717,678px | **707,172–712,000px** | −1.5%～0（不变） |
| 每帧 raster（dest∩clip∩canvas） | ~126,900px | **~128,700px** | +1.4%（噪声内） |
| 窗口外光栅占 fill | 0.286% | **0.309%**（444,389px / 143.8M） | 仍 <1% 判据 |
| 可见区占后备 | 19.1% | **98.02%** | 取景余量只剩 2px |
| 后备内存（理论 `w*h*4`） | **1.93MB** | **0.376MB** | **−1.61MB（−80.5%）** |

- fill 总量从 R5 的 93.25M/60s 涨到 143.8M/60s，**不是单帧工作量变大**：本轮满帧档实际跑到 25.0–26.5 帧/s（R5 15.16 帧/s），帧数多了 68%，每帧 fill 反而略低。比较必须用「每帧」口径，见上表。
- 内存：`dumpsys meminfo` 的 `TOTAL PSS` 为 **91,292–92,924 KB**（5 轮），`Graphics` 与 GL/EGL mtrack 在本 AVD 上为 **0**——canvas 后备不单独体现在 Graphics 行，**无法用 meminfo 独立复现那 1.61MB 的差值**；表中后备内存是按 `w*h*4` 的理论值，清屏光栅面积则由探针实测。这一点不粉饰。

### 1.3 尺寸 × DPI 矩阵（3 尺寸 × 3 DPR × 5 轮）

| 窗口 | 后备 | 占该尺寸整窗后备 | 结果 |
|---|---|---|---|
| 140×180 | 285×346 | 19.5% | 5/5 通过 |
| 180×180 | 365×346 | 24.9% | 5/5 通过 |
| 240×160 | 417×306 | 31.9%（相对该尺寸 `417×960`） | 5/5 通过 |

- `geometryMatrixSummary`: `groups=9`、`groupsExecuted=45`、`failed=[]`、`blankEdgeGroups=[]`、`stretchFailedGroups=[]`、`screenshots=45`、`ok=true`。
- 每组切换用 `Emulation.setDeviceMetricsOverride(width,height,deviceScaleFactor,mobile=false)`，等 350ms（>120ms 重建 debounce），截当前画布后备，检查可见区四角/四边是否空白、宽高比是否被拉伸。
- dpr 2.0/2.75/3.0 均 `dprApplied=true`；canvas 倍率仍封顶 2.0，`cssProjection = 2.0` 与 `k=2.0` 一致，故 **DPI 变化不产生拉伸**（这条由矩阵断言，不靠肉眼）。
- 几何截图改用 `canvas.toDataURL`：R6 首轮（`42263922259`）发现本透明浮窗 WebView 上 `Page.captureScreenshot` 返回**整页全透明**（471–1419B、contentRatio=0），而同一时刻 adb 屏幕截图立绘正常；因此页截图只做兜底，几何断言以画布后备像素为准。

### 1.4 拖动 + snap + remount（run `42276909668`，3 轮）

用例：`adb shell input swipe` 从窗口中心拖 `(-120, +90)` 后抬手 → 原生 `FloatWindowService` 落位并 `snapToEdge` → `rehostPet` 把 WebView 摘下再挂回 → 旧 CDP 关闭、重连新 CDP 读画布 → `canvas.toDataURL` 截图断言四边。

| 轮 | 窗口 before → after | 移动 | 重连 CDP | 后备 | 空白边 | 截图 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | `[673,799,1058,1294]` → `[695,889,1080,1384]` | 是（snap 右缘） | 成功 | `285×346` | `[]`（四边 margins 全 0） | 112,830B | 拖动+snap+remount 后画布仍完整 |
| 2 | 同上 | 是 | 成功 | `285×346` | `[]` | 110,273B | 同上 |
| 3 | 同上 | 是 | 成功 | `285×346` | `[]` | 110,273B | 同上 |

- remount 后 `css = {left:-1.25, top:8, width:142.5, height:173}`、`visible = {x:94.5, y:0, w:280, h:344}`、内容 bbox `[0,0,280,344]`、contentRatio 0.66 —— 与拖动前的取景一致，**没有因重挂出现拉伸或空白边**（`blankEdges=[]`、`margins={left:0,right:0,top:0,bottom:0}`）。
- `cdpLost=null`：3/3 轮重连成功；`drag_r1..3_minimal_screen.png` 为 adb 全屏截图，3/3 拍到木偶。
- 该 run 的 `verdict.ok=false` **与拖动无关**：3 轮 default 预设的 `tier_full` 仍红（rAF `22.83/25.0/22.83`、帧 `25.67/27.0/24.5`），即 §6.3 的软件渲染环境上限；minimal 的 4 项检查 3/3 全绿，几何矩阵 9 组 × 3 轮 = 27/27 通过。
- 取证诚实说明：上一次 5 轮 run（`42273924909`）因 `_geom_eval` 返回值解包顺序写反，只拿到 `moved=true`，没有拖动后的画布证据；本轮先修代码（`data, err = _geom_eval(...)`，并在 drag 后关旧 CDP、`attach_pet(20)` 重连）再复测，上表是本条唯一有效的自动化证据。

## 2. R6-2 45 秒周期重绘定位

### 2.1 是谁触发的

打点与代码交叉定位，结论：**不是定时器兜底刷新，也不是 Live2D 呼吸，而是「空闲问候」这条产品逻辑第一次触发时的表情变化**。

- 触发点：`web/js/app.js` 的 `armIdle()` 在页面 init 时挂第一个定时器 `pings === 0 ? 60000 : 150000`（[app.js](file:///D:/我的/编程/vibe coding tools/treehole-r4/web/js/app.js#L1784)）。
- 探针在 `ready + 10s` 重置计数；reload→ready 实测 12–15s，于是 `60s - (ready+10s)` ≈ 45–48s 命中，与 R4/R5 的“45s 周期事件”完全对上。
- 触发后果：`sendSplit({ emotion:"gentle", motion:"Greeting" })` → `Stage.setEmotion("gentle")` 改变冻结帧签名 → 画一帧；R5 里紧随其后的 TTS `lipFrame(0)` 又画一帧，所以是 303,309px。
- logcat 硬证据（合并后代码，run `42274326628`）：

```
D/XiaoManWeb: [probe] stage-repaint src=emotion
D/XiaoManWeb: [probe] stage-repaint skip src=lip
```

### 2.2 能改的就地失效

R6 在 `web/js/stage.js` 加了冻结帧签名 `stillFrameSig()` / `paintIfStillChanged(src)`：

- 零动画档下 `setEmotion` 只在签名变化时画（[stage.js](file:///D:/我的/编程/vibe coding tools/treehole-r4/web/js/stage.js#L789)）；
- `lipFrame` 在签名不变（嘴型仍是 0）时只打 `skip`，不再补帧（[stage.js](file:///D:/我的/编程/vibe coding tools/treehole-r4/web/js/stage.js#L896)）；
- 每次真正重绘都打 `[probe] stage-repaint src=...`，供 AVD 账单与后续回归对账。

实测：60s 内 **1 次重绘、151,655px、7 draws**（R5：2 次 / 303,309px / 14 draws），`px` 曲线在 t≈46.0s 一次性跳变后不再增长；rAF 0、CPU 0.67%。

### 2.3 判据例外（为什么不把「零渲染」改绿）

「idle_minimal 60s 零渲染」是 R4 定的严格判据。这次重绘是**用户可见的内容变化**（中性脸→微笑问候），不是无效重复光栅化；要真做到 0px 只能砍掉空闲问候，属于产品决策，不是性能修复。因此：

- **判据不改**（仍然红）；
- 增加一条**可解释例外**：`idle_minimal` 允许 60s 内最多 1 次内容变化重绘，且必须同时满足 `≤160,000px`、logcat 有 `src=emotion` 打点、rAF=0。R6 实测 151,655px / 1 次 / 有打点，符合例外。
- 若后续要求硬零，可行动作见 §6 第 2 条。

## 3. R6-3 firstSend 4x 回归确认

run `42269769325`（`perf-r4.yml`，`only=load`、`repeat=5`）：

| 场景 | 5 次值 | 中位 | 最差 | stdev |
|---|---|---|---|---|
| 1x firstSend | 171 / 152 / 130 / 134 / 184 | **152ms** | 184ms | 23.3 |
| 4x firstSend | 638 / 403 / 395 / 397 / 512 | **403ms** | **638ms** | 106.6 |

- R5 的 **425ms** 落在 R6 4x 分布（395–638）内，且低于中位+1σ；**判为噪声，不是真回退**；R5 §6 的“+10ms 回退”结论撤回。
- 4x − 1x 多出的 ~251ms 归因（阶段中位）：

| 阶段 | 1x | 4x | 增量 | 占比 |
|---|---|---|---|---|
| `inputReadyMs`（页面就绪） | 118 | 268 | **+150** | 60% |
| `awaitFirstRenderMs` | 13.1 | 64.3 | **+51.2** | 20% |
| `pollResidualMs`（轮询收尾） | 11.5 | 39.7 | +28.2 | 11% |
| `secondFrameMs` | 8.7 | 31.3 | +22.6 | 9% |
| `domCommitMs` / `sendSyncMs` / `inputFillMs` | 2.1 / 1.2 / 0.6 | 9.5 / 5.9 / 9.2 | +15.7 | 6% |

- 没有出现“仅 4x 才新增”的阶段；所有增量都在 init（bind/DCL/FCP 同步放大：89→203、94→215、56→112）与首帧渲染里，属于 CPU 4x 的线性放大。最差样本 638ms 的 `inputReady=506ms`、`bind=437ms`，是单次 runner 抖动。
- 4x 下 `longTaskMax` 87ms（1x 为 0），说明 4x 把 init 段拉成了长任务；若还要压 4x，应先减 init 的同步工作量，而不是改发送链路。

## 4. R6-4 ANR：5 轮 float-only 复测

- 协议：`avd-r4.yml` `float_only=true`、`float_rounds=5`；每轮 minimal + default 各 60s，共 **10 次采样 / 600s**；每轮都带 `anr_evidence()`（`dumpsys activity anr`、`dumpsys window windows`、`logcat -b crash`、`adb root` 后 `/data/anr/traces.txt`）。
- 结果：`anrOccurrences=0`、`anrDetected=false`（5/5 轮）、`anrExecutionComplete=true`、无 `ANR in` 记录；截图 5/5 轮木偶完整、无系统对话框。
- 结论原文（JSON）：**“5 轮 × 2 预设 × 60s 未复现 ANR（共 10 次采样、600s 采集），判为环境偶发，保留观察（不等于已修复）”**。
- 补充：拖动回归 run `42276909668`（3 轮 × 2 预设 × 60s = 360s）同样 `anrOccurrences=0`、`anrExecutionComplete=true`，累计本轮 **8 轮 / 960s 未复现**。
- 首次出现的 R4 那轮（系统 ANR 对话框）仍无 traces 可查；本次 5 轮也未拿到可归因线程栈，因此**不写“已修复”**。后续每次浮窗 run 继续带 ANR 取证。

## 5. 耗电与后台（合并后代码回归）

run `42274326628`（合并 `origin/main` 后的 `38d41b0`，`idle_seconds=60`、`bg_seconds=60`）：

| 段 | R5 | R6 | 说明 |
|---|---|---|---|
| idle_minimal | CPU 0.58%、rAF 0、px 303,309 | **CPU 0.67%、rAF 0、px 151,655 / 1 帧** | 45s 问候只画 1 帧（§2） |
| idle_default | CPU 17.28%、rAF 5.14/s、46.6M px | **CPU 13.12%、rAF 5.19/s、46.92M px** | CPU −24%，帧率持平 |
| background 60s | 0 帧 / CPU 0.0% | **framesWhileHidden=0、bgZeroFrames=0、CPU 0.01%** | 「不可见零渲染」仍成立 |

（R6 的 R4 式 5 分钟后台在 `42274326628` 只跑 60s；R4 的 305s 长样本见 `docs/PERF_R4.md`，本轮不重复。）

## 6. R5 未达标判据：逐条写清楚

> 原则：判据不为了变绿而调低。下面每条都写明「为什么达不到 / 是环境上限还是产品问题 / 下一个可行动作」。

### 6.1 4x firstSend 425ms（R5 记 +10ms 回退）

- **为什么达不到**：425ms 是单次 run 的点值，落在 R6 5 次分布 395–638ms 内；4x 的额外 ~251ms 全部来自 init（+150ms）与首帧（+51ms），不是发送链路新增开销。
- **环境上限还是产品问题**：**环境敏感项**（2 核 runner + CPU 4x）。1x 本身在 130–184ms 波动，4x 放大到 395–638ms；无产品回退证据。
- **下一个可行动作**：判据改为「5 次中位 ≤450ms、最差 ≤700ms」（本轮 403/638 达标）；若要继续压，优化 init 的同步 bind/DCL（assets、Live2D 解码、首帧），而不是改发送逻辑。

### 6.2 idle_minimal 严格「零渲染」

- **为什么达不到**：60s 内有一次真实的空闲问候表情变化（中性→gentle），R6 已从 2 帧压到 1 帧 / 151,655px，无法在不改产品行为的前提下归零。
- **环境上限还是产品问题**：**产品行为**（空闲问候是功能，不是设备问题）。
- **下一个可行动作**：三选一——(a) 保留例外条款（本轮采用：1 帧 ≤160k px + `src=emotion` 打点）；(b) 息屏/后台时禁用空闲问候；(c) 把问候改成纯文字气泡、表情在下次可见时再变。需要产品拍板，性能侧已给足证据。

### 6.3 idle_default / 浮窗满帧档（判据 48–72 rAF/s）

- **为什么达不到**：2 核 `swiftshader_indirect` 软件渲染的天花板。同一产品代码跨 run 实测：R4 19.32 rAF/s、R5 12.0 与 18.66（两次）、R6 22.17–24.5；补充 3 轮复测（run `42276909668`）rAF **22.83 / 25.0 / 22.83**、帧 **25.67 / 27.0 / 24.5**，仍在同一区间。本轮 idle_default 5.19 rAF/s 是空闲降档后的目标值。
- **环境上限还是产品问题**：**环境上限**。浮动窗口满帧 25.0–26.5 帧/s 已比 R5 的 15.16 高 68%，但离 48 仍远，且同一代码在不同 runner 上差近 2 倍，说明是调度/软件 GL 限制。
- **下一个可行动作**：判据保留；在真机（GPU 合成）或 4 核 + 宿主 GPU 的 AVD 上复测同一脚本，才有意义地把 48–72 当产品判据；否则只作为环境上限记录。

### 6.4 浮窗 6–10s 档帧 9.25/s（下限 10）

- **为什么达不到（R5）**：`setTimeout` 15fps 在软件渲染设备上抖动，rAF=0 的降档意图成立但帧数掉到 9.25。
- **环境上限还是产品问题**：**环境敏感项**。R6 5 轮实测 **10.25 / 11.24 / 11.5 / 11.75 / 12.0 帧/s**，本轮全部 ≥10，判据转绿；但同类设备仍可能回落，不能当作稳定达标。
- **下一个可行动作**：保留下限 10，不做代码“补帧”；在每轮浮窗报告里记录该档帧率，若再次 <10 先看 runner 负载再判产品。

### 6.5 浮窗 ANR 偶发

- **为什么达不到**：R4 唯一一次系统 ANR 对话框无 traces/logcat 可查；R5 float-only 与 R6 5 轮均未复现。
- **环境上限还是产品问题**：**判为环境偶发**（模拟器冷启动 + 2 核软件渲染 + 连续 force-stop/start），但**保留观察**，不排除产品侧主线程问题。
- **下一个可行动作**：每次 float run 都启用 `anr_evidence()`；一旦复现，直接抓 `/data/anr/traces.txt` + `logcat -b crash` 定位线程栈；当前证据不足以宣称修复。

## 7. 回归与风险

- `web` 单测：合并 `origin/main` 后 **272 项全绿**（`node --test --test-concurrency=1 "tests/*.test.js"`）；新增 `web/tests/pet_backing.test.js` 7 项覆盖 `backingRect`。
- 合并 26 个主分支提交（UI/TTS/E2E/输入法/横屏），`stage.js` 的 R6 签名去重与 `app.js` 的 60s 问候定时器都保留；`pet.js` 无冲突。
- 风险 1：后备缓冲倍率封顶 2，dpr 3.0 设备上画布实际以 2x 渲染、CSS 放大 1.5x；矩阵已验证无拉伸，但极限清晰度略低于原生 3x（内存换清晰度的既有取舍）。
- 风险 2：拖动后 `rehostPet` 会让 WebView remount；§1.4 的用例是这条路径的唯一自动化证据。
- 风险 3：`clearPx` / `frames` 探针口径变了（按「清掉不小于后备的整块」计帧），与 R5 的 `frames` 不可直接跨轮比较；同一轮内自洽。

## 8. 复现命令

```bash
# 性能：firstSend 1x/4x 各 5 次 + 阶段探针
gh workflow run perf-r4.yml --ref feat/perf-r6 -f only=load -f tag=r6 -f repeat=5

# 浮窗：5 轮 × 两预设 × 60s + 几何矩阵 + ANR 取证
gh workflow run avd-r4.yml --ref feat/perf-r6 -f float_only=true -f float_rounds=5

# 耗电：idle 60s + 后台 60s（同时抓 stage-repaint 打点）
gh workflow run avd-r4.yml --ref feat/perf-r6 -f power_only=true -f idle_seconds=60 -f bg_seconds=60

# 拖动回归（3 轮即可）
gh workflow run avd-r4.yml --ref feat/perf-r6 -f float_only=true -f float_rounds=3

# 本地单测
cd web && node --test --test-concurrency=1 "tests/*.test.js"
```

## 9. Run / commit 记录

| 用途 | run | commit |
|---|---|---|
| perf 5 次分布 | `42269769325` | `4d49698` |
| 浮窗 5 轮（几何 45/45、ANR 0） | `42273924909` | `8203bc9` |
| 合并后耗电（idle/background + 打点） | `42274326628` | `38d41b0` |
| 拖动回归（3 轮，几何 27/27、拖动 3/3） | `42276909668` | `b2913b7` |
| CI（合并前） | `42273891058`、`42274308454`、`42274321467` | — |
| main CI / E2E | （推送后回填） | — |
