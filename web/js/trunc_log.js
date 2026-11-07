/* trunc_log.js · 段数截断的可查记录（2026-11-07）
 * ------------------------------------------------------------
 * 背景：回复按 "||" 分条后，sendSplit 有段数上限、流式渲染有槽位上限，
 * 超出部分都是 `slice(0, n)` 静默丢弃——线上只能看到"少了一条"，却查不到
 * "原本几条、上限几条、丢了什么开头"。⑦e-4 的 month_done 事故就是这种静默：
 * 月度回放被截掉，而幂等键已经消费，当月再也不会补。
 *
 * 本模块只做两件事：
 *   1) 判断是否真的丢了非空内容（空段不算内容丢失）；
 *   2) 把段数/上限/被丢段的条数与首几个字写进 sessionStorage（xiaoman_trunc_log），
 *      与语音失败日志（xiaoman_voice_fail_log）同一套"会话内可查、关页即清"口径。
 *
 * 脱敏：只记首 6 个字符，且数字/邮箱/URL/英文名一律打码，不落全文。
 * 幂等：流式增量会反复带同一段全文，靠 (kind, seq) upsert 只留最后/最大的一条。
 * 不改上限：这里只观测，不参与任何"丢不丢"的决策。
 */
((root) => {
  "use strict";
  const KEY_LOG = "xiaoman_trunc_log";
  const MAX_LOG = 20;
  const PREVIEW_CHARS = 6;
  const PREVIEW_ITEMS = 3;

  let seqCounter = 0;
  function nextSeq() {
    seqCounter += 1;
    return seqCounter;
  }

  /** 被丢段开头的脱敏预览：仅前 6 字，拉丁/数字/邮箱/URL 字符打码 */
  function maskPreview(text) {
    const s = Array.from(String(text == null ? "" : text).trim()).slice(0, PREVIEW_CHARS).join("");
    return s.replace(/[A-Za-z0-9@._+\-/:%]/g, "*");
  }

  function defaultStore(deps) {
    if (deps?.store) return deps.store;
    try {
      return root.sessionStorage;
    } catch {
      return null;
    }
  }

  function readLog(deps) {
    const store = defaultStore(deps);
    if (!store) return [];
    try {
      const v = JSON.parse(store.getItem(KEY_LOG) || "[]");
      return Array.isArray(v) ? v : [];
    } catch {
      return [];
    }
  }

  /** 写一条截断记录；同 (kind, seq) 覆盖旧条（流式增长只留最后一条） */
  function record(entry, deps) {
    const store = defaultStore(deps);
    if (!store) return null;
    const src = entry || {};
    const dropped = Array.isArray(src.dropped) ? src.dropped : [];
    const rec = {
      at: deps && typeof deps.now === "function" ? deps.now() : Date.now(),
      kind: String(src.kind || "unknown"),
      seq: src.seq == null ? nextSeq() : src.seq,
      total: Number(src.total) || 0,
      cap: Number(src.cap) || 0,
      droppedN: Number(src.droppedN) || dropped.length,
      dropped: dropped.slice(0, PREVIEW_ITEMS).map(maskPreview),
    };
    const log = readLog({ store });
    const idx = log.findIndex((e) => e && e.kind === rec.kind && e.seq === rec.seq);
    if (idx >= 0) log[idx] = rec;
    else log.push(rec);
    while (log.length > MAX_LOG) log.shift();
    try {
      store.setItem(KEY_LOG, JSON.stringify(log));
    } catch {
      return null;
    }
    return rec;
  }

  /** 段数上限：cap 内原样返回；超出且丢了非空内容 → 写一条记录后返回前 cap 段 */
  function limitParts(parts, cap, kind, deps) {
    const list = Array.isArray(parts) ? parts : [];
    const limit = Number(cap) > 0 ? Math.floor(Number(cap)) : list.length;
    const over = list.slice(limit);
    const dropped = over.filter((s) => String(s == null ? "" : s).trim());
    if (dropped.length) {
      record({ kind, total: list.length, cap: limit, droppedN: dropped.length, dropped }, deps);
    }
    return list.slice(0, limit);
  }

  const api = { KEY_LOG, MAX_LOG, PREVIEW_CHARS, PREVIEW_ITEMS, nextSeq, maskPreview, readLog, record, limitParts };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.TruncLog = api;
})(typeof window !== "undefined" ? window : globalThis);
