package com.xiaoman.treehole;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebSettings;
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
 *  - 收起按钮：迷你窗收回悬浮球（WebView 实例保留，再点秒开）
 *  - 关闭按钮：移除浮层并停掉前台服务
 * 权限：SYSTEM_ALERT_WINDOW 特殊权限，由 MainActivity 桥引导用户到系统设置页授权。
 * FGS：API 34 起必须声明 specialUse 类型（Manifest 已配 PROPERTY_SPECIAL_USE_FGS_SUBTYPE）。
 */
public class FloatWindowService extends Service {

    public static final String ACTION_SHOW = "com.xiaoman.treehole.float.SHOW";
    public static final String ACTION_HIDE = "com.xiaoman.treehole.float.HIDE";

    private static final String CHANNEL_ID = "float_window";
    private static final int NOTIF_ID = 42;
    private static final int BALL_SIZE_DP = 60;
    private static final int TAP_SLOP_PX = 20;   // 位移小于此值视为点击（而非拖动）

    private WindowManager wm;
    private View ball;
    private LinearLayout panel;
    private WebView panelWeb;
    private WindowManager.LayoutParams ballParams;
    private WindowManager.LayoutParams panelParams;
    private boolean panelShown = false;
    private int screenW;   // 拖动松手贴边吸附用

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

    // ---------------- 前台通知 ----------------

    private void startForegroundNotify() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "小满悬浮窗",
                    NotificationManager.IMPORTANCE_MIN);
            ch.setDescription("悬浮球与迷你对话窗的后台保活通知");
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        PendingIntent pi = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        Notification notif = b
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("小满在身边")
                .setContentText("点按回到小满树洞")
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
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
        bg.setColor(Color.parseColor("#F7A8B8")); // 品牌粉
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
        if (panel == null) buildPanel();
        try {
            wm.addView(panel, panelParams);
            panelShown = true;
            // 迷你窗需要接收输入（弹键盘）：去掉 NOT_FOCUSABLE
            panelParams.flags &= ~WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
            wm.updateViewLayout(panel, panelParams);
            // 球先藏起来，避免压在面板角上
            ball.setVisibility(View.GONE);
        } catch (WindowManager.BadTokenException e) {
            stopSelf();
        }
    }

    private void collapsePanel() {
        if (!panelShown || panel == null) return;
        try { wm.removeView(panel); } catch (Exception ignored) {}
        panelShown = false;
        // 收起后必须恢复 NOT_FOCUSABLE，否则悬浮球会挡住全屏触摸
        if (ball != null && ball.getParent() != null) {
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
        bg.setColor(Color.parseColor("#FFF9F2")); // 主题奶油色
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
        panelWeb.setWebViewClient(new WebViewClient());
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
        // 加载迷你模式入口（与主壳同源 asset 路径；?float=1 触发 web 端小屏布局+跳过 Live2D）
        panelWeb.loadUrl("file:///android_asset/www/index.html?float=1");
        // 注：迷你窗不挂 AndroidTTS 桥（主 WebView 独占 TTS 引擎）；web 端检测不到桥时
        // 自动降级 speechSynthesis，语音同样可用，主界面语音不受双实例影响
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

    @Override
    public void onDestroy() {
        if (panelWeb != null) {
            panelWeb.loadUrl("about:blank");
            panelWeb.destroy();
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
