# 小满树洞 · Android 壳工程说明

## 用途
把 web/（全部前端+离线引擎+Live2D）打包成安卓 APK。WebView 壳 + 系统TTS桥。

## 结构
- app/src/main/java/com/xiaoman/treehole/MainActivity.java —— WebView + TTS 桥（纯 Java，零依赖）
- app/src/main/assets/ —— 构建时由 ../../web/ 同步注入（build_assets.sh）
- app/build.gradle.kts —— AGP 8.4，minSdk 24 / targetSdk 34，Java 17

## 构建

推荐走无 Gradle 手工流水线（CI 默认路径，无需 gradle wrapper）：
1. 设置 `ANDROID_HOME` 指向 Android SDK 根目录，安装 `platforms;android-34` `build-tools;34.0.0` `platform-tools`
2. `bash scripts/build_assets.sh` —— 把 `web/` 同步进 `android/app/src/main/assets/www`（唯一真源，务必先跑）
3. `bash scripts/build_apk.sh` —— aapt2 → javac/ecj → d8 → 签名，产物 `android/app/build/outputs/apk/debug/xiaoman-treehole-v030-debug.apk`

（也可 `cd android && ./gradlew assembleDebug`，产物 `app/build/outputs/apk/debug/app-debug.apk`；见下「发布物守卫」。）

## 发布物守卫（2026-11-08 补齐）

`assets/www` 是 `.gitignore` 的生成物，旧行为是 Gradle / Android Studio 直接 Build 不会同步它，
于是把目录里现成的旧副本打进 APK（「APK 行为落后于 web/」的唯一来源）。现已补上：

- `:app:syncAssets` —— 跑 [scripts/sync_assets.py](../scripts/sync_assets.py)：`web/` → `assets/www` 清空重建，
  并生成 `ASSET_MANIFEST.json`（`source_commit` 必须可解析，拿不到 git HEAD 直接失败）。
  与 `build_assets.sh` 同口径，但纯 Python，**不依赖 bash / tar / find**，Windows 与 Linux 一致。
- `:app:verifyAssets` —— 跑 [scripts/verify_assets.py](../scripts/verify_assets.py)：逐文件 sha256/字节比对
  `web/` 与 `assets/www`（同一排除规则，见 `scripts/build_assets.sh` 的 `asset_excludes.sh`）。
- 两者都挂在 `preBuild` 与 `merge*Assets` 上，任务非零退出即 **BUILD FAILED**，**不做 warning 放行**。
  所以 `./gradlew assembleDebug`、Android Studio Build、`build_apk.sh` 三条路径都会从 `web/` 重算发布物。

环境要求：`git` + Python 3 在 PATH（`py` / `python` 任一即可）；解释器不在 PATH 时用环境变量
`TREEHOLE_PYTHON` 指定绝对路径。手工等价命令：`bash scripts/build_assets.sh` 或 `python scripts/sync_assets.py`。

**已实测**（本机 JDK 21 + SDK 34）：`./gradlew :app:verifyAssets` 成功；把 `assets/www` 改坏后再跑校验 →
`BUILD FAILED`（`ASSET_VERIFY FAIL mismatch=1`）；`./gradlew assembleDebug` 成功后
`python scripts/verify_manifest.py --apk .../app-debug.apk` → **PASS 76 files / 1,443,082 B**。

**仍待验证**：Android Studio 图形界面点 Build（任务链与 `gradlew` 相同，未在 IDE 内实点）；
release / minify / 多变体链路（本机无签名材料，未跑）；非 Windows 的 Gradle 路径（CI `android-ci.yml`
会在推送后跑 `./gradlew assembleDebug` 复核）。

## 真机安装
adb install <上一步产物路径>
或直接传 APK 到手机点击安装（设置允许未知来源）。

## 说明
- WebView 加载 file:///android_asset/www/index.html
- 网络 API（明天接真 API）在设置页填 base URL，cleartext 已放行（仅受信域名，见 network_security_config.xml）
- 语音：优先 Web Speech（部分WebView不可用）→ JSBridge 调系统 TTS（MainActivity 已实现，含中文语言设置与完成回调 window.__ttsEnded）
