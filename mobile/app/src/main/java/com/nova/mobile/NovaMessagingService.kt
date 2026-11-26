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

        // Alarm fires even if the phone was off when the message was sent:
        // FCM delivers on reconnect, then WorkManager runs with network constraint.
        if (auto) {
            Notifications.show(this, id, "Video ready · saving", title)
        } else {
            Notifications.show(this, id, "New video ready", title)
        }
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
