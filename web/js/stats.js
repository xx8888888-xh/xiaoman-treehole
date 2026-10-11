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

  // "笑出声"类消息口径（一条消息命中一次 = 1 次）
  const HAHA_RE = /(哈{2,}|嘿嘿{1,}|嘻嘻{1,}|笑死|笑不活|绷不住|乐死|乐了|23{2,}|xswl|LOL| lol)/i;
  // 常见 emoji 捕获（主干区段，够覆盖日常）
  const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F900}-\u{1F9FF}]/gu;

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; }
    catch (e) { return {}; }
  }
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* 私密模式等 */ }
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
      const dayKey = new Date(at).toDateString();
      const days = Array.isArray(s.days) ? s.days : [];
      if (!days.includes(dayKey)) { days.push(dayKey); s.days = days.slice(-DAYS_MAX); }
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

  /** 读取视图（只读无副作用；门槛过滤后给模板直填） */
  function snapshot() {
    const s = load();
    const emojiTop = (s.emoji && !Array.isArray(s.emoji))
      ? Object.entries(s.emoji).sort((a, b) => b[1] - a[1])[0] : null;
    return {
      msgs: s.msgs || 0,
      haha: s.haha || 0,
      night: s.night || 0,
      days: (Array.isArray(s.days) ? s.days : []).length,
      emoji: emojiTop ? emojiTop[0] : null,   // 招牌 emoji（无则 null）
      emojiN: emojiTop ? emojiTop[1] : 0,
    };
  }

  function clear() {
    try { localStorage.removeItem(KEY); } catch (e) { }
  }

  return { bump, snapshot, clear, KEY };
})();
window.Stats = Stats;   // const 不挂 window，跨模块守卫需显式挂载
