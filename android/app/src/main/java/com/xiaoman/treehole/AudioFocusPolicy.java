package com.xiaoman.treehole;

/**
 * AudioFocusPolicy · 「什么时候必须停读」的纯逻辑状态机
 * ------------------------------------------------------------
 * 为什么单独抽一个类：音频焦点丢失 / 拔耳机这两条路径只有真机能触发，
 * 但它们要表达的规则是纯状态判断。放进这里就能用普通 JUnit（无 Robolectric、
 * 无模拟器）覆盖「拔耳机 → 停读」与「停完不自动恢复」两条硬约束。
 *
 * 设计约束：
 *  - 只有"正在朗读"时才响应打断事件，空闲时插拔耳机不产生任何动作；
 *  - 任何 STOP 都只单向地把状态置为"未朗读"，不提供 resume()——
 *    恢复朗读必须来自用户下一次手势，绝不自动续播到外放。
 */
final class AudioFocusPolicy {

    /** 对调用方的指令：NONE=什么都不做；STOP=立刻停读并放弃音频焦点 */
    enum Action { NONE, STOP }

    /** AudioManager.AUDIOFOCUS_* 的抽象（本类不依赖 Android，JVM 可测） */
    enum FocusEvent { GAIN, LOSS, LOSS_TRANSIENT, LOSS_TRANSIENT_CAN_DUCK }

    private boolean speaking;

    void onSpeakStarted() {
        speaking = true;
    }

    void onSpeakFinished() {
        speaking = false;
    }

    boolean isSpeaking() {
        return speaking;
    }

    /**
     * ACTION_AUDIO_BECOMING_NOISY（拔有线耳机 / 断开蓝牙）。
     * 正在朗读 → 必须立刻停，且此后状态为"未朗读"，不会自动恢复外放。
     */
    Action onBecomingNoisy() {
        if (!speaking) return Action.NONE;
        speaking = false;
        return Action.STOP;
    }

    /**
     * 其它应用抢焦点：朗读中一律停（包括短暂丢失与 duck）。
     * GAIN 不触发任何动作——焦点回来也不自动续播。
     */
    Action onFocusChange(FocusEvent event) {
        if (event == null || event == FocusEvent.GAIN || !speaking) return Action.NONE;
        speaking = false;
        return Action.STOP;
    }
}
