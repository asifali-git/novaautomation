package com.nova.mobile

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import java.util.concurrent.TimeUnit

/** WorkManager queue for offline-safe downloads (one unique work per delivery). */
object DownloadQueue {
    fun name(id: Int) = "nova_$id"

    fun enqueue(ctx: Context, delivery: Delivery, autoSave: Boolean): java.util.UUID {
        val input = workDataOf(
            DownloadWorker.KEY_ID to delivery.id,
            DownloadWorker.KEY_URL to delivery.catboxUrl,
            DownloadWorker.KEY_NAME to delivery.fileName,
            DownloadWorker.KEY_TITLE to delivery.title,
            DownloadWorker.KEY_AUTO to autoSave,
        )
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val req = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(input)
            .setConstraints(constraints)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .addTag("nova_download")
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            name(delivery.id),
            ExistingWorkPolicy.KEEP,
            req,
        )
        return req.id
    }

    fun cancel(ctx: Context, id: Int) {
        WorkManager.getInstance(ctx).cancelUniqueWork(name(id))
    }

    fun syncPending(ctx: Context, deliveries: List<Delivery>, autoSave: Boolean) {
        deliveries.forEach { if (!Prefs.isDownloaded(ctx, it.id)) enqueue(ctx, it, autoSave) }
    }
}
