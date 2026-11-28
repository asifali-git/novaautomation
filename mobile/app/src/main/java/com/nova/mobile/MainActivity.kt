package com.nova.mobile

import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
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
import com.nova.mobile.ui.FileOpenScreen
import com.nova.mobile.ui.HomeScreen
import com.nova.mobile.ui.HomeTab
import com.nova.mobile.ui.LoginScreen
import com.nova.mobile.ui.ManagedTask
import com.nova.mobile.ui.ManageTasksScreen
import com.nova.mobile.ui.NovaTheme
import kotlinx.coroutines.launch
import org.json.JSONArray

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifications.ensure(this)
        askNotificationPermission()
        Api.token = Prefs.token(this)

        // Notification tap -> open the exact file that was clicked.
        val openId = intent.getIntExtra("delivery_id", -1)
        if (openId > 0) {
            Notifications.stop(this, openId)
            PendingDelivery.title = intent.getStringExtra("title") ?: ""
            PendingDelivery.fileName = intent.getStringExtra("file_name") ?: ""
            PendingDelivery.url = intent.getStringExtra("catbox_url") ?: ""
        }

        setContent { NovaTheme { App() } }
    }

    object PendingDelivery {
        var title: String = ""
        var fileName: String = ""
        var url: String = ""
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
        data object Manage : Screen()
        data class FileOpen(val id: Int, val title: String, val url: String, val fileName: String) : Screen()
    }

    @Composable
    private fun App() {
        val scope = rememberCoroutineScope()
        val startScreen: Screen = if (PendingDelivery.title.isNotBlank()) {
            Screen.FileOpen(
                intent.getIntExtra("delivery_id", 0),
                PendingDelivery.title,
                PendingDelivery.url,
                PendingDelivery.fileName,
            )
        } else if (Api.token.isBlank()) Screen.Login else Screen.Home
        var screen by remember { mutableStateOf<Screen>(startScreen) }
        // Navigation stack: back always returns to the previous screen, never
        // exits the app (unless the user is already on Home and taps back twice).
        val backStack = remember { mutableStateListOf<Screen>() }

        fun go(next: Screen) {
            backStack.add(screen)
            screen = next
        }

        fun goBack() {
            if (backStack.isNotEmpty()) {
                screen = backStack.removeAt(backStack.size - 1)
            } else {
                screen = Screen.Home
            }
        }

        androidx.activity.compose.BackHandler(enabled = screen !is Screen.Login) {
            goBack()
        }
        var platforms by remember { mutableStateOf(emptyList<com.nova.mobile.Platform>()) }
        var deliveries by remember { mutableStateOf(emptyList<Delivery>()) }
        var loading by remember { mutableStateOf(false) }
        var error by remember { mutableStateOf<String?>(null) }
        var tab by remember { mutableStateOf(HomeTab.PLATFORMS) }
        var auto by remember { mutableStateOf(Prefs.autoDownload(this)) }
        var quality by remember { mutableStateOf(1080) }
        var manageBusy by remember { mutableStateOf(false) }
        var manageMsg by remember { mutableStateOf<String?>(null) }
        var fileSaved by remember { mutableStateOf(false) }

        fun loadSettings() {
            scope.launch {
                when (val r = Api.settings()) {
                    is Api.Result.Ok -> {
                        val s = r.value.optJSONObject("settings") ?: r.value.optJSONObject("config")
                        s?.optInt("video_quality")?.let { if (it > 0) quality = it }
                    }
                    is Api.Result.Err -> {}
                }
            }
        }

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

        fun reloadAll() {
            loadTree()
            loadDeliveries()
            loadSettings()
        }

        fun registerFcm() {
            FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
                Api.token = Prefs.token(this@MainActivity)
                if (Api.token.isBlank()) return@addOnSuccessListener
                scope.launch {
                    when (val r = Api.registerDevice(token, Prefs.deviceId(this@MainActivity))) {
                        is Api.Result.Ok -> loadDeliveries()
                        is Api.Result.Err -> {}
                    }
                }
            }
        }

        // Register FCM + pull pending every time the app opens — fixes the
        // "no device registered" case when the user was already logged in.
        LaunchedEffect(Unit) {
            if (Api.token.isNotBlank()) {
                registerFcm()
                loadDeliveries()
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
                                reloadAll()
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
                    onPlatform = { go(Screen.Tasks(it)) },
                    onManage = { go(Screen.Manage) },
                    onRefresh = { reloadAll() },
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
                onBack = { goBack() },
                onTask = { go(Screen.Links(s.platform, it)) },
            )

            is Screen.Links -> LinksScreen(
                task = s.task,
                onBack = { goBack() },
                onVideo = { go(Screen.Timeline(it)) },
            )

            is Screen.Timeline -> TimelineScreen(
                video = s.video,
                onBack = { goBack() },
            )

            Screen.Manage -> ManageTasksScreen(
                busy = manageBusy,
                message = manageMsg,
                onBack = { goBack(); manageMsg = null },
                onAdd = { url, dest ->
                    manageBusy = true; manageMsg = null
                    scope.launch {
                        when (val r = Api.addTask(url, dest)) {
                            is Api.Result.Ok -> { manageMsg = "Link added"; reloadAll() }
                            is Api.Result.Err -> manageMsg = r.message
                        }
                        manageBusy = false
                    }
                },
                onEdit = { id, url ->
                    manageBusy = true
                    scope.launch {
                        val fields = org.json.JSONObject().apply { put("source_url", url) }
                        when (val r = Api.editTask(id, fields)) {
                            is Api.Result.Ok -> { manageMsg = "Link updated"; reloadAll() }
                            is Api.Result.Err -> manageMsg = r.message
                        }
                        manageBusy = false
                    }
                },
                onDelete = { id ->
                    manageBusy = true
                    scope.launch {
                        when (val r = Api.deleteTask(id)) {
                            is Api.Result.Ok -> { manageMsg = "Link deleted"; reloadAll() }
                            is Api.Result.Err -> manageMsg = r.message
                        }
                        manageBusy = false
                    }
                },
                quality = quality,
                onQuality = { q ->
                    scope.launch {
                        when (val r = Api.setQuality(q)) {
                            is Api.Result.Ok -> { quality = q; manageMsg = "Quality set to ${q}p" }
                            is Api.Result.Err -> manageMsg = r.message
                        }
                    }
                },
                taskList = platforms.flatMap { p ->
                    p.tasks.map { t ->
                        com.nova.mobile.ui.ManagedTask(
                            t.id, t.name, t.sourceUrl,
                            t.sendToApp, t.appFile, t.uploadSocial,
                        )
                    }
                },
                onRule = { t, send, file, upload ->
                    manageBusy = true
                    scope.launch {
                        val rule = org.json.JSONObject().apply {
                            put("send_to_app", if (send) 1 else 0)
                            put("app_file", file)
                            put("upload_social", if (upload) 1 else 0)
                        }
                        when (val r = Api.setDeliveryRule(t.id, rule)) {
                            is Api.Result.Ok -> {
                                manageMsg = "Rule saved: ${if (!send) "off" else file}, upload ${if (upload) "on" else "off"}"
                                t.sendToApp = send; t.appFile = file; t.uploadSocial = upload
                            }
                            is Api.Result.Err -> manageMsg = r.message
                        }
                        manageBusy = false
                    }
                },
            )

            is Screen.FileOpen -> FileOpenScreen(
                title = s.title,
                fileName = s.fileName,
                url = s.url,
                onBack = { goBack() },
                onDownload = {
                    val d = Delivery(s.id, s.fileName, s.url, s.title, "", 0)
                    DownloadQueue.enqueue(this@MainActivity, d, true)
                    fileSaved = true
                },
                saved = fileSaved,
            )
        }
    }
}
