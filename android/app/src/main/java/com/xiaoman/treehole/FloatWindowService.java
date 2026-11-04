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
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * FloatWindowService · 小满树洞系统级悬浮窗（纯 Java，零依赖，与壳工程同风格）
 * ------------------------------------------------------------
 * 能力与交互（验收点逐一对应）：
 *  - 悬浮球：60dp 圆形「满」字，全屏可拖动（跟随手指），单击（位移<20px）展开迷你窗
 *  - 迷你窗：标题栏（收起/关闭）+ WebView（index.html?float=1 迷你模式，复用全部对话引擎）
 *  - 迷你窗内：可正常输入/发送/收语音，回复实时渲染（web 端 float-mode 小屏布局）
 *  - 收起按钮：迷你窗收回悬浮球。**收起 = 真暂停**：页面定时器（心跳/提醒轮询/打字机）
 *    全部冻结，15 秒后销毁 WebView 释放内存；再点按需重建（历史在 IndexedDB，会话不丢）
 *  - 关闭按钮：移除浮层并停掉前台服务
 * 权限：SYSTEM_ALERT_WINDOW 特殊权限，由 MainActivity 桥引导用户到系统设置页授权。
 * FGS：API 34 起必须声明 specialUse 类型（Manifest 已配 PROPERTY_SPECIAL_USE_FGS_SUBTYPE）。
 * 前台服务最小化：静默、低优先级、无常驻动画；只有"悬浮球存在"这一件事需要保活。
 */
public class FloatWindowService extends Service {

    public static final String ACTION_SHOW = "com.xiaoman.treehole.float.SHOW";
    public static final String ACTION_HIDE = "com.xiaoman.treehole.float.HIDE";

    private static final String CHANNEL_ID = "float_window";
    private static final int NOTIF_ID = 42;
    private static final int BALL_SIZE_DP = 60;
    private static final int TAP_SLOP_PX = 20;   // 位移小于此值视为点击（而非拖动）
    private static final long PANEL_DESTROY_DELAY_MS = 15_000L;

    /** 迷你窗是否展开（与 MainActivity.activityVisible 一起决定"是否有界面可见"） */
    private static volatile boolean panelVisible = false;

    static boolean isPanelVisible() {
        return panelVisible;
    }

    private WindowManager wm;
    private View ball;
    private LinearLayout panel;
    private WebView panelWeb;
    private WindowManager.LayoutParams ballParams;
    private WindowManager.LayoutParams panelParams;
    private boolean panelShown = false;
    private int screenW;   // 拖动松手贴边吸附用
    private final Handler ui = new Handler(Looper.getMainLooper());
    private Runnable destroyTask;

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        wm = (WindowManager) getSystemService(WINDOW_SERVICE);
        screenW = getResources().getDisplayMetrics().widthPixels;
        startForegroundNotify();
        showBall();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_HIDE.equals(action)) {
            collapsePanel();
            stopSelf();
            return START_NOT_STICKY;
        }
        // ACTION_SHOW / 空动作：悬浮球已由 onCreate 挂出；若曾被销毁（START_NOT_STICKY 后重建）兜底重挂
        if (ball == null || ball.getParent() == null) showBall();
        return START_STICKY;
    }

    // ---------------- 前台通知（最小化） ----------------

    private void startForegroundNotify() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "小满悬浮窗",
                    NotificationManager.IMPORTANCE_MIN);      // 最低优先级：不响、不横幅、无角标
            ch.setDescription("悬浮球与迷你对话窗的后台保活通知");
            ch.setShowBadge(false);
            ch.setSound(null, null);
            ch.enableVibration(false);
            ch.enableLights(false);
            nm.createNotificationChannel(ch);
        }
        PendingIntent pi = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        b.setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("小满在身边")
                .setContentText("点按回到小满树洞")
                .setContentIntent(pi)
                .setOngoing(true)
                .setShowWhen(false)
                .setOnlyAlertOnce(true);
        // 渠道已是 IMPORTANCE_MIN 且无声音/振动；不再额外设 setSilent（API 26 以下无此方法）
        Notification notif = b.build();
        // API 34 起必须带 FGS 类型参数；Manifest 已声明 specialUse
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIF_ID, notif,
                    android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIF_ID, notif);
        }
    }

    // ---------------- 悬浮球 ----------------

    private void showBall() {
        int size = dp(BALL_SIZE_DP);
        TextView tv = new TextView(this);
        tv.setText("满");
        tv.setTextColor(Color.WHITE);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
        tv.setGravity(Gravity.CENTER);
        tv.setContentDescription("小满悬浮球");   // TalkBack 无障碍：双击展开迷你对话窗
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.OVAL);
        bg.setColor(themeColor(R.color.ball_bg, "#F7A8B8")); // 品牌粉（深色模式由资源给出）
        bg.setStroke(dp(2), Color.WHITE);
        tv.setBackground(bg);

        ballParams = new WindowManager.LayoutParams(
                size, size,
                overlayType(),
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        ballParams.gravity = Gravity.TOP | Gravity.START;
        ballParams.x = 40;
        ballParams.y = 260;

        tv.setOnTouchListener(new DragTapListener());
        ball = tv;
        try {
            wm.addView(ball, ballParams);
        } catch (WindowManager.BadTokenException e) {
            // 未授权或窗口 token 失效：静默退出，避免崩溃
            stopSelf();
        }
    }

    /** API 26+ 用 TYPE_APPLICATION_OVERLAY；24-25 用旧 TYPE_PHONE */
    private int overlayType() {
        return Build.VERSION.SDK_INT >= 26
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE;
    }

    /** 拖动 + 点击识别：down 记起点，move 跟手，up 位移小于阈值视为单击 */
    private class DragTapListener implements View.OnTouchListener {
        float downRawX, downRawY;      // 手指按下时的屏幕坐标
        int startBallX, startBallY;    // 按下时球的位置
        boolean moved = false;

        @Override
        public boolean onTouch(View v, MotionEvent event) {
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downRawX = event.getRawX();
                    downRawY = event.getRawY();
                    startBallX = ballParams.x;
                    startBallY = ballParams.y;
                    moved = false;
                    return true;
                case MotionEvent.ACTION_MOVE: {
                    float dx = event.getRawX() - downRawX;
                    float dy = event.getRawY() - downRawY;
                    if (Math.abs(dx) > TAP_SLOP_PX || Math.abs(dy) > TAP_SLOP_PX) moved = true;
                    ballParams.x = startBallX + (int) dx;
                    ballParams.y = startBallY + (int) dy;
                    try { wm.updateViewLayout(ball, ballParams); } catch (Exception ignored) {}
                    return true;
                }
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (moved) snapToEdge();  // 拖动松手：贴边吸附
                    else togglePanel();       // 单击：展开/收起迷你窗
                    return true;
                default:
                    return false;
            }
        }
    }

    /** 拖动松手贴边吸附：球心在屏幕左半 → 吸左缘，右半 → 吸右缘（y 不变） */
    private void snapToEdge() {
        int size = dp(BALL_SIZE_DP);
        int centerX = ballParams.x + size / 2;
        ballParams.x = centerX < screenW / 2 ? 0 : screenW - size;
        try { wm.updateViewLayout(ball, ballParams); } catch (Exception ignored) {}
    }

    // ---------------- 迷你对话窗 ----------------

    private void togglePanel() {
        if (panelShown) collapsePanel(); else expandPanel();
    }

    private void expandPanel() {
        if (panelShown) return;
        if (panel == null) buildPanel();          // 上次收起后已销毁 → 现场重建
        cancelDestroy();
        try {
            wm.addView(panel, panelParams);
            panelShown = true;
            panelVisible = true;
            // 迷你窗需要接收输入（弹键盘）：去掉 NOT_FOCUSABLE
            panelParams.flags &= ~WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
            wm.updateViewLayout(panel, panelParams);
            // 球先藏起来，避免压在面板角上
            ball.setVisibility(View.GONE);
            PageLifecycle.onHostResume(panelWeb);   // 页面计时器按剩余时长重新武装
        } catch (WindowManager.BadTokenException e) {
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
        // 收起后必须恢复 NOT_FOCUSABLE，否则悬浮球会挡住全屏触摸
        if (ball != null && ball.getParent() != null) {
            ball.setVisibility(View.VISIBLE);
            try { wm.updateViewLayout(ball, ballParams); } catch (Exception ignored) {}
        }
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
        ui.postDelayed(destroyTask, PANEL_DESTROY_DELAY_MS);
    }

    private void cancelDestroy() {
        if (destroyTask != null) {
            ui.removeCallbacks(destroyTask);
            destroyTask = null;
        }
    }

    private void buildPanel() {
        int w = Math.min(screenWidthPct(0.92f), dp(340));
        int h = Math.min(screenHeightPct(0.62f), dp(460));

        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(18));
        bg.setColor(themeColor(R.color.panel_bg, "#FFF9F2"));
        bg.setStroke(dp(1), themeColor(R.color.panel_stroke, "#F7A8B8"));
        panel.setBackground(bg);
        panel.setElevation(dp(6));

        // ---- 标题栏：标题 + 收起 + 关闭 ----
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(14), dp(8), dp(8), dp(8));

        TextView title = new TextView(this);
        title.setText("小满树洞 · 迷你窗");
        title.setTextColor(themeColor(R.color.panel_text, "#5B4A43"));
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        bar.addView(title, tp);

        bar.addView(miniBtn("收起", v -> collapsePanel()));
        bar.addView(miniBtn("关闭", v -> stopSelf()));

        // ---- WebView：float=1 迷你模式（复用全部对话/记忆/提醒引擎） ----
        panelWeb = new WebView(this);
        WebShell.configureSettings(panelWeb, this);
        // 与 MainActivity 共用同一套 asset 映射与导航守卫：同 Origin 才能共享 localStorage/IndexedDB
        panelWeb.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                return MainActivity.assetResponse(getApplicationContext(), req.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return MainActivity.guardNavigation(getApplicationContext(), req.getUrl());
            }
        });
        // console → logcat 桥（与主壳一致）：[probe] 行转发 tag=XiaoManWeb，CI 可验证迷你窗内聊天链路
        panelWeb.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage cm) {
                String m = cm.message();
                if (m != null && m.contains("[probe]")) {
                    android.util.Log.d("XiaoManWeb", m.length() > 300 ? m.substring(0, 300) : m);
                }
                return false;
            }
        });
        // 加载迷你模式入口：与主界面同一份 assets 且同一 Origin（https://appassets…）。
        // 旧实现用 file:///android_asset/www/index.html —— file:// 的 Origin 为 "null"，
        // localStorage/IndexedDB 与主界面分裂，悬浮窗里看不到主界面的记忆/历史。
        panelWeb.addJavascriptInterface(new NetBridge(panelWeb), "AndroidNet");
        // 提醒镜像同样从迷你窗同步（两边同 Origin，写的是同一份 localStorage）
        panelWeb.addJavascriptInterface(new AndroidHostBridge(this, null), AndroidHostBridge.NAME);
        panelWeb.loadUrl(MainActivity.BASE_URL + "?float=1");
        // 注：迷你窗不挂 AndroidTTS 桥（主 WebView 独占 TTS 引擎）；web 端检测不到桥时
        // 自动降级 speechSynthesis，语音同样可用，主界面语音不受双实例影响
        try { panelWeb.setBackgroundColor(themeColor(R.color.panel_bg, "#FFF9F2")); } catch (Exception ignored) { }
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
        b.setTextColor(themeColor(R.color.panel_text, "#5B4A43"));
        b.setAllCaps(false);
        b.setMinWidth(dp(56));
        b.setMinimumWidth(dp(56));
        b.setMinHeight(dp(44));                 // 触控热区 44dp（原 36 偏小，易误触）
        b.setMinimumHeight(dp(44));
        b.setPadding(dp(10), dp(4), dp(10), dp(4));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(14));
        bg.setColor(themeColor(R.color.panel_btn_bg, "#FFE3E9"));
        b.setBackground(bg);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(6), 0, 0, 0);
        b.setLayoutParams(lp);
        b.setOnClickListener(onClick);
        return b;
    }

    // ---------------- 工具 ----------------

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

    /** 取主题色（深色模式走 values-night），失败时回退硬编码值 */
    private int themeColor(int resId, String fallback) {
        try { return getColor(resId); } catch (Exception ignored) { }
        try { return Color.parseColor(fallback); } catch (Exception e) { return Color.GRAY; }
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        screenW = getResources().getDisplayMetrics().widthPixels;
        if (panelParams != null && panelShown) {
            // 横竖屏切换：迷你窗按新屏幕重算尺寸，避免超出屏幕/被刘海裁切
            panelParams.width = Math.min(screenWidthPct(0.92f), dp(340));
            panelParams.height = Math.min(screenHeightPct(0.62f), dp(460));
            try { wm.updateViewLayout(panel, panelParams); } catch (Exception ignored) {}
        }
        WebShell.applyFontScale(panelWeb, this);
    }

    @Override
    public void onDestroy() {
        cancelDestroy();
        panelVisible = false;
        if (panelWeb != null) {
            try { panelWeb.loadUrl("about:blank"); } catch (Exception ignored) { }
            try { panelWeb.destroy(); } catch (Exception ignored) { }
            panelWeb = null;
        }
        if (panel != null && panel.getParent() != null) {
            try { wm.removeView(panel); } catch (Exception ignored) {}
        }
        if (ball != null && ball.getParent() != null) {
            try { wm.removeView(ball); } catch (Exception ignored) {}
        }
        panelShown = false;
        super.onDestroy();
    }
}
