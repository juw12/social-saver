package com.juw12.socialsaver

import android.app.Activity
import android.content.ContentValues
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.text.Html
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.net.URLEncoder
import kotlin.concurrent.thread

/**
 * Starts the Python app (app.py) inside this process and shows its screen.
 * Python downloads straight from this phone's internet connection, so no PC or server is needed.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private var pendingShare: String? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (request.url.host == "127.0.0.1") {
                    return false
                }
                startActivity(Intent(Intent.ACTION_VIEW, request.url)) // other links open in the browser
                return true
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(message: ConsoleMessage): Boolean {
                Log.i(TAG, "console: ${message.message()} (line ${message.lineNumber()})")
                return true
            }

            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    startActivityForResult(params.createIntent(), REQUEST_FILE)
                    true
                } catch (error: Exception) {
                    fileCallback = null
                    false
                }
            }
        }
        webView.addJavascriptInterface(Bridge(), "SocialSaverAndroid")
        webView.setDownloadListener { url, _, _, _, _ -> saveZip(url) }
        webView.loadData(LOADING_PAGE, "text/html", "utf-8")

        pendingShare = sharedText(intent)
        val selfTestUrl = intent.getStringExtra("selftest_url")
        thread(name = "social-saver-start") { startPython(selfTestUrl) }
    }

    private fun startPython(selfTestUrl: String?) {
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
            }
            val python = Python.getInstance()
            val environ = python.getModule("os").get("environ")!!
            environ.callAttr("__setitem__", "SOCIAL_SAVER_HOME", File(filesDir, "home").path)
            environ.callAttr("__setitem__", "SOCIAL_SAVER_OUTPUT_DIR", zipFolder().path)
            environ.callAttr("__setitem__", "SOCIAL_SAVER_WEB_DIR", unpackScreen().path)
            environ.callAttr("__setitem__", "SOCIAL_SAVER_IN_PROCESS", "1")
            val app = python.getModule("app")
            val url = serverUrl ?: app.callAttr("start_embedded").toString().also { serverUrl = it }
            Log.i(TAG, "SOCIAL_SAVER_READY $url")
            runOnUiThread {
                webView.loadUrl(withShare(url, pendingShare))
                pendingShare = null
            }
            if (selfTestUrl != null) {
                val count = app.callAttr("self_test", selfTestUrl).toInt()
                Log.i(TAG, if (count > 0) "SELFTEST OK $count" else "SELFTEST FAIL")
            }
        } catch (error: Throwable) {
            Log.e(TAG, "SOCIAL_SAVER_FAILED", error)
            val message = Html.escapeHtml(error.toString())
            runOnUiThread {
                webView.loadData(
                    "<p style='font-family:sans-serif;padding:24px'>Social Saver could not start.<br><br>$message</p>",
                    "text/html",
                    "utf-8",
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val text = sharedText(intent) ?: return
        val url = serverUrl
        if (url == null) {
            pendingShare = text
        } else {
            webView.loadUrl(withShare(url, text))
        }
    }

    @Deprecated("Activity result API is not needed for two simple requests")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQUEST_FILE -> {
                fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
                fileCallback = null
            }
            REQUEST_SIGN_IN -> {
                if (resultCode != RESULT_OK || data == null) {
                    return
                }
                val platform = data.getStringExtra("platform") ?: return
                val cookies = data.getStringExtra("cookies") ?: return
                webView.evaluateJavascript(
                    "window.socialSaverCookiesFromApp(${JSONObject.quote(platform)}, ${JSONObject.quote(cookies)})",
                    null,
                )
            }
        }
    }

    @Deprecated("Back handling for a single WebView")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    /** Copies a finished zip from the app's own folder into Download/SocialSaver. */
    private fun saveZip(url: String) {
        val name = Uri.parse(url).lastPathSegment ?: return
        val source = File(zipFolder(), name)
        if (!source.isFile) {
            toast("That zip is not there any more.")
            return
        }
        thread(name = "social-saver-save") {
            try {
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, name)
                    put(MediaStore.Downloads.MIME_TYPE, "application/zip")
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/SocialSaver")
                }
                val target = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw IllegalStateException("no room in Downloads")
                contentResolver.openOutputStream(target)!!.use { output ->
                    source.inputStream().use { input -> input.copyTo(output) }
                }
                runOnUiThread { toast("Saved to Download/SocialSaver/$name") }
            } catch (error: Exception) {
                runOnUiThread { toast("Could not save the zip: ${error.message}") }
            }
        }
    }

    private fun zipFolder(): File = File(getExternalFilesDir(null) ?: filesDir, "zips").apply { mkdirs() }

    /** The screen (ui.html + icons) ships as assets; Python reads it from a normal folder. */
    private fun unpackScreen(): File {
        val target = File(filesDir, "web")
        copyAssets("web", target)
        return target
    }

    private fun copyAssets(path: String, target: File) {
        val children = assets.list(path) ?: emptyArray()
        if (children.isEmpty()) {
            target.parentFile?.mkdirs()
            assets.open(path).use { input -> target.outputStream().use { output -> input.copyTo(output) } }
            return
        }
        target.mkdirs()
        for (child in children) {
            copyAssets("$path/$child", File(target, child))
        }
    }

    private fun sharedText(intent: Intent?): String? =
        if (intent?.action == Intent.ACTION_SEND) intent.getStringExtra(Intent.EXTRA_TEXT) else null

    private fun withShare(url: String, text: String?): String =
        if (text.isNullOrBlank()) url else url + "&url=" + URLEncoder.encode(text, "UTF-8")

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    /** Called from the screen: open the platform's real login page. */
    inner class Bridge {
        @JavascriptInterface
        fun signIn(platform: String, loginUrl: String, authCookie: String, domain: String) {
            val intent = Intent(this@MainActivity, SignInActivity::class.java)
                .putExtra("platform", platform)
                .putExtra("login_url", loginUrl)
                .putExtra("auth_cookie", authCookie)
                .putExtra("domain", domain)
            runOnUiThread { startActivityForResult(intent, REQUEST_SIGN_IN) }
        }
    }

    companion object {
        const val TAG = "SocialSaver"
        private const val REQUEST_FILE = 1
        private const val REQUEST_SIGN_IN = 2
        private var serverUrl: String? = null // the Python server keeps running while the app process lives
        private const val LOADING_PAGE =
            "<body style='font-family:sans-serif;padding:32px;color:#555'>Starting Social Saver...</body>"
    }
}
