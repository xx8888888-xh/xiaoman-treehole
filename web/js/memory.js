/* ============================================================
 * memory.js · 小满的长期记忆库
 * ------------------------------------------------------------
 * 设计原则：
 *  1. 记在用户设备本地（localStorage），用户可在设置里查看/删除
 *     ——朋友记得你，但不该背着你偷偷记
 *  2. 索引检索：关键词命中 + 时间衰减，取最相关的几条注入 prompt
 *  3. 写入三条路：模型 memory_updates / 用户显式"记住…" / 规则提取兜底
 * ============================================================ */

const MemoryStore = (() => {
  const KEY = "xiaoman_memories_v1";
  const MAX = 200;              // 容量上限，FIFO 淘汰最旧的低分记忆
  // 钉子户：永远注入。P0-1 扩充——大事（考试/搬家等近事件，开场要引用）、宠物（长期存在的事实）
  const PIN_KEYS = ["昵称", "生日", "重要日", "大事", "宠物"];
  let lastHitsSave = 0;                         // hits 回写节流时间戳

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; }
  }
  function save(list) { localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))); }

  /** 新增一条记忆（去重：同文本忽略） */
  function add(text, tags, kind) {
    text = (text || "").trim().slice(0, 120);
    if (!text) return false;
    const list = load();
    if (list.some(m => m.text === text)) return false;
    list.unshift({
      id: Date.now() + "_" + Math.random().toString(36).slice(2, 6),
      text, tags: tags || [], kind: kind || "fact",
      ts: Date.now(), hits: 0
    });
    save(list);
    return true;
  }

  /** 删除同 key 的旧记忆：同键新值覆盖，避免旧昵称以 pin 身份永远注入 */
  function removeByKey(key) {
    save(load().filter(m => !(m.tags && m.tags[0] === key) && !String(m.text).startsWith(key + "：")));
  }

  /** 模型 memory_updates（{键:值}）批量入馆（同键覆盖，只保留最新值） */
  function addUpdates(obj) {
    let n = 0;
    if (obj && typeof obj === "object") {
      for (const k of Object.keys(obj)) {
        const v = obj[k];
        if (v == null || v === "") continue;
        removeByKey(k);
        if (add(`${k}：${v}`, [k], PIN_KEYS.includes(k) ? "pin" : "fact")) n++;
      }
    }
    return n;
  }

  /** 删除 */
  function remove(id) { save(load().filter(m => m.id !== id)); }
  function clear() { localStorage.removeItem(KEY); }

  /**
   * 检索：与 query 相关的记忆（用于注入 prompt）
   * 打分 = 关键词重合 + 时间衰减 + 钉子户加成 + 使用频率
   */
  function recall(query, k) {
    k = k || 5;
    const q = (query || "").toLowerCase();
    const now = Date.now();
    const scored = load().map(m => {
      let s = 0;
      const text = m.text.toLowerCase();
      // 关键词重合：整词 + 中文2-gram滑窗（中文无分词，滑窗最可靠）
      const qTokens = new Set(q.match(/[一-龥a-z0-9]{2,}/g) || []);
      const grams = [];
      for (const seg of (q.match(/[一-龥]+/g) || []))
        for (let i = 0; i < seg.length - 1; i++) grams.push(seg.slice(i, i + 2)); // 重叠滑窗！
      for (const w of qTokens) if (text.includes(w)) s += 2.5;
      for (const g of grams) if (text.includes(g)) s += 1.2;
      // 时间衰减：7天内满血，之后约 3%/天（≈21%/周），下限 0.2
      const ageDays = (now - m.ts) / 86400000;
      s *= Math.max(0.2, 1 - Math.max(0, ageDays - 7) * 0.03);
      // 钉子户与常用记忆
      if (m.kind === "pin" || PIN_KEYS.some(p => m.text.startsWith(p))) s += 5;
      s += (m.hits || 0) * 0.1;
      return { m, s };
    }).filter(x => x.s > 0.5)
      .sort((a, b) => b.s - a.s)
      .slice(0, k);
    // 命中计数（下次权重+）：节流写盘，避免每条消息都落 localStorage
    const list = load();
    for (const hit of scored) {
      const item = list.find(x => x.id === hit.m.id);
      if (item) item.hits = (item.hits || 0) + 1;
    }
    if (Date.now() - lastHitsSave > 5000) { lastHitsSave = Date.now(); save(list); }
    return scored.map(x => x.m);
  }

  /**
   * 渲染注入文本：钉子户全量 + 检索相关
   * （这就是 system prompt 里的【你记得的关于ta的事】）
   */
  function renderContext(query) {
    const list = load();
    if (!list.length) return "";
    const pinned = list.filter(m => m.kind === "pin" || PIN_KEYS.some(p => m.text.startsWith(p)));
    const recalled = recall(query, 5).filter(m => !pinned.includes(m));
    const lines = [...new Set([...pinned, ...recalled].map(m => `- ${m.text}`))];
    return lines.slice(0, 8).join("\n");
  }

  /** 全量（设置页记忆抽屉用，新→旧） */
  function all() { return load(); }

  return { add, addUpdates, remove, clear, recall, renderContext, all };
})();
window.MemoryStore = MemoryStore;   // const 不挂 window，跨模块守卫需显式挂载
