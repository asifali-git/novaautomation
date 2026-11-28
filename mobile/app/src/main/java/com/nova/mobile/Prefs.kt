package com.nova.mobile

import android.content.Context
import android.content.SharedPreferences

/** Token + settings. Same login credentials as the website. */
object Prefs {
    private const val NAME = "nova"
    private const val TOKEN = "token"
    private const val USERNAME = "username"
    private const val DEVICE_ID = "device_id"
    private const val AUTO = "auto_download"

    private fun sp(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun token(ctx: Context): String = sp(ctx).getString(TOKEN, "") ?: ""
    fun username(ctx: Context): String = sp(ctx).getString(USERNAME, "") ?: ""

    fun saveLogin(ctx: Context, token: String, username: String) {
        sp(ctx).edit().putString(TOKEN, token).putString(USERNAME, username).apply()
    }

    fun clear(ctx: Context) {
        sp(ctx).edit().remove(TOKEN).remove(USERNAME).apply()
    }

    fun deviceId(ctx: Context): String {
        var id = sp(ctx).getString(DEVICE_ID, "") ?: ""
        if (id.isEmpty()) {
            id = java.util.UUID.randomUUID().toString()
            sp(ctx).edit().putString(DEVICE_ID, id).apply()
        }
        return id
    }

    fun autoDownload(ctx: Context): Boolean = sp(ctx).getBoolean(AUTO, true)
    fun setAutoDownload(ctx: Context, value: Boolean) {
        sp(ctx).edit().putBoolean(AUTO, value).apply()
    }

    // Local record of files already saved to gallery (green tick in UI).
    private const val DONE = "downloaded_ids"
    fun markDownloaded(ctx: Context, id: Int) {
        val set = sp(ctx).getStringSet(DONE, emptySet())?.toMutableSet() ?: mutableSetOf()
        set.add(id.toString())
        sp(ctx).edit().putStringSet(DONE, set).apply()
    }

    fun isDownloaded(ctx: Context, id: Int): Boolean =
        sp(ctx).getStringSet(DONE, emptySet())?.contains(id.toString()) == true
}
