/* ============================================================
 * widget.js · Q 版悬浮挂件（P2-4，2026-10-11 用户指令）
 * ------------------------------------------------------------
 * 退出主界面后，Live2D 退化成 Q 版形象缩在屏幕边上：
 *  - 单 canvas 复用：body.widget-mode 下主 UI 隐藏，现有 #stage
 *    经 CSS 缩小+圆形裁剪贴右下角；stage.focusHead() 聚焦头部
 *    ——不建第二个 Pixi 应用，不下载任何新模型资源
 *  - 定时提醒从这里播报：气泡 + TTS 照播 + 摆手（复用 Shake 动作组）
 *  - 偶尔摆摆手等可爱动作：低频随机（5-10 分钟一次），远低于心跳
 *    闲聊频率（不打扰优先——负面清单红线）
 *  - 点挂件展开回主界面
 * ============================================================ */

const Widget = (() => {
  let active = false;
  let idleTimer = null;
  const MOTIONS = ["Greeting", "Nod", "Shake", "HappyJump"];
  const IDLE_LINES = ["还在哦", "戳我回聊天", "我一直都在", "想你啦"];

  function enter() {                      // 收起 → 挂件
    if (active) return;
    active = true;
    document.body.classList.add("widget-mode");
    try { window.Stage && Stage.focusHead(); } catch (e) {}
    scheduleIdle();
    window.__widgetActive = true;         // E2E 探针（沿 __genericPet 先例）
  }

  function exit() {                       // 展开 → 主界面
    if (!active) return;
    active = false;
    document.body.classList.remove("widget-mode");
    clearTimeout(idleTimer);
    hideBubble();
    try { window.Stage && Stage.restoreView(); } catch (e) {}
    window.__widgetActive = false;
  }

  function toggle() { active ? exit() : enter(); }

  /** 挂件气泡（提醒/问候语；6s 自动消失） */
  function notify(text) {
    const el = document.getElementById("widgetBubble");
    if (!el || !active || !text) return;
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(notify._t);
    notify._t = setTimeout(hideBubble, 6000);
  }
  function hideBubble() {
    const el = document.getElementById("widgetBubble");
    if (el) el.classList.remove("show");
  }

  /** 可爱动作（摆手/点头/跳一下；默认随机） */
  function wiggle(motion) {
    try {
      window.Stage && Stage.playMotion(motion || MOTIONS[Math.floor(Math.random() * MOTIONS.length)]);
    } catch (e) {}
  }

  /** 低频随机小动作（5-10 分钟；40% 概率配一句极短气泡） */
  function scheduleIdle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (!active) return;
      wiggle();
      if (Math.random() < 0.4) notify(IDLE_LINES[Math.floor(Math.random() * IDLE_LINES.length)]);
      scheduleIdle();
    }, 5 * 60e3 + Math.random() * 5 * 60e3);
  }

  function bind() {
    const btn = document.getElementById("widgetBtn");
    if (btn) btn.addEventListener("click", toggle);
    const stageEl = document.getElementById("stage");
    if (stageEl) {
      // 挂件模式下点挂件 = 展开回主界面（capture 拦截，不触发摸头）
      stageEl.addEventListener("click", (e) => {
        if (active) { e.stopPropagation(); exit(); }
      }, true);
    }
  }

  return { enter, exit, toggle, notify, wiggle, bind, get active() { return active; } };
})();
window.Widget = Widget;   // const 不挂 window，跨模块守卫需显式挂载
