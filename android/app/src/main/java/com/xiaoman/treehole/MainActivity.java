package com.xiaoman.treehole;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;

/**
 * MainActivity · 小满树洞 WebView 壳（纯 Java，零依赖，便于手工构建）
 * - 加载 assets/www/index.html（Live2D + 活人感聊天 + 离线引擎）
 * - JSBridge "AndroidTTS"：系统 TTS 桥（Web 端 speechSynthesis 不可用时兜底）
 * - JSBridge "AndroidHost"：提醒闹钟/通知权限/自动朗读开关/心跳间隔（后台能力）
 * - 生命周期真释放：onStop 冻结页面全部定时器（心跳、提醒轮询、打字机），
 *   onStart 按剩余时长重建；会话与历史由 web 侧持久化，不丢
 * - 语音默认"点击才朗读"：注入脚本 __xmHost 会在无手势时拦下 TTS.speak
 * - WebView 远程调试仅在 debug 包开启
 */
public class MainActivity extends Activity {

    /** WebViewAssetLoader 的零依赖等价实现：把 https://appassets… 映射到 assets/。
     *  为什么不用 file://：file:// 页面的 Origin 是 "null"，模型 JSON / 贴图 XHR 会被
     *  CORS 拦（Live2D 直接不渲染），且 http 子资源会被 mixed-content 拦。 */
    static final String ASSET_HOST = "appassets.androidplatform.net";
    /** 主界面与悬浮窗共用的同源入口（同一 Origin 才能共享 localStorage/IndexedDB） */
    static final String BASE_URL = "https://" + ASSET_HOST + "/www/index.html";
    /** 桌面 Live2D 宠物页（与主界面同源：共享 localStorage 里的提醒与外观设置） */
    static final String PET_URL = "https://" + ASSET_HOST + "/www/pet.html";

    /** 界面是否对用户可见（Activity 已 start，或悬浮迷你窗展开中）——闹钟据此决定是否弹通知 */
    private static volatile boolean activityVisible = false;

    private WebView webView;
    /** <input type="file"> 的系统选择器回调（备份导入） */
    private static final int REQ_FILE_CHOOSER = 41;
    private ValueCallback<Uri[]> filePathCallback;

    /** 有界面可见时不弹系统通知（避免与页面内投递重复）；锁屏/杀进程时为 false */
    static boolean isAnyUiVisible() {
        return activityVisible || FloatWindowService.isPanelVisible();
    }

    static boolean isForeground() {
        return activityVisible;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        setContentView(webView);
        configureWebView(webView);

        ReminderNotifications.ensureChannel(getApplicationContext());

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(BASE_URL);
        }
        handleFloatIntent(getIntent());
        handleReminderIntent(getIntent());
    }

    /** 主界面与重建路径共用的 WebView 装配（设置、客户端、桥、调试开关） */
    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView(WebView wv) {
        WebShell.configureSettings(wv, this);
        wv.setWebViewClient(new AssetClient());
        wv.setWebChromeClient(newConsoleClient());
        wv.addJavascriptInterface(new AndroidHostBridge(this, this), AndroidHostBridge.NAME);
        wv.addJavascriptInterface(new NetBridge(wv), "AndroidNet");
        // 语音桥：与两条浮窗 WebView 共用同一个注入入口与同一个引擎（见 TtsController）
        WebShell.attachAndroidTts(wv, new Bridge());
        try { wv.setBackgroundColor(getColor(R.color.window_bg)); } catch (Exception ignored) { }
        boolean isDebuggable = (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
        if (isDebuggable) {
            WebView.setWebContentsDebuggingEnabled(true);
        }
    }

    // ---------------- 生命周期：退后台真释放 ----------------

    @Override
    protected void onStart() {
        super.onStart();
        activityVisible = true;
        // 浮窗服务灭屏时会 pauseTimers（对进程内所有 WebView 生效）；主界面回到前台时兜底解除，
        // 避免服务被系统回收后页面定时器被遗留暂停
        try { if (webView != null) webView.resumeTimers(); } catch (Exception ignored) { }
        PageLifecycle.onHostResume(webView);   // 恢复页面定时器（按剩余时长重新武装）
    }

    @Override
    protected void onStop() {
        activityVisible = false;
        // 冻结页面：心跳、提醒轮询、打字机与 idle 问候全部停表；
        // 提醒转而由 AlarmManager + 通知投递，不会再"投进隐藏页并 markDone"
        PageLifecycle.onHostPause(webView);
        // 页面已冻结：到期但还没被页面消化的提醒，立刻交给系统闹钟补发（后台 1s 内弹通知）。
        // 不加这一步的话，"提醒到点时 App 正好在前台（页面自投递）"之后用户马上切后台，
        // 这条提醒就再没有通道了。
        ReminderAlarmScheduler.reschedule(getApplicationContext());
        super.onStop();
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

            /** 让 <input type="file"> 真的能打开系统文件选择器（备份导入用，无此实现点了没反应） */
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = callback;
                try {
                    Intent i = params.createIntent();
                    startActivityForResult(Intent.createChooser(i, "选择备份文件"), REQ_FILE_CHOOSER);
                    return true;
                } catch (Exception e) {
                    filePathCallback = null;
                    Log.w("MainActivity", "file chooser unavailable: " + e.getMessage());
                    return false;
                }
            }
        };
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE_CHOOSER) {
            if (filePathCallback != null) {
                filePathCallback.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                filePathCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
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

    /** 点击提醒通知进来：只做落日志与前台唤起；该提醒已在原生侧记为"已送达" */
    private void handleReminderIntent(Intent i) {
        if (i == null) return;
        String id = i.getStringExtra("reminder_id");
        if (id != null && !id.isEmpty()) {
            Log.d("XiaoManWeb", "[probe] open from reminder " + id);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleFloatIntent(intent);
        handleReminderIntent(intent);
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
            configureWebView(webView);
            webView.loadUrl(BASE_URL);
        });
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        // 活动声明了 orientation|screenSize|uiMode|fontScale 等，页面不重建：
        // 这里显式让前端重算外观，并把系统字号同步进 textZoom（最大 1.5× 不溢出）
        if (webView != null) {
            WebShell.applyFontScale(webView, this);
            webView.evaluateJavascript("window.__retheme && window.__retheme()", null);
            webView.requestLayout();
        }
        try { webView.setBackgroundColor(getColor(R.color.window_bg)); } catch (Exception ignored) { }
    }

    /**
     * 资源加载器：把 https://appassets.androidplatform.net/www/… 映射到 assets/www/…
     * 纯 Java 实现（不引入 androidx.webkit），保持"零依赖、可手工构建"。
     * 好处：页面是正常 https 同源，Live2D 的 model3.json / 贴图 XHR 不再被 CORS 拦。
     * 另外在此给 index.html 注入宿主脚本（退后台冻结/提醒镜像/点击朗读），
     * 不动 web/ 源文件。
     */
    class AssetClient extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
            return assetResponse(MainActivity.this, req.getUrl());
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            return guardNavigation(MainActivity.this, req.getUrl());
        }

        @Override
        public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
            Log.e("MainActivity", "WebView render process gone: " + detail);
            recreateWebView();
            return true;
        }
    }

    /** WebViewAssetLoader 等价实现的共用入口：主界面与悬浮窗都走它，
     *  保证两边加载同一份 assets/www 且同 Origin（localStorage/IndexedDB 不分裂）。 */
    static WebResourceResponse assetResponse(Context ctx, Uri uri) {
        if (ctx == null || uri == null || !ASSET_HOST.equals(uri.getHost())) return null;
        String path = uri.getPath();
        if (path == null || !path.startsWith("/www/")) return null;

        // 宿主注入脚本（不在 assets，来自 res/raw）
        if (WebShell.isBootstrapUrl(uri)) return WebShell.bootstrapResponse(ctx);

        String assetPath = path.substring(1);   // 去掉前导 / → "www/index.html"
        // 目录穿越保护
        if (assetPath.contains("..")) return null;
        try {
            InputStream in = ctx.getAssets().open(assetPath);
            if (assetPath.endsWith("index.html")) {
                WebResourceResponse injected = WebShell.injectIntoHtml(ctx, in);
                if (injected != null) return injected;
                in = ctx.getAssets().open(assetPath);   // 注入失败则退回原始 HTML
            }
            return new WebResourceResponse(mimeOf(assetPath), charsetOf(assetPath), in);
        } catch (Exception e) {
            Log.w("MainActivity", "asset miss: " + assetPath);
            return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found",
                    new HashMap<String, String>(), null);
        }
    }

    /**
     * 导航守卫（主界面与悬浮窗共用）：
     *  - https://appassets.androidplatform.net 留在当前 WebView；
     *  - 本机 127.0.0.1/localhost（显式端口）留在当前 WebView；
     *  - 其余 http(s) 一律交给系统浏览器；
     *  - file:/javascript:/intent:/content: 等一律丢弃，防止任意页面/脚本在本 WebView 打开。
     *  @return true 表示已处理（WebView 不应再加载该 URL）
     */
    static boolean guardNavigation(Context ctx, Uri uri) {
        if (uri == null) return true;
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri.getHost();
        if ("https".equals(scheme) && ASSET_HOST.equals(host)) return false;
        if ("about".equals(scheme)) return false;
        if (("http".equals(scheme) || "https".equals(scheme))
                && ("127.0.0.1".equals(host) || "localhost".equals(host)) && uri.getPort() > 0) {
            return false;
        }
        if ("http".equals(scheme) || "https".equals(scheme)) {
            try {
                Intent i = new Intent(Intent.ACTION_VIEW, uri);
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(i);
            } catch (Exception e) {
                Log.w("MainActivity", "external nav unavailable: " + e.getMessage());
            }
            return true;
        }
        Log.w("MainActivity", "blocked nav scheme: " + scheme);
        return true;
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

    // ---------------- 系统 TTS ----------------
    // 引擎、音频焦点、拔耳机停读都收在 TtsController（进程级单例），
    // 主界面与两条浮窗 WebView 共用；这里只保留宿主侧专属的桥方法。

    /** JS 桥：系统 TTS（继承自共享桥）+ 本地备份保存 + 悬浮窗控制 */
    class Bridge extends AndroidTtsBridge {
        Bridge() {
            super(MainActivity.this);
        }

        /** 备份写进 App 专属外部目录（无需存储权限）；成功返回绝对路径，失败返回空串 */
        @JavascriptInterface
        public String saveBackup(String name, String content) {
            try {
                File dir = getExternalFilesDir("backup");
                if (dir == null) return "";
                if (!dir.exists() && !dir.mkdirs()) return "";
                String safe = name == null ? "xiaoman-backup.json" : name.replaceAll("[^A-Za-z0-9._-]", "_");
                File f = new File(dir, safe);
                try (FileOutputStream out = new FileOutputStream(f)) {
                    out.write(content.getBytes(StandardCharsets.UTF_8));
                }
                return f.getAbsolutePath();
            } catch (Exception e) {
                Log.w("MainActivity", "saveBackup failed: " + e.getMessage());
                return "";
            }
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

        /** 启动悬浮窗服务（Live2D 桌宠）；未授权或系统禁止后台启动时返回 false */
        @JavascriptInterface
        public boolean floatShow() {
            if (!Settings.canDrawOverlays(getApplicationContext())) return false;
            try {
                Intent i = new Intent(MainActivity.this, FloatWindowService.class)
                        .setAction(FloatWindowService.ACTION_SHOW);
                if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
                else startService(i);
                return true;
            } catch (Exception e) {
                // Android 12+ 后台启动前台服务受限等：不崩溃，交给 web 提示
                Log.w("MainActivity", "floatShow rejected: " + e);
                return false;
            }
        }

        /** 关闭悬浮窗（收起并停服务） */
        @JavascriptInterface
        public void floatHide() {
            try {
                Intent i = new Intent(MainActivity.this, FloatWindowService.class)
                        .setAction(FloatWindowService.ACTION_HIDE);
                startService(i);
            } catch (Exception e) {
                Log.w("MainActivity", "floatHide failed: " + e);
            }
        }
    }

    /** 主 App 前后台信号：桌宠据此避让提醒，避免双弹；页面不可见 90s 后桌宠自动接手 */
    private void notifyAppVisibility(boolean visible) {
        if (webView == null) return;
        webView.post(() -> {
            if (webView == null) return;
            webView.evaluateJavascript(
                    "window.__appVisibility && window.__appVisibility(" + visible + ")", null);
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        notifyAppVisibility(true);
    }

    @Override
    protected void onPause() {
        notifyAppVisibility(false);
        super.onPause();
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
        // TTS 引擎是进程级共享单例（浮窗可能还活着），不随 Activity 销毁 shutdown；
        // 页面退后台时 xm_host_bootstrap 已调 TTS.stop()，朗读不会残留。
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
