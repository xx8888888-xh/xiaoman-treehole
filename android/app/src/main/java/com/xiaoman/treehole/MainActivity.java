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
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.InputStream;
import java.util.HashMap;
import java.util.Locale;

/**
 * MainActivity · 小满树洞 WebView 壳（纯 Java，零依赖，便于手工构建）
 * - 加载 assets/www/index.html（Live2D + 活人感聊天 + 离线引擎）
 * - JSBridge "AndroidTTS"：系统 TTS 桥（Web 端 speechSynthesis 不可用时兜底）
 * - TTS 完成回调：UtteranceProgressListener → evaluateJavascript("window.__ttsEnded && window.__ttsEnded()")
 * - WebView 远程调试仅在 debug 包开启
 */
public class MainActivity extends Activity {

    /** WebViewAssetLoader 的零依赖等价实现：把 https://appassets… 映射到 assets/。
     *  为什么不用 file://：file:// 页面的 Origin 是 "null"，模型 JSON / 贴图 XHR 会被
     *  CORS 拦（Live2D 直接不渲染），且 http 子资源会被 mixed-content 拦。 */
    private static final String ASSET_HOST = "appassets.androidplatform.net";
    private static final String BASE_URL = "https://" + ASSET_HOST + "/www/index.html";

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
        // 页面是 https://appassets…，而本地 TTS/代理是 http://127.0.0.1：
        // 不放开 mixed-content 的话音频会被静默拦下（"有字幕没声音"）
        if (Build.VERSION.SDK_INT >= 21) {
            ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        webView.setWebViewClient(new AssetClient());
        webView.setWebChromeClient(newConsoleClient());
        webView.addJavascriptInterface(new Bridge(), "AndroidTTS");
        webView.addJavascriptInterface(new NetBridge(webView), "AndroidNet");

        boolean isDebuggable = (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
        if (isDebuggable) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(BASE_URL);
        }
        handleFloatIntent(getIntent());
    }

    /** WebView console → logcat 桥：[probe] 前缀行转发到 tag=XiaoManWeb（CI 验收与真机联调证据通道） */
    private WebChromeClient newConsoleClient() {
        return new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage cm) {
                String m = cm.message();
                if (m != null && m.contains("[probe]")) {
                    Log.d("XiaoManWeb", m.length() > 300 ? m.substring(0, 300) : m);
                }
                return false;
            }
        };
    }

    /**
     * 悬浮窗意图通道（CI 自动化与"打开 App 即挂球"共用）：
     * --ez autostart_float true → 等价设置面板开启悬浮窗（canDrawOverlays 已授权才挂）
     * --ez float_stop true       → 等价 floatHide（收球+停服务）
     * FloatWindowService 为 exported=false（安全设计），外部无法直启/直停，只能经本壳中转。
     */
    private void handleFloatIntent(Intent i) {
        if (i == null) return;
        if (i.getBooleanExtra("autostart_float", false)) {
            runOnUiThread(() -> {
                if (!Settings.canDrawOverlays(getApplicationContext())) {
                    Log.w("MainActivity", "autostart_float: overlay permission not granted, skip");
                    return;
                }
                Intent s = new Intent(MainActivity.this, FloatWindowService.class)
                        .setAction(FloatWindowService.ACTION_SHOW);
                if (Build.VERSION.SDK_INT >= 26) startForegroundService(s);
                else startService(s);
            });
        } else if (i.getBooleanExtra("float_stop", false)) {
            runOnUiThread(() -> startService(new Intent(MainActivity.this, FloatWindowService.class)
                    .setAction(FloatWindowService.ACTION_HIDE)));
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleFloatIntent(intent);
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
            if (Build.VERSION.SDK_INT >= 21) {
                ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
            }
            webView.setWebViewClient(new AssetClient());
            webView.setWebChromeClient(newConsoleClient());
            webView.addJavascriptInterface(new Bridge(), "AndroidTTS");
            webView.addJavascriptInterface(new NetBridge(webView), "AndroidNet");
            boolean isDebuggable = (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
            if (isDebuggable) {
                WebView.setWebContentsDebuggingEnabled(true);
            }
            webView.loadUrl(BASE_URL);
        });
    }

    @Override
    public void onConfigurationChanged(android.content.res.Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        // 活动声明了 uiMode，系统深浅色切换不会重建页面；显式让前端重算外观
        if (webView != null) {
            webView.evaluateJavascript("window.__retheme && window.__retheme()", null);
        }
    }

    /**
     * 资源加载器：把 https://appassets.androidplatform.net/www/… 映射到 assets/www/…
     * 纯 Java 实现（不引入 androidx.webkit），保持"零依赖、可手工构建"。
     * 好处：页面是正常 https 同源，Live2D 的 model3.json / 贴图 XHR 不再被 CORS 拦。
     */
    class AssetClient extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
            Uri uri = req.getUrl();
            if (uri == null || !ASSET_HOST.equals(uri.getHost())) return null;
            String path = uri.getPath();
            if (path == null || !path.startsWith("/www/")) return null;
            String assetPath = path.substring(1);   // 去掉前导 / → "www/index.html"
            // 目录穿越保护
            if (assetPath.contains("..")) return null;
            try {
                InputStream in = getAssets().open(assetPath);
                return new WebResourceResponse(mimeOf(assetPath), charsetOf(assetPath), in);
            } catch (Exception e) {
                Log.w("MainActivity", "asset miss: " + assetPath);
                return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found",
                        new HashMap<String, String>(), null);
            }
        }

        @Override
        public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
            Log.e("MainActivity", "WebView render process gone: " + detail);
            recreateWebView();
            return true;
        }
    }

    private static String mimeOf(String p) {
        String s = p.toLowerCase(Locale.ROOT);
        if (s.endsWith(".html")) return "text/html";
        if (s.endsWith(".js")) return "text/javascript";
        if (s.endsWith(".css")) return "text/css";
        if (s.endsWith(".json")) return "application/json";
        if (s.endsWith(".png")) return "image/png";
        if (s.endsWith(".jpg") || s.endsWith(".jpeg")) return "image/jpeg";
        if (s.endsWith(".svg")) return "image/svg+xml";
        if (s.endsWith(".mp3")) return "audio/mpeg";
        if (s.endsWith(".wasm")) return "application/wasm";
        return "application/octet-stream";
    }

    private static String charsetOf(String p) {
        String s = p.toLowerCase(Locale.ROOT);
        return (s.endsWith(".html") || s.endsWith(".js") || s.endsWith(".css")
                || s.endsWith(".json")) ? "utf-8" : null;
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
