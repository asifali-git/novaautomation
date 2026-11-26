package com.nova.mobile

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.BufferedInputStream
import java.net.HttpURLConnection
import java.net.URL

/** Download from Catbox -> save to MediaStore (gallery) -> ACK worker. */
class DownloadWorker(
    ctx: Context,
    params: WorkerParameters,
) : CoroutineWorker(ctx, params) {

    companion object {
        const val KEY_ID = "delivery_id"
        const val KEY_URL = "catbox_url"
        const val KEY_NAME = "file_name"
        const val KEY_TITLE = "title"
        const val KEY_AUTO = "auto_save"
    }

    override suspend fun doWork(): Result {
        val id = inputData.getInt(KEY_ID, 0)
        val url = inputData.getString(KEY_URL) ?: return Result.failure()
        val name = inputData.getString(KEY_NAME) ?: "video.mp4"
        val title = inputData.getString(KEY_TITLE) ?: name
        val auto = inputData.getBoolean(KEY_AUTO, true)

        return withContext(Dispatchers.IO) {
            try {
                val bytes = download(url)
                if (auto) {
                    Gallery.save(applicationContext, name, bytes)
                    Notifications.show(applicationContext, id, "Saved to gallery", title)
                } else {
                    Notifications.show(applicationContext, id, "Ready to download", title)
                }
                Api.token = Prefs.token(applicationContext)
                Api.ack(id)
                Result.success(workDataOf(KEY_ID to id))
            } catch (e: Exception) {
                if (runAttemptCount < 5) Result.retry() else Result.failure()
            }
        }
    }

    private fun download(url: String): ByteArray {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 30_000
        conn.readTimeout = 60_000
        conn.instanceFollowRedirects = true
        try {
            if (conn.responseCode !in 200..299) throw IllegalStateException("HTTP ${conn.responseCode}")
            return BufferedInputStream(conn.inputStream).use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
    }
}
