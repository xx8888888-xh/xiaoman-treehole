package com.xiaoman.treehole;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import java.util.Locale;

/**
 * ReminderNotifications · 提醒通知渠道与通知构造
 * ------------------------------------------------------------
 * - 独立渠道 reminder_attention（IMPORTANCE_HIGH，锁屏可见、可横幅）
 * - 点击 → 打开 App；「稍后 10 分钟」→ 广播回 Receiver 推迟该条
 * - 通知 id 由提醒 id 派生，同一条提醒只会有一条通知（不会堆叠重复）
 */
public final class ReminderNotifications {

    static final String CHANNEL_ID = "reminder_attention";
    private static final String CHANNEL_NAME = "提醒";

    private ReminderNotifications() {}

    public static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return;
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, CHANNEL_NAME,
                NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription("你让小满记下的事，到点会在这里提醒你");
        ch.setShowBadge(true);
        ch.enableVibration(true);
        nm.createNotificationChannel(ch);
    }

    public static boolean canNotify(Context ctx) {
        if (Build.VERSION.SDK_INT < 33) {
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            return nm != null && nm.areNotificationsEnabled();
        }
        // Android 13+ 由 POST_NOTIFICATIONS 运行时权限决定
        return NotificationManagerCompatBridge.notificationsAllowed(ctx);
    }

    private static int notifId(String reminderId) {
        return 2000 + Math.abs((reminderId == null ? "" : reminderId).hashCode() % 10000);
    }

    /** 弹一条提醒通知（调用前请先 ensureChannel 并确认有权限） */
    public static void show(Context ctx, ReminderItem item) {
        ensureChannel(ctx);
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;

        Intent open = new Intent(ctx, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra("reminder_id", item.id);
        PendingIntent openPi = PendingIntent.getActivity(ctx, notifId(item.id), open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent snooze = new Intent(ctx, ReminderAlarmReceiver.class)
                .setAction(ReminderAlarmReceiver.ACTION_SNOOZE)
                .putExtra(ReminderAlarmReceiver.EXTRA_ID, item.id);
        PendingIntent snoozePi = PendingIntent.getBroadcast(ctx, notifId(item.id) + 1, snooze,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(ctx, CHANNEL_ID)
                : new Notification.Builder(ctx);
        b.setSmallIcon(android.R.drawable.ic_popup_reminder)
                .setContentTitle("小满提醒")
                .setContentText(item.text)
                .setStyle(new Notification.BigTextStyle().bigText(item.text))
                .setContentIntent(openPi)
                .setAutoCancel(true)
                .setShowWhen(true)
                .setCategory(Notification.CATEGORY_REMINDER)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .addAction(new Notification.Action.Builder(
                        android.R.drawable.ic_popup_reminder, "稍后 10 分钟", snoozePi).build())
                .setColor(0xFFF7A8B8);
        nm.notify(notifId(item.id), b.build());
    }

    public static void cancel(Context ctx, String reminderId) {
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) nm.cancel(notifId(reminderId));
    }

    /** 通知权限的跨版本判定（Android 13 起看运行时权限，之前看系统开关） */
    static final class NotificationManagerCompatBridge {
        static boolean notificationsAllowed(Context ctx) {
            if (Build.VERSION.SDK_INT >= 33) {
                return ctx.checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                        == android.content.pm.PackageManager.PERMISSION_GRANTED;
            }
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            return nm != null && nm.areNotificationsEnabled();
        }
    }

    /** 通知渠道被用户关掉时的兜底文案（给设置页/日志用） */
    public static String channelState(Context ctx) {
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null || Build.VERSION.SDK_INT < 26) return "unknown";
        NotificationChannel ch = nm.getNotificationChannel(CHANNEL_ID);
        if (ch == null) return "absent";
        int importance = ch.getImportance();
        return String.format(Locale.ROOT, "importance=%d", importance);
    }
}
