package com.nova.mobile

import android.annotation.SuppressLint
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.text.format.DateFormat
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.Date

@SuppressLint("SetTextI18n")
class MainActivity : Activity() {

    private val ui = CoroutineScope(Dispatchers.Main)
    private lateinit var root: LinearLayout
    private lateinit var scroll: ScrollView
    private var autoBox: CheckBox? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifications.ensure(this)
        askNotificationPermission()

        Api.token = Prefs.token(this)
        scroll = ScrollView(this)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 32, 28, 32)
            setBackgroundColor(Color.parseColor("#0E1117"))
        }
        scroll.addView(root)
        setContentView(scroll)

        if (Api.token.isBlank()) showLogin() else showHome()
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(android.Manifest.permission.POST_NOTIFICATIONS),
                101,
            )
        }
    }

    // ---------- UI helpers ----------

    private fun title(text: String, size: Float = 22f): TextView = TextView(this).apply {
        this.text = text
        textSize = size
        setTextColor(Color.WHITE)
        setTypeface(null, Typeface.BOLD)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = 20 }
    }

    private fun label(text: String, color: String = "#B9C2CF", size: Float = 14f): TextView =
        TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(Color.parseColor(color))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 6 }
        }

    private fun button(text: String, onClick: () -> Unit): Button = Button(this).apply {
        this.text = text
        setBackgroundColor(Color.parseColor("#2E7D32"))
        setTextColor(Color.WHITE)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 8; bottomMargin = 12 }
        setOnClickListener { onClick() }
    }

    private fun clear() = root.removeAllViews()

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    // ---------- Login ----------

    private fun showLogin() {
        clear()
        root.addView(title("NOVA Studio", 26f))
        root.addView(label("Same username & password as the website"))

        val identity = EditText(this).apply {
            hint = "Username or email"
            setTextColor(Color.WHITE)
            setHintTextColor(Color.parseColor("#667085"))
        }
        val password = EditText(this).apply {
            hint = "Password"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
            setTextColor(Color.WHITE)
            setHintTextColor(Color.parseColor("#667085"))
        }
        root.addView(identity)
        root.addView(password)
        root.addView(button("Login") {
            val i = identity.text.toString().trim()
            val p = password.text.toString()
            if (i.isBlank() || p.isBlank()) { toast("Enter both fields"); return@button }
            ui.launch {
                when (val r = Api.login(i, p)) {
                    is Api.Result.Ok -> {
                        val username = r.value.optJSONObject("user")?.optString("username") ?: i
                        Prefs.saveLogin(this@MainActivity, Api.token, username)
                        FirebaseMessaging.getInstance().token.addOnSuccessListener { tok ->
                            Api.token = Prefs.token(this@MainActivity)
                            ui.launch { Api.registerDevice(tok, Prefs.deviceId(this@MainActivity)) }
                        }
                        showHome()
                        syncDeliveries()
                    }
                    is Api.Result.Err -> toast(r.message)
                }
            }
        })
    }

    // ---------- Home / platforms ----------

    private fun showHome() {
        clear()
        root.addView(title("Your platforms"))
        root.addView(label("Logged in as ${Prefs.username(this)}"))

        autoBox = CheckBox(this).apply {
            text = "Auto-download + save to gallery"
            isChecked = Prefs.autoDownload(this@MainActivity)
            setTextColor(Color.WHITE)
            setOnCheckedChangeListener { _, v -> Prefs.setAutoDownload(this@MainActivity, v) }
        }
        root.addView(autoBox!!)

        root.addView(button("Sync pending videos") { syncDeliveries() })
        root.addView(button("Logout") {
            Prefs.clear(this); Api.token = ""; showLogin()
        })

        ui.launch {
            root.addView(label("Loading…"))
            when (val r = Api.tree()) {
                is Api.Result.Ok -> {
                    val arr = r.value.optJSONArray("platforms")
                    clear()
                    showHomeHeader()
                    if (arr == null || arr.length() == 0) {
                        root.addView(label("No platforms yet. Add a job on the website first."))
                    } else {
                        for (i in 0 until arr.length()) {
                            val p = Platform.from(arr.getJSONObject(i))
                            root.addView(button("${p.name}  (${p.totalLinks} links)") { showTasks(p) })
                        }
                    }
                }
                is Api.Result.Err -> { clear(); showHomeHeader(); root.addView(label(r.message, "#EF5350")) }
            }
        }
    }

    private fun showHomeHeader() {
        root.addView(title("Your platforms"))
        root.addView(label("Logged in as ${Prefs.username(this)}"))
        autoBox = CheckBox(this).apply {
            text = "Auto-download + save to gallery"
            isChecked = Prefs.autoDownload(this@MainActivity)
            setTextColor(Color.WHITE)
            setOnCheckedChangeListener { _, v -> Prefs.setAutoDownload(this@MainActivity, v) }
        }
        root.addView(autoBox!!)
        root.addView(button("Sync pending videos") { syncDeliveries() })
        root.addView(button("Logout") { Prefs.clear(this); Api.token = ""; showLogin() })
    }

    private fun showTasks(platform: Platform) {
        clear()
        val back = Button(this).apply { text = "← Back" }
        back.setOnClickListener { showHome() }
        root.addView(back)
        root.addView(title("${platform.name} · links"))
        if (platform.tasks.isEmpty()) root.addView(label("No links on this platform."))
        for (task in platform.tasks) {
            root.addView(button("${task.name}  (${task.links.size})") { showLinks(task) })
        }
    }

    private fun showLinks(task: Task) {
        clear()
        val back = Button(this).apply { text = "← Back" }
        back.setOnClickListener { showHome() }
        root.addView(back)
        root.addView(title(task.name, 20f))
        root.addView(label(task.sourceUrl))
        if (task.links.isEmpty()) root.addView(label("No videos yet."))
        for (v in task.links) {
            root.addView(button("${if (v.status == "uploaded") "✓" else "•"} ${v.title.ifBlank { v.key }}") {
                showTimeline(v)
            })
        }
    }

    /** Full per-link timeline: detected → download → upload, with dates. */
    private fun showTimeline(v: Video) {
        clear()
        val back = Button(this).apply { text = "← Back" }
        back.setOnClickListener { showHome() }
        root.addView(back)
        root.addView(title(v.title.ifBlank { v.key }, 20f))
        root.addView(label("Status: ${v.status.uppercase()}", if (v.status == "uploaded") "#66BB6A" else "#FFA726"))
        v.error?.let { root.addView(label("Error: $it", "#EF5350")) }

        val rows = listOf(
            "Detected" to v.detected,
            "Dispatched" to v.dispatched,
            "Downloaded" to v.downloaded,
            "Edited" to v.edited,
            "Uploaded to server" to v.uploaded,
            "Failed" to v.failed,
            "Cancelled" to v.cancelled,
        )
        for ((name, ms) in rows) {
            val line = if (ms == null) "$name: —" else "$name: ${fmt(ms)}"
            root.addView(label(line, if (ms == null) "#667085" else "#E8EEF7"))
        }
        root.addView(label("Source: ${v.url}"))
    }

    private fun fmt(ms: Long): String {
        val d = Date(ms)
        return "${DateFormat.getDateFormat(this).format(d)} " +
            "${DateFormat.getTimeFormat(this).format(d)} " +
            DateFormat.format("MMM yyyy", d)
    }

    // ---------- Pending deliveries (offline catch-up) ----------

    private fun syncDeliveries() {
        ui.launch {
            when (val r = Api.deliveries()) {
                is Api.Result.Ok -> {
                    val arr = r.value.optJSONArray("deliveries") ?: return@launch
                    val items = (0 until arr.length()).map { Delivery.from(arr.getJSONObject(it)) }
                    DownloadQueue.syncPending(this@MainActivity, items, Prefs.autoDownload(this@MainActivity))
                    toast("${items.size} pending · downloading")
                }
                is Api.Result.Err -> toast(r.message)
            }
        }
    }
}
