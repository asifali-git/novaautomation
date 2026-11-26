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

/** Enqueue a download that survives app restarts and waits for internet. */
object DownloadQueue {
    private const val TAG = "nova_download"

    fun enqueue(ctx: Context, delivery: Delivery, autoSave: Boolean) {
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
            .addTag(TAG)
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            "nova_${delivery.id}",
            ExistingWorkPolicy.KEEP,
            req,
        )
    }

    fun syncPending(ctx: Context, deliveries: List<Delivery>, autoSave: Boolean) {
        deliveries.forEach { enqueue(ctx, it, autoSave) }
    }
}
