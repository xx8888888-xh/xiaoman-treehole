package com.xiaoman.treehole;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * NoisyGuardTest · 拔耳机广播的「注册」与「收到即停」单测
 * ------------------------------------------------------------
 * AudioFocusPolicyTest 只覆盖了状态机；这里补两条本轮修复的硬验收：
 *  1) ACTION_AUDIO_BECOMING_NOISY 的 receiver 注册动作真的被执行（且只执行一次）；
 *  2) 收到 becoming-noisy 后状态机回到 idle（isSpeaking == false）。
 * 真机插拔耳机无法在单测里做，所以把 registerReceiver 做成注入动作，
 * 生产端与单测端走同一条 NoisyGuard 路径。
 */
public class NoisyGuardTest {

    @Test
    public void register_runsActionExactlyOnce() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        NoisyGuard g = new NoisyGuard(p);
        final int[] calls = {0};

        assertTrue(g.register(() -> calls[0]++));
        assertEquals("receiver 注册动作必须执行一次", 1, calls[0]);
        assertTrue(g.isRegistered());

        // 重复调用（例如单例被多次 shared()）不得重复 registerReceiver
        g.register(() -> calls[0]++);
        assertEquals("重复调用不能重复注册", 1, calls[0]);
        assertTrue(g.isRegistered());
    }

    @Test
    public void register_failureIsRetryable() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        NoisyGuard g = new NoisyGuard(p);
        assertFalse(g.register(() -> { throw new IllegalStateException("boom"); }));
        assertFalse(g.isRegistered());
        // 失败后允许下一次再试
        assertTrue(g.register(() -> { }));
        assertTrue(g.isRegistered());
    }

    @Test
    public void becomingNoisy_afterStop_stateIsIdle() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        NoisyGuard g = new NoisyGuard(p);
        g.register(() -> { });

        p.onSpeakStarted();
        assertEquals(AudioFocusPolicy.Action.STOP, g.onBecomingNoisy());
        assertFalse("拔耳机后必须立刻回到 idle", p.isSpeaking());
        // 第二次广播（例如蓝牙断开又连上）不应再触发停止
        assertEquals(AudioFocusPolicy.Action.NONE, g.onBecomingNoisy());
    }

    @Test
    public void becomingNoisy_whenIdle_isNoop() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        NoisyGuard g = new NoisyGuard(p);
        g.register(() -> { });
        assertEquals(AudioFocusPolicy.Action.NONE, g.onBecomingNoisy());
        assertFalse(p.isSpeaking());
    }
}
