package com.juw12.socialsaver

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONArray
import org.json.JSONObject

/**
 * Opens the platform's own login page. You type your password there; once the platform sets
 * its login cookie, the cookies are handed back to the app (they never leave this phone).
 */
class SignInActivity : Activity() {

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var platform: String
    private lateinit var authCookie: String
    private lateinit var domain: String
    private var finishing = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        platform = intent.getStringExtra("platform") ?: return finish()
        authCookie = intent.getStringExtra("auth_cookie") ?: return finish()
        domain = intent.getStringExtra("domain") ?: return finish()
        val loginUrl = intent.getStringExtra("login_url") ?: return finish()

        val webView = WebView(this)
        setContentView(webView)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = WebViewClient()
        val cookies = CookieManager.getInstance()
        cookies.setAcceptCookie(true)
        cookies.setAcceptThirdPartyCookies(webView, true)
        forgetOldSession(cookies)
        webView.loadUrl(loginUrl)
        handler.postDelayed(checkSignedIn, CHECK_EVERY_MS)
    }

    private val checkSignedIn = object : Runnable {
        override fun run() {
            if (cookiePairs()[authCookie].isNullOrEmpty()) {
                handler.postDelayed(this, CHECK_EVERY_MS)
                return
            }
            if (!finishing) {
                finishing = true
                handler.postDelayed({ finishWithCookies() }, SETTLE_MS) // let the site finish setting cookies
            }
        }
    }

    private fun cookiePairs(): Map<String, String> {
        val header = CookieManager.getInstance().getCookie("https://www.$domain") ?: return emptyMap()
        return header.split(";").mapNotNull { part ->
            val pieces = part.trim().split("=", limit = 2)
            if (pieces.size == 2 && pieces[0].isNotEmpty()) pieces[0] to pieces[1] else null
        }.toMap()
    }

    /** Cookie-Editor style JSON, which the app's "Import cookies file" already understands. */
    private fun finishWithCookies() {
        val exported = JSONArray()
        for ((name, value) in cookiePairs()) {
            exported.put(
                JSONObject()
                    .put("name", name)
                    .put("value", value)
                    .put("domain", ".$domain")
                    .put("path", "/")
                    .put("secure", true),
            )
        }
        setResult(
            RESULT_OK,
            Intent().putExtra("platform", platform).putExtra("cookies", exported.toString()),
        )
        finish()
    }

    /** Start from a clean login page, even if this platform was signed in before. */
    private fun forgetOldSession(cookies: CookieManager) {
        for (name in cookiePairs().keys) {
            cookies.setCookie("https://www.$domain", "$name=; Max-Age=0; Domain=.$domain; Path=/")
            cookies.setCookie("https://www.$domain", "$name=; Max-Age=0; Path=/")
        }
        cookies.flush()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    companion object {
        private const val CHECK_EVERY_MS = 1500L
        private const val SETTLE_MS = 2500L
    }
}
