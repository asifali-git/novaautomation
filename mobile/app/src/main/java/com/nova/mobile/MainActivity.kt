package com.nova.mobile

import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessaging
import com.nova.mobile.ui.TasksScreen
import com.nova.mobile.ui.LinksScreen
import com.nova.mobile.ui.TimelineScreen
import com.nova.mobile.ui.HomeScreen
import com.nova.mobile.ui.HomeTab
import com.nova.mobile.ui.LoginScreen
import com.nova.mobile.ui.NovaTheme
import kotlinx.coroutines.launch
import org.json.JSONArray

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifications.ensure(this)
        askNotificationPermission()
        Api.token = Prefs.token(this)

        setContent { NovaTheme { App() } }
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(android.Manifest.permission.POST_NOTIFICATIONS),
                101,
            )
        }
    }

    private sealed class Screen {
        data object Login : Screen()
        data object Home : Screen()
        data class Tasks(val platform: com.nova.mobile.Platform) : Screen()
        data class Links(val platform: com.nova.mobile.Platform, val task: com.nova.mobile.Task) : Screen()
        data class Timeline(val video: com.nova.mobile.Video) : Screen()
    }

    @Composable
    private fun App() {
        val scope = rememberCoroutineScope()
        var screen by remember { mutableStateOf<Screen>(if (Api.token.isBlank()) Screen.Login else Screen.Home) }
        var platforms by remember { mutableStateOf(emptyList<com.nova.mobile.Platform>()) }
        var deliveries by remember { mutableStateOf(emptyList<Delivery>()) }
        var loading by remember { mutableStateOf(false) }
        var error by remember { mutableStateOf<String?>(null) }
        var tab by remember { mutableStateOf(HomeTab.PLATFORMS) }
        var auto by remember { mutableStateOf(Prefs.autoDownload(this)) }

        fun loadTree() {
            loading = true
            scope.launch {
                when (val r = Api.tree()) {
                    is Api.Result.Ok -> {
                        val arr = r.value.optJSONArray("platforms") ?: JSONArray()
                        platforms = (0 until arr.length()).map { com.nova.mobile.Platform.from(arr.getJSONObject(it)) }
                        loading = false
                    }
                    is Api.Result.Err -> { error = r.message; loading = false }
                }
            }
        }

        fun loadDeliveries() {
            scope.launch {
                when (val r = Api.deliveries()) {
                    is Api.Result.Ok -> {
                        val arr = r.value.optJSONArray("deliveries") ?: JSONArray()
                        deliveries = (0 until arr.length()).map { Delivery.from(arr.getJSONObject(it)) }
                    }
                    is Api.Result.Err -> error = r.message
                }
            }
        }

        fun registerFcm() {
            FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
                Api.token = Prefs.token(this@MainActivity)
                scope.launch { Api.registerDevice(token, Prefs.deviceId(this@MainActivity)) }
            }
        }

        when (val s = screen) {
            Screen.Login -> LoginScreen(
                error = error,
                loading = loading,
                onLogin = { identity, password ->
                    loading = true
                    error = null
                    scope.launch {
                        when (val r = Api.login(identity, password)) {
                            is Api.Result.Ok -> {
                                val name = r.value.optJSONObject("user")?.optString("username") ?: identity
                                Prefs.saveLogin(this@MainActivity, Api.token, name)
                                registerFcm()
                                loadTree()
                                loadDeliveries()
                                screen = Screen.Home
                                loading = false
                            }
                            is Api.Result.Err -> { error = r.message; loading = false }
                        }
                    }
                },
            )

            Screen.Home -> {
                if (platforms.isEmpty() && !loading) loadTree()
                HomeScreen(
                    username = Prefs.username(this),
                    platforms = platforms,
                    deliveries = deliveries,
                    loading = loading,
                    autoDownload = auto,
                    tab = tab,
                    onTab = { tab = it },
                    onPlatform = { screen = Screen.Tasks(it) },
                    onRefresh = { loadTree(); loadDeliveries() },
                    onSync = {
                        DownloadQueue.syncPending(this@MainActivity, deliveries, auto)
                        loadDeliveries()
                    },
                    onManualDownload = { DownloadQueue.enqueue(this@MainActivity, it, true) },
                    onAutoChange = { auto = it; Prefs.setAutoDownload(this@MainActivity, it) },
                    onLogout = {
                        Prefs.clear(this@MainActivity)
                        Api.token = ""
                        screen = Screen.Login
                    },
                )
            }

            is Screen.Tasks -> TasksScreen(
                platform = s.platform,
                onBack = { screen = Screen.Home },
                onTask = { screen = Screen.Links(s.platform, it) },
            )

            is Screen.Links -> LinksScreen(
                task = s.task,
                onBack = { screen = Screen.Tasks(s.platform) },
                onVideo = { screen = Screen.Timeline(it) },
            )

            is Screen.Timeline -> TimelineScreen(
                video = s.video,
                onBack = { screen = Screen.Home },
            )
        }
    }
}
