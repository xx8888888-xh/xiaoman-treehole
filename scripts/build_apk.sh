#!/usr/bin/env bash
# ============================================================
# build_apk.sh · 无 Gradle 手工 APK 构建流水线
# 原理：aapt2(资源) → javac(代码) → d8(转dex) → 打包 → zipalign → apksigner
# 依赖：build-tools + platform android.jar、javac 或 ecj、rg(ripgrep，用于过滤编译警告)
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- 版本统一定义（单一变量三处引用；与 CHANGELOG / web 关于页一致）----
VER_CODE=5
VER_NAME="0.5.0"
APK_BASE="xiaoman-treehole-v${VER_NAME//./}-debug"

# ---- 发布物单一真源：打包前强制把 web/ 同步进 assets/www 并生成哈希清单 ----
ASSETS_SCRIPT="$ROOT/scripts/build_assets.sh"
[ -f "$ASSETS_SCRIPT" ] || { echo "❌ 缺少 $ASSETS_SCRIPT（发布物单一真源脚本，拒绝打包）" >&2; exit 1; }
echo "[0/6] 同步 web → assets/www（唯一真源）+ 生成 ASSET_MANIFEST.json"
bash "$ASSETS_SCRIPT"

# ---- SDK 路径：强制要求 ANDROID_HOME，缺省不再用他机路径 ----
: "${ANDROID_HOME:?请设置 ANDROID_HOME 环境变量（指向 Android SDK 根目录）}"
SDK="$ANDROID_HOME"
# build-tools 自选：取版本最高且含**可执行** aapt2 的那个
# （有的本地 SDK 目录里混进的是 Windows 的 aapt2.exe，WSL 下不可执行）
BT=""
for d in "$SDK"/build-tools/*; do
  if [ -x "$d/aapt2" ]; then BT="$d"; fi
done
[ -n "$BT" ] || { echo "找不到可执行的 aapt2（ANDROID_HOME=$SDK）"; exit 1; }
# platform jar：优先 android-34，其次任意已安装平台
JAR="$SDK/platforms/android-34/android.jar"
if [ ! -f "$JAR" ]; then
  JAR="$(ls "$SDK"/platforms/*/android.jar 2>/dev/null | head -1)"
fi
[ -f "$JAR" ] || { echo "找不到 android.jar（ANDROID_HOME=$SDK）"; exit 1; }

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
  --debug-mode \
  --min-sdk-version 24 --target-sdk-version 34 \
  --version-code "$VER_CODE" --version-name "$VER_NAME" \
  "$WORK/res.zip"

echo "[2/6] javac"
# @sources.txt 按空白切分，路径里不能有空格 → 统一写相对 MAIN 的路径
find "$WORK/gen" "$MAIN/java" -name "*.java" -print0 \
  | xargs -0 -I{} realpath --relative-to="$MAIN" {} > "$WORK/sources.txt"
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
if command -v zip >/dev/null 2>&1; then
  cd "$WORK/dex" && zip -q -j "$OUT/app-unsigned.apk" classes.dex
else
  # 无 zip 的精简环境（如某些 WSL 发行版）→ 用 Python 标准库追加，零额外依赖
  python3 - "$OUT/app-unsigned.apk" "$WORK/dex/classes.dex" <<'PY'
import sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as z:
    z.write(dex, "classes.dex")
PY
fi

echo "[4/6] zipalign"
"$BT/zipalign" -f 4 "$OUT/app-unsigned.apk" "$OUT/app-aligned.apk"

echo "[5/6] debug keystore"
KS="$ECJ_CACHE_DIR/debug.keystore"   # 放缓存目录：跨构建复用，签名稳定（否则每次 install -r 都要先卸载）
KS_PASS="${KS_PASS:-xiaoman2026}"  # 仅 debug 用途，建议通过环境变量 KS_PASS 覆盖
[ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -alias xiaoman \
  -storepass "$KS_PASS" -keypass "$KS_PASS" -keyalg RSA -keysize 2048 \
  -validity 10000 -dname "CN=Xiaoman Treehole,O=Xiaoman,C=CN" >/dev/null 2>&1

echo "[6/6] apksigner"
"$BT/apksigner" sign --ks "$KS" --ks-pass "pass:$KS_PASS" \
  --out "$OUT/$APK_BASE.apk" "$OUT/app-aligned.apk"
"$BT/apksigner" verify --print-certs "$OUT/$APK_BASE.apk" | head -2

# ---- 打包后校验：APK 内 assets/www 必须与 ASSET_MANIFEST.json 逐文件一致 ----
echo "[6.1] 校验 APK assets/www 与清单一致"
VERIFY_PY="$ROOT/scripts/verify_manifest.py"
[ -f "$VERIFY_PY" ] || { echo "❌ 缺少 $VERIFY_PY（无法校验 APK 发布物）" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ 需要 python3 校验 APK 发布物" >&2; exit 1; }
python3 "$VERIFY_PY" --apk "$OUT/$APK_BASE.apk"

# 清理中间产物（防 CI 通配符误命中）
rm -f "$OUT/app-unsigned.apk" "$OUT/app-aligned.apk"

echo "APK_DONE $OUT/$APK_BASE.apk ($(du -h "$OUT/$APK_BASE.apk" | cut -f1))"
