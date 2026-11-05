package com.xiaoman.treehole;

/**
 * NoisyGuard · 拔耳机/断蓝牙的注册与状态守卫（纯 JVM，可单测）
 * -------------------------------------------------------------------------
 * 为什么要单独抽出来：registerReceiver 必须拿 Context，没法在纯 JVM 单测里调；
 * 但"注册动作恰好发生一次"和"收到 becoming-noisy 后状态机回到 idle"这两条是
 * 本轮修复的硬验收，必须能被断言。于是把注册动作做成注入的回调，生产端传
 * 真正的 registerReceiver，单测端传计数器。
 */
final class NoisyGuard {

    /** 生产端：真正执行 registerReceiver 的动作（失败抛异常即可）。 */
    interface Register {
        void run() throws Exception;
    }

    private final AudioFocusPolicy policy;
    private boolean registered;

    NoisyGuard(AudioFocusPolicy policy) {
        this.policy = policy;
    }

    /**
     * 注册拔耳机广播；重复调用不会重复注册。
     * @return true = 当前处于已注册状态
     */
    boolean register(Register action) {
        if (registered) return true;
        if (action == null) return false;
        try {
            action.run();
            registered = true;
        } catch (Exception e) {
            registered = false;
        }
        return registered;
    }

    boolean isRegistered() {
        return registered;
    }

    /**
     * 收到 ACTION_AUDIO_BECOMING_NOISY：只可能是 STOP（停止朗读）或 NONE（本来没在读）。
     * 状态机没有 resume：停了就必须等用户下一次手势，不自动恢复外放。
     */
    AudioFocusPolicy.Action onBecomingNoisy() {
        return policy.onBecomingNoisy();
    }
}
