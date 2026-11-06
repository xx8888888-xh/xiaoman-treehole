# PERF_R5：树洞性能与耗电第五轮（优化落地复测）

> 相对 [PERF_R4](PERF_R4.md) 的“优化轮”：本轮把 R4 里点名的三个杠杆真正落地，并用同一套云端探针复测。
> 全部在云端跑（Playwright headless Chromium + mock / AVD 软件渲染），本机不启浏览器、不启模拟器。

## 0. 结论速览

| 项目 | R4 | R5 | 变化 | 判定 |
|---|---|---|---|---|
| 1x `firstSendMs` | 186ms | **166ms** | −20ms（−10.8%） | 达标 |
| 4x `firstSendMs` | 415ms | **425ms** | +10ms（+2.4%） | 轻微回退（噪声内，见 §2） |
| 开场白 landed | 1250ms | **938ms** | −312ms（−25.0%） | 达标 |
| 200 轮堆增长 | +0.06MB | **+0.06MB** | 持平 | 达标（2.5MB 量级） |
| idle 默认 CPU（AVD 60s） | 54.75% | **17.28%** | −68.5% | 判据红（环境上限，见 §4） |
| idle 默认 rAF | 15.63/s | **5.14/s** | −67.1% | 同上 |
| 后台 5min 渲染 | 0 帧 | **0 帧** | 持平 | 达标（不可见零渲染仍成立） |
| 浮窗窗口外光栅 | 1.40% fill / 7.38% raster | **0.286% fill** | −79.6%（相对 fill） | 达标 |
| 浮窗默认档满帧 rAF | 19.32/s | **12.0/s** | −37.9% | 判据红（环境上限） |

**本轮没有出现功能回退**：`web` 单测 254 项全绿（R4 基线 239 + 新增 15），`ci` run 成功；产品代码只动 `web/`，Android 原生零改动。

## 1. 采样协议（与 R4 完全一致）

| 项 | 装置 | 轮次/窗口 |
|---|---|---|
| 首屏/首条/开场白 | Playwright headless Chromium + mock API；1x 与 `Emulation.setCPUThrottlingRate(4)` | 每项 3 轮取中位数 |
| 200 轮长会话 | 同上；`HeapProfiler.collectGarbage` 后取堆 | 预热 40 轮 + 200 轮，每 40 轮记一次 |
| 空闲/后台耗电 | AVD API 30 x86_64 pixel_4，2 核 `swiftshader_indirect`，`-no-window` | idle 60s ×2 预设；background 300s |
| 浮窗 | 同上，devtools 端口 9225 连 `pet.html`；页内 clip 栈探针 | 每预设 60s，每秒采样 |

探针口径未改：rAF 只计“实际执行”的回调；像素按 `drawImage`/`fillRect` 目的面积累计；浮窗区分 `fillPx`（JS 绘制）、`rasterPx`（dest∩clip）、`rasterOutsidePx`（窗口外光栅）。

## 2. 首屏与首条消息（run `42250080363`，commit `746a951`）

| 指标（中位数） | 1x R5 | 1x R4 | 4x R5 | 4x R4 |
|---|---|---|---|---|
| FCP | 64ms | 64ms | 124ms | 124ms |
| LCP | **240ms** | 292ms | **516ms** | 608ms |
| DCL | 104ms | 99ms | 229ms | 211ms |
| bind | 98ms | 93ms | 217ms | 278ms |
| inputReady | 128ms | 133ms | 290ms | 278ms |
| **firstSendMs** | **166ms** | 186ms | **425ms** | 415ms |
| 长任务 | 0 | 0 | 1（93ms） | 1（88ms） |

原始三轮 firstSend：1x = 201 / 166 / 160；4x = 425 / 422 / 433。
开场白：**landed 938ms**（938/939/936）、done 1047ms（R4：1250/1270/1249）。

**本轮改了什么**（`web/js/app.js`、`web/js/tts.js`、`web/js/stage.js`）：
- 开场白缓冲窗口的起点从 “init 完成” 提前到 “页面开始”（`performance.now()` timeOrigin）。窗口仍是 900ms 产品语义，但历史恢复慢时开场白仍排在恢复之后，顺序语义不变。
- Live2D 舞台（约 900KB）不再在 init 抢 `requestIdleCallback(timeout 1200)`，改为首条气泡落屏后 200ms 再进 idle；挂载前收到的表情/动作补放。
- 语音改走 `TTS.speakSafe`，语音失败不再拖累文字与发送（新增 3 项测试）。

**未达标 / 需要说明**：
- 4x `firstSendMs` 425ms，比 R4 高 10ms（+2.4%）。R5 三轮 422–433、R4 三轮 412–422，区间重叠；主要成本仍是 Playwright 25ms 轮询 + 4x 下的同步渲染，未做进一步微优化（收益在噪声内，且有输入框高度抖动风险）。
- 开场白已从 1250ms 降到 938ms，但其中约 900ms 是**产品规定的固定缓冲窗口**，不是可优化项；剩余 ~38ms 为渲染与贴底布局。

## 3. 200 轮长会话（同一 run）

| 指标 | R5 | R4 |
|---|---|---|
| heap start → end | 2.53 → **2.59MB** | 2.51 → 2.57MB |
| 每 40 轮增量 | 0.04 / 0.03 / 0.07 / 0.02MB | 0.04 / 0.03 / 0.07 / 0.02MB |
| GC 后标记 | 2.50 / 2.58 / 2.60 / 2.60 / 2.59MB | 同量级 |
| DOM 节点 | 307 → **307** | 307 → 307 |
| pager held / evicted / total | **38 / 1159 / 1197** | 38 / 1159 / 1197 |
| pager rendered | 14 | 14 |
| 翻页审计 | 全 true | 全 true |

堆增长 +0.06MB，逐段无单调上涨（第 3 段 0.07 后第 4 段回落到 0.02），仍在 2.5MB 量级。**达标，无回退。**

## 4. 后台与空闲耗电（AVD run `42250099389`，commit `746a951`，2 核）

### 4.1 idle 60s

| 段 | CPU | gfx 帧 | 页内 rAF | 填充像素 | 判定 |
|---|---|---|---|---|---|
| minimal（motion=false） | 0.74%（89 ticks） | 3 | **0** | 1 次 303,309px（t≈45.4s） | 严格判据红（见下） |
| default（motion=true） | **17.28%（2074 ticks）** | **307** | **308（5.14/s）** | **46,614,642px** | 判据红（环境上限） |
| default（R4 基线） | 54.75%（6577） | 918 | 940（15.63/s） | 143,818,540px | — |

- 默认档相对 R4：CPU −68.5%，rAF −67.2%，填充 −67.6%。降档轨迹：0–3s 满帧 → 3s 后 15fps → 10s 后 5fps → 30s 后 1fps（每秒计数从 5–8 降到 1）。
- 极简档 rAF 恒为 0；60s 内只有 **1 次** 303,309px 的重绘（t≈45.4s）。与 R4 完全同一位置、同一像素量的单次重绘，属于 45s 周期事件（默认档在 45s 也出现一次唤醒，随后 10–14 rAF/s），不是渲染循环。严格判据（>20k px 即判红）因此仍记红，但性质是“静止页的一次性重绘”，不是持续耗电。
- 判据红的环境原因：AVD 只有 2 核且 `swiftshader_indirect` 软件渲染，60fps 档实际只能跑到 12–19 rAF/s；探针期望 55–62 rAF/s，在本装置上不可能满足。

### 4.2 后台 5 分钟

| 指标 | R5 | R4 |
|---|---|---|
| 墙钟 | 305s | 305.1s |
| 进程 CPU | **0.01%（5 ticks）** | 0.00%（1 tick） |
| gfxinfo 新增帧 | **0** | 0 |
| 隐藏期 rAF 增量 | 2（冻结快照） | 8 |
| 隐藏期实际绘制帧 | **0** | 0 |
| logcat | `stage-pause=hidden` | 同 |

**“不可见时零渲染”成立且无回退。** 5 ticks 为采样起止的边界误差，量级仍为 0。

## 5. 浮窗模式（同一 run，`float_r4.json`）

### 5.1 极简预设

0–60s：**rAF 0 / 帧 0 / 填充 0px**（R4 相同），判定达标。

### 5.2 默认预设

几何：canvas 469×1080，CSS 234.5×540，窗口 140×180dp，dpr 2.75；`canvasOverWindow=5.014`、`visibleShare=0.1906`；`clipRect=[92,0,376,346]`、`clipMatchesWindow=true`、`jsDrawsOutsideClip=0`。

| 档位 | R5 rAF | R5 帧 | R5 填充 | R4 对照 |
|---|---|---|---|---|
| 0–6s（满帧） | **12.0/s** | 15.16/s | 10.88M px/s | 19.32/s、21.66/s、15.57M/s |
| 6–10s（15fps） | **0/s**（走 setTimeout） | 9.25/s | 6.64M/s | 11.25 帧/s |
| 10–60s（静止） | **0/s** | 0.04/s（50s 2 帧） | 28,694px/s | 0.1/s（50s 5 帧） |

60s 合计：fill 93,249,844px，raster 16,497,994px，**窗口外光栅 266,711px = fill 的 0.286%**（R4：1,814,539px = fill 的 1.40%、raster 的 7.38%）。静止补帧间隔实测 **20,001ms**（R4 为 10s 档）。
第二段 float-only 复测（run `42253920785`）复核同一比例：fill 123,379,521px、raster 21,845,507px、窗口外 353,055px = **0.286%**（两次完全一致）；满帧档 18.66 rAF/s、20.83 帧/s，接近 R4 的 19.32（同一环境上限）。

**本轮改了什么**（`web/js/pet.js`、`web/js/pet_core.js`）：
- 取景余量 `CLIP_MARGIN_PX` 6 → 2，并把可见 rect 的向外取整抽成纯函数 `clipRect`（新增 4 项几何测试：4 档窗型 × dpr 1/1.5/2 下“可见 rect 完全包含于 clip”）。
- 静止兜底补帧 10s → 20s（只在 overlay 表面可能被系统回收时补一帧，不再追求动画）。

**未达标**：
- 满帧档 12.0 rAF/s、15.16 帧/s，低于判据 48–72：仍是 2 核 swiftshader 上限，与 R4 的 19.32 同因。
- 6–10s 档实测 9.25 帧/s，低于判据下限 10：`setTimeout` 在软件渲染设备上的抖动导致，rAF=0 的“降档意图”成立。
- `invalidRasterization=false`：窗口外光栅只剩取景余量（2px），不是布局塌陷。

### 5.3 浮窗 ANR 异常（待复测）

默认档截图（`emulator_float_r4_default.png`）在采样末尾捕捉到系统对话框 **“小满树洞 isn't responding / Close app / Wait”**；极简档截图正常（木偶完整、无裁剪）。
- 同段采样数据完整：三档策略按 12.0/s → 9.25 帧/s → 0.04 帧/s 执行，`stoppedIntervalsMs=20001`，说明页面在 10–60s 内仍按策略存活（20s/40s 各补 1 帧）。
- 该 run 的 logcat 摘要只过滤了 `[probe] pet-*`，未含 ANR 记录；因此无法判定是“采样结束后的系统启动抖动/2 核 CPU 争抢”，还是产品侧主线程阻塞。
- 已给浮窗探针加 `anr_evidence()`（`dumpsys activity anr` / `dumpsys window windows` / 最近 4000 行 logcat 关键字），并支持 `float_only` 复测；复测结论见下。

**复测结论（run `42253920785`，`float_only=true`）**：**未复现**。探针新增的 `anr_evidence()` 采集 `dumpsys activity anr` / `dumpsys window windows` / 最近 4000 行 logcat，`anrDetected=false`、无任何匹配；两档截图均正常，默认档木偶完整、无裁剪、无对话框（`emulator_float_r4_default.png`）。
因此首轮 ANR 最可能是“模拟器冷启动 + 2 核软件渲染 + 连续 force-stop/start”的系统侧抖动，采样数据本身完整（三档策略按时执行）。仍不能完全排除偶发，后续每轮浮窗探针都会带 ANR 取证；本轮不把它记为产品回退，但保留为**未闭环观察项**。

## 6. 未达标项汇总（不粉饰）

1. **4x firstSend 425ms（+10ms）**：区间与 R4 重叠，视为噪声内轻微回退；未做进一步优化，原因见 §2。
2. **idle_minimal 严格判据红**：60s 内 1 次 303,309px 重绘（45s 周期事件），非循环渲染；判据未放宽。
3. **idle_default / 浮窗满帧档判据红**：2 核 `swiftshader_indirect` 上限导致，绝对指标已较 R4 降 67%–68%；这是装置上限，不是产品未优化。
4. **浮窗 6–10s 档帧 9.25/s（下限 10）**：setTimeout 抖动，rAF=0 目标成立。
5. **浮窗默认档 ANR 异常**：首轮截图偶发一次系统 ANR 对话框；float-only 复测（`42253920785`）未复现、无 ANR 记录、截图正常。记为未闭环观察项，不算产品回退。

## 7. 回归与风险

- `web` 单测：**254 项全绿**（`node --test --test-concurrency=1 "tests/*.test.js"`）；`ci` run `42253908581`（commit `f7f5bb5`）通过 lint/构建。
- 行为风险：
  - 舞台 30s 后降到 1fps，眨眼在该档不采样（保持睁眼），离开该档后恢复；默认预设仍有轻微呼吸。
  - 开场白计时起点改为页面开始，若 init 超过 900ms，开场白会在历史恢复完成后立即出现（顺序不变，视觉上更快）。
  - 舞台挂载延后到首条气泡后 200ms；挂载前的表情/动作会补放，Greeting 动作不丢。
  - 浮窗取景余量 2px：几何测试保证可见 rect 完全被 clip 包含；极简档真机截图确认无裁剪；默认档截图被 ANR 对话框遮挡，木偶边缘仍完整。
- 回退点：`web/js/stage.js`（idle governor）、`web/js/app.js`（开场白计时/挂载时序）、`web/js/pet.js` + `pet_core.js`（clipRect）、`web/js/tts.js`（speakSafe）。

## 8. 复现命令

```bash
# 性能（云端）
gh workflow run perf-r4.yml --ref feat/perf-r5

# 耗电 + 浮窗（云端）
gh workflow run avd-r4.yml --ref feat/perf-r5 -f idle_seconds=60 -f bg_seconds=300

# 只复测浮窗（跳过 idle/background）
gh workflow run avd-r4.yml --ref feat/perf-r5 -f float_only=true

# 本地单测（串行）
cd web && node --test --test-concurrency=1 "tests/*.test.js"
```
