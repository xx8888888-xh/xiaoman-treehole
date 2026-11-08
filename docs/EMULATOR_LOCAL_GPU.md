# 本机 Android 模拟器 GPU 判定与本地策略（2026-11-07）

适用机器：Windows 10 19045 / 8 逻辑核 / Intel HD Graphics 4600（Haswell，驱动 20.19.15.5171）/ 另有 GameViewer 虚拟显示适配器。
模拟器：`D:\android-sdk\emulator\emulator.exe` 36.5.11.0，AVD `treehole_api34`（720x1280 / density 320）。

## 治理策略速查（当前生效，2026-11-08）

> 依据：用户 2026-11-08 紧急裁定——本机 CPU 曾到 100%，模拟器只在 21:00 后启动，必须 `-no-audio`，整机 CPU 超 30% 立即 kill。上一版“21:00–06:00 解除限制”不再作为放行依据（见文末差异说明）。

| 项 | 规则 | 判据/读取方式 |
|---|---|---|
| 允许启动时段 | 仅 21:00–06:00；其余时段本地不开，Android E2E 走 CI | `Get-Date` 与 `emu_timewindow.ps1`；日志 `LIMIT=` |
| 音频 | 一律 `-no-audio` | `avd_local.ps1` 固定参数；进程命令行含 `-no-audio` |
| 快照 | 冷启动 `-no-snapshot-load -no-snapshot-save` | 进程命令行；日志 `snapshot=none` |
| kill 阈值 | 进程组 CPU 占整机 **>30%** | 2 秒采样，`emulator.exe + qemu-system-x86_64.exe`（含 crashpad/netsimd 子进程）TotalProcessorTime 差值 ÷ 采样间隔 ÷ 逻辑核数 ×100 |
| 冷启动豁免 | 启动后 **≤120 秒**（以进程组最早 CreationDate 为准），超过即按 30% 规则执行 | 日志 `age=...s exempt=True/False` |
| 豁免之外 | 无其他豁免；不临时调高阈值 | 唯一豁免是 120 秒冷启动窗 |
| 击杀动作 | 立即结束整组进程，并写 SUMMARY | 日志 `KILL trigger` / `SUMMARY killed emulator group` |
| 收尾 | `adb emu kill` → 强制清理 → 断言残留为 0 | 日志 `STOP OK: no residual emulator/qemu`；`Get-Process emulator,qemu-system-x86_64` 为 0 |

CPU 采样间隔用单调时钟（Stopwatch）计算，墙钟只用于时间显示与进程年龄（CreationDate），时钟跳变不会改变阈值判定。

## GPU 占用怎么测、当前是多少

测量命令（按 PID 精确归属，避免把桌面/其他程序的 GPU 算进来）：

```powershell
$qemu = (Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" | Select-Object -First 1).ProcessId
(Get-Counter '\GPU Engine(*)\Utilization Percentage').CounterSamples |
  Where-Object { $_.InstanceName -match "_pid_$($qemu)_" } |
  Measure-Object -Property CookedValue -Sum | Select-Object -ExpandProperty Sum
```

- 实例名形如 `pid_<PID>_luid_..._eng_3d_0_phys_0`；只累加该 qemu PID 的引擎，单位 %。
- **当前实测：0%**（模拟器运行、冷启动、720×1280 走查全程均为 0，GPU 计数器中无该 PID 的有效 3D 引擎占用）。
- 交叉判据（Guest 侧）：`adb shell dumpsys SurfaceFlinger | findstr GLES` → `Android Emulator OpenGL ES Translator (Google SwiftShader)`。

**为什么会出现“CPU 高但 GPU 没用”：** 本机没有可用的硬件 GPU 路径，不是计数器漏测。

1. emulator 36.5 的 `-gpu host` 依赖宿主 Vulkan；
2. 本机 Intel HD 4600（Haswell，驱动 20.19.15.5171）没有 Vulkan ICD；
3. 启动阶段 `ForceSwiftshader` 特性位生效，日志 `gles_mode_selected:swiftshader` / `initIcdPaths: ICD set to 'swiftshader'`，所有 host 类后端（host / angle_indirect / swangle / d3d11）都被改写为 SwiftShader；
4. 因此渲染全部落到 CPU 软件光栅化：CPU 高、GPU 0% 是真实状态。

## 日志落在哪、怎么复盘一次治理

路径：

| 文件 | 内容 |
|---|---|
| `D:\android-avd\watchdog.log` | 看门狗启动/`LIMIT=`/每 2s 采样（有进程时）/`KILL trigger`/`SUMMARY` |
| `D:\android-avd\watchdog.pid` | 当前看门狗 PID（用于判断是否在跑） |
| `D:\android-avd\avd_local.log` | 启动/停止流水：`MODE=`、`LIMIT=`、`launch ...`、`boot_completed=True in ...s`、`screenshot ... nonBlackRatio`、`STOP OK` |
| `D:\android-avd\avd_local.out.log` / `.err.log` | emulator 原始输出（GPU 模式、SwiftShader、崩溃） |
| `D:\android-avd\watchdog.stop` | 停止信号（存在时看门狗自行退出） |

一次治理的复盘清单（按顺序看）：

1. `avd_local.log` 有 `MODE=baseline|viewonly`、`LIMIT=on|off`、`launch ... snapshot=none`；
2. 命令行确有 `-no-audio`（`Get-CimInstance Win32_Process` 查 `emulator.exe` 的 CommandLine）；
3. `boot_completed=True in ...s`，随后 `screenshot ... size=720x1280 nonBlackRatio=...`；
4. `watchdog.log`：
   - 无 `KILL trigger` → 全程 ≤30% 或仍在 120s 豁免内；
   - 有 `KILL trigger total=... age=... exempt=False peak=...` → 看 total/age/peak，确认不是豁免期；
   - 有 `SUMMARY killed emulator group: ran=... peak_cpu=... residual=0` → 已清场；
5. 收尾断言 `STOP OK: no residual emulator/qemu`，再用 `Get-Process emulator,qemu-system-x86_64` 复核为 0；
6. GPU 按上节命令复核（当前应为 0%，若非 0 说明硬件路径被启用，需重新取证）。

当前状态快照（2026-11-08 09:25，重启后）：模拟器 0 进程、无自启动项、看门狗在跑（`watchdog.pid` 有效，`state=LIMIT=on`）。

## 结论（判死）

**本机无法给模拟器提供硬件 GPU 渲染。** host / angle_indirect / swangle / d3d11 四种后端实测全部在启动阶段被改写为 SwiftShader，Guest 侧渲染器始终是软件实现，GPU Engine 计数峰值为 0%。

判定依据（每次都要看 Guest 字符串，不能只看日志模式名）：

```powershell
# 1) 加速器
& 'D:\android-sdk\emulator\emulator.exe' -accel-check
# 输出：WHPX(10.0.19045) is installed and usable.

# 2) 显卡与驱动
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,Status

# 3) 启动日志关键行
Select-String -Path <emulator log> -Pattern 'GPU mode|emuglConfig_init|Graphics Adapter|SwiftShader|ANGLE|Direct3D'

# 4) Guest 真实渲染器（最终判据）
adb shell dumpsys SurfaceFlinger | findstr GLES

# 5) GPU Engine 按 PID 聚合
Get-Counter '\GPU Engine(*)\Utilization Percentage'
```

实测日志原文（host / angle_indirect / swangle / d3d11 完全一致）：

```
GPU mode control feature flag 'ForceSwiftshader' is set
emuglConfig_init: vulkan_mode_selected:swiftshader gles_mode_selected:swiftshader
initIcdPaths: ICD set to 'swiftshader', using Swiftshader ICD
Graphics Adapter Vendor Google (Google Inc.)
Graphics Adapter Android Emulator OpenGL ES Translator (Google SwiftShader)
AVD supportsNativeGLES=1, supportsGuestAngle=0
Platform does not support Guest Angle
```

Guest 侧：

```
GLES: Google (Google Inc.), Android Emulator OpenGL ES Translator (Google SwiftShader),
OpenGL ES 3.0 (OpenGL ES 3.0 SwiftShader 4.0.0.1)
```

阻塞原因：emulator 36.5 的 host 通路依赖宿主 Vulkan ICD；Haswell / HD 4600 无 Vulkan ICD，ANGLE/SWANGLE 也被强制回落。清除 `emu-last-feature-flags.protobuf`、删除 `hardware-qemu.ini` 后仍复现，排除了本地残留配置。

## 本地策略（默认不开）

- 默认动作是 `status`，**不会自动启动模拟器**。
- 需要看界面时手动启动，结束后必须关闭：

```powershell
# 启动（默认静音、自动挂看门狗、boot 等待、黑屏自检）
powershell -ExecutionPolicy Bypass -File D:\android-avd\avd_local.ps1 -Action start

# 关闭（adb emu kill + 强制清理 + 残留断言）
powershell -ExecutionPolicy Bypass -File D:\android-avd\avd_local.ps1 -Action stop

# 状态
powershell -ExecutionPolicy Bypass -File D:\android-avd\avd_local.ps1 -Action status
```

硬件 GPU 不可用，本地只能 `-gpu swiftshader_indirect`；Android 端到端仍以 CI（`android-e2e.yml`，`-no-window -gpu swiftshader_indirect`）为准。

## 基线策略（云端唯一基线源 / 本地仅目视）

1. **云端 720×1280 是唯一像素/几何基线源。** `docs/shots/**`、`docs/ui-shots-r2/**`、`docs/android-battery/shots/**` 全部保持 720 基线，**不重拍**——本机 GPU 不可用是硬件限制，不该拿重拍基线来补偿。
2. **本地 480×800 / 2 核只用于目视。** 不产出任何进入基线的截图，也不拿它跑会写基线数字的走查。依赖视口尺寸的布局硬线必须在云端 720×1280 上判定，本地降配结果**不得**作为通过依据。
3. **脚本写死 viewonly 守卫。** `scripts/local/avd_local.ps1` 读 AVD `config.ini` 的 `hw.lcd.width/height`：低于 720×1280（或显式 `-ViewOnly`）即进入 viewonly，启动时打印 `MODE=viewonly ... baseline_writes=REFUSED`；`-UpdateBaseline` 直接以退出码 2 拒绝；`-ShotDir` 落在上述基线目录同样拒绝。另有 `-Action guard -ShotDir <path>` 供走查脚本在写图前自检。
4. **冷却提示。** 本地若确实需要在 720 上看动画超过 120 秒，就承认会被看门狗杀掉（硬规则不调阈值）；要跑长任务去云端。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local/avd_local.ps1 -Action start -Avd treehole_gputest -Cores 2 -MemoryMb 2048
# MODE=viewonly avd=treehole_gputest lcd=480x800@240 cores=2 gpu=swiftshader_indirect baseline_writes=REFUSED reason=lcd=480x800

powershell -ExecutionPolicy Bypass -File scripts/local/avd_local.ps1 -Action start -Avd treehole_gputest -UpdateBaseline
# REFUSED baseline write: --update-baseline is not allowed in MODE=viewonly (...)，退出码 2

powershell -ExecutionPolicy Bypass -File scripts/local/avd_local.ps1 -Action guard -ShotDir docs/shots
# REFUSED baseline write: docs/shots -> docs\shots is not allowed in MODE=viewonly (...)，退出码 2
```

## 限流时间窗（21:00–次日 06:00 解除）

> 本节记录的是 2026-11-07 的历史规则。2026-11-08 紧急裁定已取代它：允许启动仍限 21:00 之后，但 30% 硬线按“立即 kill”执行。差异与代码同步项见文末“策略差异说明”。

- 本地时间落在 **21:00–06:00**：`LIMIT=off`，看门狗不采样、不杀，可全核/满分辨率跑；其余时段 `LIMIT=on`，恢复 30% 硬规则。
- 跨零点按小时判定（22:30 / 00:30 / 05:59 都在窗内，06:00 起恢复）。
- 抗时钟跳变：看门狗用单调时钟（Stopwatch）与墙钟增量对比，偏差 >5s 视为跳变，本次不切换状态并清空 CPU 采样；正常切换需连续 2 次采样一致（2s 间隔）才生效，单次向前跳变不会误判。
- 两处判定共用 `scripts/local/emu_timewindow.ps1`，避免脚本与看门狗各写一份。

边界自检（`-Action test-window`，固定日期）实测：

```
2026-11-07 20:59:00 -> LIMIT=on  PASS
2026-11-07 21:00:00 -> LIMIT=off PASS
2026-11-07 23:59:00 -> LIMIT=off PASS
2026-11-08 00:30:00 -> LIMIT=off PASS
2026-11-08 05:59:00 -> LIMIT=off PASS
2026-11-08 06:00:00 -> LIMIT=on  PASS
2026-11-08 06:01:00 -> LIMIT=on  PASS
WINDOW TEST PASS 7/7
```

窗内实测（720×1280 / 4 核 / 3072MB，原始 AVD，12 次滑动 + 返回，不降配、不加豁免）：

| 指标 | 值 |
|---|---|
| 冷启动到 boot_completed | 59.1s |
| 走查耗时 | 53.9s |
| 走查 CPU 均值 | 8.9% |
| 走查 CPU 峰值 | 42.9%（窗内允许，未触发击杀） |
| GPU 峰值 | 0%（硬件 GPU 判死，与时间窗无关） |

看门狗日志：`watchdog start ... window=21:00-06:00 state=LIMIT=off`，全程无 `KILL trigger`。
**基线规矩不变**：像素/几何基线仍只认云端 720×1280，本地不重拍。

## 看门狗（硬规则：CPU > 30% 即杀，仅限窗外时段）

`D:\android-avd\emulator_watchdog.ps1`，2 秒采样，统计 `emulator.exe + qemu-system-x86_64.exe` 进程组占整机 CPU：

- 超过 30% 且启动已超过 120 秒 → 立即结束整组进程；
- 120 秒内为冷启动豁免窗口；
- 日志 `D:\android-avd\watchdog.log`，记录时间、PID、进程名、CPU%、是否豁免、前台 Activity；
- 杀死后输出 SUMMARY（运行时长、峰值、阈值、残留数）。

实测击杀（20:44:36）：

```
KILL trigger total=30.6% > limit=30% age=186.9s exempt=False peak=68.3%
per=[emulator.exe#8672=0%, qemu-system-x86_64.exe#18152=30.6%, ...]
focus=[mCurrentFocus=Window{... com.xiaoman.treehole/com.xiaoman.treehole.MainActivity}]
SUMMARY killed emulator group: ran=186.9s peak_cpu=68.3% threshold=30% residual=0
```

## 实测数字（swiftshader，克隆 AVD `treehole_gputest`）

| 配置 | 冷启动到 boot_completed | 走查耗时 | 走查 CPU 均值 | 走查 CPU 峰值 | GPU 峰值 |
|---|---|---|---|---|---|
| 720x1280 / 4 核 / 3072MB | 48.9s | 21.7s | 16.1% | 46.0% | 0% |
| 480x800 / 2 核 / 2048MB | 48.5s | 22.3s | 11.7% | **28.5%** | 0% |

- 原 AVD `treehole_api34`（720x1280 / 4 核）恢复后：冷启动 **46.8s**，截图非黑 **87.74%**，静置 60 秒 **avg 0.7% / peak 1%**（证明 30% 线在空载下完全满足；之前的 40%–70% 来自启动/动画阶段）。
- swangle：可启动（61.4s），但 Guest 仍为 SwiftShader，30–90s CPU avg 34.9% / peak 47.5%，GPU 0%。
- 降配（480x800 + 2 核）能把走查峰值压到 30% 以下；**只测不切默认**，且按上面的基线策略它只用于目视、不写基线。若真要让降配成为默认，才需要按 480x800（0.667 倍宽高）整体重拍 720 基线——当前裁定是不重拍。

## 本次改动的 AVD 状态与回滚

- 原 AVD 黑屏根因：`treehole_api34.avd\snapshots\default_boot\snapshot.pb` 仅 11 字节（强杀导致损坏），Quick Boot 加载损坏快照后黑屏。
- 已删除该损坏快照目录（冷启动自愈），未修改 `config.ini`；原配置备份在 `D:\android-avd\backup_config_<时间戳>\`（含 `config.ini` / `hardware-qemu.ini` / `emulator-user.ini` / `AVD.conf` / `treehole_api34.ini`）。
- 旧快照目录副本在 `D:\android-avd\backup_snapshot_<时间戳>\`；如需回滚，把该目录放回 `treehole_api34.avd\snapshots\default_boot`（注意：该快照本身已损坏，不建议回滚）。
- 实验专用克隆：`D:\android-avd\treehole_gputest.avd`；所有参数实验只允许在该克隆上做。

## CI（保持不变）

`.github/workflows/android-e2e.yml` 继续使用 `reactivecircus/android-emulator-runner`，`-no-window -gpu swiftshader_indirect`。云端无显卡，不应改成 host/angle。

## 策略差异说明（2026-11-08，重要）

- **上一版（2026-11-07）**：21:00–06:00 为 `LIMIT=off`，看门狗不采样、不杀。
- **最新裁定（2026-11-08）**：模拟器只在 21:00 后启动；必须 `-no-audio`；整机 CPU 超 30% 立即 kill，唯一豁免是冷启动 120 秒。
- **代码已对齐（2026-11-08）**：
  - `emulator_watchdog.ps1` 删除 21:00–06:00 免限分支，恒定 `state=LIMIT=on`，30% / 120s / 2s 保持不变；
  - `avd_local.ps1` 增加启动闸门：窗外直接 `START REFUSED` 并以退出码 3 结束（不启动、不挂看门狗）；
  - `-no-audio`、`-no-snapshot-load -no-snapshot-save` 固化为默认启动参数；
  - 新增 `-Action dryrun` 与看门狗 `-DryRun`，不启动模拟器即可打印启动命令与判定阈值（实测输出见本文件“对齐验证”）。
- **10 套冷启动验证状态**：因本机 CPU 100% 紧急停工，仅完成第 1 轮（冷启动 112.2s、CPU 峰值 40.1%、GPU 峰值 0%、未触发 kill、收尾残留 0），其余 9 轮未执行；恢复后从第 2 轮继续，结果写入 `D:\android-avd\rounds10.json`。

## 对齐验证（不启动模拟器）

```powershell
# 看门狗阈值自检
powershell -File D:\android-avd\emulator_watchdog.ps1 -DryRun
# LIMIT=on threshold=30% grace=120s interval=2s cores=8 start_window=21:00-06:00 log=D:\android-avd\watchdog.log

# 启动器 dry-run（当前 10:xx，窗外）
powershell -File D:\android-avd\avd_local.ps1 -Action dryrun
# MODE=baseline avd=treehole_api34 lcd=720x1280@320 cores=4 gpu=swiftshader_indirect snapshot=none baseline_writes=allowed reason=
# LIMIT=on threshold=30% grace=120s interval=2s start_window=21:00-06:00 now=...
# START=blocked outside=21:00-06:00
# CMD=D:\android-sdk\emulator\emulator.exe -avd treehole_api34 -no-audio -no-boot-anim -gpu swiftshader_indirect -no-metrics -cores 4 -memory 3072 -no-snapshot-load -no-snapshot-save

# 窗外启动应被拒绝（退出码 3，不启动模拟器）
powershell -File D:\android-avd\avd_local.ps1 -Action start
# START REFUSED: outside 21:00-06:00 ...

# 时间窗边界（启动闸门共用判定）
powershell -File D:\android-avd\avd_local.ps1 -Action test-window
# WINDOW TEST PASS 7/7
```
