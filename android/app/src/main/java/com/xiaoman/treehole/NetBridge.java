package com.xiaoman.treehole;

import android.util.Base64;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * 原生 HTTP 桥（免 Key AI）。
 *
 * 为什么需要：Kilo 网关不返回 CORS 响应头，WebView 里 fetch 直连必被拦；APK 内又没有
 * 127.0.0.1:8902 代理服务。由 Java 侧 HttpURLConnection 代发请求，天然没有 Origin 头，
 * 绕开浏览器 CORS 限制。主界面与悬浮窗共用本类。
 *
 * JS 约定：AndroidNet.chat(id, url, bodyJson) → 异步回调
 *          window.__nativeChatDone(id, ok, status, base64Body)
 */
public class NetBridge {
    /** 允许代发的目标：https 的已知模型网关 + 本机 127.0.0.1:8902（mock/代理）。
     *  其余 URL 一律拒绝 —— 否则 WebView 页面一旦被注入，就能把原生桥当任意 URL 跳板
     *  （内网探测 / 数据外带）。 */
    private static final String[] ALLOWED_HTTPS_HOSTS = {
            "api.kilo.ai", "openrouter.ai", "api.openrouter.ai"
    };
    private static final int LOCAL_MOCK_PORT = 8902;

    private final WebView webView;

    public NetBridge(WebView webView) {
        this.webView = webView;
    }

    /** 目标白名单校验：禁止 userinfo、非默认 https 端口、非 http(s) scheme。 */
    static boolean isAllowedTarget(String url) {
        if (url == null) return false;
        try {
            URL u = new URL(url);
            if (u.getHost() == null || u.getUserInfo() != null) return false;
            String scheme = u.getProtocol();
            if ("https".equalsIgnoreCase(scheme)) {
                if (u.getPort() != -1 && u.getPort() != 443) return false;
                for (String h : ALLOWED_HTTPS_HOSTS) {
                    if (h.equalsIgnoreCase(u.getHost())) return true;
                }
                return false;
            }
            if ("http".equalsIgnoreCase(scheme)) {
                return "127.0.0.1".equals(u.getHost()) && u.getPort() == LOCAL_MOCK_PORT;
            }
            return false;
        } catch (Exception e) {
            return false;
        }
    }

    @JavascriptInterface
    public void chat(final String id, final String url, final String body) {
        if (!isAllowedTarget(url)) {
            Log.w("XiaoManNet", "blocked chat target: " + url);
            final WebView wv = webView;
            if (wv != null) {
                wv.post(() -> wv.evaluateJavascript(
                        "window.__nativeChatDone('" + id + "',false,0,'')", null));
            }
            return;
        }
        new Thread(() -> {
            int status = 0;
            String resp = null;
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                c.setRequestMethod("POST");
                c.setConnectTimeout(15000);
                c.setReadTimeout(60000);
                c.setInstanceFollowRedirects(false);   // 防白名单域跳转到任意站点
                c.setRequestProperty("Content-Type", "application/json");
                c.setRequestProperty("User-Agent", "XiaoMan/0.4 (Android)");
                c.setDoOutput(true);
                OutputStream os = c.getOutputStream();
                os.write(body.getBytes(StandardCharsets.UTF_8));
                os.flush();
                os.close();
                status = c.getResponseCode();
                InputStream is = status >= 400 ? c.getErrorStream() : c.getInputStream();
                resp = is == null ? "" : readAll(is);
            } catch (Exception e) {
                Log.e("XiaoManNet", "native chat failed: " + e);
            }
            final boolean ok = status >= 200 && status < 300 && resp != null;
            String b64 = "";
            if (resp != null) {
                b64 = Base64.encodeToString(resp.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
            }
            final String payload = b64;
            final int st = status;
            final WebView wv = webView;
            if (wv == null) return;
            wv.post(() -> wv.evaluateJavascript(
                    "window.__nativeChatDone('" + id + "'," + ok + "," + st + ",'" + payload + "')",
                    null));
        }, "xiaoman-net").start();
    }

    private String readAll(InputStream in) throws Exception {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        in.close();
        return new String(bos.toByteArray(), StandardCharsets.UTF_8);
    }
}
