package com.xiaoman.treehole;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * FloatWindowService · 小满桌面 Live2D 桌宠 + 迷你对话窗（纯 Java，零依赖）
 * ------------------------------------------------------------
 * 能力与交互（验收点逐一对应）：
 *  - 桌宠：140x180dp 透明 WebView 加载 pet.html，Live2D 待机呼吸/眨眼/视线漫游
 *  - 互动：单击头部/身体 → 表情 + 动作 + 气泡；双击 → 打开迷你对话窗；
 *          长按 → 页内「聊天 / 关闭」菜单；拖动 → 跟手 + 松手贴边
 *  - 提醒：pet.html 每 30s 读到期待办，经全局/单条/每小时三重节流后弹原生气泡卡片
 *  - 气泡：原生 TextView 卡片（不受小窗口裁切），点击即打开迷你窗；7s 自动收起
 *  - 降级：WebView 加载失败/超时/渲染进程崩溃 → 自动退回 60dp 圆形悬浮球（聊天仍可用）
 *  - 权限：SYSTEM_ALERT_WINDOW；未授权/被回收 → stopSelf()，不崩溃；通知栏常驻「关闭悬浮」
 *  - 前后台：FGS + START_STICKY；Android 12+ 由 Activity 前台调用，避免后台启动限制
 *  - 静音：pet.html 不加载 TTS、不创建 Audio；TTS_MUTED.flag 约束全程成立
 * 外观：pet.html 读取 xiaoman_appearance_v1，零动画时停 ticker；大字号/灰阶同步气泡。
 * FGS：API 34 起必须声明 specialUse 类型（Manifest 已配 PROPERTY_SPECIAL_USE_FGS_SUBTYPE）。
 */
public class FloatWindowService extends Service {

    public static final String ACTION_SHOW = "com.xiaoman.treehole.float.SHOW";
    public static final String ACTION_HIDE = "com.xiaoman.treehole.float.HIDE";

    private static final String CHANNEL_ID = "float_window";
    private static final int NOTIF_ID = 42;
    private static final int BALL_SIZE_DP = 60;
    private static final int PET_W_DP = 140;
    private static final int PET_H_DP = 180;
    private static final int BUBBLE_W_DP = 240;
    private static final int PET_READY_TIMEOUT_MS = 12000;
    private static final int BUBBLE_TTL_MS = 7000;
    private static final int TAP_SLOP_PX = 20;   // 位移小于此值视为点击（而非拖动）
    private static final long PANEL_DESTROY_DELAY_MS = 15_000L;

    /** 迷你窗是否展开（与 MainActivity.activityVisible 一起决定"是否有界面可见"） */
    private static volatile boolean panelVisible = false;

    static boolean isPanelVisible() {
        return panelVisible;
    }

    private WindowManager wm;
    private View ball;                       // 降级悬浮球（pet 不可用时）
    private WebView petWeb;                  // Live2D 桌宠（透明）
    private LinearLayout panel;
    private WebView panelWeb;
    private TextView bubble;                 // 提醒/互动气泡卡片
    private WindowManager.LayoutParams ballParams;
    private WindowManager.LayoutParams petParams;
    private WindowManager.LayoutParams panelParams;
    private WindowManager.LayoutParams bubbleParams;
    private boolean panelShown = false;
    private boolean bubbleShown = false;
    private boolean petReady = false;
    private boolean petTimedOut = false;     // 仅超时降级时保留 petWeb，等 ready 探针回来再升级
    private boolean largeFont = false;
    private boolean grayIcons = true;
    private int screenW;
    private int screenH;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Runnable destroyTask;
    private final Runnable fallbackTask = () -> {
        if (!petReady) fallbackToBall("timeout", true);
    };
    private final Runnable hideBubbleTask = () -> hideBubble();

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        wm = (WindowManager) getSystemService(WINDOW_SERVICE);
        screenW = getResources().getDisplayMetrics().widthPixels;
        screenH = getResources().getDisplayMetrics().heightPixels;
        // 必须先 startForeground：用 startForegroundService 启动时 5s 内不调用会被系统判 ANR
        if (!startForegroundNotify()) return;
        if (!Settings.canDrawOverlays(this)) {
            // 权限被拒绝/被系统回收：安静退出，绝不让 addView 抛异常
            Log.w("FloatWindowService", "overlay permission missing, stop");
            stopSelf();
            return;
        }
        showPet();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_HIDE.equals(action)) {
            hideBubble();
            collapsePanel();
            stopSelf();
            return START_NOT_STICKY;
        }
        // ACTION_SHOW / 空动作（START_STICKY 重建）
        if (!Settings.canDrawOverlays(this)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (petWeb == null && (ball == null || ball.getParent() == null)) showPet();
        return START_STICKY;
    }

    // ---------------- 前台通知（含「关闭悬浮」兜底入口） ----------------

    private boolean startForegroundNotify() {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (Build.VERSION.SDK_INT >= 26) {
                NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "小满悬浮窗",
                        NotificationManager.IMPORTANCE_MIN);
                ch.setDescription("桌面小满与迷你对话窗的后台保活通知");
                ch.setShowBadge(false);
                nm.createNotificationChannel(ch);
            }
            PendingIntent pi = PendingIntent.getActivity(this, 0,
                    new Intent(this, MainActivity.class),
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            PendingIntent hidePi = PendingIntent.getService(this, 1,
                    new Intent(this, FloatWindowService.class).setAction(ACTION_HIDE),
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            Notification.Builder b = Build.VERSION.SDK_INT >= 26
                    ? new Notification.Builder(this, CHANNEL_ID)
                    : new Notification.Builder(this);
            b.setSmallIcon(android.R.drawable.ic_dialog_info)
                    .setContentTitle("小满在身边")
                    .setContentText("单击互动 · 双击聊天 · 长按更多")
                    .setContentIntent(pi)
                    .setOngoing(true);
            if (Build.VERSION.SDK_INT >= 20) {
                b.addAction(0, "关闭悬浮", hidePi);
            }
            Notification notif = b.build();
            // API 34 起必须带 FGS 类型参数；Manifest 已声明 specialUse
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIF_ID, notif,
                        android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            } else {
                startForeground(NOTIF_ID, notif);
            }
            return true;
        } catch (Exception e) {
            // Android 12+ 后台启动限制 / 权限被回收：记录后退出，不崩溃
            Log.w("FloatWindowService", "startForeground rejected: " + e);
            stopSelf();
            return false;
        }
    }

    // ---------------- Live2D 桌宠 ----------------

    private void showPet() {
        if (petWeb != null) return;
        petTimedOut = false;
        int w = dp(PET_W_DP);
        int h = dp(PET_H_DP);

        WebView web = new WebView(this);
        petWeb = web;
        web.setBackgroundColor(Color.TRANSPARENT);
        // 不强制 LAYER_TYPE_HARDWARE：窗口本身已带 HARDWARE_ACCELERATED，显式层反而会让 WebGL 面
        // 走独立纹理而合成不进悬浮层（实测 only-DOM-2D 可见、WebGL 帧缓冲有像素却不显示）
        web.setSoundEffectsEnabled(false);   // 静音约束：任何系统点击/媒体音效都不发
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setMediaPlaybackRequiresUserGesture(true);
        ws.setAllowFileAccess(true);
        ws.setAllowFileAccessFromFileURLs(false);
        ws.setAllowUniversalAccessFromFileURLs(false);
        ws.setAllowContentAccess(false);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setGeolocationEnabled(false);
        if (Build.VERSION.SDK_INT >= 21) {
            ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        web.setWebViewClient(new PetWebClient(this));
        web.setWebChromeClient(new ProbeChromeClient());
        web.addJavascriptInterface(new PetBridge(this), "AndroidPet");
        // 浮窗两条 WebView 与主界面共用同一个 AndroidTTS 注入入口（以前这里漏了 → 浮窗永远没声）
        WebShell.attachAndroidTts(web, new AndroidTtsBridge(this));
        web.setOnTouchListener(new PetDragListener(this));

        petParams = new WindowManager.LayoutParams(
                w, h,
                overlayType(),
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                PixelFormat.TRANSLUCENT);
        petParams.gravity = Gravity.TOP | Gravity.START;
        petParams.x = Math.max(dp(8), screenW - w - dp(8));
        petParams.y = Math.max(dp(80), screenH / 3);

        try {
            wm.addView(petWeb, petParams);
        } catch (Exception e) {
            // 未授权 / token 失效 / 系统回收：降级不崩溃
            Log.w("FloatWindowService", "add pet failed: " + e);
            fallbackToBall("add-failed");
            return;
        }
        petReady = false;
        handler.removeCallbacks(fallbackTask);
        handler.postDelayed(fallbackTask, PET_READY_TIMEOUT_MS);
        web.loadUrl(MainActivity.PET_URL);
    }

    /** 拖动桌宠：本监听只观察，不消费事件（返回 false，WebView 仍能收到点击做互动） */
    private static class PetDragListener implements View.OnTouchListener {
        private final FloatWindowService svc;
        float downRawX, downRawY;
        int startX, startY;
        boolean moved = false;

        PetDragListener(FloatWindowService svc) { this.svc = svc; }

        @Override
        public boolean onTouch(View v, MotionEvent event) {
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downRawX = event.getRawX();
                    downRawY = event.getRawY();
                    startX = svc.petParams.x;
                    startY = svc.petParams.y;
                    moved = false;
                    return false;
                case MotionEvent.ACTION_MOVE: {
                    float dx = event.getRawX() - downRawX;
                    float dy = event.getRawY() - downRawY;
                    // 拖动期间不逐帧 updateViewLayout：overlay 里的 WebGL WebView 被高频移动后
                    // 渲染面会失效（实测整块变透明）。只记录意图，抬手时一次性落位。
                    if (Math.abs(dx) > TAP_SLOP_PX || Math.abs(dy) > TAP_SLOP_PX) moved = true;
                    return false;
                }
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (moved) {
                        float dx = event.getRawX() - downRawX;
                        float dy = event.getRawY() - downRawY;
                        svc.petParams.x = svc.clampX(startX + (int) dx, svc.petParams.width);
                        svc.petParams.y = svc.clampY(startY + (int) dy, svc.petParams.height);
                        svc.snapToEdge(svc.petParams, svc.petWeb);
                        if (svc.bubbleShown) svc.positionBubble();
                    }
                    return false;
                default:
                    return false;
            }
        }
    }

    // ---------------- 降级悬浮球 ----------------

    private void showBall() {
        int size = dp(BALL_SIZE_DP);
        TextView tv = new TextView(this);
        tv.setText("满");
        tv.setTextColor(Color.WHITE);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
        tv.setGravity(Gravity.CENTER);
        tv.setContentDescription("小满悬浮球，点按展开迷你对话窗");
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.OVAL);
        bg.setColor(Color.parseColor("#F7A8B8"));
        bg.setStroke(dp(2), Color.WHITE);
        tv.setBackground(bg);

        ballParams = new WindowManager.LayoutParams(
                size, size,
                overlayType(),
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        ballParams.gravity = Gravity.TOP | Gravity.START;
        ballParams.x = Math.max(dp(8), screenW - size - dp(8));
        ballParams.y = Math.max(dp(80), screenH / 3);

        tv.setOnTouchListener(new BallDragTapListener(this, tv));
        ball = tv;
        try {
            if (!Settings.canDrawOverlays(this)) {
                stopSelf();
                return;
            }
            wm.addView(ball, ballParams);
            Log.d("FloatWindowService", "ball added @" + ballParams.x + "," + ballParams.y);
        } catch (Exception e) {
            Log.w("FloatWindowService", "add ball failed: " + e);
            stopSelf();
        }
    }

    private void fallbackToBall(String reason) {
        fallbackToBall(reason, false);
    }

    /**
     * 降级到悬浮球。keepPet=true 只用于「加载超时」：
     * 慢设备上 WebView 冷启动可能要 10s+，此时先把球亮出来安抚用户，
     * 但保留 WebView——等页面的 pet-ready 探针到了再原地升级回桌宠，避免一次慢加载永久降级。
     */
    private void fallbackToBall(String reason, boolean keepPet) {
        Log.w("FloatWindowService", "pet fallback: " + reason);
        handler.removeCallbacks(fallbackTask);
        petReady = false;
        petTimedOut = keepPet;
        int x = petParams != null ? petParams.x : dp(8);
        int y = petParams != null ? petParams.y : screenH / 3;
        if (keepPet) {
            try {
                if (petWeb != null) petWeb.setVisibility(View.GONE);
            } catch (Exception ignored) {}
        } else {
            petTimedOut = false;
            destroyPet();
        }
        if (ball == null || ball.getParent() == null) showBall();
        if (ballParams != null) {
            ballParams.x = clampX(x, ballParams.width);
            ballParams.y = clampY(y, ballParams.height);
            try {
                if (ball != null && ball.getParent() != null) {
                    wm.updateViewLayout(ball, ballParams);
                }
            } catch (Exception ignored) {}
        }
        if (panelShown) ball.setVisibility(View.GONE);
    }

    /** 超时降级后页面终于就绪：撤掉球，原地恢复桌宠（慢设备升级路径） */
    private void revivePet() {
        petTimedOut = false;
        removeBall();
        if (petWeb != null && petWeb.getParent() != null) {
            try { petWeb.setVisibility(panelShown ? View.GONE : View.VISIBLE); } catch (Exception ignored) {}
            if (!panelShown) rehostPet("revive");
        } else {
            petWeb = null;
            if (!panelShown) showPet();
        }
        Log.d("FloatWindowService", "pet revived");
    }

    private void removeBall() {
        if (ball != null && ball.getParent() != null) {
            try { wm.removeView(ball); } catch (Exception ignored) {}
        }
        ball = null;
    }

    private void destroyPet() {
        if (petWeb != null) {
            try {
                if (petWeb.getParent() != null) wm.removeView(petWeb);
            } catch (Exception ignored) {}
            petWeb.loadUrl("about:blank");
            petWeb.destroy();
            petWeb = null;
        }
    }

    /** 拖动 + 点击识别（降级球）：位移<阈值视为单击 → 展开迷你窗 */
    private static class BallDragTapListener implements View.OnTouchListener {
        private final FloatWindowService svc;
        private final View target;
        float downRawX, downRawY;
        int startX, startY;
        boolean moved = false;

        BallDragTapListener(FloatWindowService svc, View target) {
            this.svc = svc;
            this.target = target;
        }

        @Override
        public boolean onTouch(View v, MotionEvent event) {
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downRawX = event.getRawX();
                    downRawY = event.getRawY();
                    startX = svc.ballParams.x;
                    startY = svc.ballParams.y;
                    moved = false;
                    return true;
                case MotionEvent.ACTION_MOVE: {
                    float dx = event.getRawX() - downRawX;
                    float dy = event.getRawY() - downRawY;
                    if (Math.abs(dx) > TAP_SLOP_PX || Math.abs(dy) > TAP_SLOP_PX) moved = true;
                    svc.ballParams.x = svc.clampX(startX + (int) dx, svc.ballParams.width);
                    svc.ballParams.y = svc.clampY(startY + (int) dy, svc.ballParams.height);
                    try { svc.wm.updateViewLayout(target, svc.ballParams); } catch (Exception ignored) {}
                    return true;
                }
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (moved) svc.snapToEdge(svc.ballParams, target);
                    else svc.togglePanel();
                    return true;
                default:
                    return false;
            }
        }
    }

    private void snapToEdge(WindowManager.LayoutParams params, View view) {
        if (params == null || view == null) return;
        int centerX = params.x + params.width / 2;
        params.x = centerX < screenW / 2 ? 0 : screenW - params.width;
        try { wm.updateViewLayout(view, params); } catch (Exception ignored) {}
        if (view == petWeb) rehostPet("snap");
    }

    /**
     * 重新挂载桌宠窗口：overlay 里带 WebGL 的 WebView 被移动 / 从 GONE 切回后，
     * 部分设备（软件 GL 模拟器实测）不会重新合成这块画面，界面整块变透明。
     * 摘下再挂回会让 WebView 重建渲染目标并重绘（页面状态不丢、不重载）。
     */
    private void rehostPet(String why) {
        if (petWeb == null || petParams == null) return;
        Log.d("XiaoManFloat", "rehost " + why);
        try {
            if (petWeb.getParent() != null) wm.removeView(petWeb);
            wm.addView(petWeb, petParams);
        } catch (Exception e) {
            Log.w("FloatWindowService", "rehost pet failed: " + e);
        }
        nudgePet(why);
    }

    /**
     * 桌面 overlay 窗口被移动 / 从 GONE 切回可见后，部分设备（软件 GL 模拟器实测）的
     * Chromium 会停掉这个窗口的合成，画面整块变透明。这里让页面强制补帧，并连补三枪
     * 覆盖「窗口先重建、首帧后到」的时序；页面侧还有 2.5s 看门狗兜底。
     */
    private void nudgePet(String why) {
        if (petWeb == null) return;
        Log.d("XiaoManFloat", "nudge " + why);
        try {
            petWeb.resumeTimers();
            petWeb.requestLayout();
            petWeb.invalidate();
        } catch (Exception ignored) {}
        final Runnable kick = () -> {
            if (petWeb == null) return;
            try {
                petWeb.evaluateJavascript(
                        "window.__petRepaint&&window.__petRepaint('" + why + "')", null);
            } catch (Exception ignored) {}
        };
        kick.run();
        handler.postDelayed(kick, 500);
        handler.postDelayed(kick, 1500);
    }

    private int clampX(int x, int width) {
        return Math.max(0, Math.min(Math.max(0, screenW - width), x));
    }

    private int clampY(int y, int height) {
        return Math.max(0, Math.min(Math.max(0, screenH - height), y));
    }

    // ---------------- 原生气泡卡片 ----------------

    /** JS → 原生：展示一条气泡（提醒/互动短句），点击进入迷你窗 */
    private void showBubble(String text) {
        if (text == null || text.trim().isEmpty()) return;
        if (bubble == null) {
            TextView tv = new TextView(this);
            tv.setTextColor(Color.parseColor("#2B2B2B"));
            tv.setBackground(bubbleBackground());
            tv.setPadding(dp(12), dp(9), dp(12), dp(9));
            tv.setLineSpacing(dp(2), 1f);
            tv.setMaxLines(4);
            tv.setContentDescription("小满提醒");
            tv.setOnClickListener(v -> {
                hideBubble();
                expandPanel();
            });
            bubble = tv;
        }
        bubble.setText(text.length() > 120 ? text.substring(0, 119) + "…" : text);
        bubble.setTextSize(TypedValue.COMPLEX_UNIT_SP, largeFont ? 18 : 15);
        bubble.setBackground(bubbleBackground());
        if (bubbleParams == null) {
            bubbleParams = new WindowManager.LayoutParams(
                    dp(BUBBLE_W_DP), WindowManager.LayoutParams.WRAP_CONTENT,
                    overlayType(),
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                    PixelFormat.TRANSLUCENT);
            bubbleParams.gravity = Gravity.TOP | Gravity.START;
        }
        int width = Math.min(dp(BUBBLE_W_DP), screenW - dp(16));
        bubbleParams.width = width;
        bubble.measure(
                View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.AT_MOST),
                View.MeasureSpec.makeMeasureSpec(screenH, View.MeasureSpec.AT_MOST));
        positionBubble();
        try {
            if (bubble.getParent() == null) wm.addView(bubble, bubbleParams);
            else wm.updateViewLayout(bubble, bubbleParams);
            bubbleShown = true;
        } catch (Exception e) {
            Log.w("FloatWindowService", "add bubble failed: " + e);
            bubbleShown = false;
        }
        handler.removeCallbacks(hideBubbleTask);
        handler.postDelayed(hideBubbleTask, BUBBLE_TTL_MS);
    }

    private GradientDrawable bubbleBackground() {
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(Color.parseColor("#FFFDF8"));
        // 非拟态/灰阶约束：只保留 1px 深灰细线，不用粉色描边
        bg.setStroke(dp(1), Color.parseColor(grayIcons ? "#2B2B2B" : "#8A8A8A"));
        return bg;
    }

    /** 气泡优先放桌宠上方；顶部空间不足放下方；水平居中并钳制在屏内 */
    private void positionBubble() {
        if (bubble == null || bubbleParams == null) return;
        int[] anchor = primaryRect();
        if (anchor == null) return;
        int bw = bubbleParams.width;
        int bh = bubble.getMeasuredHeight() > 0 ? bubble.getMeasuredHeight() : dp(44);
        int gap = dp(8);
        int x = anchor[0] + anchor[2] / 2 - bw / 2;
        x = Math.max(gap, Math.min(screenW - bw - gap, x));
        int y = anchor[1] - bh - gap;
        if (y < gap) y = Math.min(screenH - bh - gap, anchor[1] + anchor[3] + gap);
        bubbleParams.x = Math.max(gap, x);
        bubbleParams.y = Math.max(gap, y);
    }

    /** 当前主浮层矩形 {x,y,w,h}；面板打开时用屏底正中兜底 */
    private int[] primaryRect() {
        if (petParams != null && petWeb != null && petWeb.getVisibility() == View.VISIBLE) {
            return new int[]{petParams.x, petParams.y, petParams.width, petParams.height};
        }
        if (ballParams != null && ball != null && ball.getVisibility() == View.VISIBLE) {
            return new int[]{ballParams.x, ballParams.y, ballParams.width, ballParams.height};
        }
        return new int[]{screenW / 2, screenH / 2, 0, 0};
    }

    private void hideBubble() {
        handler.removeCallbacks(hideBubbleTask);
        if (bubble != null && bubble.getParent() != null) {
            try { wm.removeView(bubble); } catch (Exception ignored) {}
        }
        bubbleShown = false;
    }

    // ---------------- 迷你对话窗 ----------------

    private void togglePanel() {
        if (panelShown) collapsePanel(); else expandPanel();
    }

    private void expandPanel() {
        if (panelShown) return;
        hideBubble();
        if (panel == null) buildPanel();          // 上次收起后已销毁 → 现场重建
        cancelDestroy();
        try {
            wm.addView(panel, panelParams);
            panelShown = true;
            panelVisible = true;
            // 迷你窗需要接收输入（弹键盘）：去掉 NOT_FOCUSABLE
            panelParams.flags &= ~WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
            wm.updateViewLayout(panel, panelParams);
            hidePrimary();
            PageLifecycle.onHostResume(panelWeb);   // 页面计时器按剩余时长重新武装
        } catch (Exception e) {
            Log.w("FloatWindowService", "expand panel failed: " + e);
            stopSelf();
        }
    }

    private void collapsePanel() {
        if (!panelShown || panel == null) return;
        try { wm.removeView(panel); } catch (Exception ignored) {}
        panelShown = false;
        panelVisible = false;
        // 收起 = 真暂停：心跳/提醒轮询/打字机全部停表，随后销毁 WebView 释放内存
        PageLifecycle.onHostPause(panelWeb);
        // 唯一可见界面收起后，到期提醒必须改走系统通知（否则页面停表后再没人投递）
        ReminderAlarmScheduler.reschedule(this);
        scheduleDestroy();
        showPrimary();
    }

    /** 收起 15 秒后销毁第二个 WebView（内存 -60~120MB）；期间再展开可秒开 */
    private void scheduleDestroy() {
        if (panelWeb == null) return;
        cancelDestroy();
        destroyTask = () -> {
            destroyTask = null;
            if (panelShown || panelWeb == null) return;
            try {
                panelWeb.loadUrl("about:blank");
                panelWeb.destroy();
            } catch (Exception ignored) { }
            panelWeb = null;
            panel = null;
            Log.d("FloatWindow", "panel webview released");
        };
        handler.postDelayed(destroyTask, PANEL_DESTROY_DELAY_MS);
    }

    private void cancelDestroy() {
        if (destroyTask != null) {
            handler.removeCallbacks(destroyTask);
            destroyTask = null;
        }
    }

    private void hidePrimary() {
        if (petWeb != null) petWeb.setVisibility(View.GONE);
        if (ball != null) ball.setVisibility(View.GONE);
    }

    private void showPrimary() {
        if (petWeb != null && petWeb.getParent() != null) {
            Boolean wasGone = petWeb.getVisibility() == View.GONE;
            petWeb.setVisibility(View.VISIBLE);
            if (wasGone) rehostPet("visible"); else nudgePet("visible");
        } else if (ball != null && ball.getParent() != null) {
            ball.setVisibility(View.VISIBLE);
            try { wm.updateViewLayout(ball, ballParams); } catch (Exception ignored) {}
        }
    }

    private void buildPanel() {
        int w = Math.min(screenWidthPct(0.92f), dp(340));
        int h = Math.min(screenHeightPct(0.62f), dp(460));

        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(18));
        bg.setColor(Color.parseColor("#FFF9F2"));
        bg.setStroke(dp(1), Color.parseColor("#F7A8B8"));
        panel.setBackground(bg);
        panel.setElevation(dp(6));

        // ---- 标题栏：标题 + 收起 + 关闭 ----
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(14), dp(8), dp(8), dp(8));

        TextView title = new TextView(this);
        title.setText("小满树洞 · 迷你窗");
        title.setTextColor(Color.parseColor("#5B4A43"));
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        bar.addView(title, tp);

        bar.addView(miniBtn("收起", v -> collapsePanel()));
        bar.addView(miniBtn("关闭", v -> stopSelf()));

        // ---- WebView：float=1 迷你模式（复用全部对话/记忆/提醒引擎） ----
        panelWeb = new WebView(this);
        WebSettings ws = panelWeb.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setMediaPlaybackRequiresUserGesture(false);
        ws.setAllowFileAccess(true);
        ws.setAllowFileAccessFromFileURLs(false);
        ws.setAllowUniversalAccessFromFileURLs(false);
        ws.setAllowContentAccess(false);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setGeolocationEnabled(false);
        if (Build.VERSION.SDK_INT >= 21) {
            ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        panelWeb.setWebViewClient(new AssetWebClient(this));
        panelWeb.setWebChromeClient(new ProbeChromeClient());
        panelWeb.addJavascriptInterface(new NetBridge(panelWeb), "AndroidNet");
        // 迷你窗是 index.html?float=1：同样要 window.AndroidTTS，否则回复只有字没有声
        WebShell.attachAndroidTts(panelWeb, new AndroidTtsBridge(this));
        panelWeb.loadUrl(MainActivity.BASE_URL + "?float=1");
        panelWeb.setBackgroundColor(Color.parseColor("#FFF9F2"));
        LinearLayout.LayoutParams wp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        panel.addView(bar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        panel.addView(panelWeb, wp);

        panelParams = new WindowManager.LayoutParams(
                w, h,
                overlayType(),
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        panelParams.gravity = Gravity.CENTER;
    }

    private Button miniBtn(String text, View.OnClickListener onClick) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setTextColor(Color.parseColor("#5B4A43"));
        b.setAllCaps(false);
        b.setMinWidth(dp(56));
        b.setMinimumWidth(dp(56));
        b.setMinHeight(0);
        b.setMinimumHeight(0);
        b.setPadding(dp(10), dp(4), dp(10), dp(4));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(Color.parseColor("#FFE3E9"));
        b.setBackground(bg);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(6), 0, 0, 0);
        b.setLayoutParams(lp);
        b.setOnClickListener(onClick);
        return b;
    }

    // ---------------- WebView 客户端（静态嵌套类，便于 d8 稳定处理） ----------------

    /** 桌宠 WebView：资源拦截 + 导航守卫 + 主文档失败/渲染进程崩溃 → 降级悬浮球 */
    private static class PetWebClient extends WebViewClient {
        private final FloatWindowService svc;

        PetWebClient(FloatWindowService svc) { this.svc = svc; }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
            return MainActivity.assetResponse(svc.getApplicationContext(), req.getUrl());
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            return MainActivity.guardNavigation(svc.getApplicationContext(), req.getUrl());
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
            // 只有主文档失败才降级；模型图片等子资源失败由页内兜底
            if (req != null && req.isForMainFrame()) svc.fallbackToBall("load-error");
        }

        @Override
        public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            svc.fallbackToBall("render-gone");
            return true;
        }
    }

    /** 迷你窗 WebView：资源拦截 + 导航守卫 */
    private static class AssetWebClient extends WebViewClient {
        private final FloatWindowService svc;

        AssetWebClient(FloatWindowService svc) { this.svc = svc; }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
            return MainActivity.assetResponse(svc.getApplicationContext(), req.getUrl());
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            return MainActivity.guardNavigation(svc.getApplicationContext(), req.getUrl());
        }
    }

    /** 只把页面探针日志转进 logcat（tag=XiaoManWeb），供 CI 断言 */
    private static class ProbeChromeClient extends android.webkit.WebChromeClient {
        @Override
        public boolean onConsoleMessage(android.webkit.ConsoleMessage cm) {
            String m = cm.message();
            if (m != null && m.contains("[probe]")) {
                Log.d("XiaoManWeb", m.length() > 300 ? m.substring(0, 300) : m);
            }
            return false;
        }
    }

    // ---------------- JS 桥（pet.html → 原生） ----------------

    private static class PetBridge {
        private final FloatWindowService svc;

        PetBridge(FloatWindowService svc) { this.svc = svc; }

        @JavascriptInterface
        public void ping() {
            // JS 桥回调在 JavaBridge 线程，所有窗口操作必须回主线程
            svc.handler.post(() -> {
                svc.petReady = true;
                svc.handler.removeCallbacks(svc.fallbackTask);
                if (svc.petTimedOut) svc.revivePet();
            });
        }

        @JavascriptInterface
        public void bubble(String text) {
            svc.handler.post(() -> svc.showBubble(text));
        }

        @JavascriptInterface
        public void openPanel() {
            svc.handler.post(() -> {
                svc.hideBubble();
                svc.expandPanel();
            });
        }

        @JavascriptInterface
        public void close() {
            svc.handler.post(() -> {
                svc.hideBubble();
                svc.collapsePanel();
                svc.stopSelf();
            });
        }

        @JavascriptInterface
        public void setStyle(boolean motion, boolean large, boolean gray) {
            svc.handler.post(() -> {
                svc.largeFont = large;
                svc.grayIcons = gray;
                if (svc.bubbleShown && svc.bubble != null) {
                    svc.bubble.setTextSize(TypedValue.COMPLEX_UNIT_SP, svc.largeFont ? 18 : 15);
                    svc.bubble.setBackground(svc.bubbleBackground());
                    svc.bubble.measure(
                            View.MeasureSpec.makeMeasureSpec(svc.bubbleParams.width, View.MeasureSpec.AT_MOST),
                            View.MeasureSpec.makeMeasureSpec(svc.screenH, View.MeasureSpec.AT_MOST));
                    svc.positionBubble();
                    try { svc.wm.updateViewLayout(svc.bubble, svc.bubbleParams); } catch (Exception ignored) {}
                }
            });
        }
    }

    // ---------------- 工具 ----------------

    /** API 26+ 用 TYPE_APPLICATION_OVERLAY；24-25 用旧 TYPE_PHONE */
    private int overlayType() {
        return Build.VERSION.SDK_INT >= 26
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE;
    }

    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    private int screenWidthPct(float pct) {
        return (int) (getResources().getDisplayMetrics().widthPixels * pct);
    }

    private int screenHeightPct(float pct) {
        return (int) (getResources().getDisplayMetrics().heightPixels * pct);
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        screenW = getResources().getDisplayMetrics().widthPixels;
        screenH = getResources().getDisplayMetrics().heightPixels;
        // 旋转/分屏后把浮层拉回屏内，避免桌宠永远留在屏幕外
        if (petParams != null && petWeb != null) {
            petParams.x = clampX(petParams.x, petParams.width);
            petParams.y = clampY(petParams.y, petParams.height);
            try { wm.updateViewLayout(petWeb, petParams); } catch (Exception ignored) {}
            rehostPet("config");
        }
        if (ballParams != null && ball != null) {
            ballParams.x = clampX(ballParams.x, ballParams.width);
            ballParams.y = clampY(ballParams.y, ballParams.height);
            try { wm.updateViewLayout(ball, ballParams); } catch (Exception ignored) {}
        }
        if (bubbleShown) positionBubble();
    }

    @Override
    public void onDestroy() {
        cancelDestroy();
        panelVisible = false;
        handler.removeCallbacksAndMessages(null);
        hideBubble();
        if (panelWeb != null) {
            panelWeb.loadUrl("about:blank");
            panelWeb.destroy();
            panelWeb = null;
        }
        if (panel != null && panel.getParent() != null) {
            try { wm.removeView(panel); } catch (Exception ignored) {}
        }
        destroyPet();
        if (ball != null && ball.getParent() != null) {
            try { wm.removeView(ball); } catch (Exception ignored) {}
        }
        panelShown = false;
        super.onDestroy();
    }
}
