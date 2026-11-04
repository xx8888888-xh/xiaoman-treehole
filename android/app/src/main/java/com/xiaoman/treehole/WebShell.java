package com.xiaoman.treehole;

import android.content.Context;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Build;
import android.util.Log;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * WebShell · 主界面与悬浮窗共用的 WebView 配置 + 注入式宿主脚本
 * ------------------------------------------------------------
 * 为什么要在资源层注入：web/ 目录由别的分支维护，本分支不得改；
 * 但"退后台停心跳/停提醒轮询/点击才朗读"又必须在页面脚本之前生效，
 * 所以在 shouldInterceptRequest 里给 index.html 的 </head> 前插一行
 * <script src="/www/xm_host_bootstrap.js">，脚本实体放在 res/raw。
 */
final class WebShell {

    private static final String TAG = "WebShell";
    static final String BOOTSTRAP_PATH = "/www/xm_host_bootstrap.js";
    static final String BOOTSTRAP_ASSET_PATH = "www/xm_host_bootstrap.js";

    private WebShell() {}

    /** 统一的 WebView 设置（主界面/悬浮窗两份实例保持完全一致） */
    @android.annotation.SuppressLint("SetJavaScriptEnabled")
    static void configureSettings(WebView web, Context ctx) {
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        // 资源全部走 appassets 拦截，不需要 file:// 与 content:// 权限（审计 P2-6）
        ws.setAllowFileAccess(false);
        ws.setAllowFileAccessFromFileURLs(false);
        ws.setAllowUniversalAccessFromFileURLs(false);
        ws.setAllowContentAccess(false);
        // 自动播放一律需要用户手势：与"点击才朗读"一致，也挡掉后台意外出声
        ws.setMediaPlaybackRequiresUserGesture(true);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setGeolocationEnabled(false);
        // 页面是 https://appassets…，本机语音/代理是 http://127.0.0.1，
        // 不放开 mixed-content 会被静默拦下（"有字幕没声音"）
        ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        applyFontScale(web, ctx);
    }

    /** 系统字体缩放 → WebView textZoom（1.3× 系统字号不再被 configChanges 吞掉） */
    static void applyFontScale(WebView web, Context ctx) {
        if (web == null || ctx == null) return;
        float scale = 1f;
        try {
            Configuration c = ctx.getResources().getConfiguration();
            if (c != null) scale = c.fontScale;
        } catch (Exception ignored) { }
        int zoom = (int) Math.round(scale * 100f);
        zoom = Math.max(85, Math.min(130, zoom));   // 85%–130%：覆盖系统 1.3× 字号，且不撑破布局
        try { web.getSettings().setTextZoom(zoom); } catch (Exception ignored) { }
    }

    /** 把注入脚本挂进 index.html（只改内存中的响应，不落盘、不动 web/ 源文件） */
    static WebResourceResponse injectIntoHtml(Context ctx, InputStream in) {
        try {
            byte[] raw = readAll(in);
            String html = new String(raw, StandardCharsets.UTF_8);
            String tag = "<script src=\"" + BOOTSTRAP_PATH + "\"></script>";
            int at = indexOfIgnoreCase(html, "</head>");
            String out = at >= 0
                    ? html.substring(0, at) + tag + html.substring(at)
                    : tag + html;
            return new WebResourceResponse("text/html", "utf-8",
                    new ByteArrayInputStream(out.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            Log.w(TAG, "inject failed: " + e.getMessage());
            return null;
        }
    }

    /** 注入脚本本体：从 res/raw 读（不在 assets 里，避免被 web 同步脚本覆盖） */
    static WebResourceResponse bootstrapResponse(Context ctx) {
        try {
            InputStream in = ctx.getResources().openRawResource(R.raw.xm_host_bootstrap);
            return new WebResourceResponse("text/javascript", "utf-8", in);
        } catch (Exception e) {
            Log.w(TAG, "bootstrap missing: " + e.getMessage());
            return new WebResourceResponse("text/javascript", "utf-8", 404, "Not Found", null, null);
        }
    }

    static boolean isBootstrapUrl(Uri uri) {
        return uri != null && BOOTSTRAP_PATH.equals(uri.getPath());
    }

    private static byte[] readAll(InputStream in) throws Exception {
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        try { in.close(); } catch (Exception ignored) { }
        return bos.toByteArray();
    }

    private static int indexOfIgnoreCase(String hay, String needle) {
        String h = hay.toLowerCase(java.util.Locale.ROOT);
        return h.indexOf(needle.toLowerCase(java.util.Locale.ROOT));
    }
}
