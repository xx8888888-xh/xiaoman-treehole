/* greet_day.js · 开场问候的"当天 / 跨天"判定（纯函数，零依赖）
 * ------------------------------------------------------------
 * 规则：
 *  - 当天首次启动：播开场问候；
 *  - 同一天内重入（刷新 / 杀进程再开）：不再追加第二次时段问候；
 *  - 跨天（本地日历日变更）：照常问候。
 *  - 例外：有待补回复（话没丢）时，同日重入只播"话没丢"，不播时段问候。
 *
 * 日期边界按**设备本地时区的日历日 00:00** 切（用户在东八区时即 UTC+8 零点）；
 * 刻意不用 toISOString()（那是 UTC，东八区 00:00–08:00 会被算到前一天）。
 * offsetMin 可注入（东八区=480），缺省用本机时区。
 * ============================================================ */
(function (root) {
  "use strict";

  function pad2(n) { return String(n).padStart(2, "0"); }

  /** ts 毫秒时间戳 → 本地日历日 "YYYY-MM-DD"；offsetMin 分钟（东八区=480） */
  function dayKey(ts, offsetMin) {
    const t = Number(ts);
    if (!Number.isFinite(t)) return "";
    const injected = Number(offsetMin);
    const off = Number.isFinite(injected) ? injected : -new Date(t).getTimezoneOffset();
    const d = new Date(t + off * 60000);
    return d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate());
  }

  /** 是否跨天（lastDay 为空 = 从未问候过 → 视为首次，要播） */
  function shouldGreet(lastDay, todayKey) {
    return !lastDay || lastDay !== todayKey;
  }

  /** 决策：full=播完整开场（当天首次/跨天）；play=是否要插一条气泡（有欠回复时仍要）；
   *  greeting=是否包含时段问候（同日重入为 false）。 */
  function decide(lastDay, todayKey, hasPending) {
    const full = shouldGreet(lastDay, todayKey);
    return {
      full: full,
      greeting: full,
      play: full || !!hasPending,
    };
  }

  const api = { dayKey, shouldGreet, decide };
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.GreetDay = api;
})(typeof window !== "undefined" ? window : globalThis);
