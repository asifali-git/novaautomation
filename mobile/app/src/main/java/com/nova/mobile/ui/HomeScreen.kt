package com.nova.mobile.ui

import android.content.Context
import android.content.Intent
import com.nova.mobile.DownloadWorker
import com.nova.mobile.Prefs
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BottomAppBar
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.work.WorkInfo
import androidx.work.WorkManager
import kotlinx.coroutines.flow.map
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.nova.mobile.Delivery
import com.nova.mobile.Platform

enum class HomeTab { PLATFORMS, DOWNLOADS, SETTINGS }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    username: String,
    ctx: Context,
    platforms: List<Platform>,
    deliveries: List<Delivery>,
    loading: Boolean,
    autoDownload: Boolean,
    tab: HomeTab,
    onTab: (HomeTab) -> Unit,
    onPlatform: (Platform) -> Unit,
    onManage: () -> Unit,
    onRefresh: () -> Unit,
    onSync: () -> Unit,
    onManualDownload: (Delivery) -> Unit,
    onAutoChange: (Boolean) -> Unit,
    onLogout: () -> Unit,
) {
    Scaffold(
        containerColor = NovaColors.Background,
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier.size(34.dp).clip(CircleShape).background(NovaColors.PrimaryDark),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(username.take(1).uppercase(), color = Color.White, fontWeight = FontWeight.Bold)
                        }
                        Spacer(Modifier.size(12.dp))
                        Column {
                            Text("NOVA Studio", color = NovaColors.OnSurface, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                            Text(username, color = NovaColors.Muted, fontSize = 11.sp)
                        }
                    }
                },
                actions = {
                    IconButton(onClick = onRefresh) {
                        Icon(Icons.Default.Refresh, "Refresh", tint = NovaColors.Muted)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = NovaColors.Surface,
                    titleContentColor = NovaColors.OnSurface,
                ),
            )
        },
        bottomBar = {
            BottomAppBar(containerColor = NovaColors.Surface, tonalElevation = 0.dp) {
                val items = listOf(
                    Triple(HomeTab.PLATFORMS, Icons.Default.Folder, "Platforms"),
                    Triple(HomeTab.DOWNLOADS, Icons.Default.CloudDownload, "Downloads"),
                    Triple(HomeTab.SETTINGS, Icons.Default.Settings, "Settings"),
                )
                items.forEach { (t, icon, label) ->
                    NavigationBarItem(
                        selected = tab == t,
                        onClick = { onTab(t) },
                        icon = { Icon(icon, null) },
                        label = { Text(label, fontSize = 10.sp) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = NovaColors.Primary,
                            selectedTextColor = NovaColors.Primary,
                            unselectedIconColor = NovaColors.Muted,
                            unselectedTextColor = NovaColors.Muted,
                            indicatorColor = NovaColors.SurfaceAlt,
                        ),
                    )
                }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (tab) {
                HomeTab.PLATFORMS -> PlatformsTab(platforms, loading, onPlatform, onManage)
                HomeTab.DOWNLOADS -> DownloadsTab(deliveries, onSync, onManualDownload, ctx)
                HomeTab.SETTINGS -> SettingsTab(autoDownload, onAutoChange, onLogout, username)
            }
        }
    }
}

@Composable
private fun PlatformsTab(
    platforms: List<Platform>,
    loading: Boolean,
    onPlatform: (Platform) -> Unit,
    onManage: () -> Unit,
) {
    if (loading) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(color = NovaColors.Primary)
        }
        return
    }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Spacer(Modifier.height(20.dp))
            Text("Your platforms", color = NovaColors.OnSurface, fontSize = 24.sp, fontWeight = FontWeight.Bold)
            Text(
                "Tap a platform to see its links and history",
                color = NovaColors.Muted,
                fontSize = 12.sp,
            )
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = onManage,
                colors = ButtonDefaults.buttonColors(containerColor = NovaColors.SurfaceAlt, contentColor = NovaColors.Primary),
                shape = RoundedCornerShape(10.dp),
            ) {
                Icon(Icons.Default.Edit, null, Modifier.size(16.dp))
                Spacer(Modifier.size(6.dp))
                Text("Add / Edit / Delete links", fontSize = 12.sp)
            }
            Spacer(Modifier.height(12.dp))
        }
        if (platforms.isEmpty()) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                    shape = RoundedCornerShape(16.dp),
                ) {
                    Text(
                        "No platforms yet. Add a job on the NOVA website first.",
                        color = NovaColors.Muted,
                        modifier = Modifier.padding(20.dp),
                    )
                }
            }
        }
        items(platforms) { p ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 5.dp)
                    .clickable { onPlatform(p) },
                colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                shape = RoundedCornerShape(16.dp),
            ) {
                Row(
                    Modifier.fillMaxWidth().padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        Modifier.size(44.dp).clip(CircleShape).background(NovaColors.SurfaceAlt),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            p.name.take(1).uppercase(),
                            color = NovaColors.Primary,
                            fontWeight = FontWeight.Bold,
                            fontSize = 18.sp,
                        )
                    }
                    Spacer(Modifier.size(14.dp))
                    Column(Modifier.weight(1f)) {
                        Text(p.name.replaceFirstChar { it.uppercase() }, color = NovaColors.OnSurface, fontWeight = FontWeight.SemiBold)
                        Text("${p.tasks.size} jobs", color = NovaColors.Muted, fontSize = 11.sp)
                    }
                    Badge(containerColor = NovaColors.PrimaryDark) {
                        Text("${p.totalLinks}", color = Color.White, fontSize = 11.sp)
                    }
                }
            }
        }
        item { Spacer(Modifier.height(16.dp)) }
    }
}

@Composable
private fun DownloadsTab(
    deliveries: List<Delivery>,
    onSync: () -> Unit,
    onManualDownload: (Delivery) -> Unit,
    ctx: Context,
) {
    var refresh by remember { mutableStateOf(0) }
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Spacer(Modifier.height(20.dp))
        Text("Downloads", color = NovaColors.OnSurface, fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text("Videos delivered to your device", color = NovaColors.Muted, fontSize = 12.sp)
        Spacer(Modifier.height(12.dp))
        Button(
            onClick = { onSync(); refresh++ },
            colors = ButtonDefaults.buttonColors(containerColor = NovaColors.PrimaryDark),
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth(),
        ) { Icon(Icons.Default.Refresh, null, Modifier.size(18.dp)); Spacer(Modifier.size(8.dp)); Text("Sync now") }
        Spacer(Modifier.height(12.dp))
        if (deliveries.isEmpty()) {
            Card(colors = CardDefaults.cardColors(containerColor = NovaColors.Surface), shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(Icons.Default.CheckCircle, null, tint = NovaColors.Online, modifier = Modifier.size(36.dp))
                    Spacer(Modifier.size(8.dp))
                    Text("All caught up", color = NovaColors.OnSurface, fontWeight = FontWeight.SemiBold)
                    Text("No pending downloads", color = NovaColors.Muted, fontSize = 12.sp)
                }
            }
        } else {
            LazyColumn {
                items(deliveries) { d -> DownloadRow(d, ctx, refresh, onManualDownload) }
            }
        }
    }
}

@Composable
private fun DownloadRow(
    d: Delivery,
    ctx: Context,
    refresh: Int,
    onManualDownload: (Delivery) -> Unit,
) {
    var pct by remember(d.id, refresh) { mutableStateOf(-1) }
    var speed by remember(d.id, refresh) { mutableStateOf("") }
    val saved = Prefs.isDownloaded(ctx, d.id)

    LaunchedEffect(d.id, refresh) {
        WorkManager.getInstance(ctx).getWorkInfosForUniqueWorkFlow("nova_${d.id}")
            .map { infos ->
                val w = infos.firstOrNull()
                when (w?.state) {
                    WorkInfo.State.RUNNING -> Pair(w.progress.getInt(DownloadWorker.PROGRESS, 0), w.progress.getString(DownloadWorker.SPEED) ?: "")
                    WorkInfo.State.SUCCEEDED -> Pair(100, "Complete")
                    else -> Pair(-1, "")
                }
            }
            .collect { (p, s) -> pct = p; speed = s }
    }

    Card(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
        shape = RoundedCornerShape(16.dp),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(d.title.ifBlank { d.fileName }, color = NovaColors.OnSurface, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("${d.platform} · MP4 · ${d.size / 1_000_000} MB", color = NovaColors.Muted, fontSize = 11.sp)
                }
                when {
                    saved || pct == 100 -> {
                        Icon(Icons.Default.CheckCircle, "Downloaded", tint = NovaColors.Online, modifier = Modifier.size(28.dp))
                        IconButton(onClick = {
                            try { ctx.startActivity(Intent(Intent.ACTION_GET_CONTENT).apply { type = "video/*" }) } catch (_: Exception) {}
                        }) { Icon(Icons.Default.Folder, "Open", tint = NovaColors.Primary) }
                    }
                    pct in 0..99 -> {
                        Text("$pct%  $speed", color = NovaColors.Primary, fontSize = 11.sp)
                    }
                    else -> {
                        Button(
                            onClick = { onManualDownload(d) },
                            colors = ButtonDefaults.buttonColors(containerColor = NovaColors.Primary),
                            shape = RoundedCornerShape(10.dp),
                        ) { Text("Download", fontSize = 12.sp) }
                    }
                }
            }
            if (pct in 0..99) {
                Spacer(Modifier.size(8.dp))
                LinearProgressIndicator(
                    progress = { pct / 100f },
                    modifier = Modifier.fillMaxWidth().height(6.dp),
                    color = NovaColors.Primary,
                    trackColor = NovaColors.SurfaceAlt,
                )
            }
        }
    }
}

@Composable
private fun SettingsTab(
    auto: Boolean,
    onAuto: (Boolean) -> Unit,
    onLogout: () -> Unit,
    username: String,
) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Spacer(Modifier.height(20.dp))
        Text("Settings", color = NovaColors.OnSurface, fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(16.dp))
        Card(colors = CardDefaults.cardColors(containerColor = NovaColors.Surface), shape = RoundedCornerShape(16.dp)) {
            Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Auto-download", color = NovaColors.OnSurface, fontWeight = FontWeight.SemiBold)
                    Text("Save videos to gallery automatically", color = NovaColors.Muted, fontSize = 11.sp)
                }
                Switch(
                    checked = auto,
                    onCheckedChange = onAuto,
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = Color.White,
                        checkedTrackColor = NovaColors.Primary,
                        uncheckedThumbColor = NovaColors.Muted,
                        uncheckedTrackColor = NovaColors.SurfaceAlt,
                    ),
                )
            }
        }
        Spacer(Modifier.height(12.dp))
        Card(colors = CardDefaults.cardColors(containerColor = NovaColors.Surface), shape = RoundedCornerShape(16.dp)) {
            Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.Person, null, tint = NovaColors.Muted)
                Spacer(Modifier.size(12.dp))
                Column {
                    Text(username, color = NovaColors.OnSurface)
                    Text("Signed in", color = NovaColors.Online, fontSize = 11.sp)
                }
            }
        }
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = onLogout,
            colors = ButtonDefaults.buttonColors(containerColor = NovaColors.SurfaceAlt, contentColor = NovaColors.Danger),
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Icon(Icons.Default.Delete, null, Modifier.size(18.dp)); Spacer(Modifier.size(8.dp)); Text("Sign out")
        }
    }
}
