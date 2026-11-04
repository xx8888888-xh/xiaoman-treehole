/* ============================================================
 * chat_pager.js · 长会话分页（自研，零依赖，约 150 行）
 * ------------------------------------------------------------
 * 只把「当前页 + 缓冲」渲染进 DOM：窗口为 [start, start+VIEW)。
 * VIEW = PAGE(10) + BUFFER(4) = 14，200 轮之后 DOM 行数也不增长。
 * 规则：
 *  - 贴底时新消息自动跟进；用户正在翻历史时不打断，只在底部提示
 *    「N 条新消息」，滚回底部或点提示即回到最新（不抢滚动位置）。
 *  - 向上滚到头自动翻一页；若注入 loader（IndexedDB 更早记录），
 *    始终复用同一条占位条显示「正在翻出…」，占位不销毁、不闪烁。
 *  - 翻页后按 scrollHeight 差值回填 scrollTop，内容不跳动。
 *  - 全量记录留在内存数组里（只是引用），不进 DOM。
 * ============================================================ */
const ChatPager = (() => {
  const PAGE = 10;          // 每次翻页条数
  const BUFFER = 4;         // 上下缓冲合计：一页 + 缓冲
  const VIEW = PAGE + BUFFER;
  const EDGE = 24;          // 距顶多少 px 触发向上翻页
  const BOTTOM_GAP = 48;    // 距底多少 px 视为「贴着底部」
  const THROTTLE = 250;     // 翻页节流（scroll 事件会因 scrollTop 回填再次触发）

  let host = null, typing = null, scroll = null, build = null, doc = null;
  let loader = null;        // async (oldest) => 更早的记录数组（可选，持久化历史用）
  let recs = [];            // 全量记录（内存）
  let rendered = [];        // 当前挂到 DOM 的行
  let start = 0;            // 窗口起点
  let busy = false;         // 正在异步翻页（占位条保持显示）
  let exhausted = false;    // loader 已确认为空：到底了
  let lastEdgeAt = 0;
  let topBar = null, bottomBar = null;

  function el(tag, cls, text) {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function mount(opts) {
    host = opts.host;
    typing = opts.typing || null;
    scroll = opts.scroll || null;
    build = opts.build;
    loader = typeof opts.loader === "function" ? opts.loader : null;
    doc = opts.doc || host.ownerDocument;
    recs = [];
    rendered = [];
    start = 0;
    busy = false;
    exhausted = false;
    lastEdgeAt = 0;
    // 两条占位条常驻 DOM（用 hidden 切换显隐）：翻页期间只改文字，不重建节点
    topBar = el("div", "msg-row window-tip hidden", "更早的消息");
    bottomBar = el("div", "msg-row window-tip hidden", "新消息");
    topBar.onclick = () => { older(); };
    bottomBar.onclick = () => { jumpLatest(); };
    host.insertBefore(topBar, typing);
    host.insertBefore(bottomBar, typing);
    if (scroll) {
      if (typeof scroll.addEventListener === "function") {
        scroll.addEventListener("scroll", onScroll, { passive: true });
      } else {
        scroll.onscroll = onScroll;
      }
    }
    syncBars();
  }

  /** 是否贴着底部（没有布局信息时按 true 处理，保持旧行为） */
  function nearBottom() {
    if (!scroll || !scroll.scrollHeight) return true;
    return scroll.scrollHeight - scroll.scrollTop - (scroll.clientHeight || 0) <= BOTTOM_GAP;
  }

  /**
   * 重建窗口。anchor：
   *  - "bottom"：新消息/回到底部，滚到最新
   *  - "keep"：翻页，保持当前内容在视口里的位置（按高度差回填）
   */
  function render(anchor) {
    const total = recs.length;
    if (total <= VIEW) start = 0;
    else start = Math.max(0, Math.min(start, total - VIEW));
    const h0 = scroll ? scroll.scrollHeight : 0;
    const y0 = scroll ? scroll.scrollTop : 0;
    for (const node of rendered) if (node.parentElement === host) host.removeChild(node);
    rendered = [];
    const end = Math.min(total, start + VIEW);
    for (let i = start; i < end; i++) {
      const node = build(recs[i], i);
      host.insertBefore(node, typing);
      rendered.push(node);
    }
    host.insertBefore(bottomBar, typing);   // 底栏始终排在最后一行之后
    syncBars();
    if (!scroll) return;
    if (anchor === "bottom") scroll.scrollTop = scroll.scrollHeight;
    else if (anchor === "keep") scroll.scrollTop = y0 + (scroll.scrollHeight - h0);
  }

  function syncBars() {
    if (!topBar) return;
    const total = recs.length;
    const end = Math.min(total, start + VIEW);
    // 有 loader 且窗口里已有内容时，顶栏始终可见：还能翻 / 已到底都给明确说法
    const canOlder = start > 0 || (!!loader && total > 0 && !exhausted);
    const showTop = total > 0 && (start > 0 || !!loader);
    topBar.className = "msg-row window-tip" + (busy || showTop ? "" : " hidden");
    if (busy) topBar.textContent = "正在翻出更早的消息…";
    else if (start > 0) topBar.textContent = `↑ 更早的消息（还有 ${start} 条）`;
    else if (exhausted) topBar.textContent = "没有更早了";
    else topBar.textContent = "↑ 更早的消息";
    // 到这里没有更早记录时，顶栏保留提示但不再触发实际加载（visible 语义供 E2E 断言）
    if (topBar.dataset) topBar.dataset.canOlder = canOlder ? "1" : "0";
    const fresh = total - end;
    bottomBar.className = "msg-row window-tip" + (fresh > 0 ? "" : " hidden");
    bottomBar.textContent = `↓ ${fresh} 条新消息`;
  }

  /** 追加一条消息；返回句柄（记录 + 已渲染节点，未渲染时 el=null） */
  function push(rec) {
    const handle = { rec, el: null };
    recs.push(rec);
    if (nearBottom()) {
      start = Math.max(0, recs.length - VIEW);
      render("bottom");
      handle.el = rendered[rendered.length - 1] || null;
    } else {
      syncBars();   // 用户在看历史：不打断，只提示底部有新消息
    }
    return handle;
  }

  /** 向上翻一页；到头且有 loader 时异步取更早记录（占位条原地等待）
   *  loader 收到窗口里最旧的一条记录（游标 {at,id}），由数据层据此取 strictly older。 */
  async function older() {
    if (busy || !recs.length) return false;
    if (start > 0) {
      start = Math.max(0, start - PAGE);
      render("keep");
      return true;
    }
    if (!loader || exhausted) return false;
    busy = true;
    syncBars();
    let list = [];
    try { list = (await loader(recs[0])) || []; }
    catch (e) { list = null; }
    finally { busy = false; }
    if (list === null) { syncBars(); return false; }   // 异常：保留 exhausted 状态，允许重试
    // 防御：数据层若因游标退化重复返回窗口内的记录，按 id 去重（不重复、不跳条）
    const known = new Set(recs.map(r => r.id).filter(v => v != null));
    if (known.size) list = list.filter(r => r.id == null || !known.has(r.id));
    exhausted = list.length === 0;
    if (exhausted) { syncBars(); return false; }
    recs = list.concat(recs);
    start += list.length;   // 保持当前阅读位置，更早的内容在窗口上方
    render("keep");
    return true;
  }

  /** 回到最新（点击底栏或滚到底部自动触发） */
  function jumpLatest() {
    start = Math.max(0, recs.length - VIEW);
    render("bottom");
  }

  function onScroll() {
    if (busy || !scroll) return;
    const now = Date.now();
    if (now - lastEdgeAt < THROTTLE) return;
    lastEdgeAt = now;
    if (scroll.scrollTop <= EDGE) { older(); return; }
    if (nearBottom() && start + VIEW < recs.length) jumpLatest();
  }

  /** 语音已播标记：记录写到数据里，重渲染也不会丢；节点在窗口内则原地换新 */
  function markVoice(handle) {
    if (!handle || !handle.rec) return false;
    handle.rec.spoken = true;
    if (!handle.el || handle.el.parentElement !== host) return true;
    const fresh = build(handle.rec);
    const i = rendered.indexOf(handle.el);
    host.replaceChild(fresh, handle.el);
    if (i >= 0) rendered[i] = fresh;
    handle.el = fresh;
    return true;
  }

  function stats() {
    return {
      total: recs.length,
      rendered: rendered.length,
      start,
      end: Math.min(recs.length, start + VIEW),
      page: PAGE,
      view: VIEW,
      exhausted,
      first: recs.length ? recs[0].text : null,
      last: recs.length ? recs[recs.length - 1].text : null,
      windowFirst: recs[start] ? recs[start].text : null,
      windowLast: recs[Math.min(recs.length - 1, start + VIEW - 1)]
        ? recs[Math.min(recs.length - 1, start + VIEW - 1)].text : null
    };
  }

  const api = { mount, push, older, jumpLatest, markVoice, stats, PAGE, VIEW };
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.ChatPager = api;   // classic script 顶层 const 不挂 window
  return api;
})();
