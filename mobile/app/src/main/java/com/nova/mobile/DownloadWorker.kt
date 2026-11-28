package com.nova.mobile

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.BufferedInputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Download -> save to gallery -> ACK. Reports progress %, bytes and speed. */
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
        const val PROGRESS = "progress"
        const val BYTES = "bytes"
        const val TOTAL = "total"
        const val SPEED = "speed" // KB/s as string
    }

    override suspend fun doWork(): Result {
        val id = inputData.getInt(KEY_ID, 0)
        val url = inputData.getString(KEY_URL) ?: return Result.failure()
        val name = inputData.getString(KEY_NAME) ?: "video.mp4"
        val title = inputData.getString(KEY_TITLE) ?: name
        val auto = inputData.getBoolean(KEY_AUTO, true)

        return withContext(Dispatchers.IO) {
            try {
                val cache = File(applicationContext.cacheDir, "dl_${id}.mp4")
                val shownAt = System.currentTimeMillis()
                download(url, cache, id)
                // Keep the progress UI visible briefly even on very fast links.
                val remain = 700 - (System.currentTimeMillis() - shownAt)
                if (remain > 0) kotlinx.coroutines.delay(remain)
                if (auto) {
                    Gallery.save(applicationContext, name, cache.readBytes())
                    Prefs.markDownloaded(applicationContext, id)
                    Notifications.show(applicationContext, id, "Downloaded ✓", "$title · saved to gallery")
                }
                cache.delete()
                Api.token = Prefs.token(applicationContext)
                Api.ack(id)
                Result.success(workDataOf(KEY_ID to id, PROGRESS to 100))
            } catch (e: Exception) {
                val msg = e.message ?: "Download failed"
                if (runAttemptCount < 5) Result.retry()
                else Result.failure(workDataOf(KEY_ID to id, "error" to msg.take(160)))
            }
        }
    }

    private suspend fun download(url: String, dest: File, id: Int) {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 30_000
        conn.readTimeout = 60_000
        conn.instanceFollowRedirects = true
        conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android) NOVA/1.1")
        try {
            if (conn.responseCode !in 200..299) throw IllegalStateException("HTTP ${conn.responseCode}")
            val total = conn.contentLengthLong
            var done = 0L
            var lastPct = -1
            val started = System.currentTimeMillis()
            BufferedInputStream(conn.inputStream).use { input ->
                dest.outputStream().use { output ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        output.write(buf, 0, n)
                        done += n
                        val elapsed = (System.currentTimeMillis() - started).coerceAtLeast(1)
                        val speed = done * 1000 / elapsed / 1024 // KB/s
                        val pct = if (total > 0) (done * 100 / total).toInt().coerceIn(0, 100) else -1
                        if (pct != lastPct || done % (2 shl 20) < (1 shl 16)) {
                            lastPct = pct
                            setProgressAsync(
                                workDataOf(
                                    PROGRESS to pct,
                                    BYTES to done,
                                    TOTAL to total,
                                    SPEED to "$speed KB/s",
                                ),
                            )
                        }
                    }
                }
            }
        } finally {
            conn.disconnect()
        }
    }
}
