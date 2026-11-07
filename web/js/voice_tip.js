/* voice_tip.js · 语音失败提示条的存活期策略 + 可聚合的失败日志（2026-11-07）
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
 *
 * 2026-11-07 起日志条目可带 detail（失败原因 / 文本长度 / 超时 / HTTP 码）：
 * 旧 schema 只有 {at, reason}，长会话写满 20 条也只能知道「失败过 20 次」。
 * 隐私红线不变（P1-5）：**绝不落对话原文**，只记 textLen / 长度分桶 / 文本哈希。
 * detail 只接受白名单键，调用方多传的字段一律丢弃，避免把正文/堆栈写进会话存储。
 */
(function (root) {
  const KEY_OFF = "xiaoman_voice_tip_off";
  const KEY_LOG = "xiaoman_voice_fail_log";
  const TTL_MS = 8000;
  const MAX_LOG = 20;
  const MAX_ATTEMPTS = 4;
  const STR_MAX = 40;

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

  /** 文本长度 → 分桶（短句/中句/长句）；阈值与语音回复的实际长度分布对齐 */
  function bucketOf(len) {
    const n = Number(len);
    if (!Number.isFinite(n) || n <= 0) return "empty";
    if (n <= 20) return "short";
    if (n <= 60) return "mid";
    return "long";
  }

  function cleanStr(v) {
    if (typeof v !== "string" || !v) return "";
    // 去掉控制字符，截断到 40 字：只接收枚举/错误码这类短值
    const s = Array.from(v).filter((ch) => {
      const c = ch.codePointAt(0);
      return c >= 0x20 && c !== 0x7f;
    }).join("").slice(0, STR_MAX);
    return s;
  }

  function cleanAttempt(a) {
    if (!a || typeof a !== "object") return null;
    const out = {};
    const provider = cleanStr(a.provider);
    if (provider) out.provider = provider;
    const error = cleanStr(a.error || a.reason);
    if (error) out.error = error;
    const st = Number(a.httpStatus);
    if (Number.isFinite(st) && st > 0) out.httpStatus = st;
    if (a.timedOut === true) out.timedOut = true;
    const ms = Number(a.elapsedMs);
    if (Number.isFinite(ms) && ms >= 0) out.elapsedMs = Math.round(ms);
    return Object.keys(out).length ? out : null;
  }

  /** 失败诊断信息白名单化：不认识的键（可能夹带正文/堆栈）一律不落盘 */
  function sanitizeDetail(detail) {
    if (!detail || typeof detail !== "object") return null;
    const out = {};
    const provider = cleanStr(detail.provider);
    if (provider) out.provider = provider;
    const error = cleanStr(detail.error || detail.reason);
    if (error) out.error = error;
    const len = Number(detail.textLen);
    if (Number.isFinite(len) && len >= 0) {
      out.textLen = Math.round(len);
      out.bucket = bucketOf(len);
    }
    const hash = cleanStr(detail.textHash);
    if (hash) out.textHash = hash.slice(0, 12);
    const ms = Number(detail.elapsedMs);
    if (Number.isFinite(ms) && ms >= 0) out.elapsedMs = Math.round(ms);
    if (detail.timedOut === true) out.timedOut = true;
    const st = Number(detail.httpStatus);
    if (Number.isFinite(st) && st > 0) out.httpStatus = st;
    if (Array.isArray(detail.attempts)) {
      const arr = detail.attempts.slice(0, MAX_ATTEMPTS).map(cleanAttempt).filter(Boolean);
      if (arr.length) out.attempts = arr;
    }
    return Object.keys(out).length ? out : null;
  }

  /** 日志分布聚合（纯函数）：按原因/错误码/长度桶/超时/重复文本统计 */
  function stats(log) {
    const out = {
      total: 0, legacy: 0, timeouts: 0,
      byReason: {}, byError: {}, byBucket: {}, byProvider: {},
      topTextHashes: [],
    };
    const hashes = {};
    for (const e of Array.isArray(log) ? log : []) {
      if (!e || typeof e !== "object") continue;
      out.total++;
      const reason = cleanStr(e.reason) || "unknown";
      out.byReason[reason] = (out.byReason[reason] || 0) + 1;
      if (!e.detail) { out.legacy++; continue; }
      const d = e.detail;
      const err = cleanStr(d.error);
      if (err) out.byError[err] = (out.byError[err] || 0) + 1;
      const bucket = cleanStr(d.bucket) || bucketOf(d.textLen);
      out.byBucket[bucket] = (out.byBucket[bucket] || 0) + 1;
      const provider = cleanStr(d.provider) || "unknown";
      out.byProvider[provider] = (out.byProvider[provider] || 0) + 1;
      if (d.timedOut === true) out.timeouts++;
      const hash = cleanStr(d.textHash);
      if (hash) hashes[hash] = (hashes[hash] || 0) + 1;
    }
    out.topTextHashes = Object.keys(hashes)
      .map((h) => ({ hash: h, count: hashes[h] }))
      .sort((a, b) => b.count - a.count || a.hash.localeCompare(b.hash))
      .slice(0, 5);
    return out;
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

    function show(reason, detail) {
      // 失败记录先落盘：提示被静音或已过期，排障线索也不能丢
      const entry = { at: now(), reason: reason || "unknown" };
      const d = sanitizeDetail(detail);
      if (d) entry.detail = d;   // 无 detail 时保持旧 schema，旧读端不受影响
      appendLog(store, entry);
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
      stats: () => stats(readLog(store)),
      ttlMs: ttl,
    };
  }

  const api = {
    create, readLog, appendLog, sanitizeDetail, bucketOf, stats,
    TTL_MS, MAX_LOG, KEY_OFF, KEY_LOG,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.VoiceTip = api;
})(typeof window !== "undefined" ? window : globalThis);
