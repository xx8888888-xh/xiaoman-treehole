# w14 · Android AVD 真机复测与收口报告

- 分支：`feat/android-battery`（worktree `treehole-w14`，基线 `61384a0`）
- 设备：AVD `treehole_api34`（720×1280 / 320dpi / API 34，`emulator-5554`），全程 `-no-audio -no-window -gpu swiftshader_indirect`
- 约束：未改 `web/**`、`server/**`、`web/js/stage.js`；未 push / 未合并；未做 release 签名；全程无音频、无 TTS 调用

## 结论（TL;DR）

| 项目 | 结果 |
|---|---|
| 悬浮窗像素取证 | **37 / 37 通过**，0 失败（`[FLOAT-SUMMARY] 37/37 通过`） |
| 耗电（三段，各 5 分钟，AVD 真实采样） | 前台静止 **0.000384 mAh / 25.5 s CPU**；后台无桌宠 **0.00000642 mAh / 0.31 s**；后台有桌宠 **0.00329 mAh / 194.2 s（64.7% 单核）**；连续对话 **0.000662 mAh / 44.3 s（14.7%）** |
| 适配横扫 | **19 / 19 通过**，失败项 **0**；字体 1.5 实际按系统上限 130% 渲染（通过但有上限） |
| 冷启动（`am start -W` 三次） | 841 / 915 / 974 ms → **中位 915 ms**；全新安装后首屏中位 **959 ms** |
| 包体 | APK **6,222,216 B（5.93 MiB / 6.22 MB）**；`assets/www` 占压缩字节 **99.2%**，其中 4 张 Live2D 纹理 PNG 占 **88.2%**（以 stored 不压缩方式打包） |
| 遗留项 | AVD 电量模型不可用于真实 mAh、桌宠可见时单核 64.7%、字体 1.5 被 clamp、2048 纹理冗余、debug 未 minify（详见文末） |

> 重要口径：AVD 使用合成 `power_profile`（capacity 1000 mAh，`dumpsys batterystats` 的 `Computed drain` 恒为 0），**mAh 绝对值只用于同环境横向对比，不能外推到真机**；硬指标是 per-UID CPU 秒数与 `/proc` tick 增量。原始 dumpsys 见 `docs/android-battery/shots/power_*_batterystats.txt`。

---

## 1. 悬浮窗像素取证（37 项）

命令：`python scripts/emulator_float_check.py`；日志 `float_check_run.log`；截图 `shots/emulator_04..12_*.png`、`float_base.png`、`float_bar.png`。

关键像素差分行（全量项见日志）：

| 断言 | 实测 |
|---|---|
| 桌宠可见（frame 内 Live2D 差分） | `diff=21138px`，frame `(424,426,704,786)`（140×180dp） |
| 未降级为圆形悬浮球 | 主形态 = Live2D 桌宠，无悬浮球窗口 |
| 拖动并贴边 | `(564,606)→(580,1100)`，`diff=13016px`，frame 贴底 `(440,920,720,1280)` |
| 单击弹出原生气泡卡片 | bubble frame `(224,815,704,975)`，overlay 窗口 2 个 |
| 单击气泡后桌宠仍在 | `pet_frame=(440,920,720,1280)` |
| 双击展开迷你窗 | `diff=517133px`，frame `(29,243,691,1036)`（宽 662px） |
| 展开后桌宠隐藏 | `hidden_diff=0px`，`frame=None` |
| 收起后桌宠复现 | `diff=13016px` |
| 关闭开关后移除 | `diff=0px`，`box=None`，ServiceRecord=0，overlay 全部注销 |
| 权限拒绝 | 服务自停、无覆盖窗残留、无 FATAL |
| 进程被杀 | 服务随之退出（预期降级）；重开后桌宠自动恢复 `record=1` |
| 全程静音 | 无 AudioTrack / MediaPlayer / 音频焦点请求 |

失败项：**无**。模拟器为原实例（未冷重启），另做回归抽查：`ensure_panel()` 返回 `(29,243,691,1036)`、`spot_float_panel.png` 正常，未见回归。

---

## 2. 耗电实测（各 5 分钟，同一 AVD）

脚本 `scripts/emulator_power_probe.py`；日志 `power_run3.log`（A/B1/B2）、`power_C_final3.log`（C）；每段 reset `batterystats` 后静置 5 s 再开始采样，对话压力贯穿整个采样窗（28 轮，`sent_ok=28`）。

| 场景 | 时长 | UID 电量（batterystats） | per-UID CPU 秒 | 主进程 tickΔ（300 s 占比） | cpuinfo |
|---|---|---|---|---|---|
| A 前台静止（无桌宠） | 300 s | `0.000384 mAh` | 85.0 s | 2549 tick = **25.5 s（8.5%）** | app 8.4%，系统 total 2.9% |
| B1 后台（Home 后，无桌宠） | 300 s | `0.00000642 mAh` | 7.7 s | 31 tick = **0.31 s（0.1%）** | app 0.1%，系统 total 0.4% |
| B2 后台（Home 后，桌宠可见） | 300 s | `0.00329 mAh` | 285.3 s | 19421 tick = **194.2 s（64.7%）** | 系统 total 20% |
| C 连续对话（前台） | 302 s | `0.000662 mAh` | 87.6 s | 4432 tick = **44.3 s（14.7%）** | app 快照 49%，系统 total 21% |

- per-UID CPU（85.0 / 7.7 / 285.3 / 87.6 s）大于主进程 tickΔ（25.5 / 0.31 / 194.2 / 44.3 s），差值来自同 UID 的 WebView renderer 等子进程——这也是后台无桌宠时仍有 7.7 s 的原因（系统/渲染进程空转，不是业务 JS）。
- 5 分钟实测线性外推（仅同环境可比）：A ≈ 0.0046 mAh/h，B1 ≈ 0.000077 mAh/h，B2 ≈ 0.0395 mAh/h，C ≈ 0.0079 mAh/h。
- 与 w8「0 条 JS 事件」的结论一致：B1 探针 `msg_me=0 / msg_them=0`，主进程 0.1%，说明 Home 后业务 JS 与渲染确实停摆。
- **风险点**：B2 说明「后台保留桌宠可见」会让单核占用升到 64.7%（软件渲染 AVD，真机 GPU 会低一些但量级同阶），是当前最大的耗电项；若要「情感陪伴很强」，建议对桌宠做限帧（交互 30fps / 静止 8–12fps）、息屏或不可见时停渲染。

---

## 3. 适配横扫（19 项，0 失败）

脚本 `scripts/emulator_adapt_sweep.py`；最终日志 `adapt_sweep_final.log`；截图 `shots/adapt/`。

| # | 项目 | 实测 | 截图 | 结果 |
|---|---|---|---|---|
| 1 | 字体 1.0 | `scrollW=360/360`，`badRows=0`，输入栏 `top 551 / bottom 604` 在视口内 | `00-font-1.0.png` | 通过 |
| 2 | 字体 1.3 | 同上，`ovfBub=0`，`maxBubW=262` | `01-font-1.3.png` | 通过 |
| 3 | 字体 1.5 | 同上（受 `textZoom` clamp 130 影响，实际≈1.3 档） | `02-font-1.5.png` | 通过（有上限） |
| 4 | 深色 | 背景 `rgb(21,20,22) lum=20`，输入栏可见 | `03-dark.png` | 通过 |
| 5 | 浅色 | 背景 `rgb(247,244,239) lum=244` | `04-light.png` | 通过 |
| 6 | 横屏 | viewport `640×336`，输入栏完整（`57→531`） | `05-landscape.png` | 通过 |
| 7 | 竖屏恢复 | viewport `360×616`（物理 720×1280） | `04-light.png` | 通过 |
| 8 | 刘海屏模拟（double cutout） | viewport `360×576`（顶部安全区 40 CSS px），输入栏 `top 511` | `06-cutout-double.png` | 通过（布局未压挖孔区） |
| 9 | 键盘弹出 | `vh 616→353`，`imeShown=True`，输入栏 `289–342` 在视口内，无遮挡 | `07-keyboard.png` | 通过 |
| 10 | 长消息换行 | `ovfBub=0`，`scrollW=360/360`，`maxBubW=262` | `08-long-message.png` | 通过 |
| 11 | 表情消息 | 气泡渲染、`ovfBub=0`、`rows=17` | `09-emoji-message.png` | 通过 |
| 12–13 | 提醒镜像到原生 + 闹钟排程 | items `[('r1793830488746', 1793837688746)]`，`origWhen=[1793837688746]` | — | 通过 |
| 14–16 | 时区切换（`set-timezone`） | 前后绝对时刻不变；`TIMEZONE_CHANGED` 触发重排 | — | 通过 |
| 17–18 | 时钟 +72h（`set-time`） | 条目未丢（`at` 不变）；`TIME_SET` 触发重排；变更时 `scheduled in 993ms` 补发 | `10-timezone-restored.png` | 通过 |
| 19 | 时区/时钟变更全程无 FATAL | 仅有 `D ReminderAlarm: rescheduled after android.intent.action.TIME_SET / TIMEZONE_CHANGED` | — | 通过 |

补充：字体 1.5 档像素量测标题宽 209 → 264（≈1.263×），与 `MainActivity` 的 `textZoom` clamp 85–130 一致；若后续要支持 1.5 以上字号，需要放开 clamp 或改用页面级 rem 缩放（当前已保证不溢出）。

---

## 4. 冷启动与包体

### 4.1 冷启动（`am start -W -S`，三次）

| 轮次 | TotalTime | WaitTime |
|---|---|---|
| 1 | 915 ms | 924 ms |
| 2 | 974 ms | 979 ms |
| 3 | 841 ms | 844 ms |
| **中位数** | **915 ms** | 924 ms |

- 首次测量波动 931–1216 ms（系统启动后后台 churn），静置 20 s 复测收敛到 841–974 ms；日志 `cold_start.log`。
- 全新安装后首屏：1117 / 945 / 959 ms → **中位 959 ms**（截图 `install_first_screen.png`）；`pm install` 耗时约 0.4 s。
- 权限弹窗会阻塞首次启动与 WebView 创建（`POST_NOTIFICATIONS`），安装后需 `pm grant` 或引导授权。

### 4.2 APK 构成（debug，`android/app/build/outputs/apk/debug/app-debug.apk`）

| 项 | 大小 | 说明 |
|---|---|---|
| APK on-disk | 6,222,216 B（5.93 MiB / 6.22 MB） | zip 压缩后合计 6061.9 KB，raw 7233.4 KB |
| `assets/www`（压缩后） | 6011.1 KB | 占 APK 压缩字节 **99.2%**（raw 7130.4 KB，占 98.6%） |
| `Hiyori.2048/texture_01.png` | 2445.7 KB | **stored（method=0，未压缩）** |
| `Hiyori.2048/texture_00.png` | 1771.8 KB | **stored** |
| `Hiyori.1024/texture_01.png` | 667.5 KB | **stored** |
| `Hiyori.1024/texture_00.png` | 464.2 KB | **stored** |
| `js/vendor/pixi.min.js` | 449.6 → 146.4 KB | 已 deflate |
| `Hiyori.moc3` | 433.2 → 224.6 KB | 已 deflate |
| 4 张纹理合计 | **5349.2 KB（占 APK 88.2%）** | 其中 `Hiyori.2048` 两图 4217.5 KB（69.6%） |

### 4.3 可压缩项与建议（未做 release 签名）

1. **Live2D 纹理（最大项，最高优先）**：4 张 PNG 以 stored 方式打包且未压缩，合计 5.23 MB。建议转 WebP（lossy q80 通常可省 60–80%），或改用 ETC2/ASTC 纹理；预计可省 3–4 MB。
2. **纹理分档冗余**：`Hiyori.2048` 与 `Hiyori.1024` 同时随包，仅按屏宽选一档。建议只保留 1024（小屏/中端机足够），2048 改为按需下载；可再省约 4.2 MB。
3. **assets 打包方式**：核对 `aaptOptions noCompress` 是否白名单了 PNG；至少对非纹理解压可省数百 KB。
4. **JS vendor**：`pixi.min.js`、`live2dcubismcore.min.js` 已压到 ~20–34%，体积可接受；`app.js` 等业务代码很小，不是瓶颈。
5. **release 构建**：debug 包未开 minify/shrink（`build.gradle.kts` 未启用），release 会再降 dex 体积；按要求本轮未签名、未出 release 包。

---

## 5. 输入法与小屏（720×1280）

- 键盘弹出：物理点击 `(308,1155)` 命中输入框（CSS 坐标已按 dpr=2 折算），`mInputShown=true`，WebView 视口 `616→353 CSS px`，输入栏 `top 289 / bottom 342` 完整位于键盘上方，无遮挡（`07-keyboard.png` 已人工复核）。
- 长消息：`ovfBub=0`、`scrollWidth=360/360`、最大气泡宽 262，无横向滚动（`08-long-message.png`）。
- 表情消息：17 行、气泡不越界、未破版（`09-emoji-message.png` 已人工复核）。
- 横屏 640×336 下输入栏完整、无错位（`05-landscape.png` 已人工复核）。

---

## 6. 遗留项 / 建议

1. **AVD 电量模型不可信**：capacity 1000 mAh、`Computed drain` 恒 0，mAh 只能做同环境比值；真机 mAh 需在实体设备上用 `batterystats` 复测。
2. **桌宠可见时 64.7% 单核**（后台/前台均高）：建议变现为可配置的「省电模式」——静止降帧（8–12fps）、息屏停渲染、Home 且不可见时暂停 Live2D ticker；真机有 GPU 时开销会下降，但仍是头号耗电项。
3. **系统字体 1.5 被 clamp 到 130%**：`textZoom` 上限 130，实际按 1.3 渲染；如产品需要 1.5 档，需评估放开 clamp 后的布局（当前 1.3/1.5 均未溢出，风险可控）。
4. **包体 5.93 MiB，98.6% 是 assets/www**：纹理 WebP 化 + 只保留 1024 档是最高性价比优化；release 未做 minify/sign。
5. **首次启动权限弹窗**：`POST_NOTIFICATIONS` 未授予时会挡住 WebView 创建，安装后需引导授权（自动化测试需先 `pm grant`）。
6. **闹钟 dumpsys 解析依赖文本格式**：`origWhen=[...]` 与 `tag=*walarm*:...REMINDER_ALARM` 的匹配对系统版本较敏感，后续换 API 级别需回归。
7. 本轮未做 release 签名（按约束）；37 项为原实例结果 + 2 项回归抽查，未做全量重跑（按断电恢复指令）。

---

## 附：证据索引

- 日志：`docs/android-battery/float_check_run.log`、`power_run3.log`、`power_C_final3.log`、`adapt_sweep_final.log`、`cold_start.log`
- 原始 dumpsys：`docs/android-battery/shots/power_{A_fg_nofloat,B_bg_nofloat,B_bg_float,C_chat}_batterystats.txt`
- 截图：`docs/android-battery/shots/`（悬浮窗 9 张 + 备用 5 张）、`shots/adapt/`（11 张）
- 脚本：`scripts/emulator_float_check.py`、`scripts/emulator_power_probe.py`、`scripts/emulator_adapt_sweep.py`
