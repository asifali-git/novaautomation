package com.nova.mobile

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import androidx.core.app.NotificationCompat

/** Stops the repeating alarm when the user taps "Stop" on the notification. */
class AlarmStopReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val id = intent.getIntExtra("delivery_id", -1)
        if (id < 0) return
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        nm.cancel(id)
        ctx.sendBroadcast(Intent(Intent.ACTION_CLOSE_SYSTEM_DIALOGS))
    }
}
