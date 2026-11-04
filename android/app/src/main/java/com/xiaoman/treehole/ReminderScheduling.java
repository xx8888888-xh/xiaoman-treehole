package com.xiaoman.treehole;

/**
 * ReminderScheduling · 提醒调度的纯逻辑（零 Android 依赖，可直接 JUnit 覆盖）
 * ------------------------------------------------------------
 * 把"什么时候该响、能不能精确响、开机后要不要重排"这三件判断从
 * AlarmManager/SharedPreferences 里剥出来，便于单测覆盖权限分支与边界。
 */
public final class ReminderScheduling {

    /** 已到期提醒的最短补发延迟：给系统 1 秒余量，避免 RTC 时间戳刚好落在过去 */
    public static final long MIN_LEAD_MS = 1000L;
    /** 开机/时区变更后需要重排的时间窗口：未来 7 天内的提醒全部重新登记 */
    public static final long RESCHEDULE_HORIZON_MS = 7L * 24 * 3600 * 1000;

    public enum Mode { EXACT, INEXACT }

    private ReminderScheduling() {}

    /**
     * 权限分支：能调精确闹钟就用 EXACT（setExactAndAllowWhileIdle），
     * 否则降级 INEXACT（setAndAllowWhileIdle，Doze 下仍会唤醒但可能延迟数分钟）。
     * Android 12+ 需 SCHEDULE_EXACT_ALARM 被授予；12 以下无需该权限。
     */
    public static Mode modeFor(boolean canScheduleExact) {
        return canScheduleExact ? Mode.EXACT : Mode.INEXACT;
    }

    /**
     * 选下一次唤醒时间：取所有未完成提醒里最早的 at。
     * - 没有任何待办 → 返回 0，调用方应取消闹钟；
     * - 最早一条已在过去 → 返回 now + MIN_LEAD_MS（立刻补发，而不是永远等在过去）。
     */
    public static long nextTriggerAt(long[] pendingAtMs, long nowMs) {
        if (pendingAtMs == null || pendingAtMs.length == 0) return 0L;
        long min = Long.MAX_VALUE;
        for (long at : pendingAtMs) {
            if (at < min) min = at;
        }
        return Math.max(nowMs + MIN_LEAD_MS, min);
    }

    /**
     * 只选"还没到期"的最早一条（at > now）。
     * 为什么需要这个变体：闹钟刚响过、而到期项还没被处理（页面在前台自己投递，
     * 或通知权限缺失），此时若仍用 nextTriggerAt 会得到 now+1s，闹钟被自己
     * 每秒重排一次（空转耗电）。响过之后只补登记未来项即可。
     */
    public static long nextFutureAt(long[] pendingAtMs, long nowMs) {
        if (pendingAtMs == null) return 0L;
        long min = Long.MAX_VALUE;
        for (long at : pendingAtMs) {
            if (at > nowMs && at < min) min = at;
        }
        return min == Long.MAX_VALUE ? 0L : min;
    }

    /** 是否已到期 */
    public static boolean isDue(long atMs, long nowMs) {
        return atMs <= nowMs;
    }

    /** 开机/时区变更后是否需要在窗口内重排（过期或 7 天内都算） */
    public static boolean needsReschedule(long atMs, long nowMs) {
        return atMs <= nowMs || (atMs - nowMs) <= RESCHEDULE_HORIZON_MS;
    }

    /** 心跳间隔归一化：默认 60s，允许 30s–30min，越界收敛而不是报错 */
    public static long normalizeHeartbeatMs(long raw) {
        if (raw <= 0) return 60_000L;
        return Math.min(1_800_000L, Math.max(30_000L, raw));
    }
}
