# 本机 Android 模拟器 GPU 判定与本地策略（2026-11-07）

适用机器：Windows 10 19045 / 8 逻辑核 / Intel HD Graphics 4600（Haswell，驱动 20.19.15.5171）/ 另有 GameViewer 虚拟显示适配器。
模拟器：`D:\android-sdk\emulator\emulator.exe` 36.5.11.0，AVD `treehole_api34`（720x1280 / density 320）。

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

## 看门狗（硬规则：CPU > 30% 即杀）

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
- 降配（480x800 + 2 核）能把走查峰值压到 30% 以下；**只测不切默认**。若切换，`docs/shots/**`、`docs/ui-shots-r2/**` 等 720x1280 截图基线全部失效，需要按 480x800（0.667 倍宽高）整体重拍。

## 本次改动的 AVD 状态与回滚

- 原 AVD 黑屏根因：`treehole_api34.avd\snapshots\default_boot\snapshot.pb` 仅 11 字节（强杀导致损坏），Quick Boot 加载损坏快照后黑屏。
- 已删除该损坏快照目录（冷启动自愈），未修改 `config.ini`；原配置备份在 `D:\android-avd\backup_config_<时间戳>\`（含 `config.ini` / `hardware-qemu.ini` / `emulator-user.ini` / `AVD.conf` / `treehole_api34.ini`）。
- 旧快照目录副本在 `D:\android-avd\backup_snapshot_<时间戳>\`；如需回滚，把该目录放回 `treehole_api34.avd\snapshots\default_boot`（注意：该快照本身已损坏，不建议回滚）。
- 实验专用克隆：`D:\android-avd\treehole_gputest.avd`；所有参数实验只允许在该克隆上做。

## CI（保持不变）

`.github/workflows/android-e2e.yml` 继续使用 `reactivecircus/android-emulator-runner`，`-no-window -gpu swiftshader_indirect`。云端无显卡，不应改成 host/angle。
