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

    /** Repeating alarm until the user stops it or opens the app. */
    fun showAlarm(
        ctx: Context,
        id: Int,
        title: String,
        body: String,
        openIntent: PendingIntent,
    ) {
        ensure(ctx)
        val stop = PendingIntent.getBroadcast(
            ctx,
            id + 900_000,
            Intent(ctx, AlarmStopReceiver::class.java).apply { putExtra("delivery_id", id) },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_warning)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setOngoing(true)                 // stays until user stops / app opens
            .setAutoCancel(false)
            .setContentIntent(openIntent)      // tap -> specific file
            .addAction(android.R.drawable.ic_delete, "Stop", stop)
            .setVibrate(longArrayOf(0, 500, 300, 500, 300, 1000))
            .build()
        try {
            androidx.core.app.NotificationManagerCompat.from(ctx).notify(id, n)
        } catch (_: SecurityException) {
        }
    }

    /** Cancel the alarm once the app is opened. */
    fun stop(ctx: Context, id: Int) {
        try {
            androidx.core.app.NotificationManagerCompat.from(ctx).cancel(id)
        } catch (_: Exception) {
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
