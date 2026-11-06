# RELEASE 签名指南

> 目标：让任何维护者都能在**不把密钥提交进 git** 的前提下，复现出一个可安装、可校验的 release APK。
> 本仓库**不包含任何 secrets**（没有 keystore、没有 `keystore.properties`、没有密码）。
> 本文只描述步骤，仓库内不要执行真实签名。

---

## 0. 安全红线（先读）

- **禁止**把 `*.keystore` / `*.jks` / `android/keystore.properties` / 密码提交进 git；
  `.gitignore` 已排除这三类文件（见仓库根 `.gitignore` 的「Android 签名材料」段）。
- keystore 是 App 的**身份**：同一 `applicationId` 的后续升级必须用同一把 key 签名，
  丢失后将无法覆盖安装/无法在应用商店更新。请在仓库外做离线备份（建议加密后放两份异地介质）。
- 不要把密码写进构建脚本、README、CI 日志；CI 里只用 GitHub Secrets。
- 一旦怀疑泄露：立刻生成新 keystore 并更换应用分发渠道，旧 key 视为作废。

---

## 1. 生成 keystore（一次性，仓库外执行）

```bash
mkdir -p ~/.secrets
keytool -genkeypair \
  -keystore ~/.secrets/xiaoman-release.jks \
  -alias xiaoman \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass '<STORE_PASSWORD>' -keypass '<KEY_PASSWORD>' \
  -dname "CN=Xiaoman Treehole, OU=Release, O=Xiaoman, L=Shanghai, ST=Shanghai, C=CN"
```

- 建议 `storepass` 与 `keypass` 用同一个强密码（PKCS12 格式下两者本来就一致）。
- 生成后核对：

```bash
keytool -list -v -keystore ~/.secrets/xiaoman-release.jks -storepass '<STORE_PASSWORD>' | head -20
```

## 2. 本地构建：二选一提供签名材料

### 2.1 `android/keystore.properties`（推荐本地开发）

在 `android/keystore.properties` 写入（该文件已被 git 忽略）：

```properties
storeFile=/absolute/path/to/xiaoman-release.jks
storePassword=<STORE_PASSWORD>
keyAlias=xiaoman
keyPassword=<KEY_PASSWORD>
```

`storeFile` 支持绝对路径，也支持相对 `android/` 或仓库根的路径。

### 2.2 环境变量（推荐 CI / 一次性构建）

| 变量 | 含义 |
|------|------|
| `ANDROID_KEYSTORE_PATH` | keystore 文件绝对路径 |
| `ANDROID_KEYSTORE_PASSWORD` | store 密码 |
| `ANDROID_KEY_ALIAS` | key alias（示例 `xiaoman`） |
| `ANDROID_KEY_PASSWORD` | key 密码 |

环境变量优先级高于 `keystore.properties`。构建脚本
（`android/app/build.gradle.kts`）只有在 **路径可读 + 三个密码/别名齐全** 时才启用 `signingConfig release`；
否则 `assembleRelease` 只产出 `app-release-unsigned.apk`（不可直接安装，用于验证构建可复现）。

## 3. 构建 release APK

```bash
cd android
./gradlew assembleRelease --no-daemon
```

产物：

```
android/app/build/outputs/apk/release/app-release.apk          # 有签名材料时
android/app/build/outputs/apk/release/app-release-unsigned.apk # 无签名材料时
```

> Wrapper 使用 Gradle 8.7（AGP 8.4.1，需要 JDK 17+）。
> `gradle-wrapper.properties` 因官方地址会 302 到 GitHub Releases（部分网络不可达）而改用华为云镜像，
> 并用 `distributionSha256Sum` 锁定官方 zip 摘要；如需切回官方，改回 `services.gradle.org` 即可。

## 4. 校验签名

```bash
# 1) 签名完整性与证书信息（v1/v2/v3 均应为 true）
$ANDROID_HOME/build-tools/34.0.0/apksigner verify --verbose --print-certs \
  android/app/build/outputs/apk/release/app-release.apk

# 2) 对齐检查（应为 Verifies）
$ANDROID_HOME/build-tools/34.0.0/zipalign -c -v 4 \
  android/app/build/outputs/apk/release/app-release.apk | tail -3

# 3) 版本号 / 包名 / SDK 区间
$ANDROID_HOME/build-tools/34.0.0/aapt2 dump badging \
  android/app/build/outputs/apk/release/app-release.apk | head -3
# 期望：package: name='com.xiaoman.treehole' versionCode='5' versionName='0.5.0'
```

证书指纹（`SHA-256`）首次发布时记录下来，后续每次发布比对，防止「悄悄换了 key」。

## 5. 生成并分发 SHA-256

```bash
# Linux / WSL
sha256sum android/app/build/outputs/apk/release/app-release.apk \
  | tee android/app/build/outputs/apk/release/app-release.apk.sha256

# Windows PowerShell
Get-FileHash -Algorithm SHA256 .\android\app\build\outputs\apk\release\app-release.apk
```

把 APK 与该 `.sha256` 一起发布（Release notes / 下载页），用户下载后可自行校验：

```bash
sha256sum -c app-release.apk.sha256
# Windows: certutil -hashfile app-release.apk SHA256
```

## 6. CI 注入（GitHub Actions）

签名出包统一由 `.github/workflows/android-release.yml` 承担（`android-ci.yml` 不再包含 release 装配 job）；缺 secrets 时该流程产 unsigned APK 并在摘要标 `signed=NO`。
在仓库 Settings → Secrets and variables → Actions 中配置：

| Secret 名称 | 内容 |
|-------------|------|
| `ANDROID_KEYSTORE_B64` | `base64 -w0 ~/.secrets/xiaoman-release.jks` 的输出（单行） |
| `ANDROID_KEYSTORE_PASSWORD` | store 密码 |
| `ANDROID_KEY_ALIAS` | key alias |
| `ANDROID_KEY_PASSWORD` | key 密码 |

workflow 会把 base64 解码到 `/tmp/release.keystore` 并通过 `ANDROID_KEYSTORE_PATH` 传给 Gradle，
构建后跑 `apksigner verify`，再把 APK 作为 artifact 上传（保留 30 天）。

> 注意：CI 的 keystore 只在 runner 的临时目录存在，构建结束即销毁；不要把它打进缓存或 artifact。
> 若用第三方 CI，等价做法是「Secret 注入 → 运行时解码 → 环境变量传入 → 构建后清理」。

## 7. 发布前检查清单

- [ ] 三处版本号一致：`android/app/build.gradle.kts`（`versionName`/`versionCode`）、
      `scripts/build_apk.sh`（`VER_NAME`/`VER_CODE`）、`web/index.html` 关于页文案。
- [ ] `CHANGELOG.md` 已补当次版本条目（日期、Added / Changed / Fixed）。
- [ ] `bash scripts/build_assets.sh` 已执行（`web/` → `android/app/src/main/assets/www` 同步）。
- [ ] `apksigner verify --print-certs` 的证书 SHA-256 与上次发布一致。
- [ ] `sha256sum` 校验文件随 APK 一起发布。
- [ ] 仓库中 `git status` 不含任何 keystore / 密码文件。
