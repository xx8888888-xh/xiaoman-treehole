// app 模块构建脚本 —— 与 scripts/build_apk.sh 等价，但走官方 AGP 流水线（可复现 release 出包）。
// 版本号单一真源：这里 versionName/versionCode 与 scripts/build_apk.sh 的 VER_NAME/VER_CODE、
// web/index.html 关于页文案保持一致（发布前同步改这三处）。
import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
}

// ---- 签名：只从环境变量 / android/keystore.properties 读取；两者都没有 → 不签名（产出 unsigned）----
// CI 变量名见 .github/workflows/android-ci.yml 与 docs/RELEASE_SIGNING.md
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.isFile) {
        FileInputStream(keystorePropertiesFile).use { load(it) }
    }
}

fun secret(envName: String, propName: String): String? =
    System.getenv(envName)?.takeIf { it.isNotBlank() }
        ?: keystoreProperties.getProperty(propName)?.takeIf { it.isNotBlank() }

val releaseStorePath: String? = secret("ANDROID_KEYSTORE_PATH", "storeFile")
val releaseStoreFile: File? = releaseStorePath?.let { path ->
    listOf(rootProject.file(path), rootProject.file("android/$path")).firstOrNull { it.isFile }
}
val hasReleaseSigning = releaseStoreFile != null &&
    secret("ANDROID_KEYSTORE_PASSWORD", "storePassword") != null &&
    secret("ANDROID_KEY_ALIAS", "keyAlias") != null &&
    secret("ANDROID_KEY_PASSWORD", "keyPassword") != null

android {
    namespace = "com.xiaoman.treehole"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.xiaoman.treehole"
        minSdk = 24
        targetSdk = 34
        // 与手工构建 scripts/build_apk.sh 的 VER_CODE/VER_NAME 保持同源，
        // 避免 Gradle 路径与手工路径出包版本号不一致（审计 android D3）。
        versionCode = 6
        versionName = "0.6.0"
    }
    signingConfigs {
        if (hasReleaseSigning) {
            // 存在则复用，否则新建（避免同名配置重复定义）
            (findByName("release") ?: create("release")).apply {
                storeFile = releaseStoreFile
                storePassword = secret("ANDROID_KEYSTORE_PASSWORD", "storePassword")
                keyAlias = secret("ANDROID_KEY_ALIAS", "keyAlias")
                keyPassword = secret("ANDROID_KEY_PASSWORD", "keyPassword")
                // v1+v2+v3 覆盖 Android 7.0 起的老设备
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            // 无 secrets 时保持 null → 输出 app-release-unsigned.apk（可复现构建，不含签名材料）
            signingConfig = if (hasReleaseSigning) signingConfigs.getByName("release") else null
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging {
        resources.excludes += "META-INF/*"
    }
}

dependencies {
    // 提醒调度/权限分支的纯逻辑单元测试（无 Robolectric 依赖，保证离线可跑）
    testImplementation("junit:junit:4.13.2")
}

// ============================================================================
// 发布物守卫（2026-11-08）：Gradle / Android Studio 直接 Build 也强制
//   web/ → assets/www 同步 + 校验，失败即硬失败（不做 warning 放行）。
// assets/www 是 .gitignore 的生成物：不同步就会把目录里的旧副本打进 APK，
// 这正是「APK 行为落后于 web/」的唯一来源。这里不依赖 bash / tar / find，
// 统一调用 scripts/*.py（Windows 与 Linux 行为一致）。
// ============================================================================
val repoRootDir: File = rootProject.projectDir.parentFile      // android/ 的上一级 = 仓库根
val webSourceDir = File(repoRootDir, "web")
val assetsWwwDir = File(repoRootDir, "android/app/src/main/assets/www")
val assetScriptsDir = File(repoRootDir, "scripts")

// Python 解释器：环境变量 TREEHOLE_PYTHON 优先，其次 PATH 里的 py/python3/python。
// 只做文件存在性判断、不执行进程；找不到时任务运行期会以明确报错硬失败。
fun locatePython(): List<String> {
    System.getenv("TREEHOLE_PYTHON")?.takeIf { it.isNotBlank() }?.let { return listOf(it) }
    val isWindows = System.getProperty("os.name").lowercase().contains("win")
    val pathDirs = System.getenv("PATH").orEmpty().split(File.pathSeparator).filter { it.isNotBlank() }
    val names = if (isWindows) listOf("py.exe", "python.exe", "python3.exe") else listOf("python3", "python")
    for (name in names) {
        val hit = pathDirs.asSequence()
            .map { File(it, name) }
            .firstOrNull { it.isFile && it.length() > 0L }
            ?.takeIf { !isWindows || !it.absolutePath.contains("WindowsApps") }  // 跳过 Microsoft Store 占位符
        if (hit != null) {
            // py.exe 是启动器，需显式指定 Python 3
            return if (name == "py.exe") listOf(hit.absolutePath, "-3") else listOf(hit.absolutePath)
        }
    }
    return if (isWindows) listOf("python") else listOf("python3")
}
val pythonCmd = locatePython()

val syncAssets = tasks.register<Exec>("syncAssets") {
    group = "build"
    description = "web/ → android/app/src/main/assets/www 同步并生成 ASSET_MANIFEST.json"
    workingDir = repoRootDir
    environment("PYTHONIOENCODING", "utf-8")  // Windows 重定向时中文/符号不触发编码异常
    commandLine(*(pythonCmd + listOf(
        File(assetScriptsDir, "sync_assets.py").absolutePath,
        "--src", webSourceDir.absolutePath,
        "--dst", assetsWwwDir.absolutePath,
    )).toTypedArray())
}

val verifyAssets = tasks.register<Exec>("verifyAssets") {
    group = "verification"
    description = "校验 assets/www 与 web/ 逐文件一致（不一致即拒绝出包）"
    dependsOn(syncAssets)
    workingDir = repoRootDir
    environment("PYTHONIOENCODING", "utf-8")
    commandLine(*(pythonCmd + listOf(
        File(assetScriptsDir, "verify_assets.py").absolutePath,
        "--src", webSourceDir.absolutePath,
        "--dst", assetsWwwDir.absolutePath,
    )).toTypedArray())
}

// 挂到 preBuild（assembleDebug/assembleRelease、Android Studio Build 均经过）；mergeXxxAssets
// 再挂一次，避免某些变体链路绕过 preBuild 时把旧 assets 打进包。
tasks.matching { it.name == "preBuild" || (it.name.startsWith("merge") && it.name.endsWith("Assets")) }
    .configureEach { dependsOn(verifyAssets) }
