package com.xiaoman.treehole;

import android.webkit.WebView;

/**
 * PageLifecycle · 页面冻结/恢复的统一入口
 * ------------------------------------------------------------
 * 由 MainActivity.onStart/onStop 与 FloatWindowService 的展开/收起调用。
 * 只调用注入脚本 __xmHost.pause()/resume()，不动 web/ 源文件：
 *  - pause：清空页面所有 setInterval/setTimeout（心跳、提醒轮询、打字机全部停），
 *           停止 TTS，并让 Reminders.due()/markDone() 在冻结期间失效；
 *  - resume：按剩余时长重新武装计时器，会话/历史不丢。
 */
final class PageLifecycle {

    private PageLifecycle() {}

    static void pause(WebView web) {
        if (web == null) return;
        try {
            web.evaluateJavascript("window.__xmHost && window.__xmHost.pause()", null);
        } catch (Exception ignored) { }
    }

    static void resume(WebView web) {
        if (web == null) return;
        try {
            web.evaluateJavascript("window.__xmHost && window.__xmHost.resume()", null);
        } catch (Exception ignored) { }
    }

    /** 后台时一并暂停 WebView 自身的渲染/动画调度（JS 定时器由 __xmHost 负责） */
    static void onHostPause(WebView web) {
        pause(web);
        if (web == null) return;
        try { web.onPause(); } catch (Exception ignored) { }
    }

    static void onHostResume(WebView web) {
        if (web == null) return;
        try { web.onResume(); } catch (Exception ignored) { }
        resume(web);
    }
}
