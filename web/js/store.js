/* ============================================================
 * store.js · 聊天历史持久化（IndexedDB）
 * ------------------------------------------------------------
 * 目标：刷新/重启浏览器后历史不丢。会话统一使用 API.sessionId()
 * 写入 localStorage 的同一个 xiaoman_sid。
 * 结构：db=xiaoman_chat, store=messages{id auto, sid, role, content, at}
 *       index=sid_at([sid, at])，按会话倒序取最近 N 条。
 * ============================================================ */
const ChatStore = (() => {
  "use strict";

  const DB_NAME = "xiaoman_chat";
  const DB_VERSION = 1;
  const STORE = "messages";
  const MAX_KEEP = 300;   // 每个会话最多保留条数
  let dbPromise = null;

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      if (typeof indexedDB === "undefined") {
        reject(new Error("当前环境不支持 IndexedDB"));
        return;
      }
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
          store.createIndex("sid_at", ["sid", "at"]);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error("IndexedDB 打开失败"));
    });
    return dbPromise;
  }

  function txDone(tx) {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error("事务中止"));
    });
  }

  async function append(sid, msg) {
    const db = await open();
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).add({
      sid: String(sid),
      role: msg.role === "user" ? "user" : "assistant",
      content: String(msg.content == null ? "" : msg.content),
      at: Number(msg.at) || Date.now()
    });
    await txDone(tx);
  }

  async function list(sid, limit) {
    const db = await open();
    const max = Math.max(1, Number(limit) || 30);
    const tx = db.transaction(STORE, "readonly");
    const index = tx.objectStore(STORE).index("sid_at");
    const range = IDBKeyRange.bound([String(sid), 0], [String(sid), Number.MAX_SAFE_INTEGER]);
    const out = [];
    await new Promise((resolve, reject) => {
      const req = index.openCursor(range, "prev");
      req.onsuccess = () => {
        const cur = req.result;
        if (!cur) { resolve(); return; }
        out.push({ role: cur.value.role, content: cur.value.content, at: cur.value.at });
        if (out.length >= max) { resolve(); return; }
        cur.continue();
      };
      req.onerror = () => reject(req.error);
    });
    return out.reverse();
  }

  async function count(sid) {
    const db = await open();
    const tx = db.transaction(STORE, "readonly");
    const index = tx.objectStore(STORE).index("sid_at");
    const range = IDBKeyRange.bound([String(sid), 0], [String(sid), Number.MAX_SAFE_INTEGER]);
    return new Promise((resolve, reject) => {
      const req = index.count(range);
      req.onsuccess = () => resolve(req.result || 0);
      req.onerror = () => reject(req.error);
    });
  }

  /** 超出上限时删掉最旧的（保持会话体量可控） */
  async function prune(sid, keep) {
    const limit = Math.max(1, Number(keep) || MAX_KEEP);
    const total = await count(sid);
    if (total <= limit) return 0;
    const db = await open();
    const tx = db.transaction(STORE, "readwrite");
    const index = tx.objectStore(STORE).index("sid_at");
    const range = IDBKeyRange.bound([String(sid), 0], [String(sid), Number.MAX_SAFE_INTEGER]);
    let removed = 0;
    await new Promise((resolve, reject) => {
      const req = index.openCursor(range, "next");
      req.onsuccess = () => {
        const cur = req.result;
        if (!cur || removed >= total - limit) { resolve(); return; }
        cur.delete();
        removed += 1;
        cur.continue();
      };
      req.onerror = () => reject(req.error);
    });
    await txDone(tx);
    return removed;
  }

  async function clear(sid) {
    const db = await open();
    const tx = db.transaction(STORE, "readwrite");
    const index = tx.objectStore(STORE).index("sid_at");
    const range = IDBKeyRange.bound([String(sid), 0], [String(sid), Number.MAX_SAFE_INTEGER]);
    await new Promise((resolve, reject) => {
      const req = index.openCursor(range);
      req.onsuccess = () => {
        const cur = req.result;
        if (!cur) { resolve(); return; }
        cur.delete();
        cur.continue();
      };
      req.onerror = () => reject(req.error);
    });
    await txDone(tx);
  }

  return { open, append, list, count, prune, clear, MAX_KEEP };
})();

if (typeof module === "object" && module.exports) module.exports = ChatStore;
