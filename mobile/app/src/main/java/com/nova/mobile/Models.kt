package com.nova.mobile

import org.json.JSONArray
import org.json.JSONObject

/** Minimal models — plain org.json, no serialization dependency. */
data class Video(
    val id: Int,
    val key: String,
    val url: String,
    val title: String,
    val status: String,
    val error: String?,
    val detected: Long?,
    val dispatched: Long?,
    val downloaded: Long?,
    val edited: Long?,
    val uploaded: Long?,
    val failed: Long?,
    val cancelled: Long?,
    val updated: Long?,
) {
    companion object {
        fun from(o: JSONObject) = Video(
            id = o.optInt("id"),
            key = o.optString("video_key"),
            url = o.optString("url"),
            title = o.optString("title"),
            status = o.optString("status"),
            error = o.optString("error").takeIf { it.isNotBlank() },
            detected = o.optLong("detected_at", 0).takeIf { it > 0 },
            dispatched = o.optLong("dispatched_at", 0).takeIf { it > 0 },
            downloaded = o.optLong("downloaded_at", 0).takeIf { it > 0 },
            edited = o.optLong("edited_at", 0).takeIf { it > 0 },
            uploaded = o.optLong("uploaded_at", 0).takeIf { it > 0 },
            failed = o.optLong("failed_at", 0).takeIf { it > 0 },
            cancelled = o.optLong("cancelled_at", 0).takeIf { it > 0 },
            updated = o.optLong("updated_at", 0).takeIf { it > 0 },
        )
    }
}

data class Task(
    val id: Int,
    val name: String,
    val sourceUrl: String,
    val platform: String,
    val status: String,
    val links: List<Video>,
    val sendToApp: Boolean = true,
    val appFile: String = "edited",
    val uploadSocial: Boolean = true,
) {
    companion object {
        fun from(o: JSONObject) = Task(
            id = o.optInt("id"),
            name = o.optString("name"),
            sourceUrl = o.optString("source_url"),
            platform = o.optString("platform", "unknown"),
            status = o.optString("status"),
            links = run {
                val arr = o.optJSONArray("links") ?: JSONArray()
                (0 until arr.length()).map { Video.from(arr.getJSONObject(it)) }
            },
            sendToApp = o.optJSONObject("delivery")?.optInt("send_to_app", 1) != 0,
            appFile = o.optJSONObject("delivery")?.optString("app_file", "edited") ?: "edited",
            uploadSocial = o.optJSONObject("delivery")?.optInt("upload_social", 1) != 0,
        )
    }
}

data class Platform(
    val name: String,
    val totalLinks: Int,
    val tasks: List<Task>,
) {
    companion object {
        fun from(o: JSONObject) = Platform(
            name = o.optString("platform", "unknown"),
            totalLinks = o.optInt("total_links"),
            tasks = run {
                val arr = o.optJSONArray("tasks") ?: JSONArray()
                (0 until arr.length()).map { Task.from(arr.getJSONObject(it)) }
            },
        )
    }
}

data class Delivery(
    val id: Int,
    val fileName: String,
    val catboxUrl: String,
    val title: String,
    val platform: String,
    val size: Long,
) {
    companion object {
        fun from(o: JSONObject) = Delivery(
            id = o.optInt("id"),
            fileName = o.optString("file_name"),
            catboxUrl = o.optString("catbox_url"),
            title = o.optString("title"),
            platform = o.optString("platform"),
            size = o.optLong("size_bytes"),
        )
    }
}
