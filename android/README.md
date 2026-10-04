# 小满树洞 · Android 壳工程说明

## 用途
把 web/（全部前端+离线引擎+Live2D）打包成安卓 APK。WebView 壳 + 系统TTS桥。

## 结构
- app/src/main/java/com/xiaoman/treehole/MainActivity.kt —— WebView + TTS 桥
- app/src/main/assets/ —— 构建时由 ../../web/ 同步注入（build_assets.sh）
- app/build.gradle.kts —— AGP 8.4 + Kotlin，minSdk 24 / targetSdk 34

## 构建（沙箱内）
1. ANDROID_HOME=/home/z/android-sdk，sdkmanager 装 platforms;android-34 build-tools;34.0.0 platform-tools
2. gradle wrapper 或系统 gradle 8.7
3. cd android && gradle assembleDebug --no-daemon

## 真机安装
adb install app/build/outputs/apk/debug/app-debug.apk
或直接传 APK 到手机点击安装（设置允许未知来源）。

## 说明
- WebView 加载 file:///android_asset/www/index.html
- 网络 API（明天接真 API）在设置页填 base URL，cleartext 已放行
- 语音：优先 Web Speech（部分WebView不可用）→ JSBridge 调系统 TTS（MainActivity 已实现）
