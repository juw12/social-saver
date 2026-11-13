// Social Saver for Android: the same Python app (app.py + saver/) runs inside the APK with
// Chaquopy and shows its screen in a WebView. Run ../sync.sh first to copy the Python files in.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val keystoreFile: String? = System.getenv("ANDROID_KEYSTORE_FILE")

android {
    namespace = "com.juw12.socialsaver"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.juw12.socialsaver"
        minSdk = 29 // Android 10: saving into Download/ without storage permission
        targetSdk = 35
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "dev"
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64") // phones + the emulator used in CI
        }
    }

    signingConfigs {
        create("release") {
            if (keystoreFile != null) {
                storeFile = file(keystoreFile)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = "socialsaver"
                keyPassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                storeType = "pkcs12"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystoreFile != null) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    jvmToolchain(17)
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("gallery-dl")
            install("yt-dlp")
            install("flask")
            install("requests")
            install("segno")
        }
    }
}
