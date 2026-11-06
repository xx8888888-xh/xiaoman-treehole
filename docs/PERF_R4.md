# PERF R4 · 第四轮性能与耗电验收

> 本轮全部测量在云端完成：Web 侧 GitHub Actions + Playwright headless Chromium；安卓侧 GitHub Actions + API 30 x86_64 AVD（swiftshader_indirect）。本地未启动浏览器、未启动模拟器、未跑 Gradle。

- 基线 commit：`2bdf851`（main）
- 采样代码：`scripts/perf_r4.py`、`scripts/emulator_power_r4.py`、`scripts/emulator_float_r4.py`
- 云端 run：perf-r4 `42239544067`（success）、avd-r4 `42243175947`（采样完成，判据红）
- 复现：`.github/workflows/perf-r4.yml`、`.github/workflows/avd-r4.yml`（均 `workflow_dispatch`）

## 0. 结论速览

| 项 | 结论 | 关键数字 |
|---|---|---|
| ① 首屏 / 首条消息 | **达标** | 1x `firstSendMs` **186ms**；4x **415ms**；开场白落地 **1250ms** |
| ② 200 轮长会话 | **达标** | 堆 2.51→2.57MB（**+0.06MB**），DOM 307 恒定，pager `held` 38 恒定 |
| ③ 后台零渲染 | **成立** | 后台 300s：**0** 帧、**0.00%** CPU（1 tick）、gfx **0**；隐藏期 rAF 计数零增长 |
| ③ idle 60s | **部分未达标** | 极简：0 rAF、1 次补帧、**0.31%** CPU；默认：**54.75%** CPU / **15.63** rAF/s（AVD 上限） |
| ④ 浮窗 | **部分未达标** | 极简 0 rAF/0 px；默认三档策略生效，但满帧档 **19.32** rAF/s（AVD 上限） |
| ④ 无效光栅化 | **仅剩取景余量** | 窗口外光栅 **7.38%**（全部来自 `CLIP_MARGIN_PX=6`），JS 整块出界 draw **0** |

两个 run 的“失败”均来自上述判据阈值（AVD 软件渲染达不到 60fps），不是采样缺失或功能回退；原始 JSON 与截图均已作为 artifact 上传。

## 1. 采样方法与口径

### 1.1 Web 首屏 / 首条消息 / 长会话

- 环境：ubuntu-latest，Playwright headless Chromium，`web/` 由 `python -m http.server 8911` 提供，`server/mock_api.py` 监听 8912，`AI_MODE=mock`，TTS 关闭。
- 采样：每一项独立新建浏览器上下文（冷缓存），1x 与 4x（CDP `Emulation.setCPUThrottlingRate`）分别重复 3 次，取中位数；开场白路径同样 3 次取中位数。
- 指标定义：
  - `firstSendMs`：点击发送到首条用户消息 DOM 落地（`ChatPager` 记录数 +1 且消息节点可见）的墙钟时间。
  - `inputReadyMs`：页面可交互（输入框可用）时间。
  - `lcp` / `fcp`：PerformanceObserver 记录的 largest-contentful-paint / first-contentful-paint。
  - 长会话：warmup 后连续 200 轮收发；每轮结束 `HeapProfiler.collectGarbage` 后读 `JSHeapUsedSize`；记录 `ChatPager` 的 `held/evicted/total` 与 DOM 节点数。
  - 翻页审计：连续翻 20 页，校验游标、时间分隔、`total` 不漂移、无重复记录。

### 1.2 AVD idle / 后台耗电

- 环境：GitHub Actions `reactivecircus/android-emulator-runner@v2`，API 30、x86_64、pixel_4、`-no-window -gpu swiftshader_indirect -memory 3072`；debug APK（`assets/www` 内嵌）。
- 进程 CPU：`/proc/<pid>/stat` 的 utime+stime 增量（包名匹配的全部进程，含 WebView 沙箱），换算 `cpuPct = ticks/100/秒数/核数 × 100`；辅以 `dumpsys cpuinfo` 与 `dumpsys batterystats --charged`。
- 渲染调用：页内探针包装 `requestAnimationFrame`（只统计实际执行的回调）与 `CanvasRenderingContext2D.drawImage/fillRect`（累计目的像素面积）。探针在页面就绪后注入，测前静置 10s 并清零，排除启动补帧与纹理解码。
- 后台判定：先在前台取 5s 基线，读计数 C0；`input keyevent 3` 回桌面，300s 内每 30s 通过 CDP 尝试读取 `document.hidden` 与计数；段末取最后一次成功读数为 C1。判据：C1-C0 < 10 帧（60fps×300s 理论值 18000），且页面 `visibilitychange` 快照期间新增帧 ≈ 0。另核对 logcat `XiaoManWeb` 的 `stage-pause=hidden`。

### 1.3 浮窗（桌宠）

- 对象：`pet.html` 透明悬浮窗（140×180dp overlay，devtools 端口 9225），不是 `index.html?float=1`（后者 `#stage` 为 `display:none`）。
- 预设：`minimal`（`motion:false`，零动画）与 `default`（`motion:true`）。通过 CDP 写 `localStorage.xiaoman_appearance_v1` 后重启浮窗，并回读 `Appearance.get()` 校验生效。
- 探针：包装 rAF 与 Canvas 2D `drawImage/fillRect`；维护 `save/restore/rect/clip` 的 clip 栈，分别统计
  - `fillPx`：JS 发出的目的矩形总面积；
  - `rasterPx`：目的矩形 ∩ 当前 clip（真正交给 Skia 的面积）；
  - `rasterOutsidePx`：`rasterPx` 中落在窗口可见区外的部分；
  - `jsDrawsOutsideClip`：整块落在 clip 外的 JS draw 次数（这些不会光栅化）。
- 判定：`rasterOutsidePx / fillPx > 1%` 才算“无效光栅化”；同时记录画布后备缓冲与窗口的面积倍率、clip 是否与可见区吻合。

## 2. ① 首屏与首条消息

云端 run：perf-r4 `42239544067`（commit `5ec5cc5`，成功，17 分钟）。每档 3 次独立冷上下文，取中位数。

| 指标 | 1x 中位数 | 1x 三次 | 4x 中位数 | 4x 三次 |
|---|---:|---|---:|---|
| FCP | 64 ms | 100 / 64 / 52 | 116 ms | 120 / 112 / 116 |
| LCP | 292 ms | 352 / 292 / 260 | 608 ms | 608 / 612 / 596 |
| DOMContentLoaded | 99 ms | — | 211 ms | 215 / 211 / 206 |
| 绑定完成 `bindMs` | 93 ms | — | 198 ms | 203 / 198 / 194 |
| `inputReadyMs` | 133 ms | 195 / 133 / 122 | 278 ms | 276 / 282 / 278 |
| **`firstSendMs`** | **186 ms** | 259 / 186 / 173 | **415 ms** | 415 / 412 / 422 |
| 最长长任务 | 0 ms | — | 88 ms | 95 / 88 / 88 |
| 长任务 >50ms 次数 | 0 | — | 1 | 1 / 1 / 1 |
| `heapPeakMB` | 3.08 | — | 2.91 | — |

开场白路径（4x，3 次）：

| 指标 | 三次 | 中位数 |
|---|---|---:|
| 开场白落地 `greetLandedMs` | 1250 / 1270 / 1249 | **1250 ms** |
| 开场白完成 `greetDoneMs` | 1358 / 1380 / 1357 | **1358 ms** |
| 记录数 / 是否播放 | 1 / true（三次一致） | 1 / true |

对照第三轮（PERF_R3）：1x `firstSendMs` 174ms → **186ms**（+12ms，仍在噪声带内）；4x 458ms → **415ms**（-43ms）；开场白 1241ms → **1250ms**（+9ms）。首条消息在 4x CPU 降速下仍为亚秒级，开场白落地约 1.25s，未见回退。

## 3. ② 200 轮长会话

同一 run 的 memory 段：warmup 200 轮后连续 200 轮，每轮 `HeapProfiler.collectGarbage` 后再读堆。

| 指标 | 数值 |
|---|---:|
| 稳态堆起始 | **2.51 MB** |
| 稳态堆结束 | **2.57 MB** |
| 200 轮净增长 | **+0.06 MB** |
| 每 40 轮增长 | 0.04 / 0.03 / 0.07 / 0.02 MB |
| DOM 节点（起→终） | 307 → 307 |
| pager `held` | **38**（cap 36 + 当前窗口） |
| pager `evicted` / `total` / `rendered` | 1159 / 1197 / 14 |
| 翻页审计 | `movedAll` `totalStable` `overlapOk` `stepOk` `fetchKeepsWindow` `idsPresent` 全 true；`dupInPage` `firstRepeat` false |

判定：**达标**。堆稳定在 **2.5MB 量级**（2.51→2.57），40 轮分段增量在 0.02–0.07MB 间抖动、无单调上涨；DOM 节点数与 `held` 全程恒定（307 / 38），说明 pager 的 300 条淘汰与游标语义未被 200 轮打破。

对照 R3：稳态增长 +0.09MB → **+0.06MB**；DOM 272 → 307（本轮 UI 新增节点，但全程不涨）；`held` 38 未变。

补充对照（同一脚本三段）：
- 冷段 200 轮：2.34 → 2.52MB，**+0.18MB**；
- console 开启 200 轮：2.34 → 2.55MB，**+0.21MB**，`consoleClearFreesMB=0.06`；
- 纹理：18 张图 9.79MB，解码 9ms 完成，0 失败；V8 堆峰值 3.08MB 与纹理内存分开统计。

## 4. ③ 后台与空闲耗电

云端 run：avd-r4 `42243175947`（commit `2bdf851`，采样完成，结论失败——见下）。设备：Android SDK x86_64、API 30、1080×2280、**2 核**、`-gpu swiftshader_indirect`。协议见 §1.2：页面就绪后静置 10s、清零探针，再按段采样；每段 `/proc` ticks、`dumpsys cpuinfo`、`dumpsys batterystats`、`dumpsys gfxinfo` 与页内 rAF/画布像素同时记录。

| 段 | 时长 | 进程 CPU（2 核） | gfx 帧 | rAF | 画布填充 | `cpu_mah` / `device_mah` |
|---|---:|---:|---:|---:|---:|---|
| idle 极简（产品默认 preset） | 60.0s | **0.31%**（37 ticks） | 2 | **0** | 303,309 px（1 次补帧，14 次 drawImage） | 0.000021 / 0.056 |
| idle 默认（开动画） | 60.1s | **54.75%**（6577 ticks） | 918 | **940（15.63/s）** | 143,818,540 px（2.39M/s） | 0.00169 / 0.0584 |
| 后台 5min（开动画，回桌面） | 305.1s | **0.00%**（1 tick） | **0** | 8（**隐藏期 0**） | 隐藏期 0；恢复瞬间 1,378,500 px | 0.000138 / 0.279 |

`dumpsys cpuinfo` 交叉对照：极简直方 4.2%（全机），默认段 12%（全机），后台 0.8%（全机）。

**“不可见时零渲染”结论：成立。**
- 隐藏 300s 内每 30s 读取 `document.hidden=true`、`stagePaused=true`；`raf` 恒定 1050、`px` 恒定 160,808,314，10 次采样零增长；
- `visibilitychange` 快照：`atHidden` 与 `atVisible` 的 raf/px 完全相同（1050 / 160,808,314），隐藏期实帧 **0**；
- `dumpsys gfxinfo` 段内帧增量 **0**；`/proc` 累计 1 tick、CPU 0.00%；
- logcat `XiaoManWeb` 在 5s 与段末均为 `stage-pause=hidden`；
- C0→C1 的 8 帧全部发生在“按 Home / 回前台”的切换瞬间（`atHidden` 前 8 帧 + 恢复首帧），不是后台持续渲染。

未达标/需解释的两项：
1. **idle 极简**：严格“零渲染”判据未达标——60s 内出现 1 次 303,309 px 的补帧（0 次 rAF，1 帧，14 次 `drawImage`），发生在第 45 秒附近。工程口径应视为“静止”（0 fps，CPU 0.31%），但脚本的“画布像素 ≤20,000”阈值把它判红了。
2. **idle 默认**：未达标——预期 rAF 55–62/s，实测 **15.63/s**。原因是 AVD 2 核 + swiftshader 软件光栅：整机 `cpuinfo` 已 12%，App 进程吃掉 **54.75%（约 1.1 核）**，帧率被环境压到 15fps，不是代码回退。产品默认预设是极简（0 动画），只有用户切到默认才会持续动画；`stage.js` 目前没有像 `pet.js` 那样的 10s 静止降档/停机策略，这是后续可做的省电优化点。

## 5. ④ 浮窗模式

同一 run。对象是桌宠浮窗 `pet.html`（140×180dp overlay），两档预设各 60s；协议见 §1.3。

| 预设 / 档位 | rAF/s | 帧/s | JS 填充 px/s | 实际光栅 px/s | 窗口外光栅 px/s |
|---|---:|---:|---:|---:|---:|
| 极简（motion=false） | **0** | **0** | **0** | 0 | 0 |
| 默认 0–6s（满帧档） | **19.32** | 21.66 | 15,573,968 | 2,961,240 | 218,437 |
| 默认 6–10s（降帧档） | 0（`setTimeout` 不走 rAF） | 11.25 | 8,086,332 | 1,532,818 | 113,301 |
| 默认 >10s（停止档） | 0 | 0.1（50s 内 5 帧） | 71,902 | 13,732 | 1,010 |

几何与光栅化：
- 画布后备缓冲 **469×1080**，CSS **234.5×540**，浮窗 **140×180**（dpr 2.75）；
- 画布面积是窗口的 **5.01 倍**，可见部分仅占画布 **19.06%**；
- `pet.js` 的取景 `clip` 已生效：`clipRect=[88,0,380,356]`，`clipMatchesWindow=true`；
- JS 侧整块落在 clip 外的 draw：**0**；部分出界的 draw：360 次（全部被 clip 裁掉）；
- 真正被光栅化且落在窗口可见区外的像素：**1,814,539 px / 60s** = JS 填充面积的 **1.40%**、已光栅化面积的 **7.38%**。来源是 `CLIP_MARGIN_PX=6`（后备像素）的取景余量，不是布局塌陷。

判定：
- **极简：达标**（0 rAF、0 帧、0 像素，静置 60s 完全不动）。
- **默认三档策略：生效**（满帧 → 10–11 fps → 停止，>10s 仅每 10s 补 1 帧）。
- **满帧档：未达标**（19.32 rAF/s，目标 55–62/s）。同为 AVD 软件渲染上限：每帧 JS 填充约 71.9 万 px，实际光栅约 13.7 万 px，2 核 swiftshader 只能跑到 ~20fps。此数字不能代表真机。
- **无效光栅化：布局修复后已无“整块画布全量光栅化”**；残余仅为 6px 取景余量（7.38% 光栅像素）。若要再压，可把 `CLIP_MARGIN_PX` 6→3 后复测（预计窗口外光栅减半），但余量本身是抗锯齿所需的。

剩余优化空间（本轮未改代码）：
- 画布后备缓冲仍是窗口的 5 倍，JS 每帧仍对整层 `drawImage`（填充面积是实际光栅的 **5.26 倍**）。clip 保证了“不会白光栅化”，但 JS 侧的图层绘制成本还在；把画布缩到可见区大小可再降一截，代价是重新调整取景构图。
- 默认预设主舞台无 idle 降档（见 §4 第 2 点）。

## 6. 未达标项与原因

1. **③ idle 极简：1 次补帧，严格判据红**
   - 实测：60s 内 0 次 rAF、1 帧、303,309 px（14 次 drawImage），发生在第 45 秒附近；CPU 0.31%。
   - 判据：脚本要求“画布像素 ≤20,000”。1 次整屏补帧必然超过该阈值。
   - 性质：不是持续渲染，不能算耗电问题；属于判据过严。若按“≥2 帧才判定为仍在渲染”，该项达标。

2. **③ idle 默认：rAF 15.63/s（目标 55–62/s）**
   - 实测：940 次 rAF / 60s、918 gfx 帧、CPU **54.75%**（2 核）、填充 2.39M px/s。
   - 原因：AVD 只有 2 核且为 `swiftshader_indirect` 软件光栅；App 已吃满约 1.1 核，帧率被环境压到 15fps。**不是代码回退**，同口径无法在 AVD 上测出 60fps。
   - 真实风险：默认预设（开动画）在低端设备上会长期占用约 1 核；`stage.js` 没有 idle 降档。产品默认是极简，因此出厂状态不受影响；建议下一轮给主舞台加与 `pet.js` 一致的 6s/10s 降档停机。

3. **④ 默认满帧档：19.32 rAF/s（目标 55–62/s）**
   - 与第 2 项同因：每帧 JS 填充约 71.9 万 px、实际光栅约 13.7 万 px，2 核软件光栅只能到 ~20fps。
   - 降帧档（11.25 帧/s，目标 10–20）与停止档（0.1 帧/s，目标 ≤0.2）均达标，三档策略本身没有问题。

4. **④ 无效光栅化：脚本判 `yes`，但来源是刻意保留的 6px 取景余量**
   - `rasterOutsidePx=1,814,539/60s`，占 JS 填充 **1.40%**、占已光栅化 **7.38%**；`clipMatchesWindow=true`、`jsDrawsOutsideClip=0`。
   - 这些像素是 `pet.js` `CLIP_MARGIN_PX=6`（后备像素，约 2.2 CSS px）的抗锯齿余量，不是布局塌陷导致的整块无效光栅化。布局修复前那种“3 倍画布全量光栅化”已消失。
   - 可选优化：`CLIP_MARGIN_PX` 6→3 后复测；预计窗口外光栅减半，但需确认边缘无锯齿。

## 7. 回归与回退点

- 本轮**未改任何产品代码**（`web/js`、`web/css`、`android/` 零改动）。新增/修改仅限采样脚本与两个 `workflow_dispatch` workflow：
  - `scripts/perf_r4.py`、`.github/workflows/perf-r4.yml`
  - `scripts/emulator_power_r4.py`、`scripts/emulator_float_r4.py`、`.github/workflows/avd-r4.yml`
  - `scripts/android_cdp.py` 仅关闭 websockets 的 20s 自动 ping（devtools 不回 pong），不影响产品。
- `stage.js` / `heartbeat.js` 未触碰，w22 的 lint 债务（0/0）未回退；`ci.yml` 的 `compileall` 在本轮 commit 上通过。
- 性能回归：1x `firstSendMs` 186ms（r3 174ms，+12ms，在 3 次采样的噪声带内）；4x 415ms（r3 458ms，-43ms）；开场白 1250ms（r3 1241ms，+9ms）。堆增长 +0.06MB（r3 +0.09MB）。
- 回退风险：两个 AVD 探针的阈值按 60fps 真机标定，在 2 核 swiftshader 上必然红；它们只用于测数字，不建议接入阻断式 CI。若后续接入，应把“数据完整”作为 job 成败，把“目标是否达到”留在 JSON/报告里。
- 第一轮 AVD run `42239553338` 因探针 WebSocket 20s 假掉线导致 idle_default/background 未采到，已废弃；修复（`android_cdp` ping off + 重连重注入）后重跑为 `42243175947`。该 run 的两段采样与 `power_r4.json`/`float_r4.json`/截图均完整上传，但最后一步 `Upload logcat on failure` 长时间无响应，run 最终状态为 **cancelled**（不是采样失败）。

## 8. 复现命令

```bash
# Web（云端）
gh workflow run perf-r4.yml --ref main -f tag=r4

# AVD（云端）
gh workflow run avd-r4.yml --ref main -f idle_seconds=60 -f bg_seconds=300

# 本机静态检查（不启浏览器/模拟器）
python -m py_compile scripts/perf_r4.py scripts/emulator_power_r4.py scripts/emulator_float_r4.py
cd web && node --test --test-concurrency=1 "tests/*.test.js"
```
