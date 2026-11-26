plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.gms.google-services")
}

android {
    namespace = "com.nova.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.nova.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildFeatures { viewBinding = false }
}

dependencies {
    // Lightweight: no Retrofit / OkHttp / Glide / Compose.
    // FCM for push, WorkManager for offline-safe downloads.
    implementation("com.google.firebase:firebase-messaging:24.1.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
