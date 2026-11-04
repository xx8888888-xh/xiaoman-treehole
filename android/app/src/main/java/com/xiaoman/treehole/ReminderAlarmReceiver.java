package com.xiaoman.treehole;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

import java.util.List;

/**
 * ReminderAlarmReceiver · 闹钟到点 / 开机 / 时区变更的统一入口
 * ------------------------------------------------------------
 * 投递规则（刻意如此，避免"同一条提醒响两遍"）：
 *  - App 在前台 → 不弹通知，交给页面内的心跳投递（用户正在看着聊天）；
 *  - App 不在前台（锁屏 / 退后台 / 进程被杀）→ 弹通知并记为"原生已投递"；
 *    下次页面打开时 JS 会把这条直接标完成，不再补催。
 */
public final class ReminderAlarmReceiver extends BroadcastReceiver {

    private static final String TAG = "ReminderAlarm";
    public static final String ACTION_SNOOZE = "com.xiaoman.treehole.REMINDER_SNOOZE";
    public static final String EXTRA_ID = "reminder_id";
    static final long SNOOZE_MS = 10 * 60 * 1000L;

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? null : intent.getAction();
        if (action == null) return;

        if (ACTION_SNOOZE.equals(action)) {
            String id = intent.getStringExtra(EXTRA_ID);
            ReminderStore.snooze(context, id, SNOOZE_MS);
            ReminderNotifications.cancel(context, id);
            ReminderAlarmScheduler.reschedule(context);
            return;
        }

        if (ReminderAlarmScheduler.ACTION_ALARM.equals(action)) {
            handleAlarm(context);
            return;
        }

        // 开机 / 时区或系统时间变更 / 应用更新：把未来窗口内的提醒全部重排
        if (Intent.ACTION_BOOT_COMPLETED.equals(action)
                || Intent.ACTION_TIME_CHANGED.equals(action)
                || Intent.ACTION_TIMEZONE_CHANGED.equals(action)
                || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            ReminderAlarmScheduler.reschedule(context);
            Log.d(TAG, "rescheduled after " + action);
        }
    }

    private void handleAlarm(Context context) {
        long now = System.currentTimeMillis();
        List<ReminderItem> due = ReminderStore.due(context, now);
        // 主界面或悬浮迷你窗可见时页面是"活的"，由页面内投递；只看 Activity 会把
        // 悬浮窗场景误判成后台而重复弹通知（同一事实两个通道）
        boolean foreground = MainActivity.isAnyUiVisible();
        Log.d(TAG, "alarm fired due=" + due.size() + " uiVisible=" + foreground);

        if (!foreground && !due.isEmpty()) {
            if (ReminderNotifications.canNotify(context)) {
                ReminderNotifications.ensureChannel(context);
                for (ReminderItem item : due) {
                    ReminderNotifications.show(context, item);
                    ReminderStore.markDelivered(context, item.id);
                }
            } else {
                // 没给通知权限：不标记已投递，等下次进 App 时由页面内提醒补上，绝不静默吞掉
                Log.w(TAG, "notification permission missing; " + due.size() + " reminder(s) kept pending");
            }
        }
        // 响过之后只补登记未来项：到期但尚未处理的（前台自投递 / 无通知权限）不排 now+1s，
        // 否则闹钟会被自己每秒重排（空转耗电）。页面 markDone 后 sync 会重新整表对齐。
        ReminderAlarmScheduler.rescheduleFuture(context);
    }

    /** 供调试/验收：直接清掉所有提醒通知 */
    static void clearAll(Context ctx) {
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) nm.cancelAll();
    }
}
