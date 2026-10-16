/* ============================================================
 * float_mode.js · 悬浮窗迷你模式（?float=1）
 * ------------------------------------------------------------
 * 由 Android FloatWindowService 的迷你窗 WebView 加载：
 *  - body.float-mode：CSS 切小屏布局（隐藏背景装饰/顶栏/舞台）
 *  - app.js 检测 FloatMode.on：跳过 Live2D 舞台与挂件（省内存，防双实例卡顿）
 *  - 对话/记忆/提醒/语音引擎全部保留——迷你窗与小满的聊天完全同源
 * ============================================================ */

const FloatMode = (() => {
  let on = false;
  try {
    on = new URLSearchParams(location.search).get("float") === "1";
  } catch (e) { on = false; }

  if (on) {
    // 立即加 class，让 CSS 在首帧前生效（避免迷你窗闪现全屏布局）
    document.documentElement.classList.add("float-root");
    const arm = () => document.body && document.body.classList.add("float-mode");
    if (document.body) arm();
    else document.addEventListener("DOMContentLoaded", arm, { once: true });
  }

  return { on };
})();

window.FloatMode = FloatMode;   // const 不挂 window，跨模块守卫需显式挂载（同 heartbeat/widget/memory 先例）
