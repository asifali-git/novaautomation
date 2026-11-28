package com.nova.mobile.ui

import android.content.Context
import com.nova.mobile.DownloadWorker
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.work.WorkManager
import androidx.work.WorkInfo
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Shown when the user taps a push notification — the exact file they clicked. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FileOpenScreen(
    title: String,
    fileName: String,
    url: String,
    onBack: () -> Unit,
    onDownload: () -> Unit,
    saved: Boolean,
    workId: java.util.UUID? = null,
    ctx: Context,
) {
    var progress by remember { mutableStateOf(-1) }
    LaunchedEffect(workId) {
        if (workId == null) return@LaunchedEffect
        WorkManager.getInstance(ctx)
            .getWorkInfoByIdFlow(workId)
            .map { info ->
                when (info?.state) {
                    WorkInfo.State.RUNNING -> info.progress.getInt(DownloadWorker.PROGRESS, 0)
                    WorkInfo.State.SUCCEEDED -> 100
                    else -> -1
                }
            }
            .distinctUntilChanged()
            .collect { progress = it }
    }
    Scaffold(
        containerColor = NovaColors.Background,
        topBar = {
            TopAppBar(
                title = { Text("Video", color = NovaColors.OnSurface, fontWeight = FontWeight.SemiBold) },
                navigationIcon = { IconButton(onBack) { Icon(Icons.Default.ArrowBack, "Back", tint = NovaColors.OnSurface) } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = NovaColors.Surface),
            )
        },
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(16.dp)) {
            Spacer(Modifier.height(18.dp))
            Card(
                Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                shape = RoundedCornerShape(18.dp),
            ) {
                Column(Modifier.padding(20.dp)) {
                    Text(title.ifBlank { fileName }, color = NovaColors.OnSurface, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.size(8.dp))
                    Text(fileName, color = NovaColors.Muted, fontSize = 12.sp)
                }
            }
            Spacer(Modifier.height(20.dp))
            if (progress in 0..100) {
                Text("Downloading… $progress%", color = NovaColors.OnSurface, fontSize = 13.sp)
                Spacer(Modifier.size(6.dp))
                LinearProgressIndicator(
                    progress = { progress / 100f },
                    modifier = Modifier.fillMaxWidth().height(8.dp),
                    color = NovaColors.Primary,
                    trackColor = NovaColors.SurfaceAlt,
                )
                Spacer(Modifier.height(12.dp))
            }
            Button(
                onClick = onDownload,
                enabled = !saved,
                modifier = Modifier.fillMaxWidth().height(52.dp),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = NovaColors.Primary, contentColor = Color.White),
            ) {
                Icon(Icons.Default.CloudDownload, null, Modifier.size(20.dp))
                Spacer(Modifier.size(8.dp))
                Text(if (saved) "Saved to gallery" else "Download & save to gallery", fontSize = 14.sp)
            }
        }
    }
}
