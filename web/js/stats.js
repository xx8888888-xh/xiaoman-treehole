/* ============================================================
 * stats.js · 小满的小本本（P2-1 自我叙事·数据层）
 * ------------------------------------------------------------
 * 每条用户消息增量计数（真实数据，零虚构铁律）：
 *  - msgs   用户消息总数
 *  - haha   说"哈哈/笑死/233…"的消息条数（一条算一次）
 *  - night  23:00-05:00 发来的消息数（深夜来访）
 *  - days   说过话的日子（toDateString 去重，上限 400 项防膨胀）
 *  - emoji  用户 emoji 使用计数表（top 表情"招牌"）
 *
 * heartbeat 自我叙事模板直填这些数字（A 层·真实观察）。
 * node 测试可注入 fake localStorage / Date。
 * ============================================================ */
const Stats = (() => {
  const KEY = "xiaoman_stats_v1";
  const NIGHT_H = [23, 5];       // 深夜窗口（与心跳静默时段呼应）
  const DAYS_MAX = 400;          // 天数集合上限（超一年截尾，够用）
  const EMOJI_MAX = 30;          // emoji 计数表上限（防脏膨胀）
  const PER_DAY_MAX = 90;        // 按天计数保留窗口（最近 90 天，图表/连续打卡用）

  // "笑出声"类消息口径（一条消息命中一次 = 1 次）
  const HAHA_RE = /(哈{2,}|嘿嘿{1,}|嘻嘻{1,}|笑死|笑不活|绷不住|乐死|乐了|23{2,}|xswl|LOL| lol)/i;
  // 常见 emoji 捕获（主干区段，够覆盖日常）
  const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F900}-\u{1F9FF}]/gu;

  /** 本地日期键 YYYY-MM-DD（不用 toISOString：那是 UTC，会在时区边界错一天） */
  function dayKey(at) {
    const d = new Date(at == null ? Date.now() : at);
    const p = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; }
    catch (e) { return {}; }
  }
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); }
    catch (e) {
      try { if (typeof window !== "undefined" && window.__storageFull) window.__storageFull("小本本"); } catch (e2) {}
    }
  }

  /** 兼容旧数据：没有 perDay 时用 days（toDateString）折算成每天 1 条 */
  function counts(s) {
    const out = {};
    const per = s && s.perDay && typeof s.perDay === "object" && !Array.isArray(s.perDay) ? s.perDay : null;
    if (per) {
      for (const k of Object.keys(per)) {
        if (Number(per[k]) > 0) out[k] = Number(per[k]);
      }
      return out;
    }
    const days = (s && Array.isArray(s.days)) ? s.days : [];
    for (const ds of days) {
      const t = new Date(ds);
      if (!Number.isNaN(t.getTime())) out[dayKey(t.getTime())] = 1;
    }
    return out;
  }

  /** 每条用户消息调用：text=消息原文，at=时间戳（默认现在） */
  function bump(text, at) {
    try {
      at = (at == null) ? Date.now() : at;
      const s = load();
      s.msgs = (s.msgs || 0) + 1;
      if (HAHA_RE.test(text || "")) s.haha = (s.haha || 0) + 1;
      const h = new Date(at).getHours();
      if (h >= NIGHT_H[0] || h < NIGHT_H[1]) s.night = (s.night || 0) + 1;
      const dk = dayKey(at);
      // 按天计数（换天不会把昨天的数字算到今天）。
      // 注意先取 perDay，再改 days —— 旧数据折算依赖尚未写入今天的 days。
      const per = (s.perDay && typeof s.perDay === "object" && !Array.isArray(s.perDay)) ? s.perDay : counts(s);
      per[dk] = (per[dk] || 0) + 1;
      const keys = Object.keys(per).sort().slice(-PER_DAY_MAX);
      s.perDay = {};
      for (const k of keys) s.perDay[k] = per[k];
      const days = Array.isArray(s.days) ? s.days : [];
      const legacyDay = new Date(at).toDateString();
      if (!days.includes(legacyDay)) { days.push(legacyDay); s.days = days.slice(-DAYS_MAX); }
      try {
        const emojis = (String(text || "").match(EMOJI_RE) || []).slice(0, 5);
        if (emojis.length) {
          const table = (s.emoji && typeof s.emoji === "object" && !Array.isArray(s.emoji)) ? s.emoji : {};
          for (const e of emojis) table[e] = (table[e] || 0) + 1;
          const entries = Object.entries(table).sort((a, b) => b[1] - a[1]);
          s.emoji = Object.fromEntries(entries.slice(0, EMOJI_MAX));
        }
      } catch (e2) { /* 老 WebView 不支持 u-flag 正则：emoji 统计静默降级 */ }
      save(s);
      return s;
    } catch (e) { return load(); }
  }

  /** 最近 n 天（含今天；缺数据补 0），从旧到新：[{date:"MM-DD", key, n}] */
  function daily(n, now) {
    const days = Math.max(1, n || 7);
    const base = new Date(now == null ? Date.now() : now);
    base.setHours(0, 0, 0, 0);
    const c = counts(load());
    const out = [];
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(base.getTime());
      d.setDate(d.getDate() - i);
      const key = dayKey(d.getTime());
      out.push({ date: `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")}`, key, n: c[key] || 0 });
    }
    return out;
  }

  /** 连续打卡天数（今天没说话则从昨天算，不让"还没开口"把连续记录清零） */
  function streak(now) {
    const c = counts(load());
    const base = new Date(now == null ? Date.now() : now);
    base.setHours(0, 0, 0, 0);
    let cursor = new Date(base.getTime());
    if (!c[dayKey(cursor.getTime())]) cursor.setDate(cursor.getDate() - 1);
    let n = 0;
    while (c[dayKey(cursor.getTime())]) {
      n++;
      cursor.setDate(cursor.getDate() - 1);
    }
    return n;
  }

  /** 读取视图（只读无副作用；门槛过滤后给模板直填） */
  function snapshot() {
    const s = load();
    const emojiTop = (s.emoji && !Array.isArray(s.emoji))
      ? Object.entries(s.emoji).sort((a, b) => b[1] - a[1])[0] : null;
    const c = counts(s);
    return {
      msgs: s.msgs || 0,
      haha: s.haha || 0,
      night: s.night || 0,
      days: (Array.isArray(s.days) ? s.days : []).length,
      emoji: emojiTop ? emojiTop[0] : null,   // 招牌 emoji（无则 null）
      emojiN: emojiTop ? emojiTop[1] : 0,
      today: c[dayKey(Date.now())] || 0,
      streak: streak(),
      last7: daily(7),
    };
  }

  function clear() {
    try { localStorage.removeItem(KEY); } catch (e) { }
  }

  return { bump, snapshot, clear, KEY, dayKey, daily, streak };
})();
if (typeof module === "object" && module.exports) module.exports = Stats;   // node 单测用
window.Stats = Stats;   // const 不挂 window，跨模块守卫需显式挂载
