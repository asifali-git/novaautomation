package com.nova.mobile.ui

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.nova.mobile.DownloadQueue
import com.nova.mobile.DownloadWorker
import com.nova.mobile.Prefs
import kotlinx.coroutines.flow.map

/** File detail: ONE title in the top bar, status + actions below. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FileOpenScreen(
    id: Int,
    title: String,
    fileName: String,
    url: String,
    onBack: () -> Unit,
    onDownload: () -> Unit,
    onCancel: () -> Unit,
    workId: java.util.UUID?,
    ctx: Context,
) {
    var state by remember { mutableStateOf<WorkInfo.State?>(null) }
    var pct by remember { mutableStateOf(0) }
    var speed by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    val saved = Prefs.isDownloaded(ctx, id)

    LaunchedEffect(workId) {
        if (workId == null) return@LaunchedEffect
        WorkManager.getInstance(ctx).getWorkInfoByIdFlow(workId)
            .map { w ->
                Quadruple(
                    w?.state,
                    when (w?.state) {
                        WorkInfo.State.RUNNING -> w.progress.getInt(DownloadWorker.PROGRESS, 0)
                        WorkInfo.State.SUCCEEDED -> 100
                        else -> 0
                    },
                    w?.progress?.getString(DownloadWorker.SPEED) ?: "",
                    w?.outputData?.getString("error") ?: "",
                )
            }
            .collect { (s, p, sp, e) -> state = s; pct = p; speed = sp; error = e }
    }

    val running = state == WorkInfo.State.RUNNING || state == WorkInfo.State.ENQUEUED
    val failed = state == WorkInfo.State.FAILED || error.isNotBlank()

    Scaffold(
        containerColor = NovaColors.Background,
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        title.ifBlank { fileName },
                        color = NovaColors.OnSurface,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                    )
                },
                navigationIcon = {
                    IconButton(onBack) { Icon(Icons.Default.ArrowBack, "Back", tint = NovaColors.OnSurface) }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = NovaColors.Surface),
            )
        },
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(20.dp)) {
            Spacer(Modifier.height(18.dp))

            // Format / quality summary (compact chips).
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("MP4", "1080p", "Original FPS").forEach { chip ->
                    Card(
                        colors = CardDefaults.cardColors(containerColor = NovaColors.SurfaceAlt),
                        shape = RoundedCornerShape(8.dp),
                    ) { Text(chip, color = NovaColors.Primary, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp)) }
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(fileName, color = NovaColors.Muted, fontSize = 12.sp)
            Spacer(Modifier.height(22.dp))

            Card(
                Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                shape = RoundedCornerShape(18.dp),
            ) {
                Column(Modifier.padding(20.dp)) {
                    when {
                        saved || state == WorkInfo.State.SUCCEEDED -> {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Default.CheckCircle, "Downloaded", tint = NovaColors.Online, modifier = Modifier.size(30.dp))
                                Spacer(Modifier.size(12.dp))
                                Column(Modifier.weight(1f)) {
                                    Text("Downloaded", color = NovaColors.Online, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                                    Text("Saved to Movies/NOVA", color = NovaColors.Muted, fontSize = 12.sp)
                                }
                                IconButton(onClick = {
                                    try { ctx.startActivity(Intent(Intent.ACTION_GET_CONTENT).apply { type = "video/*" }) } catch (_: Exception) {}
                                }) { Icon(Icons.Default.Folder, "Open file", tint = NovaColors.Primary) }
                            }
                        }
                        running -> {
                            Text("Downloading… $pct%", color = NovaColors.OnSurface, fontWeight = FontWeight.Medium, fontSize = 14.sp)
                            Spacer(Modifier.size(8.dp))
                            LinearProgressIndicator(
                                progress = { pct / 100f },
                                modifier = Modifier.fillMaxWidth().height(8.dp),
                                color = NovaColors.Primary,
                                trackColor = NovaColors.SurfaceAlt,
                            )
                            Spacer(Modifier.size(6.dp))
                            Text(speed, color = NovaColors.Muted, fontSize = 12.sp)
                            Spacer(Modifier.size(12.dp))
                            OutlinedButton(
                                onClick = onCancel,
                                modifier = Modifier.fillMaxWidth(),
                                colors = ButtonDefaults.outlinedButtonColors(contentColor = NovaColors.Danger),
                                shape = RoundedCornerShape(12.dp),
                            ) { Text("Cancel download") }
                        }
                        failed -> {
                            Text("Download failed", color = NovaColors.Danger, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                            if (error.isNotBlank()) {
                                Spacer(Modifier.size(6.dp))
                                Text(error, color = NovaColors.Muted, fontSize = 12.sp)
                            }
                            Spacer(Modifier.size(14.dp))
                            Row {
                                Button(
                                    onClick = onDownload,
                                    modifier = Modifier.weight(1f),
                                    colors = ButtonDefaults.buttonColors(containerColor = NovaColors.Primary),
                                    shape = RoundedCornerShape(12.dp),
                                ) { Text("Retry") }
                                Spacer(Modifier.size(8.dp))
                                TextButton(onClick = onCancel, modifier = Modifier.weight(1f)) {
                                    Text("Cancel", color = NovaColors.Muted)
                                }
                            }
                        }
                        else -> {
                            Button(
                                onClick = onDownload,
                                modifier = Modifier.fillMaxWidth().height(52.dp),
                                shape = RoundedCornerShape(12.dp),
                                colors = ButtonDefaults.buttonColors(containerColor = NovaColors.Primary, contentColor = Color.White),
                            ) {
                                Icon(Icons.Default.CloudDownload, null, Modifier.size(20.dp))
                                Spacer(Modifier.size(8.dp))
                                Text("Download", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
            Text("Source", color = NovaColors.Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            Text(url, color = NovaColors.OnSurface, fontSize = 11.sp, maxLines = 2)
        }
    }
}

private data class Quadruple<A, B, C, D>(val a: A, val b: B, val c: C, val d: D)
