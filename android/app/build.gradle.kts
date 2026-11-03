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
        versionCode = 5
        versionName = "0.5.0"
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
}
