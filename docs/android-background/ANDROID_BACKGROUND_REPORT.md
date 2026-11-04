# Android 后台存活 / 提醒 / 语音策略 整改报告

- 分支：`feat/android-background`（基于主工作区 HEAD `688dd20`）
- 工作树：`treehole-w8`（未改动主工作区；另建 ASCII junction `D:\treehole-w8` 仅为解决 Gradle 单测 worker 的中文路径问题）
- 范围：`android/**`；`web/**` 仅按追加要求修改 `web/js/tts.js` 的"状态/文案映射"与对应用例
- 提交：`1152e8a`；未 push、未合并、未开 PR

---

## 1. 改动文件

### 1.1 新增（`android/app/src/main/java/com/xiaoman/treehole/`）

| 文件 | 作用 |
| --- | --- |
| `ReminderScheduling.java` | 纯 Java 调度策略：`nextTriggerAt`（补发已到期）、`nextFutureAt`（响过之后只排未来项）、`modeFor`、`isDue`、心跳间隔归一化 |
| `ReminderItem.java` | 提醒数据模型（id / text / at / done） |
| `ReminderStore.java` | SharedPreferences 镜像（`items` / `delivered`），JS 整表推送落盘、已投递清单、稍后提醒 |
| `ReminderAlarmScheduler.java` | `AlarmManager` 精确/非精确排程、单例 `PendingIntent`、`reschedule` / `rescheduleFuture` / `cancel` |
| `ReminderNotifications.java` | 通知渠道 `reminder_attention`（IMPORTANCE_HIGH）、`POST_NOTIFICATIONS` 判定、BigText 通知与"稍后 10 分钟"动作 |
| `ReminderAlarmReceiver.java` | 闹钟投递、免打扰判断、开机/时间/时区/覆盖安装重注册、snooze |
| `AndroidHostBridge.java` | JS 桥 `AndroidHost`：`syncReminders` / `drainDelivered` / `status` / 权限引导 / `isAutoRead` / 心跳间隔 |
| `PageLifecycle.java` | 页面冻结/恢复入口（`__xmHost.pause()`/`resume()` + `WebView.onPause/onResume`） |
| `WebShell.java` | WebView 统一配置、`textZoom` 跟随系统字体、注入宿主脚本、bootstrap 响应 |
| `res/raw/xm_host_bootstrap.js` | 注入脚本（计时器台账、提醒补丁、TTS 补丁、可见性覆盖） |
| `res/values/colors.xml`、`res/values-night/colors.xml` | 浅色/深色配色 |
| `res/values-night/themes.xml` | 深色主题 |
| `android/app/src/test/java/com/xiaoman/treehole/ReminderSchedulingTest.java` | 12 个纯 JUnit4 用例（见 §5.2） |

### 1.2 修改

| 文件 | 改动要点 |
| --- | --- |
| `MainActivity.java` | `onStart/onStop` 接入 `PageLifecycle`；`onStop` 追加 `ReminderAlarmScheduler.reschedule()`（把"前台没消化完的到期项"交接给通知通道）；TTS 改为"点击才初始化 + 待播队列"，`available()` 只查引擎存在性（`<queries>` + `queryIntentServices`），不再 bind 服务；`onConfigurationChanged` 同步字体与主题 |
| `FloatWindowService.java` | 前台服务最小化（`IMPORTANCE_MIN`、静音、无振动/角标、`setOnlyAlertOnce`）；API34 `SPECIAL_USE`；收起 = `PageLifecycle.onHostPause` + 15s 后销毁面板 WebView + 交接到闹钟；展开重建 |
| `AndroidManifest.xml` | 新增 `POST_NOTIFICATIONS`、`SCHEDULE_EXACT_ALARM`、`RECEIVE_BOOT_COMPLETED`；`<queries>`（TTS_SERVICE）；receiver 注册 BOOT_COMPLETED / TIME_SET / TIMEZONE_CHANGED / MY_PACKAGE_REPLACED；`resizeableActivity`、`windowLayoutInDisplayCutoutMode="never"` |
| `android/app/build.gradle.kts` | 仅新增 `testImplementation("junit:junit:4.13.2")` |
| `android/gradle.properties` | `android.overridePathCheck=true`（仓库路径含中文，AGP 默认拒绝构建） |
| `res/values/themes.xml` | `Theme.Xiaoman` 基类 + 导航栏浅色（`tools:targetApi="27"`） |
| `web/js/tts.js`（追加要求允许的最小改动） | ① `tts=off` 时**不回调 onEnd**（状态即事实：无声不得标"已播"）；② `provider()` 如实返回 `off`；③ `loadCfg()` 回退到全局 `API` 绑定（`api.js` 是顶层 `const`，浏览器里 `window.API` 恒为 `undefined`，旧实现导致真机 provider 恒为 `none`） |
| `web/tests/tts_provider.test.js` | 新增 2 条用例：off 不得出现"已播"；API 只在全局词法作用域时仍要读到配置 |
| `AGENT_SCOPE.md` | 记录负责区/禁止区与跨层契约 |

### 1.3 交付物

- 截图：`docs/android-background/screenshots/01..05-*.png`
- 本报告

---

## 2. 权限与调度说明

### 2.1 权限

| 权限 | 用途 | 申请方式 |
| --- | --- | --- |
| `POST_NOTIFICATIONS` | 后台提醒通知（API 33+ 运行时权限） | 首次同步到有待办提醒时请求（`REQ_NOTIFICATION_PERMISSION=201`）；未授予时**不弹通知**且**保留待办**，不丢提醒 |
| `SCHEDULE_EXACT_ALARM` | `setExactAndAllowWhileIdle` | 只做**引导**（`ACTION_REQUEST_SCHEDULE_EXACT_ALARM` 打开系统页），不静默申请；未授予时自动退化为 `setAndAllowWhileIdle` |
| `RECEIVE_BOOT_COMPLETED` | 重启后重注册闹钟 | manifest receiver |
| 电池优化白名单 | —— | **只引导**（`ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS`），不调用 `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`，不静默加白 |
| TTS 引擎可见性 | 判断 native 语音是否可用 | `<queries><intent><action android:name="android.intent.action.TTS_SERVICE"/></intent></queries>`，只查询不 bind |

未申请 `WAKE_LOCK`；没有任何自建 wakelock。

### 2.2 调度链路

```
JS Reminders.add/cancel/markDone
   → bootstrap patchReminders() 推整表
   → AndroidHost.syncReminders(json)
   → ReminderStore.save(json) 落盘
   → ReminderAlarmScheduler.reschedule():
        nextTriggerAt(pendingAts, now)
        ├─ 无待办 → cancel
        ├─ 精确闹钟可用 → setExactAndAllowWhileIdle(RTC_WAKEUP, at, pi)
        └─ 不可用 → setAndAllowWhileIdle(RTC_WAKEUP, at, pi)（或 SecurityException 时退化为非精确）
```

投递（`ReminderAlarmReceiver`）：

- 主界面或悬浮迷你窗可见（`MainActivity.isAnyUiVisible()`）→ **不弹通知**，由页面内投递（此时页面是"活的"）；
- 均不可见且已授权 → 逐条发通知（渠道 `reminder_attention`，category=reminder，vis=PUBLIC，带"稍后 10 分钟"动作）并写入 `delivered`；
- 均不可见但未授权 → **保留 pending**，不改数据；
- 任何分支结束后只调用 `rescheduleFuture()`：**只排未来项**，绝不把"到期但尚未处理"的项排成 `now+1s`（旧写法会导致闹钟每秒重排一次的空转耗电）。

重注册：`BOOT_COMPLETED` / `TIME_SET` / `TIMEZONE_CHANGED` / `MY_PACKAGE_REPLACED` → `reschedule()`。

进程被杀：闹钟是系统级 `PendingIntent`，`SharedPreferences` 为持久镜像，进程重启后 receiver 直接读盘投递；`delivered` 清单在页面恢复时由 `drainDelivered()` 标记完成，避免"通知已看、页面又弹一次"。

### 2.3 真后台释放（无"隐藏页投递 + markDone"路径）

`xm_host_bootstrap.js`：

- 包装 `setInterval/setTimeout`，登记台账（`delay` / `remaining`）；
- `__xmHost.pause()`：清空所有 interval/timeout（记录剩余时长）、`TTS.stop()`、`document.hidden/visibilityState` 置为 hidden、广播 `visibilitychange`、通知宿主 `onPageHidden()`；**同时**：`Reminders.markDone` 在 paused 期间变为空操作、`Reminders.due()` 在 paused 期间返回 `[]`；
- `__xmHost.resume()`：按剩余时长重新武装，会话数据（localStorage/IndexedDB）不丢。

宿主侧：`MainActivity.onStart/onStop`、悬浮窗收起 → `PageLifecycle.onHostPause/onHostResume`（同时 `WebView.onPause/onResume`）。

因此在页面不可见时，**既没有提醒轮询，也没有 markDone 的入口**——"隐藏页仍被投递提醒并 markDone"这条路径被同时从两端堵住。

---

## 3. 语音策略与文案修复

- **默认"点击才朗读"**：`patchTTS()` 只放行"最近 5s 内有用户点读手势"的调用，或显式开启自动朗读（`AndroidHost.isAutoRead()` / `localStorage.xiaoman_tts_autoplay==="1"`）。
- **自动朗读开关**：桥 `AndroidHost.isAutoRead/setAutoRead`（prefs `treehole_host_prefs/tts_auto_read`，默认 `false`）。
- **四态 provider 如实显示**：`native → server → browser → off`；都不可用返回 `none`。
- **文案 bug（追加要求）**：`tts=off` 时原实现会走"muted 静默收尾"并回调 `onEnd`，而 `onEnd` 在 app.js 里承担"气泡标记 🔊 语音已播"——于是无声却被标"已播"。现在 `off` 分支**只静默返回** `{spoke:false, provider:"off"}`，**不回调 onEnd**，不触碰合成/播放逻辑，未改 `risk.js`。
- **同时修掉真正的根因**：`api.js` 的 `API` 是顶层 `const`，浏览器里不挂在 `window` 上；`tts.js` 只查 `W.API` 导致真机永远读到空配置（provider 恒 `none`）。`loadCfg()` 增加全局绑定回退后，设置页与真实链路才一致。

---

## 4. 耗电对比数据（前后各一段）

> 说明：本机 AVD（API 34 x86_64，内核未导出电源统计）的 `dumpsys batterystats` 对每个 uid 显示 `CPU : -1 (unsupported)`，因此**拿不到 mA/CPU 秒**：
>
> ```
> uid 10141:
>   SCREEN_ON : -1 (unsupported), ..., CPU : -1 (unsupported), ...
> u0a141:
>   Wake lock *launch* realtime
>   Apk com.xiaoman.treehole: Service org.chromium.content.app.SandboxedProcessService0:0
> ```
>
> 于是改用可复现的两个量化口径：**uid 下进程的 `/proc/<pid>/stat` utime+stime 增量**（100 tick = 1s）与**同窗口内的 JS 运行时事件数**（`[probe] msg them` / `heartbeat` 日志条数）。

### 4.1 基线（HEAD `688dd20`，同一 AVD）

场景：启动 → 等 25s 首屏完成 → 按 Home → 静置 120s。

```
=== BASELINE T0 ===
pid=7216 uid=10141 utime=125 stime=1484 cmd=com.xiaoman.treehole
TOTAL_TICKS=1609
=== BASELINE T1 ===
pid=7216 uid=10141 utime=128 stime=1717 cmd=com.xiaoman.treehole
TOTAL_TICKS=1845
=== LOG COUNT(120s) === 25
```

- CPU：**+236 tick = +2.36s / 120s**（≈ 1.9% 单核）
- 后台 JS 事件：**25 条 / 120s**（心跳与消息渲染在后台照常跑）

### 4.2 新版（本分支）

```
=== PAUSE PROBE ===
11-04 15:35:13  [probe] host pause        ← 81s 内再无任何 msg them / heartbeat
=== 120s 窗口 ===
[probe] msg them / heartbeat 计数 = 0
```

- 后台 JS 事件：**0 条 / 120s**（页面在 Home 后即冻结，心跳、提醒轮询、打字机全部停表）
- CPU 采样：同一口径得到 `+2495 tick / 120s`，但该次采样窗口内宿主机同时存在另一路 AVD 与 adb server 被反复重启（`device 'emulator-5556' not found` 多次），**该 CPU 数字被宿主抢占污染，不作为结论**；事件数 25→0 是可复现的、与代码路径一一对应的结论。

### 4.3 另一处耗电回归的修复（可复现）

旧实现"闹钟响过就 `reschedule()`"会把到期但未处理的项算成 `now+1s`，形成每秒一次重排。修复后 `rescheduleFuture()` 只排未来项：

```
无权限分支之后：dumpsys alarm 中已无本包 pending 项（grep 仅命中历史快照）
```

---

## 5. 验证

### 5.1 构建与静态检查

| 项 | 结果 |
| --- | --- |
| `gradlew assembleDebug` | **BUILD SUCCESSFUL**（APK ≈ 5.0 MB） |
| `gradlew lintDebug` | **0 error / 9 warning**；warning 全部为既有类别（`DataExtractionRules`、`IconLauncherShape`×5、`MonochromeLauncherIcon`、`ClickableViewAccessibility`×2），本次未新增 |
| `gradlew testDebugUnitTest` | **12/12 通过** |
| `node --test web/tests/*.test.js` | **97/97 通过**（含新增 2 条 off / 全局 API 用例） |

### 5.2 单测覆盖（`ReminderSchedulingTest`，纯 JUnit4）

1. `modeFor` 在精确闹钟可用/不可用时分别给出 EXACT/INEXACT（权限分支）
2. `nextTriggerAt` 空表 → 0（不排程）
3. `nextTriggerAt` 全部过期 → `now+MIN_LEAD`（补发一次）
4. `nextTriggerAt` 混合 → 取最早，含过期时补发
5. `isDue` 边界（`at == now` 算到期）
6. `needsReschedule` 窗口判定
7. `normalizeHeartbeatMs` 下界 30s / 上界 30min / 正常值原样
8. `nextFutureAt` 跳过过期取最近未来项
9. `nextFutureAt` 全过期 / 空表 / null → 0
10. `nextTriggerAt` 与 `nextFutureAt` 的差异（防 `now+1s` 空转）
11. `MIN_LEAD_MS` 常量语义
12. 心跳默认值归一化

### 5.3 真机/模拟器证据（API 34 AVD，`-no-audio` 全程静音）

**A. 页面冻结（Home 后）**

```
11-04 15:35:13 ... D XiaoManWeb: [probe] host pause
（随后 85s 内 0 条 msg them/heartbeat）
```

**B. 后台/熄屏投递（已授权，进程在后台）**

```
D ReminderAlarm: alarm fired due=1 uiVisible=false
NotificationRecord(... pkg=com.xiaoman.treehole id=9846 importance=4
  channel=reminder_attention category=reminder vis=PUBLIC)
android.title=String (小满提醒)
android.text=String (该喝水了（w8 后台投递验收）)
prefs: delivered=["r1793806974022"]
dumpsys power: mWakefulness=Asleep        ← 屏幕熄灭时投递
```

**C. 未授权分支（`POST_NOTIFICATIONS` 未授予）**

```
D ReminderAlarm: alarm fired due=1 uiVisible=false
W ReminderAlarm: notification permission missing; reminder kept pending
prefs: items=[{... done:false}], delivered=[]
dumpsys alarm: 本包无 pending 项（无每秒重排）
```

**D. 开机重注册**

```
D ReminderAlarm: scheduled in 789356ms
D ReminderAlarm: rescheduled after android.intent.action.BOOT_COMPLETED
dumpsys alarm: tag=*walarm*:com.xiaoman.treehole.REMINDER_ALARM
               origWhen=2026-11-04 16:03:23.858（= 用户设定的时刻）
```

另在一次运行中捕获 `rescheduled after android.intent.action.TIME_SET`。

**E. `tts=off` 文案一致性（CDP 注入探针）**

```
RESULT: {"res":{"spoke":false,"provider":"off"},"ended":0,"frames":1,"provider":"off"}
```

`ended=0` 即 `onEnd` 未回调 → 气泡不会被标「🔊 语音已播」；修复前同一探针为 `provider:"none"` 且 `ended:1`。

### 5.4 适配（截图见 `docs/android-background/screenshots/`）

| 场景 | 文件 | 结论 |
| --- | --- | --- |
| 浅色竖屏（1080×2340@440dpi） | `01-light-portrait.png` | 头部三按钮/消息/输入框完整 |
| 深色模式（`cmd uimode night yes`） | `02-dark-mode.png` | 状态栏、导航栏、页面背景一并切暗，气泡可读 |
| 字体缩放 1.3×（`font_scale=1.3`） | `03-font-scale-1.3.png` | 文字放大后气泡自动换行，无溢出/截断，输入框与按钮不挤压 |
| 横屏（`user_rotation=1`） | `04-landscape.png` | 布局自适应，导航栏在右侧，无内容被裁切 |
| 小屏 720×1280@320dpi | `05-small-screen-720x1280.png` | 头部/列表/输入框完整，无横向溢出 |
| 刘海/手势栏 | manifest `windowLayoutInDisplayCutoutMode="never"` + `resizeableActivity` | 内容避让挖孔区，导航栏（含手势条）不覆盖输入框 |

---

## 6. 遗留项与已知风险

1. **设置页 UI 属 `web/**`（禁止区）**：本分支只提供桥（`AndroidHost.isAutoRead/setAutoRead`、`getHeartbeatIntervalMs/setHeartbeatIntervalMs`、`status()` 四态）与 `localStorage.xiaoman_tts_autoplay` 约定；"自动朗读"开关控件、"native/server/browser/off"文案渲染需在 web 侧（`web/index.html`、`web/js/app.js`）接上，未在本分支改动。
2. **AVD 无 TTS 引擎**（`pm list packages` 无 tts/speech/pico）：真机上 `provider()` 会显示 `none`，这是**如实显示**；`native` 路径只能靠单测与代码审查验证，需在有引擎的真机复测一次。
3. **`dumpsys batterystats` 在本机 AVD 上不提供 per-uid CPU**（`CPU : -1 (unsupported)`），耗电对比用的是 `/proc` tick 与 JS 事件数；真机建议补一段 `batterystats --charged` 的 mA 对比。
4. **新版 CPU 采样被宿主抢占污染**（4 核 / 15GB，同时存在另一路 AVD 与会话反复重启 adb），需在真机复测。
5. **提醒通知截图缺失**：该 AVD 无锁屏密码，亮屏直接进桌面，无法截到"锁屏上的通知"；投递由 `dumpsys notification` + `mWakefulness=Asleep` + `delivered` 记录佐证。可在有锁屏的真机上补一张。
6. **`SCHEDULE_EXACT_ALARM` 在 API 34 默认不授予**（实测 `exactAlarm:false`）：当前走非精确 `setAndAllowWhileIdle`（投递窗口可达 +1min）。若产品要求分钟级精准，需要引导用户开启"闹钟和提醒"特殊权限，或改用 `USE_EXACT_ALARM`（受商店政策限制）。
7. **资源文件由脚本生成**：`android/app/src/main/assets/www` 不在版本库内，构建前需 `scripts/build_assets.sh`（本次已验证 `ASSET_VERIFY PASS`，47 files）。未提交任何生成物。
8. 未 push / 未合并 / 未开 PR；未触碰 `treehole-w5`、`treehole-w6`。
