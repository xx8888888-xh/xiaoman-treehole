/* idb_stub.js · 极简内存版 IndexedDB 桩（仅测试用，零依赖）
 * ------------------------------------------------------------
 * 只实现 ChatStore 用到的子集：
 *   open / onupgradeneeded / createObjectStore / createIndex / indexNames
 *   transaction.objectStore / add / index.openCursor / index.count
 *   复合键（数组）比较、IDBKeyRange.bound（含开区间）、cursor.continue/delete
 * 事务语义：请求按微任务派发；pending 归零后触发 oncomplete。
 * 不覆盖真实 IDB 的全部行为，但复合索引的排序/区间语义与规范一致。
 */

function cmpKey(a, b) {
  const aa = Array.isArray(a) ? a : [a];
  const bb = Array.isArray(b) ? b : [b];
  const n = Math.min(aa.length, bb.length);
  for (let i = 0; i < n; i++) {
    const c = cmpValue(aa[i], bb[i]);
    if (c) return c;
  }
  return aa.length - bb.length;
}

function cmpValue(a, b) {
  if (typeof a === "number" && typeof b === "number") return a === b ? 0 : (a < b ? -1 : 1);
  if (a === b) return 0;
  return String(a) < String(b) ? -1 : 1;
}

class FakeKeyRange {
  constructor(lower, upper, lowerOpen, upperOpen) {
    this.lower = lower;
    this.upper = upper;
    this.lowerOpen = !!lowerOpen;
    this.upperOpen = !!upperOpen;
  }
  static bound(lower, upper, lowerOpen, upperOpen) {
    return new FakeKeyRange(lower, upper, lowerOpen, upperOpen);
  }
}

function inRange(key, range) {
  if (!range) return true;
  if (range.lower !== undefined) {
    const c = cmpKey(key, range.lower);
    if (c < 0 || (c === 0 && range.lowerOpen)) return false;
  }
  if (range.upper !== undefined) {
    const c = cmpKey(key, range.upper);
    if (c > 0 || (c === 0 && range.upperOpen)) return false;
  }
  return true;
}

class FakeTransaction {
  constructor(db) {
    this.db = db;
    this._pending = 0;
    this._closed = false;
    this.oncomplete = null;
    this.onerror = null;
    this.onabort = null;
  }

  objectStore(name) {
    const store = this.db._stores.get(name);
    if (!store) throw new Error("NotFoundError: " + name);
    return new FakeStoreHandle(this, store);
  }

  _enter() { this._pending += 1; }

  _leave() {
    this._pending -= 1;
    if (this._pending === 0 && !this._closed) {
      this._closed = true;
      // 用宏任务让调用方有机会先挂上 oncomplete（真实 IDB 也在 success 之后的下一个任务里完成）
      setTimeout(() => { if (this.oncomplete) this.oncomplete(); }, 0);
    }
  }

  /** 派发一个请求：exec 同步求值；cursor 续期由调用方在 onsuccess 里置 _cont */
  _request(exec) {
    const req = { result: undefined, error: null, onsuccess: null, onerror: null, _cont: false };
    const tx = this;
    tx._enter();
    const step = () => {
      try {
        req.result = exec();
      } catch (e) {
        req.error = e;
        if (req.onerror) req.onerror();
        tx._leave();
        return;
      }
      if (req.result && typeof req.result === "object" && req.result.continue) {
        req.result._req = req;
      }
      if (req.onsuccess) req.onsuccess();
      if (req._cont) {
        req._cont = false;
        tx._enter();
        queueMicrotask(step);
      }
      tx._leave();
    };
    queueMicrotask(step);
    return req;
  }
}

class FakeIndex {
  constructor(store, keyPath, tx) {
    this.store = store;
    this.keyPath = keyPath;
    this.tx = tx;
  }

  _keyOf(value) {
    return this.keyPath.map(p => value[p]);
  }

  _entries(range, direction) {
    const snap = Array.from(this.store.records.keys())
      .map(pk => ({ pk, key: this._keyOf(this.store.records.get(pk)) }))
      .filter(e => inRange(e.key, range));
    snap.sort((a, b) => cmpKey(a.key, b.key));
    if (direction === "prev") snap.reverse();
    return snap;
  }

  openCursor(range, direction) {
    const entries = this._entries(range, direction);
    const store = this.store;
    let i = 0;
    const tx = this.tx;
    return tx._request(() => {
      // 跳过已被其他游标删除的记录（prune 场景）
      while (i < entries.length && !store.records.has(entries[i].pk)) i++;
      if (i >= entries.length) return null;
      const e = entries[i++];
      const value = store.records.get(e.pk);
      const key = e.key;
      return {
        key,
        primaryKey: e.pk,
        value,
        continue() { /* 由外层 _request 的 step 读取 */ this._req._cont = true; },
        delete() { store.records.delete(e.pk); return null; }
      };
    });
  }

  count(range) {
    return this.tx._request(() => this._entries(range, "next").length);
  }
}

class FakeStoreHandle {
  constructor(tx, store) {
    this.tx = tx;
    this.store = store;
  }

  add(value) {
    const s = this.store;
    let key;
    if (s.autoIncrement) {
      s.nextId += 1;
      key = s.nextId;
      if (s.keyPath) value[s.keyPath] = key;
    } else {
      key = s.keyPath ? value[s.keyPath] : undefined;
    }
    s.records.set(key, value);
    const tx = this.tx;
    return tx._request(() => key);
  }

  index(name) {
    const keyPath = this.store.indexes.get(name);
    if (!keyPath) throw new Error("NotFoundError: index " + name);
    return new FakeIndex(this.store, keyPath, this.tx);
  }

  get(key) {
    return this.tx._request(() => this.store.records.get(key));
  }

  put(value) {
    const s = this.store;
    const key = s.keyPath ? value[s.keyPath] : undefined;
    s.records.set(key, value);
    return this.tx._request(() => key);
  }

  delete(key) {
    const existed = this.store.records.delete(key);
    return this.tx._request(() => existed);
  }
}

class FakeObjectStoreNames {
  constructor(db) { this.db = db; }
  contains(name) { return this.db._stores.has(name); }
}

class FakeDB {
  constructor(name, version) {
    this.name = name;
    this.version = version;
    this._stores = new Map();
    this.objectStoreNames = new FakeObjectStoreNames(this);
  }

  createObjectStore(name, opts) {
    const store = {
      name,
      keyPath: (opts && opts.keyPath) || null,
      autoIncrement: !!(opts && opts.autoIncrement),
      nextId: 0,
      records: new Map(),
      indexes: new Map(),
      tx: null
    };
    this._stores.set(name, store);
    return {
      createIndex: (indexName, keyPath) => { store.indexes.set(indexName, keyPath); },
      indexNames: { contains: n => store.indexes.has(n) }
    };
  }

  transaction(name, mode) {
    this._stores.get(name);
    return new FakeTransaction(this);
  }
}

class FakeIndexedDB {
  constructor() { this._dbs = new Map(); }

  open(name, version) {
    const req = { result: null, onsuccess: null, onerror: null, onupgradeneeded: null, transaction: null };
    queueMicrotask(() => {
      let db = this._dbs.get(name);
      if (!db) {
        db = new FakeDB(name, 0);
        this._dbs.set(name, db);
      }
      const target = version || 1;
      req.result = db;
      if (db.version < target) {
        db.version = target;
        const upgradeTx = new FakeTransaction(db);
        req.transaction = upgradeTx;
        if (req.onupgradeneeded) req.onupgradeneeded();
      }
      queueMicrotask(() => { if (req.onsuccess) req.onsuccess(); });
    });
    return req;
  }
}

module.exports = { FakeIndexedDB, FakeKeyRange, cmpKey, inRange };
