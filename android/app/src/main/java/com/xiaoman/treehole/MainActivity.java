package com.xiaoman.treehole;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
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
            public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
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
                public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
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

        // ---- 悬浮窗（FloatWindowService）----

        /** 悬浮窗特殊权限是否已授予（Settings.canDrawOverlays，API 23+，minSdk 24 恒可用） */
        @JavascriptInterface
        public boolean floatGranted() {
            return Settings.canDrawOverlays(getApplicationContext());
        }

        /** 跳系统"显示在其他应用上层"授权页；授权后返回 App 再调 floatShow */
        @JavascriptInterface
        public void floatOpenSettings() {
            runOnUiThread(() -> {
                try {
                    Intent i = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            Uri.parse("package:" + getPackageName()));
                    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(i);
                } catch (Exception e) {
                    Log.w("MainActivity", "overlay settings page unavailable: " + e);
                }
            });
        }

        /** 启动悬浮窗服务（悬浮球）；未授权时返回 false 由 web 端引导授权 */
        @JavascriptInterface
        public boolean floatShow() {
            if (!Settings.canDrawOverlays(getApplicationContext())) return false;
            runOnUiThread(() -> {
                Intent i = new Intent(MainActivity.this, FloatWindowService.class)
                        .setAction(FloatWindowService.ACTION_SHOW);
                if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
                else startService(i);
            });
            return true;
        }

        /** 关闭悬浮窗（收起并停服务） */
        @JavascriptInterface
        public void floatHide() {
            runOnUiThread(() -> startService(new Intent(MainActivity.this, FloatWindowService.class)
                    .setAction(FloatWindowService.ACTION_HIDE)));
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
