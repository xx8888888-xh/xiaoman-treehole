package com.xiaoman.treehole;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.pm.ApplicationInfo;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.util.Locale;

/**
 * MainActivity · 小满树洞 WebView 壳（纯 Java，零依赖，便于手工构建）
 * - 加载 assets/www/index.html（Live2D + 活人感聊天 + 离线引擎）
 * - JSBridge "AndroidTTS"：系统 TTS 桥（Web 端 speechSynthesis 不可用时兜底）
 * - TTS 完成回调：UtteranceProgressListener → evaluateJavascript("window.__ttsEnded && window.__ttsEnded()")
 * - WebView 远程调试仅在 debug 包开启
 */
public class MainActivity extends Activity {

    private WebView webView;
    private TextToSpeech tts;
    private volatile boolean ttsReady = false;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        tts = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS) {
                int result = tts.setLanguage(Locale.SIMPLIFIED_CHINESE);
                ttsReady = (result == TextToSpeech.LANG_COUNTRY_AVAILABLE
                        || result == TextToSpeech.LANG_AVAILABLE);
                if (!ttsReady) {
                    Log.w("MainActivity", "TTS: SIMPLIFIED_CHINESE not supported, result=" + result);
                }
            } else {
                ttsReady = false;
                Log.e("MainActivity", "TTS init failed: status=" + status);
            }
        });

        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override
            public void onStart(String utteranceId) {}

            @Override
            public void onDone(String utteranceId) {
                if (webView != null) {
                    webView.post(() -> webView.evaluateJavascript(
                            "window.__ttsEnded && window.__ttsEnded()", null));
                }
            }

            @Override
            public void onError(String utteranceId) {
                if (webView != null) {
                    webView.post(() -> webView.evaluateJavascript(
                            "window.__ttsEnded && window.__ttsEnded()", null));
                }
            }
        });

        webView = new WebView(this);
        setContentView(webView);

        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setAllowFileAccess(true);
        ws.setAllowFileAccessFromFileURLs(false);
        ws.setAllowUniversalAccessFromFileURLs(false);
        ws.setAllowContentAccess(false);
        ws.setMediaPlaybackRequiresUserGesture(false);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setGeolocationEnabled(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean onRenderProcessGone(android.webkit.RenderProcessGoneDetail detail) {
                Log.e("MainActivity", "WebView render process gone: " + detail);
                recreateWebView();
                return true;
            }
        });
        webView.setWebChromeClient(new WebChromeClient());
        webView.addJavascriptInterface(new Bridge(), "AndroidTTS");

        boolean isDebuggable = (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
        if (isDebuggable) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl("file:///android_asset/www/index.html");
        }
    }

    // 返回键：优先回退 WebView 历史，否则交还系统。
    // 本项目为零依赖纯 Java 壳（手工 ecj 构建，不含 androidx），
    // 故不引入 OnBackPressedDispatcher，覆写 onBackPressed 保持可编译与行为等价。
    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    private void recreateWebView() {
        runOnUiThread(() -> {
            if (webView != null) {
                webView.destroy();
            }
            webView = new WebView(this);
            setContentView(webView);
            WebSettings ws = webView.getSettings();
            ws.setJavaScriptEnabled(true);
            ws.setDomStorageEnabled(true);
            ws.setAllowFileAccess(true);
            ws.setAllowFileAccessFromFileURLs(false);
            ws.setAllowUniversalAccessFromFileURLs(false);
            ws.setAllowContentAccess(false);
            ws.setMediaPlaybackRequiresUserGesture(false);
            ws.setCacheMode(WebSettings.LOAD_DEFAULT);
            ws.setGeolocationEnabled(false);
            webView.setWebViewClient(new WebViewClient() {
                @Override
                public boolean onRenderProcessGone(android.webkit.RenderProcessGoneDetail detail) {
                    Log.e("MainActivity", "WebView render process gone: " + detail);
                    recreateWebView();
                    return true;
                }
            });
            webView.setWebChromeClient(new WebChromeClient());
            webView.addJavascriptInterface(new Bridge(), "AndroidTTS");
            boolean isDebuggable = (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
            if (isDebuggable) {
                WebView.setWebContentsDebuggingEnabled(true);
            }
            webView.loadUrl("file:///android_asset/www/index.html");
        });
    }

    /** JS 桥：系统 TTS */
    class Bridge {
        @JavascriptInterface
        public boolean speak(String text) {
            if (!ttsReady || tts == null) return false;
            tts.setPitch(1.15f);
            tts.setSpeechRate(1.02f);
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, new Bundle(), "xiaoman_" + System.currentTimeMillis());
            return true;
        }

        @JavascriptInterface
        public void stop() {
            if (tts != null) tts.stop();
        }

        @JavascriptInterface
        public boolean available() {
            return ttsReady;
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (webView != null) {
            webView.saveState(outState);
        }
    }

    @Override
    protected void onDestroy() {
        if (tts != null) tts.shutdown();
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
