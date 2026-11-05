/* time_divider.js · 对话时间分隔（纯函数，零依赖）
 * ------------------------------------------------------------
 * 规则（克制优先）：
 *  - 同一天、间隔 < 5 分钟：不加分隔（连续聊几句不该被切开）
 *  - 同一天、间隔 >= 5 分钟：只显示时刻 "14:20"
 *  - 跨天：显示 "今天 14:20 / 昨天 09:05 / 9月3日 08:12 / 2025年12月3日 08:12"
 *  - 会话第一条（没有上一条）：不加分隔，避免开场就顶着一条日期
 *  - 时间戳缺失/异常：不加分隔（宁缺勿错）
 * 分页窗口滑动的每一行都按 (上一条时间, 本条时间) 现算，所以不会重复、不会错位。
 * ============================================================ */
(function () {
  const MIN_GAP = 5 * 60 * 1000;

  function pad2(n) { return String(n).padStart(2, "0"); }
  function hm(at) {
    const d = new Date(at);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function dayStart(at) {
    const d = new Date(at);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }
  function dateLabel(at, now) {
    const d = new Date(at);
    const n = new Date(now);
    const days = Math.round((dayStart(now) - dayStart(at)) / 86400000);
    if (days === 0) return "今天";
    if (days === 1) return "昨天";
    if (d.getFullYear() === n.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
  }

  /** 缺失/空串/非数字一律视为"没有时间戳"（Number(null) 是 0，不能直接转） */
  function stamp(v) {
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  /** prevAt：上一条消息的时间；at：本条；now 可注入（测试用） */
  function label(prevAt, at, now) {
    const a = stamp(at);
    const p = stamp(prevAt);
    if (a == null || p == null) return null;
    if (a - p < 0) return null;
    if (dayStart(a) !== dayStart(p)) {
      const injected = stamp(now);
      return dateLabel(a, injected == null ? Date.now() : injected) + " " + hm(a);
    }
    return a - p >= MIN_GAP ? hm(a) : null;
  }

  const api = { label, dateLabel, hm, MIN_GAP };
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.XMTimeDivider = api;
})();
