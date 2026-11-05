package com.xiaoman.treehole;

import android.content.Context;
import android.webkit.JavascriptInterface;

/**
 * AndroidTtsBridge · window.AndroidTTS 的 TTS 部分（薄封装，状态都在 TtsController 单例）
 * ------------------------------------------------------------------------------------
 * 两类宿主共用它：
 *  - MainActivity.Bridge 继承本类（主界面额外挂了 saveBackup / float* 等宿主方法）；
 *  - FloatWindowService 的桌宠与迷你窗 WebView 直接挂本类。
 * 三条 WebView 因此共享同一个引擎与同一套音频焦点策略。
 *
 * public：WebView 的 addJavascriptInterface 走反射取方法，跨包继承（MainActivity.Bridge）
 * 时类可见性能少踩一个坑；方法本身也必须是 public + @JavascriptInterface。
 */
public class AndroidTtsBridge {

    static final String NAME = "AndroidTTS";

    private final TtsController tts;

    AndroidTtsBridge(Context ctx) {
        this.tts = TtsController.shared(ctx);
    }

    @JavascriptInterface
    public boolean speak(String text) {
        return tts.speak(text, 1.02f);
    }

    /** 带语速的朗读（慢/正常/快三档由 web 端映射成 0.86/1.02/1.18） */
    @JavascriptInterface
    public boolean speakRate(String text, float rate) {
        return tts.speak(text, rate);
    }

    @JavascriptInterface
    public void stop() {
        tts.stop();
    }

    /**
     * 语音能力是否可用：只查"系统里有没有 TTS 引擎"，不 bind 服务、不预热。
     * 为什么不再返回 ttsReady：那是"异步初始化完成没"，启动瞬间必然 false，
     * 会让设置页把 native 误报成不可用（provider 掉到 none），文案与实际能力不一致。
     * 真正的初始化延迟由 speak 的待播队列兜住。
     */
    @JavascriptInterface
    public boolean available() {
        return tts.available();
    }
}
