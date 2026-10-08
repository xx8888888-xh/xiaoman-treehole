#!/usr/bin/env bash
# ============================================================
# build_apk.sh · 无 Gradle 手工 APK 构建流水线
# 原理：aapt2(资源) → javac(代码) → d8(转dex) → 打包 → zipalign → apksigner
# 依赖：build-tools + platform android.jar、javac 或 ecj、rg(ripgrep，用于过滤编译警告)
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- 版本统一定义（单一变量三处引用）----
VER_CODE=3
VER_NAME="0.3.0"
APK_BASE="xiaoman-treehole-v${VER_NAME//./}-debug"

# ---- SDK 路径：强制要求 ANDROID_HOME，缺省不再用他机路径 ----
: "${ANDROID_HOME:?请设置 ANDROID_HOME 环境变量（指向 Android SDK 根目录）}"
SDK="$ANDROID_HOME"
BT="$SDK/build-tools/34.0.0"
JAR="$SDK/platforms/android-34/android.jar"

APP="$ROOT/android/app"
MAIN="$APP/src/main"
WORK="$APP/build/manual"
OUT="$APP/build/outputs/apk/debug"

# ---- ECJ 回落：缓存到项目 .cache 目录，下载后校验 SHA-256 ----
ECJ_CACHE_DIR="$ROOT/.cache"
ECJ_JAR="$ECJ_CACHE_DIR/ecj-3.33.0.jar"
ECJ_URL="https://mirrors.cloud.tencent.com/nexus/repository/maven-public/org/eclipse/jdt/ecj/3.33.0/ecj-3.33.0.jar"
ECJ_SHA256="f7686c4960cf70c2ebc5c500a73a8cfc04541b730c18f1c5c21329889b137f45"  # 官方 ecj-3.33.0.jar

rm -rf "$WORK"; mkdir -p "$WORK"/{gen,classes,dex} "$OUT" "$ECJ_CACHE_DIR"
cd "$MAIN"

echo "[1/6] aapt2 compile+link"
"$BT/aapt2" compile --dir res -o "$WORK/res.zip"
"$BT/aapt2" link -o "$WORK/base.apk" -I "$JAR" --manifest AndroidManifest.xml \
  -A "$MAIN/assets" --java "$WORK/gen" \
  --min-sdk-version 24 --target-sdk-version 34 \
  --version-code "$VER_CODE" --version-name "$VER_NAME" \
  "$WORK/res.zip"

echo "[2/6] javac"
find "$WORK/gen" "$MAIN/java" -name "*.java" > "$WORK/sources.txt"
# 优先用系统 javac（GitHub runner/本地JDK均有），否则回落 ecj（沙箱JRE-only环境）
if command -v javac >/dev/null 2>&1; then
  # 编译错误不应被过滤器隐藏：先跑 javac 拿真实退出码，再过滤回显 stderr（CI runner 可能无 rg）
  JAVAC_EXIT=0
  javac -source 1.8 -target 1.8 -nowarn -classpath "$JAR" -d "$WORK/classes" @"$WORK/sources.txt" 2>"$WORK/javac.err" || JAVAC_EXIT=$?
  echo "[javac] sources: $(wc -l < "$WORK/sources.txt") entries; classes produced: $(find "$WORK/classes" -name '*.class' 2>/dev/null | wc -l)"
  if [ "$JAVAC_EXIT" -ne 0 ]; then
    echo "---- javac.err ----" >&2
    cat "$WORK/javac.err" >&2
    echo "-------------------" >&2
  fi
  grep -v -e bootstrap -e deprecat "$WORK/javac.err" >&2 || true
  if [ "$JAVAC_EXIT" -ne 0 ]; then
    echo "❌ javac 编译失败（退出码 $JAVAC_EXIT），错误日志：" >&2
    cat "$WORK/javac.err" >&2
    exit "$JAVAC_EXIT"
  fi
else
  if [ ! -f "$ECJ_JAR" ]; then
    echo "[*] 下载 ecj.jar 到 $ECJ_JAR ..."
    curl -sL "$ECJ_URL" -o "$ECJ_JAR"
    # 下载后强校验 SHA-256（防供应链篡改），校验不通过直接失败
    if command -v sha256sum >/dev/null 2>&1; then
      ACTUAL_SHA=$(sha256sum "$ECJ_JAR" | cut -d' ' -f1)
      if [ "$ACTUAL_SHA" != "$ECJ_SHA256" ]; then
        echo "❌ ecj.jar 校验失败：期望 $ECJ_SHA256，实际 $ACTUAL_SHA" >&2
        rm -f "$ECJ_JAR"
        exit 1
      fi
    else
      echo "⚠ 缺少 sha256sum，无法校验 ecj.jar 完整性" >&2
    fi
  fi
  ECJ_EXIT=0
  java -jar "$ECJ_JAR" -source 1.8 -target 1.8 -nowarn -classpath "$JAR" -d "$WORK/classes" @"$WORK/sources.txt" 2>"$WORK/ecj.err" || ECJ_EXIT=$?
  grep -v -e bootstrap -e deprecat -e warning "$WORK/ecj.err" >&2 || true
  if [ "$ECJ_EXIT" -ne 0 ]; then
    echo "❌ ecj 编译失败（退出码 $ECJ_EXIT），错误日志：" >&2
    cat "$WORK/ecj.err" >&2
    exit "$ECJ_EXIT"
  fi
fi

echo "[3/6] d8 → dex"
# 避免 word-splitting 与 ARG_MAX：用 find -print0 | xargs -0
# 逐批合并到同一 dex（xargs 分批时后续调用不会覆盖先前输出）
find "$WORK/classes" -name "*.class" -print0 | xargs -0 "$BT/d8" --release --lib "$JAR" --min-api 24 --output "$WORK/dex"
[ -f "$WORK/dex/classes.dex" ] || { echo "ERROR: classes.dex missing after d8"; exit 1; }
echo "[3.1] class files: $(find "$WORK/classes" -name '*.class' | wc -l), dex: $(ls -la "$WORK/dex" | tail -1)"
# classes.dex 必须在 APK 根目录
cp "$WORK/base.apk" "$OUT/app-unsigned.apk"
cd "$WORK/dex" && zip -q -j "$OUT/app-unsigned.apk" classes.dex

echo "[4/6] zipalign"
"$BT/zipalign" -f 4 "$OUT/app-unsigned.apk" "$OUT/app-aligned.apk"

echo "[5/6] debug keystore"
KS="$WORK/debug.keystore"
KS_PASS="${KS_PASS:-xiaoman2026}"  # 仅 debug 用途，建议通过环境变量 KS_PASS 覆盖
[ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -alias xiaoman \
  -storepass "$KS_PASS" -keypass "$KS_PASS" -keyalg RSA -keysize 2048 \
  -validity 10000 -dname "CN=Xiaoman Treehole,O=Xiaoman,C=CN" >/dev/null 2>&1

echo "[6/6] apksigner"
"$BT/apksigner" sign --ks "$KS" --ks-pass "pass:$KS_PASS" \
  --out "$OUT/$APK_BASE.apk" "$OUT/app-aligned.apk"
"$BT/apksigner" verify --print-certs "$OUT/$APK_BASE.apk" | head -2
# 清理中间产物（防 CI 通配符误命中）
rm -f "$OUT/app-unsigned.apk" "$OUT/app-aligned.apk"

echo "APK_DONE $OUT/$APK_BASE.apk ($(du -h "$OUT/$APK_BASE.apk" | cut -f1))"
