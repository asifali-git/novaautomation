package com.nova.mobile.ui

import android.content.Context
import android.content.Intent
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
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
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
import com.nova.mobile.DownloadWorker
import com.nova.mobile.Prefs
import kotlinx.coroutines.flow.map

/** File detail shown after tapping a push notification or a download row. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FileOpenScreen(
    id: Int,
    title: String,
    fileName: String,
    url: String,
    onBack: () -> Unit,
    onDownload: () -> Unit,
    workId: java.util.UUID?,
    ctx: Context,
) {
    var pct by remember { mutableStateOf(-1) }
    var speed by remember { mutableStateOf("") }
    var done by remember { mutableStateOf(Prefs.isDownloaded(ctx, id)) }

    LaunchedEffect(workId) {
        if (workId == null) return@LaunchedEffect
        WorkManager.getInstance(ctx).getWorkInfoByIdFlow(workId)
            .map { info ->
                when (info?.state) {
                    WorkInfo.State.RUNNING -> Pair(
                        info.progress.getInt(DownloadWorker.PROGRESS, 0),
                        info.progress.getString(DownloadWorker.SPEED) ?: "",
                    )
                    WorkInfo.State.SUCCEEDED -> Pair(100, "Complete")
                    else -> Pair(-1, "")
                }
            }
            .collect { (p, s) ->
                pct = p
                speed = s
                if (p == 100) done = true
            }
    }

    val busy = pct in 0..99

    Scaffold(
        containerColor = NovaColors.Background,
        topBar = {
            TopAppBar(
                title = { Text("Video details", color = NovaColors.OnSurface, fontWeight = FontWeight.SemiBold) },
                navigationIcon = { IconButton(onBack) { Icon(Icons.Default.ArrowBack, "Back", tint = NovaColors.OnSurface) } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = NovaColors.Surface),
            )
        },
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(20.dp)) {
            Spacer(Modifier.height(16.dp))
            Card(
                Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                shape = RoundedCornerShape(18.dp),
            ) {
                Column(Modifier.padding(20.dp)) {
                    Text(
                        title.ifBlank { fileName },
                        color = NovaColors.OnSurface,
                        fontSize = 19.sp,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.size(6.dp))
                    Text(fileName, color = NovaColors.Muted, fontSize = 12.sp)
                    Spacer(Modifier.size(10.dp))
                    Text("MP4 · 1080p · original frame rate", color = NovaColors.Primary, fontSize = 11.sp)
                }
            }
            Spacer(Modifier.height(24.dp))

            when {
                busy -> {
                    Text("Downloading… $pct%", color = NovaColors.OnSurface, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Spacer(Modifier.size(8.dp))
                    LinearProgressIndicator(
                        progress = { pct / 100f },
                        modifier = Modifier.fillMaxWidth().height(8.dp),
                        color = NovaColors.Primary,
                        trackColor = NovaColors.SurfaceAlt,
                    )
                    Spacer(Modifier.size(6.dp))
                    Text(speed, color = NovaColors.Muted, fontSize = 12.sp)
                }
                done -> {
                    Card(
                        Modifier.fillMaxWidth(),
                        colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.CheckCircle, "Downloaded", tint = NovaColors.Online, modifier = Modifier.size(26.dp))
                            Spacer(Modifier.size(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text("Downloaded", color = NovaColors.Online, fontWeight = FontWeight.SemiBold)
                                Text("Saved in Movies/NOVA", color = NovaColors.Muted, fontSize = 11.sp)
                            }
                            IconButton(onClick = {
                                try {
                                    val i = Intent(Intent.ACTION_GET_CONTENT).apply { type = "video/*" }
                                    ctx.startActivity(i)
                                } catch (_: Exception) {}
                            }) { Icon(Icons.Default.Folder, "Open folder", tint = NovaColors.Primary) }
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
}
