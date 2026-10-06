plugins {
    id("com.android.application")
}

android {
    namespace = "com.xiaoman.treehole"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.xiaoman.treehole"
        minSdk = 24
        targetSdk = 34
        // 与手工构建 scripts/build_apk.sh 的 VER_CODE/VER_NAME 保持同源，
        // 避免 Gradle 路径与手工路径出包版本号不一致（审计 android D3）。
        versionCode = 3
        versionName = "0.3.0"
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            signingConfig = signingConfigs.release
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging {
        resources.excludes += "META-INF/*"
    }
    signingConfigs {
        create("release") {
            storeFile = file(System.getenv("ANDROID_KEYSTORE_PATH") ?: "")
            storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
            keyAlias = System.getenv("ANDROID_KEY_ALIAS")
            keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
        }
    }
}

dependencies {
}
