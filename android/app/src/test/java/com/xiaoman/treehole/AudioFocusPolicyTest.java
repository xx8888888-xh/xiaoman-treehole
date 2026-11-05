package com.xiaoman.treehole;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * AudioFocusPolicyTest · 「拔耳机立刻停读、且不自动恢复外放」的规则单测
 * ----------------------------------------------------------------------
 * 真机上的 ACTION_AUDIO_BECOMING_NOISY 需要插拔耳机才能触发，AVD 也模拟不了；
 * 但"收到这个事件时该不该停"是纯状态判断，抽到 AudioFocusPolicy 后用普通 JUnit
 * 就能钉死，不需要 Robolectric、不需要模拟器。
 */
public class AudioFocusPolicyTest {

    @Test
    public void becomingNoisy_whileSpeaking_stopsImmediately() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        p.onSpeakStarted();
        assertEquals(AudioFocusPolicy.Action.STOP, p.onBecomingNoisy());
        assertFalse("停读后状态必须回到未朗读", p.isSpeaking());
    }

    @Test
    public void becomingNoisy_whenIdle_doesNothing() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        assertEquals(AudioFocusPolicy.Action.NONE, p.onBecomingNoisy());
    }

    @Test
    public void afterNoisyStop_neverAutoResumes() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        p.onSpeakStarted();
        p.onBecomingNoisy();
        // 焦点恢复（GAIN）也不能把朗读重新拉起来：恢复只能来自用户下一次手势
        assertEquals(AudioFocusPolicy.Action.NONE, p.onFocusChange(AudioFocusPolicy.FocusEvent.GAIN));
        assertFalse(p.isSpeaking());
        // 再拔一次耳机也不会重复触发停止（已经不在朗读）
        assertEquals(AudioFocusPolicy.Action.NONE, p.onBecomingNoisy());
    }

    @Test
    public void focusLoss_whileSpeaking_stops_andDuckAlsoStops() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        p.onSpeakStarted();
        assertEquals(AudioFocusPolicy.Action.STOP, p.onFocusChange(AudioFocusPolicy.FocusEvent.LOSS));
        assertFalse(p.isSpeaking());

        AudioFocusPolicy duck = new AudioFocusPolicy();
        duck.onSpeakStarted();
        assertEquals(AudioFocusPolicy.Action.STOP,
                duck.onFocusChange(AudioFocusPolicy.FocusEvent.LOSS_TRANSIENT_CAN_DUCK));
        assertFalse(duck.isSpeaking());
    }

    @Test
    public void focusLoss_whenIdle_isIgnored() {
        AudioFocusPolicy p = new AudioFocusPolicy();
        assertEquals(AudioFocusPolicy.Action.NONE,
                p.onFocusChange(AudioFocusPolicy.FocusEvent.LOSS_TRANSIENT));
        assertTrue(!p.isSpeaking());
    }
}
