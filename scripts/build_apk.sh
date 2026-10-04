#!/usr/bin/env bash
# ============================================================
# build_apk.sh · 无 Gradle 手工 APK 构建流水线
# 原理：aapt2(资源) → javac(代码) → d8(转dex) → 打包 → zipalign → apksigner
# 依赖仅为 build-tools + platform android.jar（已装），全程 <2 分钟。
# ============================================================
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK="${ANDROID_HOME:-/home/z/android-sdk}"
BT="$SDK/build-tools/34.0.0"
JAR="$SDK/platforms/android-34/android.jar"
APP="$ROOT/android/app"
MAIN="$APP/src/main"
WORK="$APP/build/manual"
OUT="$APP/build/outputs/apk/debug"

rm -rf "$WORK"; mkdir -p "$WORK"/{gen,classes,dex} "$OUT"
cd "$MAIN"

echo "[1/6] aapt2 compile+link"
"$BT/aapt2" compile --dir res -o "$WORK/res.zip"
"$BT/aapt2" link -o "$WORK/base.apk" -I "$JAR" --manifest AndroidManifest.xml \
  -A "$MAIN/assets" --java "$WORK/gen" "$WORK/res.zip"

echo "[2/6] javac"
find "$WORK/gen" "$MAIN/java" -name "*.java" > "$WORK/sources.txt"
# 优先用系统 javac（GitHub runner/本地JDK均有），否则回落 ecj（沙箱JRE-only环境）
if command -v javac >/dev/null 2>&1; then
  javac -source 1.8 -target 1.8 -nowarn -classpath "$JAR" -d "$WORK/classes" @"$WORK/sources.txt" 2>&1 | rg -v "bootstrap|deprecat" || true
else
  ECJ_JAR="${ECJ_JAR:-/home/z/ecj.jar}"
  [ -f "$ECJ_JAR" ] || curl -sL "https://mirrors.cloud.tencent.com/nexus/repository/maven-public/org/eclipse/jdt/ecj/3.33.0/ecj-3.33.0.jar" -o "$ECJ_JAR"
  java -jar "$ECJ_JAR" -source 1.8 -target 1.8 -nowarn -classpath "$JAR" -d "$WORK/classes" @"$WORK/sources.txt" 2>&1 | rg -v "bootstrap|deprecat|warning" || true
fi

echo "[3/6] d8 → dex"
"$BT/d8" --release --lib "$JAR" --min-api 24 --output "$WORK/dex" $(find "$WORK/classes" -name "*.class")
# classes.dex 必须在 APK 根目录
cp "$WORK/base.apk" "$OUT/app-unsigned.apk"
cd "$WORK/dex" && zip -q -j "$OUT/app-unsigned.apk" classes.dex

echo "[4/6] zipalign"
"$BT/zipalign" -f 4 "$OUT/app-unsigned.apk" "$OUT/app-aligned.apk"

echo "[5/6] debug keystore"
KS="$WORK/debug.keystore"
[ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -alias xiaoman \
  -storepass xiaoman2026 -keypass xiaoman2026 -keyalg RSA -keysize 2048 \
  -validity 10000 -dname "CN=Xiaoman Treehole,O=Xiaoman,C=CN" >/dev/null 2>&1

echo "[6/6] apksigner"
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:xiaoman2026 \
  --out "$OUT/xiaoman-treehole-v0.3-debug.apk" "$OUT/app-aligned.apk"
"$BT/apksigner" verify --print-certs "$OUT/xiaoman-treehole-v0.3-debug.apk" | head -2

echo "APK_DONE $OUT/xiaoman-treehole-v0.3-debug.apk ($(du -h $OUT/xiaoman-treehole-v0.3-debug.apk | cut -f1))"
