/* ============================================================
 * backup.js · 倾诉记录导出 / 导入（本地 JSON，不做云同步）
 * ------------------------------------------------------------
 * 用户对自己的倾诉记录有完全处置权：
 *  - 导出：当前会话的消息 + 长期记忆 → 一个 JSON 文件（手机本地）
 *  - 导入：把之前导出的 JSON 合并回本机（按 role+时间+内容去重）
 * 纯函数部分可在 node:test 里直接测；DOM/存储访问只在调用时才发生。
 * ============================================================ */

const Backup = (() => {
  "use strict";

  const APP_TAG = "xiaoman-treehole-backup";
  const VERSION = 3;   // v3：补上 提醒 / 小本本统计 / 服务端会话（记忆槽）
  const MAX_MESSAGES = 500;
  const MAX_CONTENT = 1000;
  const MAX_RISK_EVENTS = 50;
  const MAX_REMINDERS = 200;
  const MAX_REMINDER_TEXT = 80;
  const MAX_SERVER_MESSAGES = 200;
  const MAX_MEMORIES = 200;
  const MAX_SLOTS = 80;
  const MAX_DAYS = 400;
  const MAX_PER_DAY = 90;
  const MAX_EMOJI = 30;
  const RISK_LOG_KEY = "xiaoman_risk_log";

  function normalizeMessage(m) {
    if (!m || typeof m !== "object") return null;
    const role = m.role === "user" ? "user" : (m.role === "assistant" ? "assistant" : null);
    if (!role) return null;
    const content = String(m.content == null ? "" : m.content).slice(0, MAX_CONTENT).trim();
    if (!content) return null;
    const at = Number(m.at) || Date.parse(String(m.at || "")) || 0;
    return { role, content, at: at > 0 ? at : 0 };
  }

  function normalizeMemory(m) {
    if (m == null) return null;
    const src = typeof m === "string" ? { text: m } : m;
    const text = String(src.text == null ? "" : src.text).trim().slice(0, 120);
    if (!text) return null;
    return {
      text,
      tags: Array.isArray(src.tags) ? src.tags.map(String).slice(0, 4) : [],
      kind: src.kind === "pin" ? "pin" : "fact",
      ts: Number(src.ts) || 0,
    };
  }

  /** 危机命中记录（红队 P0：命中记录要能被导出） */
  function normalizeRiskEvent(e) {
    if (!e || typeof e !== "object") return null;
    const level = ["high", "medium", "low"].includes(e.level) ? e.level : "low";
    const at = Number(e.at) || 0;
    const reasons = Array.isArray(e.reasons) ? e.reasons.map(String).slice(0, 6) : [];
    const text = String(e.text == null ? "" : e.text).slice(0, 200);
    return { at, level, imminent: !!e.imminent, reasons, text };
  }

  function riskKey(e) { return `${e.at}\u0000${e.level}\u0000${e.text}`; }

  /** 提醒（提醒表/闹钟）：时间必须有效，否则导入后调度器会拿到脏数据 */
  function normalizeReminder(r) {
    if (!r || typeof r !== "object") return null;
    const text = String(r.text == null ? "" : r.text).trim().slice(0, MAX_REMINDER_TEXT);
    const at = Number(r.at);
    if (!text || !Number.isFinite(at) || at <= 0) return null;
    return {
      id: String(r.id || ""),
      text,
      at,
      done: !!r.done,
      doneAt: Number(r.doneAt) || 0,
      createdAt: Number(r.createdAt) || 0,
      updatedAt: Number(r.updatedAt) || 0,
    };
  }

  /** 小本本统计：计数取非负整数，按天/表情表做类型校验 */
  function normalizeStats(s) {
    if (!s || typeof s !== "object" || Array.isArray(s)) return null;
    const n = v => Math.max(0, Math.floor(Number(v) || 0));
    const days = (Array.isArray(s.days) ? s.days : []).map(String).filter(Boolean).slice(-MAX_DAYS);
    const perDay = {};
    if (s.perDay && typeof s.perDay === "object" && !Array.isArray(s.perDay)) {
      for (const k of Object.keys(s.perDay).sort().slice(-MAX_PER_DAY)) {
        const v = n(s.perDay[k]);
        if (v > 0) perDay[k] = v;
      }
    }
    const emoji = {};
    if (s.emoji && typeof s.emoji === "object" && !Array.isArray(s.emoji)) {
      const top = Object.entries(s.emoji).map(([k, v]) => [k, n(v)]).filter(x => x[1] > 0)
        .sort((a, b) => b[1] - a[1]).slice(0, MAX_EMOJI);
      for (const [k, v] of top) emoji[k] = v;
    }
    return { msgs: n(s.msgs), haha: n(s.haha), night: n(s.night), days, perDay, emoji };
  }

  /**
   * 服务端会话（含记忆槽）：状态 + 落盘历史 + slots。
   * 服务端不可达时导出为 null，导入时同样跳过——不能因为一段拿不到的数据让整份备份失败。
   */
  function normalizeServer(src) {
    if (!src || typeof src !== "object" || Array.isArray(src)) return null;
    const state = (src.state && typeof src.state === "object" && !Array.isArray(src.state))
      ? Object.assign({}, src.state) : {};
    const memories = {};
    if (state.memories && typeof state.memories === "object" && !Array.isArray(state.memories)) {
      for (const k of Object.keys(state.memories).slice(0, MAX_MEMORIES)) {
        const key = String(k).slice(0, 20);
        const val = String(state.memories[k] == null ? "" : state.memories[k]).trim().slice(0, 120);
        if (key && val) memories[key] = val;
      }
    }
    state.memories = memories;
    const messages = (Array.isArray(src.messages) ? src.messages : [])
      .map(normalizeMessage).filter(Boolean).slice(-MAX_SERVER_MESSAGES);
    const rawSlots = src.slots && typeof src.slots === "object" ? (src.slots.slots || []) : [];
    const slots = (Array.isArray(rawSlots) ? rawSlots : [])
      .filter(x => x && typeof x === "object" && x.value)
      .slice(0, MAX_SLOTS)
      .map(x => ({
        id: String(x.id || ""),
        key: String(x.key || "").slice(0, 20),
        value: String(x.value).slice(0, 80),
        kind: String(x.kind || "fact"),
        emotion: Number(x.emotion) || 0,
        confidence: Number(x.confidence) || 0,
        source: String(x.source || ""),
        created: Number(x.created) || 0,
        updated: Number(x.updated) || 0,
        last_used: Number(x.last_used) || 0,
        uses: Number(x.uses) || 0,
      }));
    return {
      sessionId: String(src.session_id || ""),
      state,
      messages,
      slots: { version: 1, slots },
    };
  }

  function mergeRiskEvents(existing, incoming, limit) {
    const cap = Math.max(1, Number(limit) || MAX_RISK_EVENTS);
    const seen = new Set((existing || []).map(riskKey));
    const out = [];
    for (const raw of (incoming || [])) {
      const e = normalizeRiskEvent(raw);
      if (!e) continue;
      const k = riskKey(e);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(e);
    }
    out.sort((a, b) => a.at - b.at);
    return out.slice(-cap);
  }

  /** 组装导出对象（纯函数） */
  function buildBackup(input) {
    const src = input || {};
    const messages = (src.messages || []).map(normalizeMessage).filter(Boolean).slice(-MAX_MESSAGES);
    const memories = (src.memories || []).map(normalizeMemory).filter(Boolean);
    const riskEvents = (src.riskEvents || []).map(normalizeRiskEvent).filter(Boolean).slice(-MAX_RISK_EVENTS);
    const reminders = (src.reminders || []).map(normalizeReminder).filter(Boolean).slice(-MAX_REMINDERS);
    return {
      app: APP_TAG,
      version: VERSION,
      exportedAt: new Date(Number(src.exportedAt) || Date.now()).toISOString(),
      sid: String(src.sid || ""),
      messages,
      memories,
      riskEvents,
      reminders,
      stats: normalizeStats(src.stats),
      server: normalizeServer(src.server),
    };
  }

  /** 解析导入文件；失败时返回 { ok:false, error: 用户看得懂的原因 }。兼容 v2 老备份。 */
  function parseBackup(text) {
    let obj;
    try { obj = JSON.parse(String(text || "")); }
    catch { return { ok: false, error: "这不是有效的 JSON 文件" }; }
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      return { ok: false, error: "备份文件格式不对" };
    }
    if (obj.app !== APP_TAG) return { ok: false, error: "这不是小满树洞的备份文件" };
    if (!Array.isArray(obj.messages)) return { ok: false, error: "备份里没有聊天记录" };
    const data = buildBackup({
      sid: obj.sid,
      messages: obj.messages,
      memories: obj.memories,
      riskEvents: obj.riskEvents,
      reminders: obj.reminders,
      stats: obj.stats,
      server: obj.server,
      exportedAt: Date.parse(String(obj.exportedAt || "")) || Date.now(),
    });
    return { ok: true, data };
  }

  function msgKey(m) { return `${m.role}\u0000${m.at}\u0000${m.content}`; }

  /**
   * 合并消息（纯函数）：按 role+at+content 去重，按时间升序，超上限留最新
   * @returns 仅需要新写入的消息数组
   */
  function mergeMessages(existing, incoming, limit) {
    const cap = Math.max(1, Number(limit) || MAX_MESSAGES);
    const seen = new Set((existing || []).map(msgKey));
    const out = [];
    for (const raw of (incoming || [])) {
      const m = normalizeMessage(raw);
      if (!m) continue;
      const k = msgKey(m);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(m);
    }
    out.sort((a, b) => a.at - b.at);
    return out.slice(-cap);
  }

  function filename(now) {
    const d = new Date(Number(now) || Date.now());
    const p = n => String(n).padStart(2, "0");
    return `xiaoman-backup-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.json`;
  }

  /** 浏览器/WebView 下载（安卓另有原生保存桥，失败时回落到这里） */
  function download(text, name) {
    if (typeof document === "undefined" || typeof URL === "undefined") return false;
    try {
      const blob = new Blob([text], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return true;
    } catch (e) { return false; }
  }

  /** 服务端会话（状态+历史+记忆槽）；不可达返回 null（备份仍然成立，只是少了这一段） */
  async function fetchServer(sid) {
    if (typeof window === "undefined" || !window.API || typeof window.API.sessionExport !== "function") return null;
    try { return await window.API.sessionExport(sid); } catch (e) { return null; }
  }

  /** 导出当前会话（消息 + 记忆 + 提醒 + 统计 + 服务端会话） */
  async function exportNow(sid) {
    const messages = window.ChatStore ? await ChatStore.list(sid, MAX_MESSAGES) : [];
    const memories = window.MemoryStore ? MemoryStore.all() : [];
    const riskEvents = typeof window.__riskLog === "function" ? window.__riskLog() : [];
    const reminders = window.Reminders ? Reminders.all() : [];
    const stats = window.Stats ? Stats.raw() : null;
    const server = await fetchServer(sid);
    const data = buildBackup({ sid, messages, memories, riskEvents, reminders, stats, server });
    const text = JSON.stringify(data, null, 2);
    const name = filename();
    const meta = {
      count: data.messages.length,
      reminders: data.reminders.length,
      server: !!data.server,
      slots: data.server ? data.server.slots.slots.length : 0,
    };
    // 安卓壳：写入 App 专属外部目录（无需存储权限），返回真实路径给用户看
    try {
      if (window.AndroidTTS && typeof window.AndroidTTS.saveBackup === "function") {
        const path = window.AndroidTTS.saveBackup(name, text);
        if (path) return Object.assign({ path, name }, meta);
      }
    } catch (e) { /* 回落浏览器下载 */ }
    download(text, name);
    return Object.assign({ path: "", name }, meta);
  }

  /** 导入：消息去重写入当前会话；记忆/提醒/统计/服务端会话合并还原 */
  async function importNow(text, sid) {
    const parsed = parseBackup(text);
    if (!parsed.ok) return parsed;
    const data = parsed.data;
    const existing = window.ChatStore ? await ChatStore.list(sid, MAX_MESSAGES) : [];
    const fresh = mergeMessages(existing, data.messages, MAX_MESSAGES);
    for (const m of fresh) {
      await ChatStore.append(sid, m);
    }
    if (fresh.length && window.ChatStore) {
      await ChatStore.prune(sid, ChatStore.MAX_KEEP);
    }
    let memAdded = 0;
    if (window.MemoryStore) {
      for (const m of data.memories) {
        if (MemoryStore.add(m.text, m.tags, m.kind)) memAdded++;
      }
    }
    // 提醒：按 id / 文案+时刻 去重合并，还原"几点几分该做什么"
    let remind = { added: 0, updated: 0 };
    if (window.Reminders) remind = Reminders.merge(data.reminders);
    // 小本本：逐项取较大值，换机等于完整还原，本机重复导入不翻倍
    if (data.stats && window.Stats) Stats.merge(data.stats);
    // 服务端会话（含记忆槽）：整体覆盖写回；失败只影响这一段，不阻断其余数据
    let server = null;
    if (data.server && window.API && typeof window.API.sessionImport === "function") {
      try { server = await window.API.sessionImport(sid, data.server); }
      catch (e) { server = { ok: false, error: e && e.message ? e.message : "服务端不可达" }; }
    }
    // 危机记录：合并进本机日志（不覆盖已有的，按 at+level+text 去重）
    let crises = 0;
    if (typeof localStorage !== "undefined" && data.riskEvents.length) {
      try {
        let existingRisk = [];
        try { existingRisk = JSON.parse(localStorage.getItem(RISK_LOG_KEY)) || []; } catch (e) {}
        const merged = mergeRiskEvents(existingRisk, data.riskEvents, MAX_RISK_EVENTS);
        crises = merged.length - existingRisk.length;
        localStorage.setItem(RISK_LOG_KEY, JSON.stringify(merged));
      } catch (e) { crises = 0; }
    }
    return { ok: true, imported: fresh.length, skipped: data.messages.length - fresh.length,
             memories: memAdded, crises, reminders: remind.added, remindersUpdated: remind.updated,
             stats: !!data.stats, server };
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      if (!file) { reject(new Error("没有选择文件")); return; }
      const r = new FileReader();
      r.onload = () => resolve(String(r.result || ""));
      r.onerror = () => reject(new Error("文件读取失败"));
      r.readAsText(file);
    });
  }

  return {
    APP_TAG, VERSION, MAX_MESSAGES, MAX_RISK_EVENTS, MAX_REMINDERS,
    buildBackup, parseBackup, mergeMessages, mergeRiskEvents, filename,
    normalizeReminder, normalizeStats, normalizeServer,
    exportNow, importNow, readFile,
  };
})();

if (typeof module === "object" && module.exports) module.exports = Backup;
if (typeof window !== "undefined") window.Backup = Backup;
