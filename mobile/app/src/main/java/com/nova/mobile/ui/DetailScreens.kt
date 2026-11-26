package com.nova.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Pending
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.nova.mobile.Platform
import com.nova.mobile.Task
import com.nova.mobile.Video
import java.util.Date

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DetailTopBar(title: String, onBack: () -> Unit) {
    TopAppBar(
        title = { Text(title, color = NovaColors.OnSurface, fontSize = 18.sp, fontWeight = FontWeight.SemiBold) },
        navigationIcon = {
            IconButton(onBack) { Icon(Icons.Default.ArrowBack, "Back", tint = NovaColors.OnSurface) }
        },
        colors = TopAppBarDefaults.topAppBarColors(containerColor = NovaColors.Surface),
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(platform: Platform, onBack: () -> Unit, onTask: (Task) -> Unit) {
    Scaffold(containerColor = NovaColors.Background, topBar = { DetailTopBar(platform.name.replaceFirstChar { it.uppercase() }, onBack) }) { pad ->
        LazyColumn(Modifier.fillMaxSize().padding(pad).padding(horizontal = 16.dp)) {
            item {
                Spacer(Modifier.height(12.dp))
                Text("${platform.totalLinks} links across ${platform.tasks.size} jobs", color = NovaColors.Muted, fontSize = 12.sp)
                Spacer(Modifier.height(10.dp))
            }
            items(platform.tasks) { t ->
                Card(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp).clickable { onTask(t) },
                    colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                    shape = RoundedCornerShape(16.dp),
                ) {
                    Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier.size(40.dp).background(NovaColors.SurfaceAlt, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            Icon(Icons.Default.CloudDownload, null, tint = NovaColors.Primary, modifier = Modifier.size(20.dp))
                        }
                        Spacer(Modifier.size(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(t.name, color = NovaColors.OnSurface, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(t.status.uppercase(), color = if (t.status == "active") NovaColors.Online else NovaColors.Muted, fontSize = 10.sp)
                        }
                        Text("${t.links.size}", color = NovaColors.OnSurface, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LinksScreen(task: Task, onBack: () -> Unit, onVideo: (Video) -> Unit) {
    Scaffold(containerColor = NovaColors.Background, topBar = { DetailTopBar(task.name, onBack) }) { pad ->
        LazyColumn(Modifier.fillMaxSize().padding(pad).padding(horizontal = 16.dp)) {
            item {
                Spacer(Modifier.height(12.dp))
                Text(task.sourceUrl, color = NovaColors.Muted, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(10.dp))
            }
            items(task.links) { v ->
                val (icon, tint) = when (v.status) {
                    "uploaded" -> Icons.Default.CheckCircle to NovaColors.Online
                    "failed", "cancelled" -> Icons.Default.Error to NovaColors.Danger
                    else -> Icons.Default.Pending to NovaColors.Warning
                }
                Card(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp).clickable { onVideo(v) },
                    colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                    shape = RoundedCornerShape(16.dp),
                ) {
                    Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(icon, null, tint = tint, modifier = Modifier.size(22.dp))
                        Spacer(Modifier.size(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(
                                v.title.ifBlank { v.key },
                                color = NovaColors.OnSurface,
                                fontWeight = FontWeight.Medium,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(v.status.uppercase(), color = tint, fontSize = 10.sp)
                        }
                        v.uploaded?.let { Text(fmt(it), color = NovaColors.Muted, fontSize = 10.sp) }
                    }
                }
            }
        }
    }
}

/** Full per-link timeline — vertical dots + connecting line, dates under each event. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TimelineScreen(video: Video, onBack: () -> Unit) {
    Scaffold(
        containerColor = NovaColors.Background,
        topBar = { DetailTopBar(video.title.ifBlank { video.key }, onBack) },
    ) { pad ->
        LazyColumn(Modifier.fillMaxSize().padding(pad).padding(horizontal = 20.dp)) {
            item {
                Spacer(Modifier.height(18.dp))
                Text("VIDEO TITLES & TAGS", color = NovaColors.Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.size(8.dp))
                CopyRow("Original title (source)", video.originalTitle.ifBlank { video.title }, NovaColors.OnSurface)
                CopyRow("Edited title (AI analysis)", video.finalTitle, NovaColors.Primary)
                CopyRow("Hashtags", video.hashtags, NovaColors.Online, singleLine = false)
                Spacer(Modifier.height(16.dp))
                Card(
                    Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                    shape = RoundedCornerShape(18.dp),
                ) {
                    Column(Modifier.padding(20.dp)) {
                        Text("STATUS", color = NovaColors.Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.size(6.dp))
                        Text(
                            video.status.uppercase(),
                            color = if (video.status == "uploaded") NovaColors.Online else NovaColors.Warning,
                            fontSize = 22.sp,
                            fontWeight = FontWeight.Bold,
                        )
                        video.error?.let {
                            Spacer(Modifier.size(10.dp))
                            Text(it, color = NovaColors.Danger, fontSize = 12.sp)
                        }
                    }
                }
                Spacer(Modifier.height(22.dp))
                Text("ACTIVITY", color = NovaColors.Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(12.dp))
            }

            val events = listOf(
                Triple("Video detected", video.detected, Icons.Default.CloudDownload),
                Triple("Sent to pipeline", video.dispatched, Icons.Default.Pending),
                Triple("Downloaded", video.downloaded, Icons.Default.CloudDownload),
                Triple("Edited", video.edited, Icons.Default.CheckCircle),
                Triple("Uploaded to server", video.uploaded, Icons.Default.CheckCircle),
                Triple("Failed", video.failed, Icons.Default.Error),
                Triple("Cancelled", video.cancelled, Icons.Default.Error),
            )
            items(events) { (label, time, icon) ->
                TimelineRow(
                    label = label,
                    time = time?.let { fmt(it) },
                    icon = icon,
                    done = time != null,
                    danger = label == "Failed" || label == "Cancelled",
                )
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }
}

@Composable
private fun TimelineRow(
    label: String,
    time: String?,
    icon: ImageVector,
    done: Boolean,
    danger: Boolean,
) {
    val tint = when {
        !done -> NovaColors.Divider
        danger -> NovaColors.Danger
        else -> NovaColors.Primary
    }
    Row(Modifier.height(76.dp)) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.width(40.dp)) {
            Box(
                Modifier.size(30.dp).background(if (done) NovaColors.SurfaceAlt else Color.Transparent, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, null, tint = tint, modifier = Modifier.size(16.dp))
            }
            Box(Modifier.width(2.dp).height(46.dp).background(if (done) NovaColors.Divider else Color.Transparent))
        }
        Spacer(Modifier.size(12.dp))
        Column(Modifier.padding(top = 4.dp)) {
            Text(label, color = if (done) NovaColors.OnSurface else NovaColors.Muted, fontWeight = FontWeight.Medium, fontSize = 14.sp)
            Spacer(Modifier.size(3.dp))
            Text(time ?: "—", color = if (done) NovaColors.Muted else NovaColors.Divider, fontSize = 11.sp)
        }
    }
}

private fun fmt(ms: Long): String {
    val fmt = java.text.SimpleDateFormat("dd MMM yyyy, hh:mm a", java.util.Locale.getDefault())
    return fmt.format(Date(ms))
}
