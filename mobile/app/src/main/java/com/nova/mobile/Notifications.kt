package com.nova.mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat

/** Alarm-style channel: sound + vibration + heads-up popover. */
object Notifications {
    const val CHANNEL = "nova_video_alarm"

    fun ensure(ctx: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = ctx.getSystemService(NotificationManager::class.java) ?: return
            if (mgr.getNotificationChannel(CHANNEL) != null) return
            val ch = NotificationChannel(
                CHANNEL,
                "NOVA video delivery",
                NotificationManager.IMPORTANCE_HIGH, // popover + sound + vibration
            ).apply {
                description = "Alarm when a new video arrives"
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 400, 200, 400, 200, 800)
                setSound(
                    android.media.RingtoneManager.getDefaultUri(
                        android.media.RingtoneManager.TYPE_ALARM,
                    ),
                    null,
                )
                lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
            }
            mgr.createNotificationChannel(ch)
        }
    }

    fun show(ctx: Context, id: Int, title: String, body: String, pending: PendingIntent? = null) {
        ensure(ctx)
        val intent = pending ?: PendingIntent.getActivity(
            ctx,
            id,
            Intent(ctx, MainActivity::class.java).apply { flags = Intent.FLAG_ACTIVITY_SINGLE_TOP },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setAutoCancel(true)
            .setContentIntent(intent)
            .setVibrate(longArrayOf(0, 400, 200, 400, 200, 800))
            .build()
        try {
            androidx.core.app.NotificationManagerCompat.from(ctx).notify(id, n)
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS denied on Android 13+ — nothing else to do.
        }
    }
}
