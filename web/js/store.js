/* ============================================================
 * store.js · 聊天历史持久化（IndexedDB）
 * ------------------------------------------------------------
 * 目标：刷新/重启浏览器后历史不丢。会话统一使用 API.sessionId()
 * 写入 localStorage 的同一个 xiaoman_sid。
 * 结构：db=xiaoman_chat, store=messages{id auto, sid, role, content, at}
 *       index=sid_at(["sid","at"])，按会话倒序取最近 N 条；
 *       index=sid_at_id(["sid","at","id"])，历史分页的稳定游标
 *       （同一毫秒内多条时用自增 id 作为第二排序键，翻页不重不漏）。
 * ============================================================ */
const ChatStore = (() => {
  "use strict";

  const DB_NAME = "xiaoman_chat";
  const DB_VERSION = 2;   // v2：新增 sid_at_id 复合索引（老库升级时自动回填）
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
          store.createIndex("sid_at_id", ["sid", "at", "id"]);
          return;
        }
        // 老库（v1）只有 sid_at：新建复合索引时 IndexedDB 会自动为存量记录回填
        const store = req.transaction.objectStore(STORE);
        if (!store.indexNames.contains("sid_at_id")) {
          store.createIndex("sid_at_id", ["sid", "at", "id"]);
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

  function rangeFor(sid, at, id) {
    const lo = [String(sid), 0, 0];
    if (at == null) return IDBKeyRange.bound(lo, [String(sid), Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER]);
    if (id == null) {
      // 退化分支：游标没有 id（旧记录/未回填）。只取严格更早的时间，
      // 避免把游标自身带回；同一毫秒的兄弟记录会被跳过——调用方应始终传 id。
      return IDBKeyRange.bound(lo, [String(sid), Number(at), Number.MAX_SAFE_INTEGER], false, true);
    }
    return IDBKeyRange.bound(lo, [String(sid), Number(at), Number(id)], false, true);
  }

  /** 游标遍历（单事务内）。dir="prev" 取更早，max 为上限；返回 {id, role, content, at} */
  function walk(index, range, dir, max) {
    const out = [];
    return new Promise((resolve, reject) => {
      const req = index.openCursor(range, dir);
      req.onsuccess = () => {
        const cur = req.result;
        if (!cur || out.length >= max) { resolve(out); return; }
        out.push({
          id: cur.value.id,
          role: cur.value.role,
          content: cur.value.content,
          at: cur.value.at
        });
        cur.continue();
      };
      req.onerror = () => reject(req.error);
    });
  }

  /** 追加一条；resolve 为自增主键 id（历史分页游标的第二键） */
  async function append(sid, msg) {
    const db = await open();
    const tx = db.transaction(STORE, "readwrite");
    const req = tx.objectStore(STORE).add({
      sid: String(sid),
      role: msg.role === "user" ? "user" : "assistant",
      content: String(msg.content == null ? "" : msg.content),
      at: Number(msg.at) || Date.now()
    });
    const id = await new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    await txDone(tx);
    return id;
  }

  /** 最近 limit 条（升序，含 id）：首屏恢复用 */
  async function list(sid, limit) {
    const db = await open();
    const max = Math.max(1, Number(limit) || 30);
    const tx = db.transaction(STORE, "readonly");
    const index = tx.objectStore(STORE).index("sid_at_id");
    const out = await walk(index, rangeFor(sid), "prev", max);
    return out.reverse();
  }

  /**
   * 分页取更早：严格早于游标 before={at,id} 的最近 limit 条（升序，含 id）。
   * before 为空时取最旧的一页（便于自检/首次填充）。
   * 游标双键：同毫秒多条时靠自增 id 定位，保证不重复、不跳条、不丢条。
   */
  async function listBefore(sid, limit, before) {
    const db = await open();
    const max = Math.max(1, Number(limit) || 10);
    const tx = db.transaction(STORE, "readonly");
    const index = tx.objectStore(STORE).index("sid_at_id");
    if (!before) {
      const base = await walk(index, rangeFor(sid), "next", max);
      return base;
    }
    const at = Number(before.at);
    if (!Number.isFinite(at)) return [];
    const id = before.id == null ? null : Number(before.id);
    const rows = await walk(index, rangeFor(sid, at, id), "prev", max);
    const out = id == null ? rows.filter(m => m.at < at) : rows;
    return out.reverse();
  }

  async function count(sid) {
    const db = await open();
    const tx = db.transaction(STORE, "readonly");
    const index = tx.objectStore(STORE).index("sid_at_id");
    return new Promise((resolve, reject) => {
      const req = index.count(rangeFor(sid));
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
    const index = tx.objectStore(STORE).index("sid_at_id");
    const range = rangeFor(sid);
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
    const index = tx.objectStore(STORE).index("sid_at_id");
    const range = rangeFor(sid);
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

  return { open, append, list, listBefore, count, prune, clear, MAX_KEEP };
})();

if (typeof module === "object" && module.exports) module.exports = ChatStore;
// 浏览器全局：classic script 的顶层 const 不会挂到 window，显式导出供 app.js 使用
if (typeof window !== "undefined") window.ChatStore = ChatStore;
