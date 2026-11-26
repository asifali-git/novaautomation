package com.nova.mobile

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

/** Tiny HTTP client — HttpURLConnection only, no OkHttp/Retrofit. */
object Api {
    // Set this to your Cloudflare Worker URL (wrangler.toml PUBLIC_BASE_URL).
    var baseUrl: String = "https://novaautomation.asifaliasmr05.workers.dev"
    var token: String = ""

    sealed class Result<out T> {
        data class Ok<T>(val value: T) : Result<T>()
        data class Err(val message: String) : Result<Nothing>()
    }

    private suspend fun request(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        auth: Boolean = true,
    ): JSONObject = withContext(Dispatchers.IO) {
        val conn = URL(baseUrl + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 20_000
        conn.readTimeout = 60_000
        conn.setRequestProperty("Accept", "application/json")
        if (auth && token.isNotBlank()) conn.setRequestProperty("Authorization", "Bearer $token")
        if (body != null) {
            conn.setRequestProperty("Content-Type", "application/json")
            conn.doOutput = true
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.use { BufferedReader(InputStreamReader(it, Charsets.UTF_8)).readText() } ?: ""
        conn.disconnect()
        if (code !in 200..299) {
            val msg = runCatching { JSONObject(text).optString("error") }.getOrNull().takeIf { !it.isNullOrBlank() }
                ?: "HTTP $code"
            throw IllegalStateException(msg)
        }
        if (text.isBlank()) JSONObject() else JSONObject(text)
    }

    suspend fun login(identity: String, password: String): Result<JSONObject> = try {
        val r = request("/api/auth/login", "POST", JSONObject().apply {
            put("identity", identity); put("password", password)
        }, auth = false)
        token = r.optString("token")
        Result.Ok(r)
    } catch (e: Exception) {
        Result.Err(e.message ?: "login failed")
    }

    suspend fun registerDevice(fcmToken: String, deviceId: String): Result<JSONObject> = try {
        Result.Ok(request("/api/devices/register", "POST", JSONObject().apply {
            put("token", fcmToken); put("device_id", deviceId)
        }))
    } catch (e: Exception) {
        Result.Err(e.message ?: "register failed")
    }

    suspend fun tree(): Result<JSONObject> = try {
        Result.Ok(request("/api/mobile/tree"))
    } catch (e: Exception) {
        Result.Err(e.message ?: "tree failed")
    }

    suspend fun deliveries(): Result<JSONObject> = try {
        Result.Ok(request("/api/deliveries"))
    } catch (e: Exception) {
        Result.Err(e.message ?: "deliveries failed")
    }

    suspend fun ack(deliveryId: Int): Result<JSONObject> = try {
        Result.Ok(request("/api/deliveries/$deliveryId/ack", "POST"))
    } catch (e: Exception) {
        Result.Err(e.message ?: "ack failed")
    }
}
