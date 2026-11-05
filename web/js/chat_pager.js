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
  const FOLLOW_SLACK = 96;  // 原本贴底时，容忍内容增高后 1 行内的偏移仍继续跟随
  const THROTTLE = 250;     // 翻页节流（scroll 事件会因 scrollTop 回填再次触发）

  let host = null, typing = null, scroll = null, build = null, doc = null;
  let loader = null;        // async (oldest) => 更早的记录数组（可选，持久化历史用）
  let recs = [];            // 全量记录（内存）
  let rendered = [];        // 当前挂到 DOM 的行
  let start = 0;            // 窗口起点
  let busy = false;         // 正在异步翻页（占位条保持显示）
  let exhausted = false;    // loader 已确认为空：到底了
  let lastEdgeAt = 0;
  let pinned = true;        // 是否"跟着最新"：仅在用户手动上滚时置 false
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
    pinned = true;
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
        scroll.addEventListener("scroll", syncFade, { passive: true });
      } else {
        scroll.onscroll = onScroll;
      }
      syncFade();
    }
    syncBars();
  }

  /** 滚动时给对话区加 is-scrolled：顶部渐隐层只在有历史内容被裁切时出现 */
  function syncFade() {
    if (!scroll || !scroll.parentElement) return;
    scroll.parentElement.classList.toggle("is-scrolled", (scroll.scrollTop || 0) > 4);
  }

  /** 离底部的滚动距离（没有布局信息时按 0，保持旧行为） */
  function scrollGap() {
    if (!scroll || !scroll.scrollHeight) return 0;
    return scroll.scrollHeight - scroll.scrollTop - (scroll.clientHeight || 0);
  }

  function nearBottom() {
    return scrollGap() <= BOTTOM_GAP;
  }

  /**
   * 分页窗口里第 i 条的上一条时间戳（供时间分隔用）。
   * 向前找最近一条带 at 的记录：窗口首行也能拿到窗口外的前一条，
   * 翻页后"同一天/跨天"的判定不会因为窗口边界而错位。
   * 找不到（整段都没有时间戳）返回 null，交由调用方决定是否放弃分隔。
   */
  function prevStamp(i) {
    for (let k = i - 1; k >= 0; k--) {
      const raw = recs[k] && recs[k].at;
      if (raw == null || raw === "") continue;   // 缺失时间戳不能当 0 用
      const at = Number(raw);
      if (Number.isFinite(at)) return at;
    }
    return null;
  }

  /**
   * 状态区（错误卡/旁白行）出现或收起后重新贴底。
   * 只在用户仍"跟随最新"（pinned）时生效：主动上滑看历史时绝不抢滚动位置。
   * 用 behavior:"instant" 绕开 CSS 的 scroll-behavior:smooth，
   * 否则高度变化的瞬间视口还在缓动，最后一条会被顶出半行。
   */
  function followBottom() {
    if (!scroll || !pinned) return false;
    const top = scroll.scrollHeight || 0;
    if (typeof scroll.scrollTo === "function") {
      scroll.scrollTo({ top, left: 0, behavior: "instant" });
    } else {
      scroll.scrollTop = top;
    }
    return true;
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
    // 高度差回填只在翻页(keep)时需要；bottom 直接滚底，省一次强制布局读取
    const keep = !!scroll && anchor === "keep";
    const h0 = keep ? scroll.scrollHeight : 0;
    const y0 = keep ? scroll.scrollTop : 0;
    for (const node of rendered) if (node.parentElement === host) host.removeChild(node);
    rendered = [];
    const end = Math.min(total, start + VIEW);
    // 整页重建走 DocumentFragment：一次插入一个片段，减少逐条插入引发的样式/布局计算
    const frag = doc && typeof doc.createDocumentFragment === "function"
      ? doc.createDocumentFragment() : null;
    for (let i = start; i < end; i++) {
      const node = build(recs[i], i, i > 0 ? recs[i - 1] : null, prevStamp(i));
      if (frag) frag.appendChild(node); else host.insertBefore(node, typing);
      rendered.push(node);
    }
    if (frag) host.insertBefore(frag, typing);
    host.insertBefore(bottomBar, typing);   // 底栏始终排在最后一行之后
    syncBars();
    if (!scroll) return;
    if (anchor === "bottom") scroll.scrollTop = scroll.scrollHeight;
    else if (anchor === "keep") scroll.scrollTop = y0 + (scroll.scrollHeight - h0);
  }

  /** 只在值真的变化时才写 DOM：push 每条都会走 syncBars，重复写同一串
   *  仍会替换文本节点/触发样式失效，逐字流式下是纯浪费。 */
  function setClass(node, cls) {
    if (node && node.className !== cls) node.className = cls;
  }

  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  function syncBars() {
    if (!topBar) return;
    const total = recs.length;
    const end = Math.min(total, start + VIEW);
    // 有 loader 且窗口里已有内容时，顶栏始终可见：还能翻 / 已到底都给明确说法
    const canOlder = start > 0 || (!!loader && total > 0 && !exhausted);
    const showTop = total > 0 && (start > 0 || !!loader);
    setClass(topBar, "msg-row window-tip" + (busy || showTop ? "" : " hidden"));
    if (busy) setText(topBar, "正在翻出更早的消息…");
    else if (start > 0) setText(topBar, `↑ 更早的消息（还有 ${start} 条）`);
    else if (exhausted) setText(topBar, "没有更早了");
    else setText(topBar, "↑ 更早的消息");
    // 到这里没有更早记录时，顶栏保留提示但不再触发实际加载（visible 语义供 E2E 断言）
    if (topBar.dataset) {
      const flag = canOlder ? "1" : "0";
      if (topBar.dataset.canOlder !== flag) topBar.dataset.canOlder = flag;
    }
    const fresh = total - end;
    setClass(bottomBar, "msg-row window-tip" + (fresh > 0 ? "" : " hidden"));
    setText(bottomBar, `↓ ${fresh} 条新消息`);
  }

  /** 追加一条消息；返回句柄（记录 + 已渲染节点，未渲染时 el=null） */
  function push(rec) {
    const handle = { rec, el: null };
    recs.push(rec);
    // pinned：原本停在底部；容忍内容增高导致的半行级偏移，避免新消息悄悄不显示
    if (nearBottom() || (pinned && scrollGap() <= FOLLOW_SLACK)) {
      const total = recs.length;
      // 增量滑窗：窗口已满且只需前移 1 行时，只删首行/建末行（原来整页重建 14 行）
      if (total > VIEW && rendered.length === VIEW && start === total - VIEW - 1) {
        const first = rendered[0];
        if (first && first.parentElement === host) host.removeChild(first);
        rendered.shift();
        start = total - VIEW;
        const node = build(recs[total - 1], total - 1, recs[total - 2], prevStamp(total - 1));
        host.insertBefore(node, typing);
        rendered.push(node);
        host.insertBefore(bottomBar, typing);
        syncBars();
        if (scroll) scroll.scrollTop = scroll.scrollHeight;
        handle.el = node;
        return handle;
      }
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
    pinned = false;                 // 主动看历史：暂停跟随最新
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
    pinned = true;
    render("bottom");
  }

  function onScroll() {
    if (busy || !scroll) return;
    pinned = nearBottom();          // 用户手动上滚即解除"跟随最新"
    const now = Date.now();
    if (now - lastEdgeAt < THROTTLE) return;
    lastEdgeAt = now;
    if (scroll.scrollTop <= EDGE) { older(); return; }
    if (pinned && start + VIEW < recs.length) jumpLatest();
  }

  /** 语音已播标记：记录写到数据里，重渲染也不会丢；节点在窗口内则原地换新 */
  function markVoice(handle) {
    if (!handle || !handle.rec) return false;
    handle.rec.spoken = true;
    if (!handle.el || handle.el.parentElement !== host) return true;
    const i = rendered.indexOf(handle.el);
    const abs = start + (i >= 0 ? i : 0);
    const fresh = build(handle.rec, abs, abs > 0 ? recs[abs - 1] : null, prevStamp(abs));
    host.replaceChild(fresh, handle.el);
    if (i >= 0) rendered[i] = fresh;
    handle.el = fresh;
    return true;
  }

  /** 删除一条记录（单条消息删除用）：窗口起点随时间前移，避免越界后整页空白 */
  function remove(rec) {
    const i = recs.indexOf(rec);
    if (i < 0) return false;
    recs.splice(i, 1);
    if (i < start) start = Math.max(0, start - 1);
    render(pinned ? "bottom" : "keep");
    return true;
  }

  /** 就地改文案（编辑后重发用）：记录在窗口内则原地重建该行，否则等下次重建 */
  function patch(rec, text) {
    if (!rec) return false;
    rec.text = String(text == null ? "" : text);
    const i = recs.indexOf(rec);
    if (i < start || i >= start + VIEW) return true;
    const old = rendered[i - start];
    if (!old || old.parentElement !== host) return true;
    const abs = i;
    const fresh = build(rec, abs, abs > 0 ? recs[abs - 1] : null, prevStamp(abs));
    host.replaceChild(fresh, old);
    rendered[i - start] = fresh;
    return true;
  }

  /** 全量记录快照（只读；单条删除/编辑定位用） */
  function list() { return recs.slice(); }

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

  const api = {
    mount, push, older, jumpLatest, followBottom, markVoice, remove, patch,
    list, stats, PAGE, VIEW
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.ChatPager = api;   // classic script 顶层 const 不挂 window
  return api;
})();
