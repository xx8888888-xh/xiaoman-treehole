/* voice_tip.js · 语音失败提示条的存活期策略（2026-11-07）
 *
 * 旧行为：提示一旦出现就常驻，直到用户手动 ×（而 × 会写 xiaoman_voice_tip_off，
 * 整个会话都不再提示）——「不关就一直赖着，关了以后永远不知道」。
 *
 * 目标同时满足两件：
 *   1) 失败时看得见（条形提示 + aria-live），不能静悄悄吞掉；
 *   2) 不永久占屏 —— 存活 TTL 后自动收起；
 *   3) 收起后不会被重绘刷回来（可见性只有一个开关 show()，其余 render 只按状态重放）；
 *   4) 收起后失败记录仍在 sessionStorage（排障线索不丢）。
 *
 * 为什么选「存活期」而不是「下次成功播报时清除」：设备持续故障时，后者会让提示
 * 永久驻留，正是要修的毛病；TTL 不依赖成功事件，每次新的失败重新计时并写一条记录。
 * TTL 取 8s：一行提示的阅读时间足够，且失败恰好发生在用户等声音的那一刻，注意力在。
 */
(function (root) {
  const KEY_OFF = "xiaoman_voice_tip_off";
  const KEY_LOG = "xiaoman_voice_fail_log";
  const TTL_MS = 8000;
  const MAX_LOG = 20;

  function safeGet(store, k) {
    try { return store.getItem(k); } catch (e) { return null; }
  }
  function safeSet(store, k, v) {
    try { store.setItem(k, v); } catch (e) { /* 隐私模式/配额满：日志丢就丢，不能挡提示 */ }
  }

  function readLog(store) {
    try {
      const v = JSON.parse(safeGet(store, KEY_LOG) || "[]");
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }

  function appendLog(store, entry, max) {
    const log = readLog(store);
    log.push(entry);
    const cap = max || MAX_LOG;
    while (log.length > cap) log.shift();
    safeSet(store, KEY_LOG, JSON.stringify(log));
    return log;
  }

  function create(deps) {
    const el = deps.el;
    const store = deps.store;
    const now = deps.now || (() => Date.now());
    const ttl = deps.ttlMs == null ? TTL_MS : deps.ttlMs;
    const setT = deps.setTimeout || ((fn, ms) => setTimeout(fn, ms));
    const clrT = deps.clearTimeout || ((h) => clearTimeout(h));
    let timer = null;
    let on = false;   // 唯一可见状态：只由 show() 置 true，到期/hide() 置 false

    function paint() {
      if (!el) return;
      if (on) el.classList.remove("hidden");
      else el.classList.add("hidden");
    }

    function hide() {
      on = false;
      if (timer) { clrT(timer); timer = null; }
      paint();
    }

    function show(reason) {
      // 失败记录先落盘：提示被静音或已过期，排障线索也不能丢
      appendLog(store, { at: now(), reason: reason || "unknown" });
      if (safeGet(store, KEY_OFF) === "1") { on = false; paint(); return false; }
      on = true;
      paint();
      if (timer) clrT(timer);
      timer = setT(() => { timer = null; on = false; paint(); }, ttl);
      return true;
    }

    return {
      show,
      hide,
      render: paint,                       // 幂等重放：已收起就不会被重绘翻回可见
      visible: () => on,
      log: () => readLog(store),
      ttlMs: ttl,
    };
  }

  const api = { create, readLog, appendLog, TTL_MS, MAX_LOG, KEY_OFF, KEY_LOG };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.VoiceTip = api;
})(typeof window !== "undefined" ? window : globalThis);
