package com.xiaoman.treehole;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;
import android.webkit.JavascriptInterface;

import org.json.JSONObject;

/**
 * AndroidHostBridge · 页面与原生宿主的能力桥（JS 标识 AndroidHost）
 * ------------------------------------------------------------
 * 与 AndroidTTS 分开命名，职责是"后台/提醒/语音策略"这类系统能力：
 *  提醒镜像同步、通知与精确闹钟权限、Doze 白名单引导（只引导不静默申请）、
 *  自动朗读开关、心跳间隔配置、后台冻结通知。
 * 所有方法都在 JS 线程被调用；涉及 UI/权限的一律 post 回主线程。
 */
public final class AndroidHostBridge {

    static final String NAME = "AndroidHost";
    private static final String TAG = "AndroidHost";
    private static final String PREFS = "treehole_host_prefs";
    private static final String KEY_AUTO_READ = "tts_auto_read";
    private static final String KEY_HEARTBEAT_MS = "heartbeat_interval_ms";
    private static final int REQ_NOTIFICATIONS = 201;

    private final Context appCtx;
    private final Activity activity;   // 悬浮窗场景没有 Activity，权限请求跳过

    AndroidHostBridge(Context ctx, Activity activity) {
        this.appCtx = ctx.getApplicationContext();
        this.activity = activity;
    }

    private SharedPreferences prefs() {
        return appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // ---------------- 提醒 ----------------

    /** JS 每次增删改提醒后推整表过来；返回宿主状态（供设置页显示真实可用性） */
    @JavascriptInterface
    public String syncReminders(String json) {
        try {
            ReminderStore.save(appCtx, json);
            ReminderAlarmScheduler.reschedule(appCtx);
            // 第一次真的存在待办提醒时才申请通知权限（上下文合理，不在启动时骚扰）
            if (ReminderStore.pendingAts(appCtx).length > 0 && !isNotificationGranted()) {
                ensureNotificationPermissionOnce();
            }
        } catch (Exception e) {
            Log.w(TAG, "syncReminders: " + e.getMessage());
        }
        return status();
    }

    private static final String KEY_NOTIF_ASKED = "notif_permission_asked";

    /** 只在用户第一次建提醒时弹一次系统授权框；被拒绝后不再反复弹（可去设置页手动开） */
    private void ensureNotificationPermissionOnce() {
        if (activity == null || Build.VERSION.SDK_INT < 33) return;
        if (prefs().getBoolean(KEY_NOTIF_ASKED, false)) return;
        prefs().edit().putBoolean(KEY_NOTIF_ASKED, true).apply();
        requestNotificationPermission();
    }

    /** 取走"原生已投递"的提醒 id（数组 JSON），页面据此直接标完成 */
    @JavascriptInterface
    public String drainDelivered() {
        try {
            return ReminderStore.drainDelivered(appCtx);
        } catch (Exception e) {
            return "[]";
        }
    }

    /** 宿主能力现状：权限/闹钟/白名单/自动朗读/心跳间隔，供设置页如实展示 */
    @JavascriptInterface
    public String status() {
        JSONObject o = new JSONObject();
        try {
            o.put("notifications", isNotificationGranted());
            o.put("exactAlarm", isExactAlarmAllowed());
            o.put("ignoringBattery", isIgnoringBatteryOptimizations());
            o.put("autoRead", isAutoRead());
            o.put("heartbeatMs", getHeartbeatIntervalMs());
            o.put("pendingReminders", ReminderStore.pendingAts(appCtx).length);
        } catch (Exception ignored) { }
        return o.toString();
    }

    // ---------------- 权限 ----------------

    @JavascriptInterface
    public boolean isNotificationGranted() {
        return ReminderNotifications.canNotify(appCtx);
    }

    @JavascriptInterface
    public boolean isExactAlarmAllowed() {
        return ReminderAlarmScheduler.canScheduleExact(appCtx);
    }

    /** 申请通知权限（Android 13+ 弹系统框；12 及以下直接返回系统当前开关） */
    @JavascriptInterface
    public void requestNotificationPermission() {
        if (activity == null || Build.VERSION.SDK_INT < 33) return;
        activity.runOnUiThread(() -> {
            try {
                activity.requestPermissions(
                        new String[]{"android.permission.POST_NOTIFICATIONS"}, REQ_NOTIFICATIONS);
            } catch (Exception e) {
                Log.w(TAG, "request notifications failed: " + e.getMessage());
            }
        });
    }

    /** 引导到"精确闹钟"授权页（Android 12+）；用户拒绝则自动退化为不精确闹钟 */
    @JavascriptInterface
    public void requestExactAlarmPermission() {
        if (Build.VERSION.SDK_INT < 31) return;
        openSystemPage(new Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                Uri.parse("package:" + appCtx.getPackageName())));
    }

    /** Doze 白名单只做引导：跳系统电池优化列表，不静默申请 REQUEST_IGNORE_BATTERY_OPTIMIZATIONS */
    @JavascriptInterface
    public void openBatteryOptimizationSettings() {
        openSystemPage(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
    }

    @JavascriptInterface
    public boolean isIgnoringBatteryOptimizations() {
        try {
            PowerManager pm = (PowerManager) appCtx.getSystemService(Context.POWER_SERVICE);
            return pm != null && pm.isIgnoringBatteryOptimizations(appCtx.getPackageName());
        } catch (Exception e) {
            return false;
        }
    }

    private void openSystemPage(Intent intent) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            appCtx.startActivity(intent);
        } catch (Exception e) {
            Log.w(TAG, "open settings failed: " + e.getMessage());
        }
    }

    // ---------------- 语音策略 ----------------

    /**
     * 自动朗读开关（默认关 = 点击才朗读）。
     * 注入脚本每次朗读前会读它；设为 true 才恢复"每条回复自动朗读"的老行为。
     */
    @JavascriptInterface
    public boolean isAutoRead() {
        return prefs().getBoolean(KEY_AUTO_READ, false);
    }

    @JavascriptInterface
    public void setAutoRead(boolean on) {
        prefs().edit().putBoolean(KEY_AUTO_READ, on).apply();
    }

    // ---------------- 心跳频率 ----------------

    @JavascriptInterface
    public long getHeartbeatIntervalMs() {
        return ReminderScheduling.normalizeHeartbeatMs(prefs().getLong(KEY_HEARTBEAT_MS, 60_000L));
    }

    /** 心跳间隔可配置（30s–30min），由设置页或调试调用 */
    @JavascriptInterface
    public void setHeartbeatIntervalMs(double ms) {
        prefs().edit().putLong(KEY_HEARTBEAT_MS,
                ReminderScheduling.normalizeHeartbeatMs((long) ms)).apply();
    }

    // ---------------- 生命周期回执（便于 logcat 验收） ----------------

    @JavascriptInterface
    public void onPageHidden() {
        Log.d("XiaoManWeb", "[probe] host pause");
    }

    @JavascriptInterface
    public void onPageVisible() {
        Log.d("XiaoManWeb", "[probe] host resume");
    }
}
