package com.nova.mobile

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** FCM data message -> alarm + WorkManager download (works while phone was off). */
class NovaMessagingService : FirebaseMessagingService() {

    override fun onMessageReceived(msg: RemoteMessage) {
        super.onMessageReceived(msg)
        val data = msg.data
        if (data["type"] != "nova_video") {
            msg.notification?.let {
                Notifications.show(this, System.currentTimeMillis().toInt(), it.title ?: "NOVA", it.body ?: "")
            }
            return
        }

        val id = data["delivery_id"]?.toIntOrNull() ?: return
        val url = data["catbox_url"] ?: return
        val name = data["file_name"] ?: "video.mp4"
        val title = data["title"] ?: name
        val auto = Prefs.autoDownload(this)

        val delivery = Delivery(id, name, url, title, data["platform"] ?: "", 0)
        DownloadQueue.enqueue(this, delivery, auto)

        // Tap opens THIS file directly (MainActivity reads the delivery id).
        val open = android.app.PendingIntent.getActivity(
            this,
            id,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra("delivery_id", id)
                putExtra("catbox_url", url)
                putExtra("file_name", name)
                putExtra("title", title)
            },
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE,
        )

        // Alarm rings until the user taps Stop or opens the app.
        Notifications.showAlarm(this, id, "New video: $title", "Tap to open · Stop to silence", open)
    }

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Api.token = Prefs.token(this)
        if (Api.token.isBlank()) return
        CoroutineScope(Dispatchers.IO).launch {
            Api.registerDevice(token, Prefs.deviceId(this@NovaMessagingService))
        }
    }
}
