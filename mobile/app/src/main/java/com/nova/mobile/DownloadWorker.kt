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

/** Download from the delivery URL -> save to gallery -> ACK worker.
 *  Reports 0-100 progress so the UI can show a real progress bar. */
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
        const val PROGRESS_BYTES = "progress_bytes"
        const val PROGRESS_TOTAL = "progress_total"
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
                download(url, cache)
                if (auto) {
                    Gallery.save(applicationContext, name, cache.readBytes())
                    Notifications.show(applicationContext, id, "Saved to gallery", title)
                } else {
                    Notifications.show(applicationContext, id, "Ready to download", title)
                }
                cache.delete()
                Api.token = Prefs.token(applicationContext)
                Api.ack(id)
                Result.success(workDataOf(KEY_ID to id, PROGRESS to 100))
            } catch (e: Exception) {
                if (runAttemptCount < 5) Result.retry() else Result.failure()
            }
        }
    }

    /** Streamed download with progress updates. */
    private suspend fun download(url: String, dest: File) {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 30_000
        conn.readTimeout = 60_000
        conn.instanceFollowRedirects = true
        conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android) NOVA/1.1")
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw IllegalStateException("HTTP $code")
            val total = conn.contentLengthLong
            var done = 0L
            var last = -1
            BufferedInputStream(conn.inputStream).use { input ->
                dest.outputStream().use { output ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        output.write(buf, 0, n)
                        done += n
                        if (total > 0) {
                            val pct = (done * 100 / total).toInt().coerceIn(0, 100)
                            if (pct != last) {
                                last = pct
                                setProgressAsync(
                                    workDataOf(
                                        PROGRESS to pct,
                                        PROGRESS_BYTES to done,
                                        PROGRESS_TOTAL to total,
                                    ),
                                )
                            }
                        }
                    }
                }
            }
        } finally {
            conn.disconnect()
        }
    }
}
