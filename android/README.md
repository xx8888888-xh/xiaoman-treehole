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

（若仓库补上了 Gradle Wrapper，也可 `cd android && ./gradlew assembleDebug`，产物名为 `app-debug.apk`。）

## 真机安装
adb install <上一步产物路径>
或直接传 APK 到手机点击安装（设置允许未知来源）。

## 说明
- WebView 加载 file:///android_asset/www/index.html
- 网络 API（明天接真 API）在设置页填 base URL，cleartext 已放行（仅受信域名，见 network_security_config.xml）
- 语音：优先 Web Speech（部分WebView不可用）→ JSBridge 调系统 TTS（MainActivity 已实现，含中文语言设置与完成回调 window.__ttsEnded）
