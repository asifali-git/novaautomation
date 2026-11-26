package com.nova.mobile.ui

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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Screen to add / edit / delete tasks (platform links) and set best quality. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ManageTasksScreen(
    busy: Boolean,
    message: String?,
    onBack: () -> Unit,
    onAdd: (String, String) -> Unit,
    onEdit: (Int, String) -> Unit,
    onDelete: (Int) -> Unit,
    quality: Int,
    onQuality: (Int) -> Unit,
    taskList: List<Triple<Int, String, String>>, // id, name, source_url
) {
    var showAdd by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<Triple<Int, String, String>?>(null) }
    var newUrl by remember { mutableStateOf("") }
    var editUrl by remember { mutableStateOf("") }

    Scaffold(
        containerColor = NovaColors.Background,
        topBar = {
            TopAppBar(
                title = { Text("Manage links", color = NovaColors.OnSurface, fontWeight = FontWeight.SemiBold) },
                navigationIcon = { IconButton(onBack) { Icon(Icons.Default.ArrowBack, "Back", tint = NovaColors.OnSurface) } },
                actions = {
                    IconButton(onClick = { showAdd = true }) { Icon(Icons.Default.Add, "Add", tint = NovaColors.Primary) }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = NovaColors.Surface),
            )
        },
    ) { pad ->
        LazyColumn(Modifier.fillMaxSize().padding(pad).padding(horizontal = 16.dp)) {
            item {
                Spacer(Modifier.height(14.dp))
                Text("Download quality", color = NovaColors.Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.size(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(360, 480, 720, 1080).forEach { q ->
                        Button(
                            onClick = { onQuality(q) },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (quality == q) NovaColors.Primary else NovaColors.SurfaceAlt,
                                contentColor = if (quality == q) Color.White else NovaColors.Muted,
                            ),
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier.weight(1f),
                        ) { Text("${q}p", fontSize = 12.sp) }
                    }
                }
                if (busy) {
                    Spacer(Modifier.size(10.dp))
                    CircularProgressIndicator(Modifier.size(22.dp), color = NovaColors.Primary, strokeWidth = 2.dp)
                }
                message?.let {
                    Spacer(Modifier.size(10.dp))
                    Text(it, color = NovaColors.Online, fontSize = 12.sp)
                }
                Spacer(Modifier.height(20.dp))
                Text("Your links (${taskList.size})", color = NovaColors.OnSurface, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.size(8.dp))
            }
            items(taskList) { (id, name, url) ->
                Card(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                    shape = RoundedCornerShape(14.dp),
                ) {
                    Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(name, color = NovaColors.OnSurface, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(url, color = NovaColors.Muted, fontSize = 10.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        IconButton(onClick = { editing = Triple(id, name, url); editUrl = url }) {
                            Icon(Icons.Default.Edit, "Edit", tint = NovaColors.Warning, modifier = Modifier.size(18.dp))
                        }
                        IconButton(onClick = { onDelete(id) }) {
                            Icon(Icons.Default.Delete, "Delete", tint = NovaColors.Danger, modifier = Modifier.size(18.dp))
                        }
                    }
                }
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }

    val fieldColors = OutlinedTextFieldDefaults.colors(
        focusedBorderColor = NovaColors.Primary,
        unfocusedBorderColor = NovaColors.Divider,
        focusedTextColor = NovaColors.OnSurface,
        unfocusedTextColor = NovaColors.OnSurface,
        cursorColor = NovaColors.Primary,
        focusedLabelColor = NovaColors.Primary,
        unfocusedLabelColor = NovaColors.Muted,
    )

    if (showAdd) {
        AlertDialog(
            onDismissRequest = { showAdd = false },
            containerColor = NovaColors.Surface,
            title = { Text("Add platform link", color = NovaColors.OnSurface) },
            text = {
                Column {
                    OutlinedTextField(
                        value = newUrl,
                        onValueChange = { newUrl = it },
                        label = { Text("Channel / page URL") },
                        singleLine = true,
                        colors = fieldColors,
                        shape = RoundedCornerShape(10.dp),
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    if (newUrl.isNotBlank()) onAdd(newUrl.trim(), "youtube")
                    newUrl = ""; showAdd = false
                }) { Text("Add", color = NovaColors.Primary) }
            },
            dismissButton = { TextButton(onClick = { showAdd = false }) { Text("Cancel", color = NovaColors.Muted) } },
        )
    }

    editing?.let { (id, name, url) ->
        AlertDialog(
            onDismissRequest = { editing = null },
            containerColor = NovaColors.Surface,
            title = { Text("Edit $name", color = NovaColors.OnSurface) },
            text = {
                OutlinedTextField(
                    value = editUrl,
                    onValueChange = { editUrl = it },
                    label = { Text("Source URL") },
                    singleLine = true,
                    colors = fieldColors,
                    shape = RoundedCornerShape(10.dp),
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    if (editUrl.isNotBlank()) onEdit(id, editUrl.trim())
                    editing = null
                }) { Text("Save", color = NovaColors.Primary) }
            },
            dismissButton = { TextButton(onClick = { editing = null }) { Text("Cancel", color = NovaColors.Muted) } },
        )
    }
}
