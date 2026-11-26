package com.nova.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** A labelled text row with a one-tap copy button. */
@Composable
fun CopyRow(
    label: String,
    value: String,
    tint: androidx.compose.ui.graphics.Color = NovaColors.Primary,
    singleLine: Boolean = true,
) {
    if (value.isBlank()) return
    val ctx = LocalContext.current
    Card(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
        shape = RoundedCornerShape(12.dp),
    ) {
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(label, color = NovaColors.Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.size(3.dp))
                Text(
                    value,
                    color = tint,
                    fontSize = 13.sp,
                    maxLines = if (singleLine) 1 else 4,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            IconButton(onClick = {
                val clip = ClipboardManager::class.java
                val mgr = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                mgr.setPrimaryClip(ClipData.newPlainText(label, value))
                Toast.makeText(ctx, "$label copied", Toast.LENGTH_SHORT).show()
            }) {
                Icon(Icons.Default.ContentCopy, "Copy $label", tint = NovaColors.Muted, modifier = Modifier.size(18.dp))
            }
        }
    }
}
