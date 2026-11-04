package com.xiaoman.treehole;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * ReminderAlarmScheduler · 把下一次提醒交给系统闹钟
 * ------------------------------------------------------------
 * - 有精确闹钟权限 → setExactAndAllowWhileIdle（Doze 下也准时）
 * - 没有（Android 12+ 用户拒绝）→ setAndAllowWhileIdle（仍会唤醒，但可能延迟）
 * - 单 PendingIntent 复用（只登记"最早的一条"），触发后由 Receiver 重新登记下一条，
 *   这样常驻闹钟数恒为 1，不随提醒条数增长。
 */
public final class ReminderAlarmScheduler {

    private static final String TAG = "ReminderAlarm";
    static final int REQUEST_CODE = 1001;
    static final String ACTION_ALARM = "com.xiaoman.treehole.REMINDER_ALARM";

    private ReminderAlarmScheduler() {}

    static PendingIntent alarmIntent(Context ctx) {
        Intent i = new Intent(ctx, ReminderAlarmReceiver.class).setAction(ACTION_ALARM);
        return PendingIntent.getBroadcast(ctx, REQUEST_CODE, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /** Android 12+ 精确闹钟权限；12 以下恒为 true（无需申请） */
    public static boolean canScheduleExact(Context ctx) {
        if (Build.VERSION.SDK_INT < 31) return true;
        try {
            AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
            return am != null && am.canScheduleExactAlarms();
        } catch (Exception e) {
            return false;
        }
    }

    /** 按当前提醒表重排下一条；无待办则取消 */
    public static void reschedule(Context ctx) {
        try {
            AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;
            PendingIntent pi = alarmIntent(ctx);
            am.cancel(pi);

            long at = ReminderScheduling.nextTriggerAt(
                    ReminderStore.pendingAts(ctx), System.currentTimeMillis());
            if (at <= 0) return;

            armAt(am, ctx, at, pi);
            Log.d(TAG, "scheduled in " + (at - System.currentTimeMillis()) + "ms");
        } catch (SecurityException e) {
            // 权限在设置里被撤销（Android 12+）：退化为不精确，绝不让提醒静默消失
            Log.w(TAG, "exact alarm denied, fallback inexact: " + e.getMessage());
            try {
                AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
                long at = ReminderScheduling.nextTriggerAt(
                        ReminderStore.pendingAts(ctx), System.currentTimeMillis());
                if (am != null && at > 0) {
                    am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, alarmIntent(ctx));
                }
            } catch (Exception ignored) { }
        } catch (Exception e) {
            Log.w(TAG, "reschedule failed: " + e.getMessage());
        }
    }

    /**
     * 闹钟响过之后的补登记：只排"还没到期"的项。
     * 不能再用 reschedule()：已到期但还没被处理（前台页面自投递 / 通知权限缺失）的项
     * 会被算成 now+1s，导致闹钟每秒重排一次空转耗电。
     */
    static void rescheduleFuture(Context ctx) {
        try {
            AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;
            PendingIntent pi = alarmIntent(ctx);
            am.cancel(pi);

            long at = ReminderScheduling.nextFutureAt(
                    ReminderStore.pendingAts(ctx), System.currentTimeMillis());
            if (at <= 0) return;                 // 只剩到期未处理项：等页面 markDone 后的 sync 再排

            armAt(am, ctx, at, pi);
            Log.d(TAG, "future scheduled in " + (at - System.currentTimeMillis()) + "ms");
        } catch (SecurityException e) {
            Log.w(TAG, "exact alarm denied, future inexact fallback: " + e.getMessage());
            try {
                AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
                long at = ReminderScheduling.nextFutureAt(
                        ReminderStore.pendingAts(ctx), System.currentTimeMillis());
                if (am != null && at > 0) {
                    am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, alarmIntent(ctx));
                }
            } catch (Exception ignored) { }
        } catch (Exception e) {
            Log.w(TAG, "rescheduleFuture failed: " + e.getMessage());
        }
    }

    /** 精确/非精确统一入口（权限在调用时实时判断） */
    private static void armAt(AlarmManager am, Context ctx, long at, PendingIntent pi) {
        ReminderScheduling.Mode mode = ReminderScheduling.modeFor(canScheduleExact(ctx));
        if (mode == ReminderScheduling.Mode.EXACT) {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
        } else {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
        }
    }

    public static void cancel(Context ctx) {
        try {
            AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
            if (am != null) am.cancel(alarmIntent(ctx));
        } catch (Exception ignored) { }
    }
}
