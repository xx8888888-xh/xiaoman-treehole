# r2 · Android 耗电与包体专项（断电后恢复续做）

- 分支：`feat/apk-slim-power`（worktree `treehole-w17`，起点 `r4-20261105` / `86c2d11`）
- 范围：本轮**未启动 AVD、未跑 5 分钟耗电采样、未在本地 Gradle 构建**；只做代码与资产改动 + 本地短时页面级验证
- 文件域：`web/js/pet.js`、`web/assets/**`、`android/**`、`scripts/optimize_art.py`、`scripts/asset_excludes.sh`、`scripts/verify_assets.py`、`docs/android-battery/**`

## 结论（TL;DR）

| 项目 | 结果 |
|---|---|
| 桌宠降载 | **已落地**：满帧 → 6s 后 15fps（改走 `setTimeout`，不再占 rAF）→ 10s 完全停 rAF + draw（仅 10s 呼吸补一帧）；`blur`/`pagehide`/息屏/被遮挡沿用可见性停渲；渲染加**可见区取景 clip**，软件光栅面积降到约 20%。页面冒烟 **7/7**、`test_raf_budget` **8/8** |
| 包体 | APK 实测旧 **6,232,085 B（5.94 MiB）** → 新构建**投影 987,098 B（0.94 MiB）**，目标 ≤3.5 MiB 大幅满足；`assets/www` 5.89 → 0.89 MiB，图片 5.73 → 0.72 MiB |
| 视觉 | 模型区域逐像素比对：均差 **0.32 / 255**，>8 灰阶像素仅 **0.035%**；`test_live2d_original` 改动前后均 **24/24**，截图见下 |
| 字体 | 130% 上限是**历史保守值、从未实测**（横扫里的「1.5」实际按 1.3 渲染）；现放开到 **150%**，Chromium textZoom 语义模拟 1.0/1.3/1.45/1.5 **4/4 无横向溢出**，真机复测项见文末 |
| 闸门 | 本地已过：raf_budget 8/8、live2d 24/24、verify_assets PASS、node 资产契约 7/7、字号布局 4/4；完整闸门（node 181 / pytest 145+19 / biome / E2E 60 / feature_loop 43）**交云端** |

> 口径修正（重要）：`ANDROID_BATTERY_REPORT.md`（w14，`61384a0`）里的「4 张 Live2D 2048 纹理占 88.2%」在当前 `r4-20261105` 上**已经过时**。HEAD 的桌宠是分层水彩（`web/js/vendor/puppet.js` + `LayeredPuppet`），APK 内图片构成是 `art/full.png` + `art/layers` 基准层 + 两档 tier + silhouette + thumb，合计 **6,007,822 B（占旧 APK 96.4%）**；树内已无 `.moc3`。本轮据此重新做包体专项。

---

## 1. 桌宠渲染降载（`web/js/pet.js`）

### 1.1 三档调度

| 静止时长 | 行为 | 实现 |
|---|---|---|
| 0–6 s | 满帧（rAF，60fps） | `frame()` 走 `requestAnimationFrame` |
| 6–10 s | **15fps** | 降帧期改走 `setTimeout(1000/15)`，**rAF 计数直接归零**；单帧 `dt` 上限放宽到 90ms，动画速度不变 |
| ≥10 s | **完全停渲** | `enterIdleStop()`：取消 rAF 与定时器，只留 10s 一次的呼吸节拍（`renderFrame()` 一帧） |
| 触摸 / 互动 / 原生补帧 / 可见性变化 | 立刻回满帧 | `noteActivity()` → `startLoop()`；看门狗不会把「刻意停掉」的循环拉起（以 `idleStopped` 判定） |

`__pet.state()` 新增 `mode`（`full`/`preview`/`stopped`）与 `loop`，供自动化断言，不影响旧字段。

### 1.2 只画看得见的部分（取景 clip）

`#petStage` 里的画布 CSS 高 **300% 窗高**、宽屏时左右也溢出，真正可见的只有约 1/3 高、60% 宽（≈整块面积的 **20%**）。Canvas2D 在软件光栅下会为整块后备缓冲逐帧上色，于是每帧 `ctx.clip()` 到可见矩形（含 6px 余量），Skia 只处理可见带。帧量不变，像素量按面积下降。这不是引擎级脏矩形，但等效于「只重画会显示的区域」；引擎（`web/js/vendor/puppet.js`）在文件域外，未改。

### 1.3 验证（本地短时，非真机）

| 断言 | 结果 |
|---|---|
| 满帧 1s rAF ≥30 | **PASS** |
| 6s 后 `mode=preview`，2s 内 rAF = **0**，循环仍在 | **PASS** |
| 10s 后 `mode=stopped`，3s 内 rAF = 0，画面 `toDataURL` 完全定格 | **PASS** |
| 点击后回 `mode=full`，0.8s rAF ≥30 | **PASS** |
| `visibilitychange → hidden`：2s 内 rAF = 0、无定时器 | **PASS** |
| 恢复可见：0.6s 内 rAF ≥20 | **PASS** |
| `scripts/test_raf_budget.py`（`--seconds 20` 本地快速档） | **8/8 PASS**（云端跑 60s 全档） |

截图：`docs/android-battery/shots/raf/pet_idle_r2.png`（隐身静止时的桌宠，模型完整、无裁切硬边）。

### 1.4 仍待真机确认的假设

1. **息屏/被完全遮挡是否触发 `hidden`**：Android WebView 通常随 `onWindowVisibilityChanged` 发 `visibilitychange`，但悬浮窗（`TYPE_APPLICATION_OVERLAY`）在桌面可见时 `document.hidden=false` 属预期（桌宠本来就该继续动）。若复测发现息屏后仍在跑，在原生 `onWindowVisibilityChanged` 里给页面派发 `visibilitychange` 即可复用现有停渲逻辑（已有 `window.__petRepaint()` 反向钩子），不需要改调度。
2. **CPU 预期**：后台桌宠 5 分钟 194.2s（64.7%）→ 静置后前 10s 按旧逻辑走、之后零帧，**预期 ≤20s（≤7%）**；前台静止 25.5s → **预期 <8s**。真实数字以复测为准，本轮不预填。

---

## 2. 包体瘦身

### 2.1 前后构成

| 项 | 旧 APK（实测 zip） | 新（`build_assets.sh` 产物 + 同规则打包投影） |
|---|---|---|
| APK 总字节 | 6,232,085 B（5.94 MiB） | 987,098 B（0.94 MiB） |
| `assets/www` | 6,179,043 B（99.2%） | 934,056 B |
| 其中图片（stored） | 6,007,822 B | 759,552 B |
| 其中文本/JSON（deflate） | 171,221 B | 174,504 B |
| 非 `assets/www`（dex/资源/清单） | 53,042 B | 53,042 B（未变） |
| 条目数 | 87（www） | 68（www） |

图片逐项（新）：

| 文件 | 旧 | 新 |
|---|---|---|
| `art/full.png`（静态降级整图） | 1,900,078 | 324,488 |
| `art/tiers/s070/layers/haori.png` | 605,650 | 99,641 |
| `art/tiers/s070/layers/body.png` | 336,746 | 61,353 |
| `art/tiers/s070/layers/head.png` | 189,662 | 39,595 |
| `art/tiers/s050/layers/haori.png` | 353,463 | 60,411 |
| `art/tiers/s050/layers/body.png` | 202,166 | 36,783 |
| `art/silhouette.png` | 135,062 | 28,378 |
| `art/tiers/s050/layers/head.png` | 106,784 | 22,939 |
| 眼/嘴小图（20 张，<20KiB 不量化） | ≈70,000 | 不变 |
| `art/layers/**` 基准层（16 张） | 2,064,456 | **0（不进 APK）** |
| `art/thumb.png` | 224,118 | **0（不进 APK）** |

### 2.2 做法与依据

1. **256 色调色板 PNG**（`scripts/optimize_art.py`，无抖动、幂等）：只处理运行时会加载、且 ≥20KiB 的 8 张图，3.83 MiB → 674 KiB（17–21%）。
   - 先逐张比对过 WebP q90/q85：q90 合计 1.17 MiB、q85 0.99 MiB，**反而比调色板 PNG 大**，且改扩展名要动 `manifest`、`stage.js` 与两套测试的路径契约；调色板方案**尺寸、清单、tier decoded、断言全不变**，是这一轮风险最低的解。
   - 眼睛/嘴型（1–14KiB）保留原始 PNG，不做量化。
2. **APK 剔除运行时不会请求的图**（`scripts/asset_excludes.sh` + `verify_assets.py` 同步）：
   - `art/layers/**`（全尺寸基准层 2.06 MiB）：`pickTier` 必给 `s070`/`s050`，`mapPath` 把 `art/layers/` 换成档内同路径，基准层永远不被请求；沿用既有 Hiyori 2048 的排除先例。
   - `art/thumb.png`（224 KiB）：全仓无引用（`grep -rn 'thumb' web/` 为空）。
   - **仅在 APK 资产里剔除，`web/` 源文件保留**（网页/容器版与 node 资产契约测试不受影响）。
3. **`noCompress` 检查**：`android/app/build.gradle.kts` 没有自定义 `androidResources.noCompress`；图片按 aapt2 默认 **stored** 打包，体积等于原始字节 —— 这正是照片/水彩 PNG 必须做量化/降采样才有收益的原因，本轮无需改构建配置。

### 2.3 重新生成与校验

```bash
# 重新压缩（幂等，已 ≤256 色的图会跳过）
python scripts/optimize_art.py --apply
# 重建 APK 资产（Linux/WSL）
wsl -d Ubuntu --cd "D:\我的\编程\vibe coding tools\treehole-w17" -- bash scripts/build_assets.sh
python scripts/verify_assets.py
```

`build_assets.sh` 输出：`assets/www 1.18 MiB · 67 个文件`，`verify_assets` 源↔产物逐文件一致 **PASS**。

---

## 3. 视觉把关

| 检查 | 结果 |
|---|---|
| `test_live2d_original.py`（改动前基线） | **24/24 PASS**（`artifacts/live2d_before`） |
| 同上（量化 + 剔除后） | **24/24 PASS**（`artifacts/live2d_after`） |
| 静态降级帧（确定性，`05-degrade-static.png`）模型区 0–600px 逐像素 | 均差 **0.32/255**，最大 19，>8 灰阶仅 **0.035%** → 肉身不可辨 |
| 默认页（`01-default-light.png`）整页逐像素 | 均差 0.18；差异集中在正文气泡区（该次截图多出一条随机问候，与资产无关） |
| 桌宠（s070 档）截图 | 清晰、无锯齿/色块（`shots/raf/pet_idle_r2.png`） |
| 逐张人眼比对 | `head`（脸部发丝）2× 放大、`haori`（衣袖渐变）1:1：无可辨色带，仅透明度边缘有 ≤2 级差 |
| node 资产契约 `web/tests/live2d_assets.test.js` | **7/7 PASS**（尺寸/清单/tier decoded 均未变） |

截图路径：
- 基线：`docs/android-battery/shots/r2-before/01-default-light.png`、`05-degrade-static.png`、`06-degrade-silhouette.png`、`08-widget-focus.png`
- 改后：`artifacts/live2d_after/`（同名）
- 桌宠：`docs/android-battery/shots/raf/pet_idle_r2.png`

### 3.1 待真机确认

- 静态降级与 s070 纹理在 **AVD/真机 WebView（GPU 光栅）** 下的观感：调色板量化在 CPU 光栅下已证不可辨，GPU 放大 3×（s070 为 0.7× 源图）风险极低，但仍应在新一轮适配横扫的截图里复看一次。
- 若后续要回到「无损」：`web/` 里保留的是**已量化成品**，原始高清图不在树内；需要时从 `treehole-local-assets` 重新导出（本轮按文件域规定未动该目录）。

---

## 4. 字体 130% → 150%

### 4.1 原因的查证

`WebShell.applyFontScale()` 的 `Math.min(130, zoom)` 与注释「85%–130%：覆盖系统 1.3× 字号，且不撑破布局」是**迁入时一次性写死的保守上限**，全仓无对应实测（`docs/android-battery` 里横扫脚本只调了 1.0/1.3，`font_scale 1.5` 也被同一个 clamp 吃成 1.3）。所以「1.5 被降级到 130%」不是布局容不下，而是**上限从未放开过**。

### 4.2 本轮做法与实测

- `WebShell.java`：上限 **130 → 150**（系统「特大」原样生效），下限仍 85；同步修正 `MainActivity` 注释。
- 用 Chromium 复刻 textZoom 语义（按元素 computed `font-size` ×k 重排），在 390×844@2x + mock 会话（一条长用户消息 + 回复）下断言：无横向溢出（`scrollWidth ≤ vw`）、无气泡越界、输入栏完整可见。

| 字号 | scrollW/vw | 输入栏 | 越界气泡 | 溢出气泡行 | 结果 |
|---|---|---|---|---|---|
| 1.0 | 390/390 | 可见 | 0 | 0 | **PASS** |
| 1.3 | 390/390 | 可见 | 0 | 0 | **PASS** |
| 1.45 | 390/390 | 可见 | 0 | 0 | **PASS** |
| 1.5 | 390/390 | 可见 | 0 | 0 | **PASS** |

截图：`docs/android-battery/shots/r2-font/font-1.0.png`、`font-1.3.png`、`font-1.45.png`、`font-1.5.png`。1.5 下气泡宽度仍受 `max-width` 百分比约束（未变宽），只变高——页面纵向滚动，输入栏不动。

### 4.3 待真机确认

- 真机 `settings put system font_scale 1.5` 后 `textZoom=150` 的实际渲染与横扫 19 项复跑（重点：弹窗/设置页/输入栏）。WebView 的 textZoom 只放大字号、不改 px 行高，与本轮模拟口径一致；若真机出现横向溢出，把上限改回 140 即可（单行改动）。

---

## 5. 闸门

| 闸门 | 状态 |
|---|---|
| pet 静止分档冒烟（自建 7 项） | **7/7 PASS**（本地） |
| `test_raf_budget.py` | **8/8 PASS**（本地 `--seconds 20`；`--seconds 60` 交云端） |
| `test_live2d_original.py` | **24/24 PASS**（改动前后各一次，本地） |
| `verify_assets.py` | **PASS**（本地，WSL 重建后） |
| node `live2d_assets.test.js` | **7/7 PASS**（本地） |
| 字号布局（自建 4 项） | **4/4 PASS**（本地 Chromium 模拟） |
| node 全量 181 / pytest 145+19 / biome / E2E 60 / feature_loop 43 | **未在本地跑**，按负载纪律交云端 GitHub Actions（`feat/apk-slim-power`） |
| AVD 耗电采样 / 适配横扫 / 真实 APK 构建 | **未跑**（本轮明确不做，见下节） |

---

## 6. 待真机复测清单（下一轮单独安排）

命令模板（沿用主干脚本，`-no-audio`，只跑后台桌宠段，限时 5 分钟）：

```bash
python scripts/emulator_power_probe.py --segment B_bg_float --seconds 300 --no-audio
```

| # | 项 | 预期 / 判定 |
|---|---|---|
| 1 | 后台桌宠 5 分钟 CPU | 旧 194.2s（64.7%）→ **≤20s（≤7%）**；采样日志里 `pet_ready=1` 才有效（断电前 r2 那次 `pet_ready=0`，已留档 `docs/android-battery/power-r2/`，**不可作基线**） |
| 2 | 前台静止 5 分钟 CPU | 旧 25.5s → **<8s** |
| 3 | 息屏 / 遮挡 | 息屏后 rAF 归零；桌面可见时仍按 15fps→停帧运行（设计如此） |
| 4 | APK 实包 | CI 构建后 `unzip -l` 核对：图片 stored 总量 ≈ 742 KiB，APK ≈ 0.9–1.0 MiB（debug）；<3.5 MiB 目标 |
| 5 | 首屏 & 模型观感 | 冷启动不因剔图回退（s070 命中）；截图复看量化纹理无糊 |
| 6 | 字体 1.5 | 横扫 19/19；设置页/弹窗不破版；WebView `textZoom=150` 生效 |
| 7 | 电量 | AVD mAh 不可外推，只记 CPU 秒数与 tick 增量对比 |

---

## 7. 本轮提交

| 提交 | 内容 |
|---|---|
| `fix(pet): 静止分档停帧 + 可见区取景裁剪…` | `web/js/pet.js` 三档调度、setTimeout 降帧、可见区 clip、`state.mode` |
| `docs(android-battery): 断电前 r2 采样留档…` | `power-r2/`（`pet_ready=0`，非有效基线） |
| `perf(assets): 运行时美术转 256 色调色板 + APK 剔除未请求的基准层/缩略图` | 8 张图量化、`optimize_art.py`、排除清单、`verify_assets` |
| `fix(android): textZoom 上限 130%→150%` | `WebShell.java` + `MainActivity` 注释；附字号实测截图 |
