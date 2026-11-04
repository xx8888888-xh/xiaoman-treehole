# AGENT_SCOPE · feat/android-background (treehole-w8)

> 本 worktree 只服务一个任务：Android 端后台存活 / 提醒 / 语音策略 / 耗电 / 适配。
> 其他 worktree（`treehole-w5` 悬浮窗、`treehole-w6` UI、主工作区）不要在本分支改动。

## 允许改动

- `android/**`（含 `android/app/src/main/java/**`、`res/**`、`AndroidManifest.xml`、`app/build.gradle.kts`、`gradle.properties`）

## 禁止改动

- `web/**`
- `server/**`
- `android/app/src/main/assets/www/**`（构建期由 `web/` 同步注入，属 web 归属）
- `scripts/verify_assets.py`、`scripts/build_assets.sh` 等构建脚本

## 跨层约定（给 web 侧 owner 的接口契约）

Android 壳通过 `WebViewClient.shouldInterceptRequest` 在 `assets/www/index.html` 的
`</head>` 前注入 `xm_host_bootstrap.js`（源文件在 `android/app/src/main/res/raw/`），
不修改任何 `web/` 源文件。注入脚本对外暴露：

- `window.__xmHost.pause()` / `.resume()`：由 Activity/FloatWindowService 生命周期调用，
  冻结页面全部 `setInterval/setTimeout`，并在暂停期间禁止 `Reminders.due()` 与
  `Reminders.markDone()`（杜绝"隐藏页收到提醒还标记完成"）。
- `window.__xmHost.paused`：当前是否处于后台冻结态。
- JS 桥 `window.AndroidHost`（Java 侧 `AndroidHostBridge`）：
  - `syncReminders(json)` / `drainDelivered()`：提醒列表与原生 `SharedPreferences` 双向同步
  - `isAutoRead()` / `setAutoRead(bool)`：自动朗读开关（默认关 = 点击才朗读）
  - `getHeartbeatIntervalMs()` / `setHeartbeatIntervalMs(ms)`：心跳间隔可配置
  - `requestNotificationPermission()` / `requestExactAlarmPermission()` /
    `openBatteryOptimizationSettings()` / `status()`
- 注入脚本识别的"点读手势"选择器：
  `#messages`、`[data-tts]`、`[class*=voice]`、`[id*=voice]`、`[class*=speak]`、
  `[aria-label*=朗读]`；web 侧后续加"气泡喇叭"时命中以上任一即可发声。
- 自动朗读开关的 JS 侧持久化键：`localStorage["xiaoman_tts_autoplay"] = "1"`（注入脚本直接识别，
  web 设置页写入该键即可生效，无需再改 Android）。

## 不做的事

- 不 push、不合并、不开 PR（交付只给分支 + commit 哈希）
- 不抢占 `emulator-5554`（其他会话在用）
