package com.xiaoman.treehole

import android.annotation.SuppressLint
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.JavascriptInterface
import androidx.appcompat.app.AppCompatActivity

/**
 * MainActivity · 小满树洞 WebView 壳
 * - 加载 assets/www/index.html（Live2D + 活人感聊天 + 离线引擎）
 * - JSBridge "AndroidTTS"：Web 端 TTS 降级桥（部分 WebView 无 speechSynthesis 时启用）
 * - WebGL：Live2D 渲染需要，硬件加速开启（manifest 已配置）
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 系统 TTS 初始化
        tts = TextToSpeech(this) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) tts?.language = java.util.Locale.SIMPLIFIED_CHINESE
        }
        tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) {
                runOnUiThread { webView.evaluateJavascript("window.__ttsEnded && window.__ttsEnded()", null) }
            }
            @Deprecated("Deprecated in Java")
            override fun onError(id: String?) {
                runOnUiThread { webView.evaluateJavascript("window.__ttsEnded && window.__ttsEnded()", null) }
            }
        })

        webView = WebView(this)
        setContentView(webView)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true            // localStorage（记忆/配置）
            mediaPlaybackRequiresUserGesture = false  // 自动播语音
            allowFileAccess = true
            cacheMode = WebView.LOAD_DEFAULT
        }
        webView.webViewClient = WebViewClient()
        webView.addJavascriptInterface(Bridge(), "AndroidTTS")

        WebView.setWebContentsDebuggingEnabled(true)
        webView.loadUrl("file:///android_asset/www/index.html")
    }

    /** JS 桥：系统 TTS 合成 + 结束回调 */
    inner class Bridge {
        @JavascriptInterface
        fun speak(text: String): Boolean {
            if (!ttsReady) return false
            tts?.setPitch(1.15f)
            tts?.setSpeechRate(1.02f)
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle.EMPTY, "xiaoman_${System.currentTimeMillis()}")
            return true
        }

        @JavascriptInterface
        fun stop() { tts?.stop() }

        @JavascriptInterface
        fun available(): Boolean = ttsReady
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onDestroy() {
        tts?.shutdown()
        webView.destroy()
        super.onDestroy()
    }
}
