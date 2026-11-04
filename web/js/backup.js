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
  const VERSION = 2;
  const MAX_MESSAGES = 500;
  const MAX_CONTENT = 1000;
  const MAX_RISK_EVENTS = 50;
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
    return {
      app: APP_TAG,
      version: VERSION,
      exportedAt: new Date(Number(src.exportedAt) || Date.now()).toISOString(),
      sid: String(src.sid || ""),
      messages,
      memories,
      riskEvents,
    };
  }

  /** 解析导入文件；失败时返回 { ok:false, error: 用户看得懂的原因 } */
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

  /** 导出当前会话（消息 + 记忆） */
  async function exportNow(sid) {
    const messages = window.ChatStore ? await ChatStore.list(sid, MAX_MESSAGES) : [];
    const memories = window.MemoryStore ? MemoryStore.all() : [];
    const riskEvents = typeof window.__riskLog === "function" ? window.__riskLog() : [];
    const data = buildBackup({ sid, messages, memories, riskEvents });
    const text = JSON.stringify(data, null, 2);
    const name = filename();
    // 安卓壳：写入 App 专属外部目录（无需存储权限），返回真实路径给用户看
    try {
      if (window.AndroidTTS && typeof window.AndroidTTS.saveBackup === "function") {
        const path = window.AndroidTTS.saveBackup(name, text);
        if (path) return { path, count: data.messages.length, name };
      }
    } catch (e) { /* 回落浏览器下载 */ }
    download(text, name);
    return { path: "", count: data.messages.length, name };
  }

  /** 导入：消息去重写入当前会话；记忆按文本去重合并 */
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
             memories: memAdded, crises };
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
    APP_TAG, VERSION, MAX_MESSAGES, MAX_RISK_EVENTS,
    buildBackup, parseBackup, mergeMessages, mergeRiskEvents, filename,
    exportNow, importNow, readFile,
  };
})();

if (typeof module === "object" && module.exports) module.exports = Backup;
if (typeof window !== "undefined") window.Backup = Backup;
