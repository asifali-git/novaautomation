package com.nova.mobile.ui

import android.app.Activity
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// WhatsApp-inspired professional palette.
object NovaColors {
    val Primary = Color(0xFF00A884)
    val PrimaryDark = Color(0xFF008069)
    val Background = Color(0xFF0B141A)     // deep chat-like dark
    val Surface = Color(0xFF1F2C34)
    val SurfaceAlt = Color(0xFF2A3942)
    val OnSurface = Color(0xFFE9EDEF)
    val Muted = Color(0xFF8696A0)
    val Online = Color(0xFF4CAF50)
    val Warning = Color(0xFFFFA726)
    val Danger = Color(0xFFEF5350)
    val Divider = Color(0xFF23323B)
}

private val DarkScheme = darkColorScheme(
    primary = NovaColors.Primary,
    onPrimary = Color.White,
    primaryContainer = NovaColors.PrimaryDark,
    onPrimaryContainer = Color.White,
    background = NovaColors.Background,
    onBackground = NovaColors.OnSurface,
    surface = NovaColors.Surface,
    onSurface = NovaColors.OnSurface,
    surfaceVariant = NovaColors.SurfaceAlt,
    onSurfaceVariant = NovaColors.Muted,
    outline = NovaColors.Divider,
    error = NovaColors.Danger,
)

@Composable
fun NovaTheme(content: @Composable () -> Unit) {
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = NovaColors.Background.toArgb()
            window.navigationBarColor = NovaColors.Background.toArgb()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                window.isNavigationBarContrastEnforced = false
            }
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = false
        }
    }
    MaterialTheme(colorScheme = DarkScheme, content = content)
}
