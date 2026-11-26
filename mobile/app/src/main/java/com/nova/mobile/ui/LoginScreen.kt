package com.nova.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun LoginScreen(
    error: String?,
    loading: Boolean,
    onLogin: (String, String) -> Unit,
) {
    var identity by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }

    Box(Modifier.fillMaxSize()) {
        Column(
            Modifier
                .fillMaxSize()
                .padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(
                Modifier
                    .size(76.dp)
                    .clip(CircleShape)
                    .background(NovaColors.Primary),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "N",
                    color = Color.White,
                    fontSize = 38.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.height(16.dp))
            Text(
                "NOVA Studio",
                color = NovaColors.OnSurface,
                fontSize = 28.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Your videos, delivered automatically",
                color = NovaColors.Muted,
                fontSize = 13.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(28.dp))

            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(20.dp),
                colors = CardDefaults.cardColors(containerColor = NovaColors.Surface),
                elevation = CardDefaults.cardElevation(0.dp),
            ) {
                Column(Modifier.padding(20.dp)) {
                    Text(
                        "Welcome back",
                        color = NovaColors.OnSurface,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        "Use the same account as the NOVA website",
                        color = NovaColors.Muted,
                        fontSize = 12.sp,
                    )
                    Spacer(Modifier.height(18.dp))

                    val fieldColors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = NovaColors.Primary,
                        unfocusedBorderColor = NovaColors.Divider,
                        focusedTextColor = NovaColors.OnSurface,
                        unfocusedTextColor = NovaColors.OnSurface,
                        cursorColor = NovaColors.Primary,
                        focusedLabelColor = NovaColors.Primary,
                        unfocusedLabelColor = NovaColors.Muted,
                    )
                    OutlinedTextField(
                        value = identity,
                        onValueChange = { identity = it },
                        label = { Text("Username or email") },
                        leadingIcon = { Icon(Icons.Default.Person, null, tint = NovaColors.Muted) },
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                        colors = fieldColors,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("Password") },
                        leadingIcon = { Icon(Icons.Default.Lock, null, tint = NovaColors.Muted) },
                        trailingIcon = {
                            IconButton(onClick = { showPassword = !showPassword }) {
                                Icon(
                                    if (showPassword) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                                    null,
                                    tint = NovaColors.Muted,
                                )
                            }
                        },
                        visualTransformation = if (showPassword) VisualTransformation.None
                        else PasswordVisualTransformation(),
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                        colors = fieldColors,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(18.dp))
                    Button(
                        onClick = { onLogin(identity.trim(), password) },
                        enabled = !loading && identity.isNotBlank() && password.isNotBlank(),
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(50.dp),
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = NovaColors.Primary,
                            contentColor = Color.White,
                        ),
                    ) {
                        if (loading) {
                            CircularProgressIndicator(
                                Modifier.size(20.dp),
                                color = Color.White,
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Text("Sign in", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                    if (!error.isNullOrBlank()) {
                        Spacer(Modifier.height(10.dp))
                        Text(error, color = NovaColors.Danger, fontSize = 12.sp)
                    }
                }
            }
            Spacer(Modifier.height(20.dp))
            Text(
                "Secure · End-to-end encrypted delivery",
                color = NovaColors.Muted,
                fontSize = 11.sp,
            )
        }
    }
}
