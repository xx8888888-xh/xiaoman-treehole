package com.xiaoman.treehole;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * ReminderSchedulingTest · 提醒调度与权限分支的单元测试
 * ------------------------------------------------------------
 * 覆盖 P0 要求的两块"容易只写不验"的逻辑：
 *  1) 权限分支：无精确闹钟权限时必须退化 INEXACT（不能抛/不能静默丢提醒）；
 *  2) 时间边界：无待办要取消、过期要立即补发、多条只取最早、开机重排窗口。
 * 纯 JUnit4，不依赖 Android 运行时（gradlew testDebugUnitTest 可离线跑）。
 */
public class ReminderSchedulingTest {

    private static final long NOW = 1_700_000_000_000L;

    // ---------- 权限分支 ----------

    @Test
    public void exactGranted_usesExactMode() {
        assertEquals(ReminderScheduling.Mode.EXACT, ReminderScheduling.modeFor(true));
    }

    @Test
    public void exactDenied_degradesToInexact() {
        // Android 12+ 用户可在系统里撤销 SCHEDULE_EXACT_ALARM：必须退化而不是崩/丢弃
        assertEquals(ReminderScheduling.Mode.INEXACT, ReminderScheduling.modeFor(false));
    }

    // ---------- 下一次唤醒时间 ----------

    @Test
    public void noPending_cancelsAlarm() {
        assertEquals(0L, ReminderScheduling.nextTriggerAt(new long[0], NOW));
        assertEquals(0L, ReminderScheduling.nextTriggerAt(null, NOW));
    }

    @Test
    public void picksEarliestAmongMany() {
        long[] ats = {NOW + 3600_000L, NOW + 60_000L, NOW + 86_400_000L};
        assertEquals(NOW + 60_000L, ReminderScheduling.nextTriggerAt(ats, NOW));
    }

    @Test
    public void overdue_firesImmediatelyWithLead() {
        // 过期很久也不能"等在过去"：立刻补发（1s 余量避开 RTC 竞态）
        long[] ats = {NOW - 3_600_000L};
        assertEquals(NOW + ReminderScheduling.MIN_LEAD_MS,
                ReminderScheduling.nextTriggerAt(ats, NOW));
    }

    @Test
    public void overduePlusFuture_picksOverdueBranch() {
        long[] ats = {NOW - 60_000L, NOW + 600_000L};
        assertEquals(NOW + ReminderScheduling.MIN_LEAD_MS,
                ReminderScheduling.nextTriggerAt(ats, NOW));
    }

    // ---------- 响过之后的补登记：只排未来项（防 now+1s 空转） ----------

    @Test
    public void nextFutureAt_skipsOverdueAndKeepsNearestFuture() {
        long[] ats = {NOW - 60_000L, NOW + 600_000L, NOW + 1200_000L};
        assertEquals(NOW + 600_000L, ReminderScheduling.nextFutureAt(ats, NOW));
    }

    @Test
    public void nextFutureAt_allOverdueOrEmpty_returnsZero() {
        long[] overdue = {NOW - 60_000L, NOW};
        assertEquals(0L, ReminderScheduling.nextFutureAt(overdue, NOW));  // 到点也算已到期
        assertEquals(0L, ReminderScheduling.nextFutureAt(new long[0], NOW));
        assertEquals(0L, ReminderScheduling.nextFutureAt(null, NOW));
    }

    @Test
    public void nextTriggerAt_vs_nextFutureAt_overdueDiffers() {
        // 同一条到期项：reschedule 会给 now+1s（补发）；响过之后的补登记必须返回 0（不再空转）
        long[] ats = {NOW - 60_000L};
        assertEquals(NOW + ReminderScheduling.MIN_LEAD_MS,
                ReminderScheduling.nextTriggerAt(ats, NOW));
        assertEquals(0L, ReminderScheduling.nextFutureAt(ats, NOW));
    }

    // ---------- 到期判断 ----------

    @Test
    public void dueBoundary() {
        assertTrue(ReminderScheduling.isDue(NOW, NOW));        // 正好到点算到期
        assertTrue(ReminderScheduling.isDue(NOW - 1, NOW));
        assertFalse(ReminderScheduling.isDue(NOW + 1, NOW));
    }

    // ---------- 开机 / 时区变更重排 ----------

    @Test
    public void rescheduleWindow_coversOverdueAndNextSevenDays() {
        assertTrue(ReminderScheduling.needsReschedule(NOW - 1000L, NOW));      // 过期 → 重排
        assertTrue(ReminderScheduling.needsReschedule(NOW + 3600_000L, NOW));  // 1 小时后 → 重排
        long horizon = ReminderScheduling.RESCHEDULE_HORIZON_MS;
        assertTrue(ReminderScheduling.needsReschedule(NOW + horizon, NOW));    // 窗口边界含
        assertFalse(ReminderScheduling.needsReschedule(NOW + horizon + 1, NOW)); // 超出窗口不重排
    }

    // ---------- 心跳间隔归一化（耗电配置入口） ----------

    @Test
    public void heartbeatNormalization() {
        assertEquals(60_000L, ReminderScheduling.normalizeHeartbeatMs(0));
        assertEquals(60_000L, ReminderScheduling.normalizeHeartbeatMs(-5));
        assertEquals(30_000L, ReminderScheduling.normalizeHeartbeatMs(10_000));   // 下限 30s
        assertEquals(1_800_000L, ReminderScheduling.normalizeHeartbeatMs(9_999_999)); // 上限 30min
        assertEquals(300_000L, ReminderScheduling.normalizeHeartbeatMs(300_000)); // 合法值原样保留
    }
}
