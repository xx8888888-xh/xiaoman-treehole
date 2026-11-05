package com.xiaoman.treehole;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.media.AudioManager;
import android.os.Build;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import android.webkit.WebView;

import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;

/**
 * TtsController · 进程级唯一的系统 TTS 通道（主界面 + 两条浮窗共用一个引擎）
 * -------------------------------------------------------------------------
 * 为什么抽出来：主界面 WebView 与浮窗的 pet/迷你窗三条 WebView 都要 window.AndroidTTS。
 * 以前引擎、音频焦点、完成回调都长在 MainActivity.Bridge 里，浮窗拿不到 →
 * 浮窗永远没声音。现在引擎与音频策略收在这一个单例，三条 WebView 只挂同一个桥。
 *
 * 音频约定（本类负责）：
 *  1) 朗读前 requestAudioFocus(AUDIOFOCUS_GAIN_TRANSIENT)：其它 App 的音乐先让路；
 *     minSdk 24，用旧 API（requestAudioFocus(listener, streamType, durationHint)），
 *     不为了新 AudioFocusRequest 抬高 minSdk。
 *  2) 朗读结束 / onError / 主动 stop / 焦点丢失 → abandonAudioFocus。
 *  3) 注册 ACTION_AUDIO_BECOMING_NOISY：拔耳机或断蓝牙立刻停读，且不自动恢复外放
 *     （AudioFocusPolicy 是单向状态机，没有 resume）。
 *  4) 引擎懒初始化：不朗读就不拉起系统语音服务，保持"默认不出声"的省电约定。
 */
final class TtsController {

    private static final String TAG = "XiaoManAudio";

    private static volatile TtsController shared;

    /** 进程内单例（应用 Context，不持有 Activity/Service） */
    static TtsController shared(Context ctx) {
        TtsController c = shared;
        if (c != null) return c;
        synchronized (TtsController.class) {
            if (shared == null) shared = new TtsController(ctx.getApplicationContext());
            return shared;
        }
    }

    private final Context app;
    private final AudioManager audio;
    private final AudioFocusPolicy policy = new AudioFocusPolicy();
    private final NoisyGuard noisyGuard = new NoisyGuard(policy);
    /** 需要 __ttsEnded 回调的页面（主界面 + 迷你窗）；WeakReference 防止泄漏 */
    private final List<WeakReference<WebView>> pages = new ArrayList<>();

    private TextToSpeech tts;
    private volatile boolean ttsReady = false;
    private boolean initStarted = false;
    private String pendingText;
    private float pendingRate = 1.02f;

    /** 当前这一句的 utteranceId：只有它的完成回调才算"这一轮读完了" */
    private String currentId;
    private boolean focusHeld = false;

    private TtsController(Context app) {
        this.app = app;
        this.audio = (AudioManager) app.getSystemService(Context.AUDIO_SERVICE);
        registerNoisyReceiver();
    }

    // ---------------- 对外（JS 桥） ----------------

    synchronized boolean speak(String text, float rate) {
        if (text == null || text.isEmpty()) return false;
        if (tts != null && ttsReady) return speakNow(text, rate);
        if (!hasEngine()) return false;   // 设备没有引擎：如实返回 false，交给下一级 provider
        ensureEngine();                   // 首次真正要发声时才启动引擎（不提前拉服务）
        if (ttsReady) return speakNow(text, rate);
        // 引擎还在初始化：登记这一次，初始化成功后补播（避免"第一下点了没声音"）
        pendingText = text;
        pendingRate = rate;
        return true;
    }

    /** 主动停止（暂停/切页/用户点停止）：停读 + 放弃焦点，不自动续播 */
    void stop() {
        stopReading("js-stop");
    }

    /**
     * 语音能力是否可用：只查"系统里有没有 TTS 引擎"，不 bind 服务、不预热。
     * 需要 manifest 里的 <queries> 声明，否则 Android 11+ 的包可见性会把结果过滤成空。
     */
    boolean available() {
        return hasEngine();
    }

    // ---------------- 页面完成回调 ----------------

    void bindWebView(WebView web) {
        if (web == null) return;
        synchronized (pages) {
            Iterator<WeakReference<WebView>> it = pages.iterator();
            while (it.hasNext()) {
                WebView w = it.next().get();
                if (w == null) it.remove();
                else if (w == web) return;
            }
            pages.add(new WeakReference<>(web));
        }
    }

    private void notifyEnded() {
        List<WebView> alive = new ArrayList<>();
        synchronized (pages) {
            Iterator<WeakReference<WebView>> it = pages.iterator();
            while (it.hasNext()) {
                WebView w = it.next().get();
                if (w == null) it.remove();
                else alive.add(w);
            }
        }
        for (WebView w : alive) {
            try {
                w.post(() -> {
                    try {
                        w.evaluateJavascript("window.__ttsEnded && window.__ttsEnded()", null);
                    } catch (Exception ignored) { }
                });
            } catch (Exception ignored) { }
        }
    }

    // ---------------- 引擎 ----------------

    private boolean hasEngine() {
        try {
            List<ResolveInfo> engines = app.getPackageManager().queryIntentServices(
                    new Intent("android.intent.action.TTS_SERVICE"), PackageManager.MATCH_DEFAULT_ONLY);
            return engines != null && !engines.isEmpty();
        } catch (Exception e) {
            Log.w(TAG, "TTS engine query failed: " + e.getMessage());
            return false;
        }
    }

    private synchronized void ensureEngine() {
        if (tts != null || initStarted) return;
        initStarted = true;
        try {
            tts = new TextToSpeech(app, status -> {
                if (status == TextToSpeech.SUCCESS && tts != null) {
                    int result = tts.setLanguage(Locale.SIMPLIFIED_CHINESE);
                    ttsReady = (result == TextToSpeech.LANG_COUNTRY_AVAILABLE
                            || result == TextToSpeech.LANG_AVAILABLE);
                    if (!ttsReady) {
                        Log.w(TAG, "TTS: SIMPLIFIED_CHINESE not supported, result=" + result);
                    } else {
                        flushPending();   // 补播"点了但引擎还在初始化"的那一次
                    }
                } else {
                    ttsReady = false;
                    Log.e(TAG, "TTS init failed: status=" + status);
                }
            });
            tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override
                public void onStart(String utteranceId) { }

                @Override
                public void onDone(String utteranceId) {
                    finish(utteranceId);
                }

                @Override
                public void onError(String utteranceId) {
                    finish(utteranceId);
                }

                /** API 23+：QUEUE_FLUSH / stop() 会把上一句标记为 interrupted */
                @Override
                public void onStop(String utteranceId, boolean interrupted) {
                    finish(utteranceId);
                }
            });
        } catch (Exception e) {
            Log.w(TAG, "TTS unavailable: " + e.getMessage());
            tts = null;
            ttsReady = false;
        }
    }

    private synchronized void flushPending() {
        String text = pendingText;
        float rate = pendingRate;
        pendingText = null;
        if (text != null) speakNow(text, rate);
    }

    /** 真正发声；调用方需保证 tts != null && ttsReady */
    private synchronized boolean speakNow(String text, float rate) {
        if (tts == null || !ttsReady || text == null || text.isEmpty()) return false;
        // 先拿焦点再出声：拿不到就如实失败，不"偷偷外放"
        if (!requestFocus()) {
            Log.w(TAG, "[probe] tts-skip no-audio-focus");
            return false;
        }
        float r = rate < 0.5f ? 0.5f : Math.min(rate, 2.0f);
        String id = "xiaoman_" + System.currentTimeMillis();
        try {
            tts.setPitch(1.15f);
            tts.setSpeechRate(r);
            currentId = id;
            policy.onSpeakStarted();
            int rc = tts.speak(text, TextToSpeech.QUEUE_FLUSH, new Bundle(), id);
            if (rc != TextToSpeech.SUCCESS) {
                Log.w(TAG, "speak rejected: " + rc);
                currentId = null;
                policy.onSpeakFinished();
                abandonFocus();
                return false;
            }
            return true;
        } catch (Exception e) {
            Log.w(TAG, "speak failed: " + e.getMessage());
            currentId = null;
            policy.onSpeakFinished();
            abandonFocus();
            return false;
        }
    }

    /** 一句读完/被打断：只认当前这一句，避免 FLUSH 后旧回调把新朗读的焦点放掉 */
    private void finish(String utteranceId) {
        synchronized (this) {
            if (utteranceId != null && !utteranceId.equals(currentId)) return;
            currentId = null;
            policy.onSpeakFinished();
        }
        abandonFocus();
        notifyEnded();
    }

    private void stopReading(String why) {
        currentId = null;
        if (tts != null) {
            try { tts.stop(); } catch (Exception ignored) { }
        }
        policy.onSpeakFinished();
        abandonFocus();
        notifyEnded();
        Log.d(TAG, "[probe] tts-stop " + why);
    }

    // ---------------- 音频焦点 ----------------

    private final AudioManager.OnAudioFocusChangeListener focusListener = change -> {
        AudioFocusPolicy.FocusEvent ev;
        switch (change) {
            case AudioManager.AUDIOFOCUS_GAIN:
                ev = AudioFocusPolicy.FocusEvent.GAIN;
                break;
            case AudioManager.AUDIOFOCUS_LOSS_TRANSIENT:
                ev = AudioFocusPolicy.FocusEvent.LOSS_TRANSIENT;
                break;
            case AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK:
                ev = AudioFocusPolicy.FocusEvent.LOSS_TRANSIENT_CAN_DUCK;
                break;
            case AudioManager.AUDIOFOCUS_LOSS:
            default:
                ev = AudioFocusPolicy.FocusEvent.LOSS;
                break;
        }
        if (policy.onFocusChange(ev) == AudioFocusPolicy.Action.STOP) {
            Log.d(TAG, "[probe] audio-focus lost change=" + change + " → stop");
            stopReading("focus-lost");
        }
    };

    private boolean requestFocus() {
        if (focusHeld) return true;
        if (audio == null) return false;
        try {
            int rc = audio.requestAudioFocus(focusListener, AudioManager.STREAM_MUSIC,
                    AudioManager.AUDIOFOCUS_GAIN_TRANSIENT);
            focusHeld = (rc == AudioManager.AUDIOFOCUS_REQUEST_GRANTED);
            Log.d(TAG, "[probe] audio-focus request " + (focusHeld ? "granted" : "denied"));
            return focusHeld;
        } catch (Exception e) {
            Log.w(TAG, "audio focus failed: " + e.getMessage());
            return false;
        }
    }

    private void abandonFocus() {
        if (!focusHeld) return;
        focusHeld = false;
        try { audio.abandonAudioFocus(focusListener); } catch (Exception ignored) { }
    }

    // ---------------- 拔耳机/断蓝牙 ----------------

    private final BroadcastReceiver noisyReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (intent == null
                    || !AudioManager.ACTION_AUDIO_BECOMING_NOISY.equals(intent.getAction())) {
                return;
            }
            if (noisyGuard.onBecomingNoisy() == AudioFocusPolicy.Action.STOP) {
                // 只停，不恢复：恢复朗读必须等用户下一次手势
                Log.d(TAG, "[probe] audio-becoming-noisy → stop");
                stopReading("becoming-noisy");
            }
        }
    };

    private void registerNoisyReceiver() {
        boolean ok = noisyGuard.register(() -> {
            IntentFilter f = new IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY);
            if (Build.VERSION.SDK_INT >= 33) {
                // 系统广播仍可送达 NOT_EXPORTED；不给第三方 App 伪造"拔耳机"的机会
                app.registerReceiver(noisyReceiver, f, Context.RECEIVER_NOT_EXPORTED);
            } else {
                app.registerReceiver(noisyReceiver, f);
            }
        });
        if (ok) {
            Log.d(TAG, "[probe] noisy-receiver registered");
        } else {
            Log.w(TAG, "noisy receiver register failed");
        }
    }
}
