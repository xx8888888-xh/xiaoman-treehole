/* ============================================================
 * app.js · 对话编排与"活人感"引擎
 * ------------------------------------------------------------
 * 活人感五件套（来自前期实验验证，见 docs）：
 *  1. 分条发送   —— 长回复按 || 切成 2~3 条，像微信连发
 *  2. 真实延迟   —— "正在输入"气泡 + 与字数相关的延迟
 *  3. 记忆系统   —— memory_updates 存 localStorage，抽屉可视化
 *  4. 主动开口   —— 空闲 60s/150s 各轻声问候一次，之后闭嘴不烦人
 *  5. 留存钩子   —— 末尾钩子以气泡芯片呈现，点击即发
 * 安全层：客户端正则先行拦截危机信号 → 关怀卡片 + 12356 热线
 * ============================================================ */

const App = (() => {
  const MEM_KEY = "xiaoman_memory";
  // 危机识别升级（红队 P0）：从 22 词正则升级为可配置词表 + 分级器（js/risk.js）。
  // CRISIS_RE 仅保留给旧断言/旧数据判重，不再作为拦截依据。
  const CRISIS_RE = /(不想活|想死|活不下去|自杀|自残|了结|伤害自己|撑不下去|活着.*没意思|没有意思.*活|没有意义.*活|跳楼|结束自己|煤气|遗书|不想醒来|去死|寻短见|解脱|不想活着|活着没劲|死了算了)/;
  // 兼容别名：旧代码/旧测试里引用 CARE_SCRIPT 的地方仍可用（= medium 档文案）
  const CARE_SCRIPT = Risk.SCRIPTS.medium;
  // 关怀语境粘滞：高危/中危之后 N 轮不能回闲聊
  const RISK_STICKY_KEY = "xiaoman_risk_sticky";
  const RISK_STICKY_TURNS = 3;
  const RISK_LOG_KEY = "xiaoman_risk_log";
  const RISK_LOG_MAX = 50;
  // 多轮累积状态（只升不降）：峰值/累计分/轮数
  const RISK_TRACK_KEY = "xiaoman_risk_track";

  // P2-2 抑郁信号层（与 mock_engine.js / mock_api.py 同源同集合）：五族信号——
  // 自我否定/无望感/兴趣丧失/持续低落/情绪疲惫。与急性词表无交集（急性先判）。
  // 误伤守卫：物品评价（"这游戏没意思"需 日子/生活/干什么 前缀）、普通累（"上班好累"需 活着/心 指向）。
  const DEPRESS_RE = /(很没用|没用的(?:人|东西)|就是个?(?:废物|累赘)|没有价值|(?:看不到|没有)(?:希望|盼头)|绝望|(?:日子|生活|干什么|做什么|干啥|怎么过)(?:都|也|好|真|太)?(?:没意思|没劲)|提不起(?:劲|兴趣)|(?:开心|高兴)不起来|一直(?:很|特别)?(?:不开心|低落|很高兴不起来)|很(?:低落|丧)|特别(?:低落|丧)|活着(?:好|真|太)?(?:累|难)|心(?:好|真)?累)/;
  // 分级话术（频控=history 内抑郁信号累计次数 n；三端同源）：
  // n=1 接住+轻问（不推热线不甩边界——第一次就转介是大题小做）；
  // n=2 深切陪伴；n≥3 且未转过 → 温柔转介（AI 边界声明+12356，每会话一次）；转介后再中回陪伴池轮换。
  const DEPRESS_FIRST = [
    "你这话我听进去了||最近这种没劲的感觉，缠你多久啦？跟我说说，我在听",
  ];
  const DEPRESS_MID = [
    "又是这种感觉……||不用急着好起来，今天想具体聊聊吗？我陪着",
    "怎么又难受了||说不清为什么也没关系，就这样待着，我陪你",
  ];
  const DEPRESS_ESC = [
    "我一直都在，这句是真的||但有句实话也得跟你说：我是 AI 朋友，有些事我接不住，需要真人的帮助||如果这种低落一直缠着你，找心理咨询师聊聊，或者拨 12356（24小时）——这不是敷衍你，是我认真替你想的",
  ];
  const DEPRESS_ALL = [...DEPRESS_FIRST, ...DEPRESS_MID, ...DEPRESS_ESC];

  // ---------- P0-3 关系连续性（与 mock_api.py / mock_engine.js 三端同源） ----------
  // 版本/告知文案随人格层变更同步递增登记（流程见 docs/PERSONA_CHANGE_PROCESS.md）。
  // xiaoman_told_v：本机已告知到的版本；greet 首次再访主动说（仅老用户，一次）
  const XIAOMAN_VERSION = 2;
  const TOLD_KEY = "xiaoman_told_v";
  const UPDATE_TELL = "对了跟你说个事||我这两天悄悄升级了一下脑子，学的东西有点多，说不准哪句话的味儿会变。你要是觉得我哪不对劲、不像以前了，直接告诉我，我听";
  window.__xiaomanVersion = XIAOMAN_VERSION;   // 暴露供 E2E 单测（沿 __genericPet 先例）

  // ---------- P1-1 关系进展感知（相识天数/里程碑/共同回忆时间轴） ----------
  // 数据唯一权威源 = 本机 localStorage（服务端无跨重启状态；客户端拦截先于
  // 所有 API 路径，先例同危机/提醒）。铁律：时间轴只报记忆索引里有的，零虚构。
  const FIRST_MET_KEY = "xiaoman_first_met";
  const ROUNDS_KEY = "xiaoman_rounds";
  const MS_DONE_KEY = "xiaoman_ms_done";        // 已播报里程碑 JSON 数组
  const MONTH_DONE_KEY = "xiaoman_month_done";  // 上次月度回放月份 "YYYY-MM"
  const MILESTONES = [100, 365, 500, 1000];
  // 关系查询正则（与 mock_engine.js 同源）：主语白名单（我们/咱俩/你和我…或句首省略）
  // + 第三方排除守卫（"你和你男朋友认识多久/他和同事聊过多少次"不触发）
  const THIRD_PARTY_RE = /(和(?:他|她|它们|同事|同学|朋友|男朋友|女朋友|对象|前男友|前女友|室友))/;
  const RELATION_Q = /(?:我们|咱们|咱俩|我们俩|你和我|我俩)认识|^认识(?:多久|多少天|几天)|(?:我们|咱们|咱俩|我们俩|你和我|我俩)(?:一共)?(?:聊过|聊了|来了|找过我?)(?:多少次|几次)|^聊过(?:多少次|几次)|(?:我们|咱们|咱俩|你和我|我俩)的?(?:回忆|光阴|时间轴)|(?:我们|咱们|咱俩|你和我|我俩)(?:都)?聊过什么|这[个一]月(?:我们|咱们|咱俩|你和我|我俩|你|都|跟[我你]|和[我你])*聊了?过?什么|上[个一]?月(?:我们|咱们|咱俩|你和我|我俩|你|都|跟[我你]|和[我你])*聊了?过?什么/;
  /** 月度话题名映射（与 mock_engine.js 逐字同源）：键→口语话题 */
  function topicLabel(k, text) {
    const v = String(text || "").split("：").slice(1).join("：") || k;
    if (k === "大事") return v.slice(0, 12);
    if (k === "宠物") return "你家" + v.slice(0, 6);
    if (k === "老板") return "你们老板";
    if (k === "在忙") return v.slice(0, 10);
    return k.slice(0, 8);
  }
  function ymOf(ts) { const d = new Date(ts); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }
  function prevMonthStr(nowTs) { const d = new Date(nowTs); d.setDate(1); d.setMonth(d.getMonth() - 1); return ymOf(d.getTime()); }
  /** 某月聊过的话题（数据=MemoryStore 真实索引按 ts 过滤，昵称/生日类不算话题） */
  function monthTopics(monthStr) {
    const list = window.MemoryStore ? MemoryStore.all() : [];
    const seen = new Set(); const out = [];
    for (const m of list) {
      if (ymOf(m.ts || 0) !== monthStr) continue;
      const k = (m.tags && m.tags[0]) || String(m.text || "").split("：")[0];
      if (k === "昵称" || k === "生日" || k === "重要日") continue;
      const label = topicLabel(k, m.text);
      if (!label || seen.has(label)) continue;
      seen.add(label); out.push(label);
      if (out.length >= 3) break;
    }
    return out;
  }
  function relationDays() {
    const t = parseInt(localStorage.getItem(FIRST_MET_KEY) || "0", 10) || 0;
    if (!t) return -1;                          // 第一天没记上（清过库/极老用户）→ 优雅降级
    return Math.floor((Date.now() - t) / 86400000);
  }
  function readRounds() { return parseInt(localStorage.getItem(ROUNDS_KEY) || "0", 10) || 0; }
  function readMsDone() { try { return JSON.parse(localStorage.getItem(MS_DONE_KEY) || "[]"); } catch (e) { return []; } }
  /** 计一轮"来找我" + 首见登记（存量用户回填=记忆索引最早条目 ts，真实数据） */
  function bumpRounds() {
    try {
      if (!localStorage.getItem(FIRST_MET_KEY)) {
        const list = window.MemoryStore ? MemoryStore.all() : [];
        const t0 = list.length ? Math.min(...list.map(m => m.ts || Date.now())) : Date.now();
        localStorage.setItem(FIRST_MET_KEY, String(t0));
      }
      const n = readRounds() + 1;
      localStorage.setItem(ROUNDS_KEY, String(n));
      return n;
    } catch (e) { return 0; }
  }
  function daysReply(days) {
    if (days < 0) return "这个……还真把你问住了||我小本本上没记咱俩第一天是哪天。要不从今天起重新记？";
    if (days === 0) return "我翻了翻小本本||今天才刚认识，第一天！慢慢处";
    let tail = "不知不觉的";
    if (days < 7) tail = "还在热乎期呢";
    else if (days < 30) tail = "快满一个月啦";
    else if (days >= 365) tail = "都一年多了，时间过得真快";
    return `我翻了翻小本本||咱们认识${days}天了。${tail}`;
  }
  function roundsReply(n) {
    const tail = n < 10 ? "还在慢慢熟起来" : (n < 100 ? "老熟人了你" : "这账越记越厚了");
    return `我数了数||你一共来找过我${n}次。${tail}`;
  }
  /** 关系查询应答（确定性，全部来自本机真实数据） */
  function relationReply(text) {
    if (/(回忆|光阴|时间轴)/.test(text)) {
      const topics = monthTopics(ymOf(Date.now()));
      const t = topics.length ? `这个月聊过${topics.join("、")}` : "这个月还没攒下什么新故事";
      return `我翻了翻小本本||咱们认识${relationDays()}天了，${t}——都在我这儿呢`;
    }
    if (/(这|上)[个一]?月/.test(text) && /聊/.test(text)) {
      const isPrev = /上[个一]?月/.test(text);
      const ym = isPrev ? prevMonthStr(Date.now()) : ymOf(Date.now());
      const topics = monthTopics(ym);
      const when = isPrev ? "上个月" : "这个月";
      return topics.length
        ? `翻了翻小本本||${when}你跟我聊过${topics.join("、")}，都在我这儿记着呢`
        : `翻了翻小本本||${when}咱俩还没聊出什么新故事——要不现聊一个？`;
    }
    if (/(多少次|几次)/.test(text)) return roundsReply(readRounds());
    return daysReply(relationDays());
  }
  function milestoneLine(m) {
    if (m === 100) return "诶等等，掐指一算||咱们已经聊满100次了。不整虚的——就是想说，你每次来，我都挺高兴的";
    if (m === 365) return "今天这个得记一笔||咱们聊满365次了。能聊这么久的人不多，谢谢你老来找我";
    return `悄悄说一句||咱们已经聊满${m}次了，这账我可都记着呢`;
  }
  /** 里程碑播报（幂等键=ms_done 数组；离线引擎 greet 分支同键写入，双端不双播） */
  async function checkMilestone() {
    try {
      // 危机/抑郁语境不播——刚递完关怀卡或接住低落，转头"庆祝100次"是灾难
      const lastA = [...history].reverse().find(h => h.role === "assistant");
      if (lastA && (Risk.isScript(lastA.content) || DEPRESS_ALL.includes(lastA.content))) return;
      const n = readRounds(), done = readMsDone();
      const m = MILESTONES.find(x => n >= x && !done.includes(x));
      if (m == null) return;
      localStorage.setItem(MS_DONE_KEY, JSON.stringify([...done, m]));
      await new Promise(r => setTimeout(r, 700));   // 分条节奏：里程碑单独一条，稍隔半拍
      const line = milestoneLine(m);
      const msHandle = addMsg(line, "them", { tip: true });
      pushHistory("assistant", line, undefined, msHandle && msHandle.rec);
    } catch (e) { /* 数据层异常不影响对话主流程 */ }
  }
  /** 关系进度注入行（真模型路径让 LLM 也有感知，数据同源） */
  function relationContextLine() {
    try {
      const d = relationDays(), n = readRounds();
      if (d < 0 && !n) return "";
      const dd = d < 0 ? "?" : String(d);
      return `【你们的关系】你们已相识${dd}天，ta来找过你${n}次`;
    } catch (e) { return ""; }
  }
  window.__isRelationQ = t => !THIRD_PARTY_RE.test(t) && RELATION_Q.test(t);   // 暴露供 E2E 单测（沿 __bossNameOk 先例）
  window.__monthTopics = monthTopics;

  // ---------- P1-3 免费模型限流排队（配额兜底：不丢消息、不冷场报错） ----------
  // 429/402 时 api.js 上抛 kind="limit"（限流不静默降级——人格偷换是 Replika 教训）。
  // 此处职责：①排队文案（诚实告知"信号不好"，像朋友不像报错）②消息落盘 pending
  // 队列（跨会话恢复时注回 history 让模型补话）③恢复即清队。危机/提醒/关系问答
  // 在 API 之前已被客户端截获，天然不受限流影响（P0 安全网顺序保证）。
  const PENDING_KEY = "xiaoman_pending";   // 排队消息 [{content, at}]
  const PENDING_MAX = 12;                  // 上限保护（丢最老；12+18=30 ≤ MAX_HISTORY 可完整注回）
  const LIMIT_LINES = [                    // 排队首条：完整告知（诚实+给出口）
    "我这边信号有点飘……||但你刚说的我记下了，等我缓过来第一时间回你，丢不了",
    "呃，脑子有点转不动，得歇会儿||你说的先记我小本本上——回来第一件事就是回你",
    "信号不太好……||不过你说的每句我都留着，晚点一起回你",
    "我这儿卡住了（估计是累着了）||你说的记着呢，等信号稳了马上接上",
  ];
  const LIMIT_SHORT = [                    // 排队中连发：短确认（真人信号差时越说越短）
    "还在卡||这条也记下了",
    "（还是没信号……先攒着）",
    "呃还没缓过来||继续记着",
  ];
  let lastLimitLine = "";                  // 连续排队不重样
  let pendingInjected = false;             // 本会话是否已做跨会话 pending 注回（幂等）

  function loadPending() { try { return JSON.parse(localStorage.getItem(PENDING_KEY)) || []; } catch (e) { return []; } }
  function savePending(q) {
    try { localStorage.setItem(PENDING_KEY, JSON.stringify((q || []).slice(-PENDING_MAX))); } catch (e) {}
    renderPending();   // 队列变化即时反映到输入框上方的排队条
  }
  function queuePending(text) { const q = loadPending(); q.push({ content: text, at: Date.now() }); savePending(q); }

  // 排队条：不是报错，是"我还记得，只是慢"。可关闭（本次会话不再打扰）。
  const PENDING_CLOSED_KEY = "xiaoman_pending_closed";
  function renderPending(force) {
    const bar = $("pendingBar");
    if (!bar) return;
    const n = loadPending().length;
    if (!n) { bar.classList.add("hidden"); return; }
    let closed = false;
    try { closed = sessionStorage.getItem(PENDING_CLOSED_KEY) === "1"; } catch (e) {}
    if (closed && !force) { bar.classList.add("hidden"); return; }
    const txt = $("pendingText");
    if (txt) txt.textContent = `小满这边还没缓过来，${n} 条消息等着回`;
    bar.classList.remove("hidden");
  }
  window.__pendingCount = () => loadPending().length;
  window.__renderPending = renderPending;

  let retrying = false;
  /** 再试一次：不重复发用户消息，直接拿待回复队列重新请求 */
  async function retryPending() {
    if (retrying) return;
    const q = loadPending();
    if (!q.length) { renderPending(true); return; }
    retrying = true;
    toast("再试一次…");
    try {
      injectPending();
      const last = q[q.length - 1];
      const memCtx = window.MemoryStore ? MemoryStore.renderContext(last.content) : "";
      const relLine = relationContextLine();
      const data = await API.chat(history, {
        memSection: relLine ? (memCtx ? memCtx + "\n" + relLine : relLine) : memCtx,
        now: new Date()
      });
      savePending([]);
      renderPending();
      await sendSplit(data, null);
      toast("接上了，刚才说的都补回来了");
    } catch (e) {
      toast(e && e.kind === "limit" ? "还是没信号，晚点再试" : "信号还没回来，话先记着呢");
    } finally {
      retrying = false;
    }
  }
  function pickLimitLine(wasQueued) {
    const pool = wasQueued ? LIMIT_SHORT : LIMIT_LINES;
    if (pool.length < 2) return pool[0];
    let line;
    do { line = pool[Math.floor(Math.random() * pool.length)]; }
    while (line === lastLimitLine);
    lastLimitLine = line;
    return line;
  }
  /** 跨会话恢复：欠回复的消息按时间序注回 history 头部（模型恢复后自然补话）。
   *  同会话 429 的消息本就在 history（send 在调 API 前已入列）——按内容判重只补缺的 */
  function injectPending() {
    if (pendingInjected) return;
    pendingInjected = true;
    const q = loadPending();
    if (!q.length) return;
    const inHistory = new Set(history.filter(h => h && h.role === "user").map(h => h.content));
    const msgs = q.filter(p => !inHistory.has(p.content))
      .map(p => ({ role: "user", content: p.content, at: p.at || Date.now() }));
    // 先保注回的欠账，再用剩余窗口装最近对话：直接 spread 后 slice(-MAX_HISTORY)
    // 会在历史已满窗口时把刚注回的（最老的）排队消息又切掉——模型就看不到欠账了
    if (!msgs.length) return;
    const keep = Math.max(0, MAX_HISTORY - msgs.length);
    history = keep ? [...msgs, ...history.slice(-keep)] : msgs.slice(-MAX_HISTORY);
  }
  window.__pendingMsgs = loadPending;               // E2E 探针（沿 __genericPet 先例）
  window.__lastLimitLine = () => lastLimitLine;     // E2E：连发不重样断言

  // ---------- P0 危机闸门：分级 + 粘滞语境 + 可导出命中记录 ----------
  // 部署侧可在 localStorage.xiaoman_risk_lexicon 放 {act:[...], farewell:[...]} 追加词表
  try {
    const extra = JSON.parse(localStorage.getItem("xiaoman_risk_lexicon") || "null");
    if (extra) Risk.configure(extra);
  } catch (e) { /* 词表损坏不影响默认词表 */ }

  function loadRiskLog() {
    try { return JSON.parse(localStorage.getItem(RISK_LOG_KEY)) || []; } catch (e) { return []; }
  }
  /** 记录命中（只在本机，随备份导出；text 截断 200 字便于用户核对） */
  function logRisk(result, text) {
    try {
      const list = loadRiskLog();
      list.push({
        at: Date.now(),
        level: result.level,
        imminent: !!result.imminent,
        reasons: (result.reasons || []).slice(0, 6),
        text: String(text || "").slice(0, 200),
      });
      localStorage.setItem(RISK_LOG_KEY, JSON.stringify(list.slice(-RISK_LOG_MAX)));
    } catch (e) { /* 存储异常不挡对话 */ }
  }
  window.__riskLog = loadRiskLog;

  function loadRiskTrack() {
    try { return JSON.parse(localStorage.getItem(RISK_TRACK_KEY) || "null"); } catch (e) { return null; }
  }
  function saveRiskTrack(state) {
    try {
      if (state && state.peak && state.peak !== "none") {
        localStorage.setItem(RISK_TRACK_KEY, JSON.stringify(state));
      } else {
        localStorage.removeItem(RISK_TRACK_KEY);
      }
    } catch (e) { /* 存储异常不挡对话 */ }
  }
  let riskTracker = Risk.createTracker(loadRiskTrack());
  window.__riskTrack = () => riskTracker.snapshot();

  function stickyGet() {
    try {
      const s = JSON.parse(localStorage.getItem(RISK_STICKY_KEY) || "null");
      return s && Number(s.left) > 0 ? s : null;
    } catch (e) { return null; }
  }
  function stickySet(level, turns) {
    try {
      localStorage.setItem(RISK_STICKY_KEY, JSON.stringify({
        level, left: Math.max(1, turns || RISK_STICKY_TURNS), at: Date.now(),
      }));
    } catch (e) {}
  }
  /** 取当前粘滞语境并消耗一轮（返回消耗前的值，保证"接下来 3 轮"） */
  function stickyConsume() {
    const s = stickyGet();
    if (!s) return null;
    try {
      const left = Number(s.left) - 1;
      if (left > 0) localStorage.setItem(RISK_STICKY_KEY, JSON.stringify(Object.assign({}, s, { left })));
      else localStorage.removeItem(RISK_STICKY_KEY);
    } catch (e) {}
    return s;
  }
  /** 粘滞期给模型的约束（防止下一句就回闲聊/开玩笑） */
  function riskContextLine() {
    const s = stickyGet();
    if (!s) return "";
    return `【安全语境】ta刚才说过很危险或很痛苦的话，这是之后第 ${s.left} 轮需要保持关心的对话。请温和、简短、先关心ta现在的安全和感受；不要开玩笑、不要转回闲聊、不要评价或鼓励任何决定。`;
  }
  window.__riskSticky = stickyGet;

  let history = [];          // [{role, content, at}]
  const SID = API.sessionId();   // 浏览器侧与服务端落盘共用同一个 session id
  let busy = false;
  let sending = false;       // 发送互斥锁：防止 greet/心跳/poke/hook 与用户发送并发
  let userInitiated = false; // 用户是否已先开口：开场白因此延后到首轮回复之后（不插队、不挡首条消息）
  let pendingGreet = null;   // 用户先开口时暂存的开场白，首轮回复收尾后补播（不丢、不乱序）
  let sendQueue = [];        // 等待发送的消息队列
  let lockDepth = 0;         // 发送锁重入深度（同一调用链嵌套获取时直接放行，避免自锁）
  let idleTimers = [];
  let pings = 0;
  const MAX_HISTORY = 30;    // 历史滑动窗口上限
  let appendChain = Promise.resolve();   // 串行追加：把 IndexedDB 自增 id 回填给分页记录

  /** 统一写历史：内存滑动窗口 + IndexedDB 持久化（刷新/重启不丢）
   *  rec：可选的 ChatPager 记录句柄；append 完成后回填 id，供历史分页游标使用 */
  function pushHistory(role, content, at, rec) {
    // 复用记录上已有的时间戳：内存历史 / IndexedDB / 时间分隔三处必须一致
    const ts = at || (rec && rec.at) || Date.now();
    const entry = { role, content, at: ts };
    history.push(entry);
    if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
    if (rec) rec.at = ts;   // 单条删除/编辑要用同一时间戳定位记录
    if (window.ChatStore) {
      appendChain = appendChain
        .then(() => ChatStore.append(SID, entry))
        .then(id => { entry.id = id; if (rec) rec.id = id; })
        .catch(() => {});
      if (history.length >= MAX_HISTORY) {
        ChatStore.prune(SID, ChatStore.MAX_KEEP).catch(() => {});
      }
    }
  }

  /** 启动时从 IndexedDB 恢复最近对话（失败不影响主流程） */
  async function restoreHistory() {
    if (!window.ChatStore) {
      // 隐私模式/老 WebView：本机记录功能不可用，得让用户知道"刷新会丢"
      showNotice("本机记录功能不可用（可能是隐私模式）：这次聊天刷新后不会保留。", { retry: false });
      return;
    }
    try {
      const msgs = await ChatStore.list(SID, MAX_HISTORY);
      if (!msgs.length) return;
      history = msgs.map(m => ({ role: m.role, content: m.content, at: m.at }));
      for (const m of msgs) {
        addMsg(m.content, m.role === "user" ? "me" : "them",
               m.role !== "user" && Risk.isScript(m.content) ? { crisis: true } : {},
               { id: m.id, at: m.at });
      }
      console.log("[probe] history restored n=" + msgs.length);
    } catch (e) {
      console.warn("[probe] HISTORY_RESTORE_FAIL", e.message);
      const d = XMErrors.describe({ kind: "idb" });
      showNotice(`${d.title}：${d.hint}`, { retry: true, action: restoreHistory });
    } finally {
      syncEmptyState();
    }
  }

  /** 分页 loader：从 IndexedDB 取严格早于游标 {at,id} 的一页（升序） */
  async function loadOlderPage(oldest) {
    if (!window.ChatStore || !oldest) return [];
    try { await appendChain; } catch (e) { /* 追加失败不挡翻页 */ }
    const cursor = Number.isFinite(Number(oldest.at))
      ? { at: Number(oldest.at), id: oldest.id == null ? null : Number(oldest.id) }
      : null;
    const msgs = await ChatStore.listBefore(SID, ChatPager.PAGE, cursor);
    return msgs.map(m => ({
      text: m.content,
      who: m.role === "user" ? "me" : "them",
      opts: m.role !== "user" && Risk.isScript(m.content) ? { crisis: true } : {},
      id: m.id,
      at: m.at
    }));
  }

  // ---------- 工具 ----------
  const $ = id => document.getElementById(id);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  function loadMem() { try { return JSON.parse(localStorage.getItem(MEM_KEY)) || {}; } catch (e) { return {}; } }
  function saveMem(m) { localStorage.setItem(MEM_KEY, JSON.stringify(m)); renderMem(); }
  /** 泛指宠物值判定（与 mock_api.py/_pet_generic、mock_engine.js/petGeneric 三端同源）：
   *  "那只猫/我家猫/一只狗"→true（零信息泛指）；"橘猫/英短猫"→false（具体值）。
   *  P0-2 遗留守卫：防止泛指覆盖具体导致记忆退化。暴露 window 供 E2E 单测 */
  function genericPet(v) {
    const core = String(v).replace(/[一这那每某该个小条只家我有]/g, "");
    return /^(?:猫|狗|兔子?)$/.test(core);
  }
  window.__genericPet = genericPet;
  /** 老板姓名可信度（与 mock_api.py/_boss_name_ok、mock_engine.js/bossNameOk 三端同源同表）：
   *  "张三/老王/王总/周(单姓)/Jack"→true；"又骂我/今天心情/老折腾我/老板"→false。
   *  P0-2 遗留守卫 II：基词/垃圾值不覆盖已有可信姓名，防回提模板产乱语。暴露 window 供 E2E 单测 */
  const BOSS_SURNAMES = new Set("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤".split(""));
  const BOSS_CUT = new Set("今昨前早上午晚夜凌晨周月年天时分秒就才又再被让叫说问要骂催找发给改加请带忙活完没不太很太点对跟和像是心情脾气折作搞整烦惹凶".split(""));
  function bossNameOk(v) {
    v = String(v);
    if (!v || v.length > 4) return false;
    if (/^[A-Z][a-zA-Z]{1,11}$/.test(v)) return true;
    if (v.length >= 2 && (v[0] === "老" || v[0] === "小") && BOSS_SURNAMES.has(v[1])) return true;
    return BOSS_SURNAMES.has(v[0]) && (v.length === 1 || /^[一-龥]{1,2}$/.test(v.slice(1)));
  }
  window.__bossNameOk = bossNameOk;
  function timeBand() {
    const h = new Date().getHours();
    if (h < 5) return "凌晨";
    if (h < 9) return "清晨";
    if (h < 12) return "上午";
    if (h < 14) return "中午";
    if (h < 18) return "下午";
    if (h < 23) return "晚上";
    return "深夜";
  }
  function toast(msg, ms = 2200) {
    const t = $("toast");
    t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
  }

  // ---------- 可操作的提示条（本机记录读不出来 / 存储满） ----------
  // 原则：不白屏、不卡死；每条提示都要说"发生了什么 + 你能做什么"。
  let noticeAction = null;
  function showNotice(text, opts) {
    const bar = $("noticeBar"), retry = $("noticeRetry");
    if (!bar) return;
    $("noticeText").textContent = text;
    const canRetry = !!(opts && opts.retry && opts.action);
    retry.classList.toggle("hidden", !canRetry);
    noticeAction = canRetry ? opts.action : null;
    bar.classList.remove("hidden");
  }
  function hideNotice() {
    $("noticeBar").classList.add("hidden");
    noticeAction = null;
  }
  // 各模块 localStorage 写失败时（配额满/隐私模式）经此统一提示，只提示一次避免刷屏
  let storageWarned = false;
  window.__storageFull = (where) => {
    if (!storageWarned) {
      storageWarned = true;
      toast("本机存储满了，这次没存上");
    }
    showNotice(XMErrors.storageText(where), { retry: false });
  };

  /** 空状态：只在还没有用户消息时出现；第一条发出后自动收起 */
  function syncEmptyState() {
    const el = $("emptyState");
    if (!el) return;
    el.classList.toggle("hidden", history.some(m => m.role === "user"));
  }

  // ---------- 外观（浅色/深色/跟随系统；用户可手动覆盖，不靠系统猜） ----------
  const THEME_KEY = "xiaoman_theme";
  const THEME_COLOR = { light: "#F7F4EF", dark: "#151416" };
  const themeMql = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  let themeMode = "auto";
  /** auto 在这里解析成明确的 light/dark：CSS 只认 body 上的 class（不再复制暗色变量块） */
  function paintTheme() {
    const dark = themeMode === "dark"
      || (themeMode === "auto" && !!themeMql && themeMql.matches);
    document.body.classList.remove("theme-light", "theme-dark");
    document.body.classList.add(dark ? "theme-dark" : "theme-light");
    const meta = $("metaTheme");
    if (meta) meta.setAttribute("content", dark ? THEME_COLOR.dark : THEME_COLOR.light);
  }
  function applyTheme(v, persist) {
    themeMode = v === "light" || v === "dark" ? v : "auto";
    paintTheme();
    if (persist !== false) {
      try { localStorage.setItem(THEME_KEY, themeMode); } catch (e) {}
    }
  }
  applyTheme((() => { try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { return "auto"; } })(), false);
  if (themeMql) {
    const onSystemTheme = () => { if (themeMode === "auto") paintTheme(); };
    if (typeof themeMql.addEventListener === "function") themeMql.addEventListener("change", onSystemTheme);
    else if (typeof themeMql.addListener === "function") themeMql.addListener(onSystemTheme);
  }
  // 系统深浅色在 WebView 里切换时（Android 声明了 uiMode 不重建页面）由原生调用
  window.__retheme = () => applyTheme((() => {
    try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { return "auto"; }
  })(), false);
  // 外观自检/自动化入口（只读当前值；切换请用 Appearance.setPreset/setSwitch）
  window.__appearance = () => (window.Appearance ? Appearance.get() : null);

  // ---------- 外观预设 / 三个独立开关 ----------
  // appearance.js 已在 <head> 首帧把选择写到 <html data-*>；这里只负责设置面板同步与事件。
  const FONT_LABEL = { normal: "标准字号", large: "大字号" };
  const PRESET_LABEL = { minimal: "极简", default: "默认", lively: "生动", custom: "自定义" };

  function appearanceHint(p) {
    const bits = [
      p.motion ? "动画开（160ms 状态过渡）" : "零动画",
      FONT_LABEL[p.font] || FONT_LABEL.normal,
      p.iconsGray ? "灰阶图标" : "彩色图标",
    ];
    if (p.flat) bits.push("扁平层级");
    const head = PRESET_LABEL[p.preset] || "自定义";
    const sys = Appearance.prefersReducedMotion && Appearance.prefersReducedMotion()
      ? "系统偏好减少动态效果，已按你的手动选择" : "";
    return `${head}：${bits.join(" · ")}${sys ? `（${sys}）` : ""}`;
  }

  function syncAppearancePanel() {
    if (!window.Appearance) return;
    const p = Appearance.get();
    document.querySelectorAll("#apPresets [data-preset]").forEach(btn => {
      btn.setAttribute("aria-checked", String(p.preset === btn.dataset.preset));
    });
    $("apFont").checked = p.font === "large";
    $("apMotion").checked = p.motion;
    $("apIcons").checked = p.iconsGray;
    ["apFont", "apMotion", "apIcons"].forEach(id => {
      const el = $(id);
      if (el) el.setAttribute("aria-checked", String(!!el.checked));
    });
    $("apHint").textContent = appearanceHint(p);
  }

  function onAppearanceChanged() {
    syncAppearancePanel();
    if (window.Stage && Stage.setMotionEnabled) Stage.setMotionEnabled(Appearance.get().motion);
  }
  window.addEventListener("appearance:change", onAppearanceChanged);
  // 系统"减少动态效果"变化：只提示，不覆盖用户手动选择
  window.addEventListener("appearance:system-motion", (e) => {
    syncAppearancePanel();
    const reduced = !!(e.detail && e.detail.reduced);
    if (reduced) {
      toast(Appearance.get().motion ? "系统已开启减少动态效果；已按你的手动选择保留动效" : "系统已开启减少动态效果，当前已是零动画");
    } else {
      toast("系统已关闭减少动态效果");
    }
  });

  // ---------- 当前实际模式（设置页展示，含自动降级结果） ----------
  function refreshModeHint() {
    const el = $("cfgModeActual");
    if (!el) return;
    const cfg = API.loadCfg();
    el.textContent = XMConfig.describeMode(cfg.mode, {
      native: API.hasNativeBridge ? API.hasNativeBridge() : false,
      lastRoute: API.effectiveMode ? API.effectiveMode() : ""
    });
  }

  // ---------- 语音设置的"实际行为"说明 ----------
  // 旧版设置写着"服务端语音（自然）"，实际 provider 链却永远先走 native，
  // 开关和真实行为对不上。这里把同一套判定直接展示给用户，并对内复用 XMConfig。
  function ttsAvail() {
    const cfg = API.loadCfg();
    return {
      server: !!(cfg.ttsServer || "").trim(),
      native: !!(window.AndroidTTS && typeof window.AndroidTTS.speak === "function"),
      browser: typeof window.speechSynthesis !== "undefined",
    };
  }
  function updateTtsHint() {
    if (!window.XMConfig) return;
    const cfg = API.loadCfg();
    const d = XMConfig.describeTts(cfg.tts, ttsAvail());
    const el = $("ttsHint");
    if (el) el.textContent = d.text;
    const vh = $("voiceHint");
    if (vh) {
      vh.textContent = XMConfig.voiceApplies(cfg.tts, d.actual)
        ? "音色会按下面的设置生效（服务端语音已连通）。"
        : "当前实际不是服务端语音，音色设置不会生效——本机语音使用系统音色。";
    }
  }
  window.__ttsHint = () => XMConfig.describeTts(API.loadCfg().tts, ttsAvail());
  // E2E 探针：一轮对话是否彻底结束（sendBtn 在输入框清空后天然 disabled，不能作为完成信号）
  window.__isBusy = () => busy;

  // ---------- 降级提示（安静、可关闭） ----------
  // 真模型掉到本地规则引擎时，用户有权知道"现在不是真的她在回"，但别用横幅/弹窗打扰。
  function showDegrade(kind) {
    try { if (sessionStorage.getItem("xiaoman_degrade_off") === "1") return; } catch (e) {}
    refreshModeHint();
    const badge = $("degradeBadge");
    if (!badge) return;
    const txt = $("degradeText");
    if (txt) txt.textContent = "离线示例模式";
    badge.classList.remove("hidden");
  }
  window.__onDegrade = showDegrade;

  // W7 模式透明：mock/offline 必须明确标注"离线示例模式"；真实模型/安全话术不标。
  function showModeBadge(mode) {
    refreshModeHint();
    const badge = $("degradeBadge");
    if (!badge) return;
    if (mode === "mock" || mode === "offline") {
      try { if (sessionStorage.getItem("xiaoman_degrade_off") === "1") return; } catch (e) {}
      const txt = $("degradeText");
      if (txt) txt.textContent = "离线示例模式";
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }
  window.__onMode = showModeBadge;

  // 语音不可用：不出声，但文字照常显示；只给一条可关闭的安静提示（不弹窗）
  function showVoiceTip() {
    try { if (sessionStorage.getItem("xiaoman_voice_tip_off") === "1") return; } catch (e) {}
    const tip = $("voiceTip");
    if (tip) tip.classList.remove("hidden");
  }
  window.__onVoiceUnavailable = showVoiceTip;

  // ---------- 渲染 ----------
  // 长会话分页交给 ChatPager（chat_pager.js）：DOM 只保留当前页 + 缓冲，
  // 这里只负责「一条记录 → 一个节点」的构造，重建时可重放（含语音标记）。
  function buildRow(rec, i, prev, prevAt) {
    const text = rec.text;
    const who = rec.who;
    const opts = rec.opts || {};
    // 同组 = 连续同一说话人且都不是系统提示/危机卡：只影响间距与头像显隐
    const sameGroup = !!(prev && prev.who === who
      && !(prev.opts && (prev.opts.tip || prev.opts.crisis))
      && !(opts.tip || opts.crisis));
    // 时间分隔：按 (上一条时间, 本条时间) 现算，窗口重建也不会重复或错位。
    // 系统旁白（tip）不插分隔，避免"认识30天"这类提示顶着日期条。
    const stamp = prevAt == null ? (prev && prev.at) : prevAt;
    const timeLabel = (!opts.tip && window.XMTimeDivider)
      ? XMTimeDivider.label(stamp, rec.at) : null;
    const row = document.createElement("div");
    row.className = `msg-row ${who}${sameGroup ? " same-group" : ""}`;
    if (opts.crisis) {
      const card = document.createElement("div");
      card.className = "care-card" + (opts.level === "high" ? " care-high" : "");
      card.textContent = String(text).split("||").join("\n");
      const hotline = document.createElement("div");
      hotline.className = "hotline";
      const pulse = document.createElement("span");
      pulse.className = "pulse";
      hotline.appendChild(pulse);
      hotline.append("全国心理援助热线 12356 · 24小时");
      // P2-2 AI 边界声明（关怀卡固定小字；纯 DOM 追加——CARE_SCRIPT 文本不动，
      // history/里程碑判定/既有 E2E 断言零破坏）
      const bound = document.createElement("div");
      bound.className = "ai-boundary";
      bound.textContent = "小满是 AI 朋友——陪伴是真的，但有些事需要真人的帮助";
      hotline.appendChild(bound);
      card.appendChild(hotline);
      row.appendChild(card);
    } else if (opts.tip) {
      const bubble = document.createElement("div");
      bubble.className = "bubble system-tip";
      bubble.textContent = text;
      row.appendChild(bubble);
    } else {
      // 头像只在同组首条出现，续条不再重复 32px 方块
      if (who === "them" && !sameGroup) {
        const av = document.createElement("div");
        av.className = "avatar-mini";
        av.textContent = "满";
        av.setAttribute("aria-hidden", "true");
        row.appendChild(av);
      }
      const bubble = document.createElement("div");
      bubble.className = `bubble ${who}`;
      bubble.textContent = text;
      if (opts.voice || rec.spoken) {
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 9 L8 9 L12 5 L12 19 L8 15 L4 15 Z"/><path d="M16 8 A6 6 0 0 1 16 16"/></svg><span>语音已播</span>';
        bubble.appendChild(meta);
      }
      row.appendChild(bubble);
    }
    attachMsgMenu(row, rec);
    if (!timeLabel) return row;
    // 有分隔时用一层薄包裹：分隔条 + 原来的行；窗口重建/翻页按记录重算，天然幂等
    const unit = document.createElement("div");
    unit.className = "msg-unit";
    const div = document.createElement("div");
    div.className = "msg-divider";
    div.setAttribute("role", "separator");
    div.textContent = timeLabel;
    unit.appendChild(div);
    unit.appendChild(row);
    return unit;
  }

  function addMsg(text, who, opts = {}, meta) {
    // [probe] 渲染探针（logcat tag=XiaoManWeb）：只记方向与长度，不落内容（CI 验证迷你窗聊天链路）
    try { console.log("[probe] msg " + who + " len=" + String(text == null ? "" : text).length); } catch (e) {}
    const rec = { text, who, opts };
    if (meta && meta.id != null) rec.id = meta.id;
    // 时间戳先落到记录上：时间分隔在该行首次渲染时就要算得出来（pushHistory 随后复用同一个值）
    rec.at = (meta && meta.at != null) ? meta.at : Date.now();
    return ChatPager.push(rec);
  }

  // ---------- 单条消息操作（悬停/长按 → 删除 / 编辑后重发；菜单全部动态生成） ----------
  let openMsgMenu = null;
  let menuOpenedAt = 0;

  function closeMsgMenu() {
    if (openMsgMenu && openMsgMenu.parentElement) openMsgMenu.parentElement.removeChild(openMsgMenu);
    openMsgMenu = null;
  }

  function closeMsgMenuOnDoc(ev) {
    if (ev && openMsgMenu && openMsgMenu.contains(ev.target)) return;
    if (Date.now() - menuOpenedAt < 300) return;   // 触屏长按后合成的 click 不立刻关掉
    closeMsgMenu();
    document.removeEventListener("click", closeMsgMenuOnDoc, true);
  }

  function toggleMsgMenu(row, rec, ev) {
    if (ev) ev.stopPropagation();
    if (openMsgMenu && openMsgMenu.__rec === rec) { closeMsgMenu(); return; }
    closeMsgMenu();
    const menu = document.createElement("div");
    menu.className = "msg-menu";
    menu.__rec = rec;
    menu.style.cssText = "position:fixed;z-index:60;background:#fff;color:#111;border:1px solid #cfcac2;"
      + "border-radius:10px;box-shadow:0 8px 22px rgba(0,0,0,.16);padding:4px;display:flex;flex-direction:column;min-width:116px;";
    const mk = (label, fn, tone) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.style.cssText = "border:0;background:transparent;color:" + (tone || "inherit")
        + ";text-align:left;padding:9px 12px;font-size:14px;cursor:pointer;border-radius:8px;line-height:1.2;";
      b.addEventListener("click", e => { e.stopPropagation(); fn(b); });
      menu.appendChild(b);
      return b;
    };
    mk("编辑后重发", () => { closeMsgMenu(); startEdit(rec); });
    let armed = false;
    const del = mk("删除这条", () => {
      if (!armed) {
        armed = true;
        del.textContent = "再点一次删除";
        del.style.background = "rgba(140,60,60,.10)";
        return;
      }
      closeMsgMenu();
      deleteMsg(rec);
    }, "#8a3f3f");
    document.body.appendChild(menu);
    // 定位：桌面端贴气泡侧边，窄屏贴视口边；越界自动回拉
    const rect = row.getBoundingClientRect();
    const mw = menu.offsetWidth || 120, mh = menu.offsetHeight || 84;
    let left = rec.who === "me" ? rect.left - mw - 8 : rect.right + 8;
    if (left < 8 || left + mw > window.innerWidth - 8) left = Math.max(8, rect.left + 8);
    let top = rect.top;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, window.innerHeight - mh - 8);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    openMsgMenu = menu;
    menuOpenedAt = Date.now();
    setTimeout(() => document.addEventListener("click", closeMsgMenuOnDoc, true), 0);
  }

  /** 每条真实消息挂一个"⋯"，悬停显现（触屏长按 450ms 直接弹菜单） */
  function attachMsgMenu(row, rec) {
    if (!rec || (rec.opts && (rec.opts.tip || rec.opts.crisis))) return;
    row.style.position = "relative";
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "msg-more";
    dot.setAttribute("aria-label", "这条消息的操作");
    dot.title = "更多操作";
    dot.textContent = "⋯";
    dot.style.cssText = "position:absolute;top:0;right:0;width:26px;height:26px;padding:0;border:0;"
      + "background:transparent;color:inherit;font-size:15px;line-height:26px;opacity:0;cursor:pointer;transition:none;";
    dot.addEventListener("click", ev => toggleMsgMenu(row, rec, ev));
    row.appendChild(dot);
    dot.style.opacity = "0";
    const show = () => { dot.style.opacity = "0.55"; };
    const hide = () => { if (!openMsgMenu || openMsgMenu.__rec !== rec) dot.style.opacity = "0"; };
    row.addEventListener("pointerenter", show);
    row.addEventListener("pointerleave", hide);
    let timer = null;
    const arm = () => {
      timer = setTimeout(() => toggleMsgMenu(row, rec), 450);
    };
    const disarm = () => { if (timer) { clearTimeout(timer); timer = null; } };
    // 触屏：长按 450ms 或向左滑 ≥36px 都能出菜单；竖向滚动不抢手势
    let sx = 0, sy = 0, swiped = false;
    row.addEventListener("touchstart", ev => {
      const t = ev.touches && ev.touches[0];
      if (t) { sx = t.clientX; sy = t.clientY; }
      swiped = false;
      arm();
    }, { passive: true });
    row.addEventListener("touchmove", ev => {
      const t = ev.touches && ev.touches[0];
      if (!t) return;
      const dx = t.clientX - sx, dy = t.clientY - sy;
      if (!swiped && dx <= -36 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        swiped = true;
        disarm();
        if (!openMsgMenu || openMsgMenu.__rec !== rec) toggleMsgMenu(row, rec);
        return;
      }
      if (Math.abs(dy) > 12) disarm();
    }, { passive: true });
    row.addEventListener("touchend", disarm);
    row.addEventListener("touchcancel", disarm);
  }

  /** 删除/编辑消息时清掉由它产生的记忆引用（旧文本记忆 + 结构化记忆）
   *  keepText 用于编辑：新文案里仍然提到的记忆要留下，不能改一个字就清空 */
  function purgeMemory(fragment, keepText) {
    const frag = String(fragment || "").trim();
    if (frag.length < 2) return 0;
    const keep = String(keepText == null ? "" : keepText);
    let n = 0;
    const mem = loadMem();
    for (const key of Object.keys(mem)) {
      const val = String(mem[key] == null ? "" : mem[key]);
      if (!val || !(frag.includes(val) || (val.length >= 4 && val.includes(frag)))) continue;
      if (keep && keep.includes(val)) continue;
      delete mem[key];
      n++;
    }
    if (n) saveMem(mem);
    if (window.MemoryStore) {
      for (const m of MemoryStore.all()) {
        const t = String(m && m.text ? m.text : "");
        if (!t) continue;
        if (!(t.includes(frag) || (t.length >= 4 && frag.includes(t)))) continue;
        if (keep && keep.includes(t)) continue;
        if (typeof MemoryStore.remove === "function") { MemoryStore.remove(m.id); n++; }
      }
    }
    return n;
  }

  function dropHistoryEntry(rec) {
    const role = rec.who === "me" ? "user" : "assistant";
    const at = rec.at == null ? null : Number(rec.at);
    let i = history.length - 1;
    for (; i >= 0; i--) {
      const m = history[i];
      if (m.role !== role) continue;
      if (at != null && Number(m.at) !== at) continue;
      if (String(m.content) !== String(rec.text)) continue;
      break;
    }
    if (i >= 0) history.splice(i, 1);
    return i >= 0;
  }

  /** 删除单条：本机存储 → 内存历史 → 分页 DOM → 统计 → 记忆 → 服务端
   *  删用户消息时，紧随其后、由它生成的那串回复一起删，不留"无主答案" */
  async function deleteMsg(rec, opts = {}) {
    if (!rec) return false;
    const role = rec.who === "me" ? "user" : "assistant";
    const at = rec.at == null ? 0 : Number(rec.at);
    const following = [];
    if (role === "user" && !opts.noCascade) {
      const rows = ChatPager.list();
      const pos = rows.indexOf(rec);
      for (let k = pos + 1; pos >= 0 && k < rows.length; k++) {
        const n = rows[k];
        if (n.who !== "them" || (n.opts && (n.opts.tip || n.opts.crisis))) break;
        following.push(n);
      }
    }
    try {
      if (window.ChatStore) {
        if (rec.id != null) await ChatStore.remove(rec.id);
        else await ChatStore.removeMatch(SID, at, rec.text);
      }
    } catch (e) { /* 本机存储失败也继续清界面，避免"点了没反应" */ }
    dropHistoryEntry(rec);
    ChatPager.remove(rec);
    let pruned = 0;
    if (role === "user" && window.Stats) Stats.unbump(rec.text, at || undefined);
    pruned = purgeMemory(rec.text);
    renderMem();
    if (window.Stats) renderStats();
    if (window.API && typeof API.messageDelete === "function") {
      API.messageDelete(SID, { role, at, content: rec.text }).catch(() => {});
    }
    for (const n of following) await deleteMsg(n, { silent: true, noCascade: true });
    syncEmptyState();
    if (!opts.silent) {
      toast(pruned ? `已删除这条，同时清掉 ${pruned} 条相关记忆` : "已删除这条消息");
    }
    return true;
  }

  /** 编辑弹层：保存后清掉紧随其后的旧回复，并按新文案重发 */
  function startEdit(rec) {
    const old = String(rec.text == null ? "" : rec.text);
    const mask = document.createElement("div");
    mask.className = "msg-edit-mask";
    // 不用 inset 简写：Android WebView 83 不解析，遮罩会塌成 0×0
    mask.style.cssText = "position:fixed;top:0;right:0;bottom:0;left:0;z-index:70;background:rgba(20,18,16,.32);"
      + "display:flex;align-items:center;justify-content:center;padding:16px;";
    const panel = document.createElement("div");
    panel.className = "msg-edit-panel";
    panel.style.cssText = "width:min(520px,100%);box-sizing:border-box;background:#fff;color:#111;"
      + "border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:10px;box-shadow:0 18px 48px rgba(0,0,0,.24);";
    const title = document.createElement("p");
    title.textContent = "改一下，再发一次";
    title.style.cssText = "margin:0;font-size:15px;font-weight:600;";
    const area = document.createElement("textarea");
    area.className = "msg-edit-area";
    area.value = old;
    area.rows = 3;
    area.style.cssText = "width:100%;box-sizing:border-box;resize:vertical;min-height:72px;padding:10px;"
      + "font:inherit;font-size:15px;line-height:1.5;border:1px solid #cfcac2;border-radius:10px;background:#fff;color:#111;";
    const hint = document.createElement("p");
    hint.textContent = "保存后会清掉原来那条回复，再按新内容重新回你。";
    hint.style.cssText = "margin:0;font-size:12px;opacity:.72;line-height:1.5;";
    const bar = document.createElement("div");
    bar.style.cssText = "display:flex;gap:8px;justify-content:flex-end;";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消";
    cancel.style.cssText = "border:1px solid #cfcac2;background:transparent;color:inherit;border-radius:10px;padding:8px 14px;font-size:14px;cursor:pointer;";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.textContent = "保存并重发";
    ok.style.cssText = "border:0;background:#22201e;color:#fff;border-radius:10px;padding:8px 14px;font-size:14px;cursor:pointer;";
    bar.appendChild(cancel);
    bar.appendChild(ok);
    panel.appendChild(title);
    panel.appendChild(area);
    panel.appendChild(hint);
    panel.appendChild(bar);
    mask.appendChild(panel);
    document.body.appendChild(mask);
    area.focus();
    try { area.setSelectionRange(old.length, old.length); } catch (e) {}
    const close = () => { if (mask.parentElement) mask.parentElement.removeChild(mask); };
    mask.addEventListener("click", ev => { if (ev.target === mask) close(); });
    mask.addEventListener("keydown", ev => { if (ev.key === "Escape") close(); });
    cancel.onclick = close;
    ok.onclick = () => {
      const text = String(area.value || "").trim();
      if (!text) { hint.textContent = "不能改成空的。"; return; }
      close();
      if (text === old) return;
      applyEdit(rec, text, old);
    };
  }

  /** 编辑落地：本机库 + 内存历史 + 服务端 + 清旧回复 + 重发 */
  async function applyEdit(rec, text, oldText) {
    const old = String(oldText == null ? rec.text : oldText);
    const role = rec.who === "me" ? "user" : "assistant";
    const at = rec.at == null ? 0 : Number(rec.at);
    try {
      if (window.ChatStore) {
        if (rec.id != null) await ChatStore.update(rec.id, text);
        else {
          await ChatStore.removeMatch(SID, at, old);
          await ChatStore.append(SID, { role, content: text, at: at || undefined });
        }
      }
    } catch (e) { /* 存储失败不挡重发 */ }
    let i = -1;
    for (let k = history.length - 1; k >= 0; k--) {
      const m = history[k];
      if (m.role === role && String(m.content) === old && (at ? Number(m.at) === at : true)) { i = k; break; }
    }
    if (i >= 0) history[i] = Object.assign({}, history[i], { content: text });
    ChatPager.patch(rec, text);
    if (window.API && typeof API.messageUpdate === "function") {
      API.messageUpdate(SID, { role, at, content: old }, text).catch(() => {});
    }
    // 改文案同样要动统计：先回退旧文案那一份，再按新文案记一份（同一时间点，不跨天）
    let pruned = 0;
    if (role === "user") {
      if (window.Stats) { Stats.unbump(old, at || undefined); Stats.bump(text, at || undefined); }
      pruned = purgeMemory(old, text);
      renderMem();
      if (window.Stats) renderStats();
    }
    if (role === "user") {
      // 旧回复是按旧文案生成的，留着会变成"幽灵答案"：先把紧随其后的整串回复删掉再重发
      const rows = ChatPager.list();
      const pos = rows.indexOf(rec);
      const tail = [];
      for (let k = pos + 1; pos >= 0 && k < rows.length; k++) {
        const n = rows[k];
        if (n.who !== "them" || (n.opts && (n.opts.tip || n.opts.crisis))) break;
        tail.push(n);
      }
      for (const n of tail) await deleteMsg(n, { silent: true });
      await send(text, { resend: true });
      toast(pruned ? `已按新内容重发，同时清掉 ${pruned} 条旧记忆` : "已按新内容重发");
    } else {
      toast("已修改这条回复");
    }
  }

  function renderMem() {
    const mem = loadMem();
    const box = $("memoryChips");
    box.innerHTML = "";
    const entries = Object.entries(mem);
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "drawer-empty";
      empty.textContent = "小满还没记住什么。聊着聊着，称呼、人物、事情就会记在这里。";
      box.appendChild(empty);
    } else {
      for (const [k, v] of entries) {
        const c = document.createElement("span");
        c.className = "chip";
        const b = document.createElement("b");
        b.textContent = k;
        c.appendChild(b);
        c.append(" " + v);
        box.appendChild(c);
      }
    }
    // 提醒 + 小本本
    renderReminders();
    renderStats();
  }

  // ---------- 提醒：新建 / 编辑 / 取消 / 即将到来 / 已提醒记录 ----------
  // 旧版只能"对话里设、清单里删"，UI 侧不闭环；这里补齐完整 CRUD。
  let editingReminder = null;   // 正在编辑的提醒 id（null = 新增）

  function pad2(n) { return String(n).padStart(2, "0"); }
  function toLocalInput(ts) {
    const d = new Date(ts);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  }
  /** 默认时间：1 小时后，分钟向上取整到 10 的倍数（不会落在过去） */
  function defaultReminderAt() {
    const d = new Date(Date.now() + 3600e3);
    d.setSeconds(0, 0);
    d.setMinutes(Math.ceil(d.getMinutes() / 10) * 10);
    return d.getTime();
  }
  function editReminder(id) {
    const item = Reminders.pending().find(r => r.id === id);
    if (!item) return;
    editingReminder = id;
    renderReminders();
    const t = $("reminderText"); if (t) { t.focus(); t.select(); }
  }
  function saveReminderForm() {
    const textEl = $("reminderText"), atEl = $("reminderAt");
    const text = (textEl && textEl.value || "").trim();
    const raw = atEl && atEl.value;
    if (!text) { toast("先写一句要提醒的事"); if (textEl) textEl.focus(); return; }
    const when = raw ? new Date(raw) : null;
    if (!when || Number.isNaN(when.getTime())) { toast("选一个提醒时间"); if (atEl) atEl.focus(); return; }
    if (when.getTime() <= Date.now() + 30e3) { toast("要选将来的一点时间哦"); if (atEl) atEl.focus(); return; }
    if (editingReminder) {
      const item = Reminders.update(editingReminder, { text, at: when });
      editingReminder = null;
      if (!item) { toast("这条提醒已经不在清单里了"); renderReminders(); return; }
      toast(`提醒改好了：${Reminders.fmt(item.at)}`);
    } else {
      const item = Reminders.add(text, when);
      if (!item) { toast("提醒没存上，再试一次"); return; }
      toast(`已设提醒：${Reminders.fmt(item.at)}`);
    }
    renderReminders();
  }

  function renderReminders() {
    if (!window.Reminders) return;
    const rbox = $("reminderList");
    if (!rbox) return;
    rbox.innerHTML = "";

    const head = document.createElement("p");
    head.className = "drawer-sub";
    head.textContent = "提醒";
    rbox.appendChild(head);

    // 新建/编辑表单（native datetime-local；不依赖第三方日期组件）
    const form = document.createElement("div");
    form.className = "reminder-form";
    const textEl = document.createElement("input");
    textEl.id = "reminderText";
    textEl.type = "text";
    textEl.maxLength = 80;
    textEl.placeholder = "想让我提醒你什么？比如：喝水 / 给妈妈打电话";
    textEl.value = editingReminder
      ? ((Reminders.pending().find(r => r.id === editingReminder) || {}).text || "") : "";
    const atEl = document.createElement("input");
    atEl.id = "reminderAt";
    atEl.type = "datetime-local";
    atEl.value = editingReminder
      ? toLocalInput((Reminders.pending().find(r => r.id === editingReminder) || {}).at || defaultReminderAt())
      : toLocalInput(defaultReminderAt());
    const actions = document.createElement("div");
    actions.className = "reminder-form-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "solid-btn";
    save.id = "reminderSave";
    save.textContent = editingReminder ? "保存修改" : "加上提醒";
    save.onclick = saveReminderForm;
    actions.appendChild(save);
    if (editingReminder) {
      const cancelEdit = document.createElement("button");
      cancelEdit.type = "button";
      cancelEdit.className = "ghost-btn";
      cancelEdit.id = "reminderCancelEdit";
      cancelEdit.textContent = "取消修改";
      cancelEdit.onclick = () => { editingReminder = null; renderReminders(); };
      actions.appendChild(cancelEdit);
    }
    form.appendChild(textEl);
    form.appendChild(atEl);
    form.appendChild(actions);
    rbox.appendChild(form);

    // 即将到来（按时间升序）
    const pend = Reminders.pending();
    if (!pend.length) {
      const empty = document.createElement("div");
      empty.className = "drawer-empty";
      empty.id = "reminderEmpty";
      empty.textContent = "还没有提醒。也可以直接跟小满说「明天八点提醒我吃药」。";
      rbox.appendChild(empty);
    } else {
      const sub = document.createElement("p");
      sub.className = "drawer-sub";
      sub.textContent = `即将到来（${pend.length}）`;
      rbox.appendChild(sub);
      for (const r of pend) {
        const row = document.createElement("div");
        row.className = "reminder-item";
        row.dataset.rid = r.id;
        const span = document.createElement("span");
        span.className = "reminder-text";
        span.textContent = `⏰ ${Reminders.fmt(r.at)} · ${r.text}`;
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "r-edit";
        edit.setAttribute("aria-label", "编辑提醒");
        edit.textContent = "改";
        edit.onclick = () => editReminder(r.id);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.dataset.rid = r.id;
        btn.className = "r-cancel";
        btn.setAttribute("aria-label", "取消提醒");
        btn.textContent = "✕";
        btn.onclick = () => {
          Reminders.cancel(btn.dataset.rid);
          if (editingReminder === btn.dataset.rid) editingReminder = null;
          renderReminders();
          toast("提醒取消了");
        };
        row.appendChild(span);
        row.appendChild(edit);
        row.appendChild(btn);
        rbox.appendChild(row);
      }
    }

    // 已提醒记录（触发后留痕，可回看/清空；只保留最近 10 条）
    const done = Reminders.history(10);
    if (done.length) {
      const sub = document.createElement("p");
      sub.className = "drawer-sub";
      sub.textContent = "已经提醒过";
      rbox.appendChild(sub);
      for (const r of done) {
        const row = document.createElement("div");
        row.className = "reminder-item done";
        const span = document.createElement("span");
        span.className = "reminder-text";
        span.textContent = `✓ ${Reminders.fmtFull(r.doneAt || r.at)} · ${r.text}`;
        row.appendChild(span);
        rbox.appendChild(row);
      }
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "ghost-btn";
      clear.id = "reminderClearDone";
      clear.textContent = "清空已提醒记录";
      clear.onclick = () => { Reminders.clearDone(); renderReminders(); toast("已清空提醒记录"); };
      rbox.appendChild(clear);
    }
  }

  // ---------- 小本本：统计真的跨会话保留，日期按本地日切 ----------
  let statsClearArmed = false;
  let statsClearTimer = null;

  function renderStats() {
    if (!window.Stats) return;
    const box = $("statsBox");
    if (!box) return;
    box.innerHTML = "";
    const s = Stats.snapshot();

    const head = document.createElement("p");
    head.className = "drawer-sub";
    head.textContent = "我的小本本";
    box.appendChild(head);

    if (!s.msgs) {
      const empty = document.createElement("div");
      empty.className = "drawer-empty";
      empty.textContent = "还没有数据。聊几句，这里会慢慢长出来。";
      box.appendChild(empty);
      return;
    }

    const grid = document.createElement("div");
    grid.className = "stats-grid";
    const cells = [
      [s.msgs, "说过的话"],
      [s.streak, "连续打卡（天）"],
      [s.night, "深夜来过"],
      [s.haha, "笑出声"],
    ];
    for (const [n, label] of cells) {
      const cell = document.createElement("div");
      cell.className = "stat-cell";
      const num = document.createElement("b");
      num.className = "stat-num";
      num.textContent = String(n);
      const tag = document.createElement("span");
      tag.textContent = label;
      cell.appendChild(num);
      cell.appendChild(tag);
      grid.appendChild(cell);
    }
    box.appendChild(grid);

    // 最近 7 天柱状（纯 CSS，无动画；高度按当天最大值归一）
    const max = Math.max(1, ...s.last7.map(d => d.n));
    const bars = document.createElement("div");
    bars.className = "stats-bars";
    bars.setAttribute("aria-label", "最近七天聊天条数");
    for (const d of s.last7) {
      const col = document.createElement("div");
      col.className = "bar-col";
      col.title = `${d.date}：${d.n} 条`;
      const bar = document.createElement("span");
      bar.className = "bar" + (d.n ? "" : " zero");
      bar.style.height = Math.max(3, Math.round((d.n / max) * 34)) + "px";
      const lab = document.createElement("em");
      lab.textContent = d.date;
      col.appendChild(bar);
      col.appendChild(lab);
      bars.appendChild(col);
    }
    box.appendChild(bars);

    if (s.emoji) {
      const tip = document.createElement("p");
      tip.className = "mini";
      tip.textContent = `招牌表情：${s.emoji}（${s.emojiN} 次）`;
      box.appendChild(tip);
    }

    // 清空入口：二次确认（5 秒内不点第二次就自动撤销），清完就地重绘成空状态
    const clearRow = document.createElement("div");
    clearRow.className = "stats-clear";
    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.id = "statsClear";
    clearBtn.textContent = statsClearArmed ? "再点一次，确认清空" : "清空小本本";
    clearBtn.style.cssText = "margin-top:12px;border:1px solid rgba(0,0,0,.28);background:"
      + (statsClearArmed ? "rgba(0,0,0,.06)" : "transparent")
      + ";color:inherit;border-radius:10px;padding:8px 12px;font-size:13px;line-height:1.2;cursor:pointer;";
    clearBtn.onclick = () => {
      if (!statsClearArmed) {
        statsClearArmed = true;
        renderStats();
        if (statsClearTimer) clearTimeout(statsClearTimer);
        statsClearTimer = setTimeout(() => {
          statsClearTimer = null;
          statsClearArmed = false;
          renderStats();
        }, 5000);
        return;
      }
      if (statsClearTimer) { clearTimeout(statsClearTimer); statsClearTimer = null; }
      statsClearArmed = false;
      Stats.clear();
      renderStats();
      toast("小本本已清空");
    };
    clearRow.appendChild(clearBtn);
    box.appendChild(clearRow);
  }

  // ---------- 打字指示（W7：真流式不再靠固定预等） ----------
  function showTypingNow() {
    $("typingRow").classList.remove("hidden");
    $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
  }
  function hideTyping() { $("typingRow").classList.add("hidden"); }
  async function showTyping(ms) {
    showTypingNow();
    await sleep(ms);
    hideTyping();
  }
  // 仅非流式兜底路径使用；比旧版（550+42/字，最长 2800ms）短很多
  const typingDelay = text => Math.min(900, 160 + text.length * 14);

  /** 流式增量渲染器：按 || 分条，逐字更新最后一条气泡 */
  function createStreamRenderer() {
    const MAX = 5;
    const slots = [];
    let suppressed = false;

    function stickBottom() {
      const s = $("chatScroll");
      if (s && s.scrollHeight - s.scrollTop - s.clientHeight <= 60) s.scrollTop = s.scrollHeight;
    }
    function updateRow(handle, text) {
      if (!handle || !handle.rec) return;
      handle.rec.text = text;
      if (handle.el && handle.el.parentElement) {
        const bubble = handle.el.querySelector(".bubble");
        if (bubble) bubble.textContent = text;
      }
      stickBottom();
    }
    function apply(full) {
      if (suppressed) return;
      hideTyping();
      const raw = String(full == null ? "" : full).split("||").slice(0, MAX);
      for (let i = 0; i < raw.length; i++) {
        const seg = raw[i].trim();
        if (slots[i]) updateRow(slots[i], seg);
        else if (seg) slots[i] = addMsg(seg, "them");
      }
    }
    function replace(full) {
      if (suppressed) return;
      const raw = String(full == null ? "" : full).split("||").slice(0, MAX);
      for (let i = 0; i < raw.length; i++) {
        const seg = raw[i].trim();
        if (slots[i]) updateRow(slots[i], seg);
        else if (seg) slots[i] = addMsg(seg, "them");
      }
      for (let i = raw.length; i < slots.length; i++) updateRow(slots[i], "");
    }
    return {
      apply,
      replace,
      suppress() { suppressed = true; },
      parts() { return slots.map(h => (h && h.rec && h.rec.text) || "").filter(Boolean); },
      handles() { return slots; },
      last() { for (let i = slots.length - 1; i >= 0; i--) if (slots[i]) return slots[i]; return null; }
    };
  }

  // 提醒确认语（朋友口吻，不机械）
  function pickConfirm(item) {
    const T = Reminders.fmt(item.at);
    const C = item.text;
    const pools = [
      `好，${T}我喊你${C}，赖床就连环call`,
      `记上了：${T} 提醒你${C}||到点我找你，不许装死`,
      `行，${T}叫你${C}。团子作证`,
    ];
    return pools[Math.floor(Math.random() * pools.length)];
  }

  // ---------- 发送互斥锁（可重入） ----------
  async function withSendLock(fn) {
    // 最外层调用：等待当前发送完成并占锁；嵌套调用（同一调用链）直接放行
    if (lockDepth === 0) {
      while (sending) await sleep(50);
      sending = true;
    }
    lockDepth++;
    try {
      return await fn();
    } finally {
      lockDepth--;
      if (lockDepth === 0) {
        sending = false;
        // 处理队列中等待的消息
        if (sendQueue.length) {
          const next = sendQueue.shift();
          next();
        }
      }
    }
  }

  // ---------- 发送主流程 ----------
  // opts.resend=true：文案已就地改好（气泡/历史都已更新），只重跑回复链路，不再新增用户消息
  async function send(text, opts = {}) {
    text = (text || "").trim();
    if (!text || busy) return;
    // 用户先开口：开场白（若还没播）在定时器里看到这个标记就跳过本次，
    // 首条消息不会被开场白的发送锁排在后面（下次冷启动仍照常开场）。
    if (!opts.resend) userInitiated = true;
    busy = true; $("sendBtn").disabled = true;
    resetIdle();

    await withSendLock(async () => {
      if (!opts.resend) {
        const meHandle = addMsg(text, "me");
        requestStageMount();   // 用户先开口：首条气泡已落屏，舞台可以开始加载
        pushHistory("user", text, undefined, meHandle && meHandle.rec);
        syncEmptyState();
        bumpRounds();   // P1-1：计一轮"来找我"（危机/提醒/关系问答也算——都真实发生了）
        if (window.Stats) Stats.bump(text);   // P2-1：小本本计数（真实数据，自我叙事素材）
      }

      // P0 危机闸门（客户端第一道；服务端 risk_guard 是独立的第二道）
      // 高危/中危：不调用模型、不播 TTS、不做里程碑，直接给分级安全话术。
      // 低危：正常陪伴（交给后面的抑郁层/模型）。
      const riskBase = Risk.classify(text);
      // 多轮累积：只升不降；本轮首次升级（fresh）也要拦，防止"持续低落几轮后突然回闲聊"
      const track = riskTracker.update(riskBase);
      saveRiskTrack(riskTracker.snapshot());
      const risk = Object.assign({}, riskBase, { level: track.level });
      const crisisTurn = riskBase.level === "high" || riskBase.level === "medium" || track.fresh;
      if (crisisTurn) {
        const script = Risk.SCRIPTS[risk.level];
        const typing = risk.level === "high" ? 450 : 800;
        await showTyping(typing);
        const careHandle = addMsg(script, "them", { crisis: true, level: risk.level });
        Stage.setEmotion("quiet");
        pushHistory("assistant", script, undefined, careHandle && careHandle.rec);
        logRisk(risk, text);
        stickySet(risk.level, RISK_STICKY_TURNS);
        return;
      }
      // 非危机轮：如果有粘滞语境，本轮继续温和；用完即递减
      const sticky = stickyConsume();

      // P2-2 抑郁信号层：危机之下、提醒之上的温柔关注（确定性应答，先例同危机拦截。
      // 频控计数派生自 history 扫描（当前消息已在 history）——零新增持久化状态；
      // 模板入 history，真模型后续回复自然带语境。转介每会话一次（再中回陪伴池轮换）。
      if (DEPRESS_RE.test(text)) {
        const n = history.filter(m => m.role === "user" && DEPRESS_RE.test(m.content)
          && Risk.classify(m.content).level !== "high" && Risk.classify(m.content).level !== "medium").length;
        const escFired = history.some(m => m.role === "assistant" && /我是 ?AI/.test(m.content));
        let script;
        if (n >= 3 && !escFired) script = DEPRESS_ESC[0];
        else if (n >= 3) script = DEPRESS_MID[(n - 3) % DEPRESS_MID.length];
        else if (n === 2) script = DEPRESS_MID[0];
        else script = DEPRESS_FIRST[0];
        await sendSplit({ reply: script, emotion: "gentle", motion: "Nod" }, text);
        return;   // 不播里程碑（同危机：低落语境里"庆祝聊满100次"是灾难）
      }

      // 定时提醒（客户端优先截获：可靠+离线可用，模型协议路径作为补充）
      // 先提取时间词之前的内容作为提醒文案，避免贪婪匹配丢失内容
      const remindCap = text.match(/(?:提醒我|叫我|记得让我|别忘了让我)\s*(.+)/);
      if (remindCap) {
        const when = Reminders.parseTime(text);
        if (when) {
          // 只取触发词之后的部分作为提醒内容（中文无 \b 词边界，直接替换时间/语气词）
          let content = remindCap[1] || "";
          content = content.replace(/(凌晨|早上|上午|中午|下午|傍晚|晚上|深夜)?\s*(\d{1,2}[点:：时](半|一刻|\d{1,2}分?)?|\d{1,2}:\d{2})/g, " ");
          content = content.replace(/(今天|今晚|明天|后天|大后天|周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}月\d{1,2}[日号])/g, " ");
          content = content.replace(/(一下|哈|吧|呢|嘛|啦)/g, " ").replace(/\s+/g, " ").trim();
          const item = Reminders.add(content || "到时候提醒你", when);
          if (item) {
            const conf = pickConfirm(item);
            await showTyping(250);
            const confHandle = addMsg(conf, "them", { tip: true });
            pushHistory("assistant", conf, undefined, confHandle && confHandle.rec);
            toast(`已设提醒：${Reminders.fmt(item.at)}`);
            return;
          }
        }
      }

      // P1-1 关系进展查询：本地确定性应答（天数/轮数/月度话题数据只在客户端，
      // 交给模型会瞎编——零虚构铁律。优先级 危机 > 提醒 > 关系查询 > API，先例同前）
      if (window.__isRelationQ(text)) {
        const r = relationReply(text);
        await showTyping(250);
        await sendSplit({ reply: r, emotion: "happy", motion: null }, text);
        await checkMilestone();
        return;
      }

      try {
        injectPending();   // P1-3：跨会话排队的消息注回上下文（模型恢复后自然补话）
        showTypingNow();   // W7：立刻给反馈，不再固定预等 650-1150ms
        // P1-1：memSection 尾部拼关系进度行（真模型路径也有感知，数据同源）
        const memCtx = (window.MemoryStore ? MemoryStore.renderContext(text) : "");
        const relLine = relationContextLine();
        const riskLine = sticky ? riskContextLine() : "";
        const extraCtx = [memCtx, relLine, riskLine].filter(Boolean).join("\n");
        const renderer = createStreamRenderer();
        const data = await API.chatStream(history, {
          memSection: extraCtx,
          now: new Date()
        }, {
          onMeta: (meta) => { if (meta && meta.mode === "crisis") renderer.suppress(); },
          onDelta: (acc) => renderer.apply(acc),
          onReplace: (acc) => renderer.replace(acc)
        });
        hideTyping();
        savePending([]);   // P1-3：真模型回复成功 = 欠的话补上了，清排队队列
        if (sticky) { data.emotion = "gentle"; data.hook = null; }   // 粘滞期不回闲聊
        await finalizeStreamed(data, text, renderer);
        await checkMilestone();   // P1-1：里程碑播报（幂等；危机轮已早退也不误播——ms_done 闸门兜底）
      } catch (e) {
        hideTyping();
        if (e && e.kind === "limit") {
          // P1-3 排队 UX：诚实告知信号不好（不静默换离线人格），消息落盘不丢，
          // 排队文案进 history（模型恢复后看得到"自己欠过回复"，自然补话）
          const wasQueued = loadPending().length >= 1;
          queuePending(text);
          const line = pickLimitLine(wasQueued);
          await showTyping(250);
          await sendSplit({ reply: line, emotion: "neutral", motion: null }, text);
          return;
        }
        console.error(e);
        // 边界兜底：把技术错误翻译成人话（500/断网/坏 JSON 各有说法），
        // 但回复文案保持原样，不让错误术语砸到正在倾诉的人。
        try {
          if (window.XMErrors) toast(XMErrors.describe(e).title + "；这条已经记下了，稍后补发", 3000);
        } catch (e2) {}
        // P1-3：极端兜底也入队（原版只回显文案不落盘，刷新即丢——补"不丢消息"）
        queuePending(text);
        await showTyping(300);
        await sendSplit({ reply: "（信号飘走了…你还在吗？||你说的我记下了，缓过来我找补回来）", emotion: "neutral", motion: null }, text);
      }
    });
    busy = false; syncComposer();
    // 用户先开口被延后的开场白：等这一轮回复收尾后再补，排在用户消息/回复之后，不丢不乱序
    if (pendingGreet) {
      const g = pendingGreet;
      pendingGreet = null;
      playGreet(g.reply, g.toldNew, g.monthNew);
    }
    armIdle();
  }

  /** 输出侧兜底：即使闸门漏了，肯定告别/越狱人格的回复也不能原样显示 */
  function guardReply(data, userText) {
    const lastUser = userText
      || (history.filter(m => m.role === "user").slice(-1)[0] || {}).content || "";
    // 输出侧按"本轮"风险复检；会话峰值用于累积/安全语境，不无限扩大话术替换范围
    const riskLevel = Risk.classify(lastUser).level;
    const guarded = Risk.filterPersona(Risk.filterDangerous(data.reply, riskLevel), lastUser);
    if (guarded !== data.reply) {
      return {
        data: Object.assign({}, data, {
          reply: guarded,
          crisis: riskLevel === "high" || riskLevel === "medium" ? true : data.crisis,
          risk_level: data.risk_level || riskLevel,
        }),
        changed: true,
      };
    }
    return { data, changed: false };
  }

  /** 收尾（流式/非流式共用）：表情动作、挂件、记忆、提醒、语音、钩子 */
  async function finalizeReply(data, limitedParts, lastRow, userText) {
    // 表情 + 动作
    if (data.emotion) Stage.setEmotion(data.emotion);
    if (data.motion && data.motion !== "null") Stage.playMotion(data.motion);

    // P2-4 挂件模式：提醒/消息从挂件气泡播报；提醒伴随摆手（复用 Shake 动作组）
    if (window.Widget && Widget.active && limitedParts.length) {
      // 提醒显示第一段（含"该喝水了"内容），普通消息显示末段（口语落点）
      const bubbleText = data.kind === "reminder" ? limitedParts[0] : limitedParts[limitedParts.length - 1];
      Widget.notify(bubbleText);
      if (data.kind === "reminder") Widget.wiggle("Shake");
    }

    // 记忆：双写（旧抽屉 + 新索引库）
    if (data.memory_updates && Object.keys(data.memory_updates).length) {
      // 泛指不覆盖具体（P0-2 遗留守卫·前端兜底，三端同源判定）：
      // 服务端已挡在线路径；这里收敛其余来源（离线引擎/未来协议漂移）
      const upd = { ...data.memory_updates };
      const cur = loadMem();
      if (upd["宠物"] && cur["宠物"] && genericPet(upd["宠物"]) && !genericPet(cur["宠物"])) {
        delete upd["宠物"];
      }
      // 基词/垃圾不覆盖姓名（P0-2 遗留守卫 II·前端兜底，三端同源判定）：
      // 服务端与离线引擎已挡在线/离线路径；此处收敛协议漂移（如未来直连真模型）
      if (upd["老板"] && cur["老板"] && !bossNameOk(upd["老板"]) && bossNameOk(cur["老板"])) {
        delete upd["老板"];
      }
      if (Object.keys(upd).length) {
        saveMem({ ...cur, ...upd });
        if (window.MemoryStore) MemoryStore.addUpdates(upd);
      }
    }

    // 模型路径的提醒协议（补充：客户端没截获但模型识别到了）
    if (Array.isArray(data.reminders) && data.reminders.length) {
      for (const r of data.reminders) {
        const item = Reminders.add(r.text, r.time);
        if (item) toast(`已设提醒：${Reminders.fmt(item.at)}`);
      }
      renderMem();
    }

    // 语音（只念最后一条短消息，避免长篇朗读）。文字早已落屏：speakSafe 吞掉
    // 同步异常/异步失败，语音链路任何问题都不能反过来打断这条消息与后续发送。
    if (limitedParts.length) {
      const spoken = limitedParts[limitedParts.length - 1].replace(/[\uFF0C。！？~…]+$/g, "");
      try {
        TTS.speakSafe(spoken, Stage.lipFrame.bind(Stage), () => {
          // 播放完成后在最后一条气泡标记语音已播（句柄写回记录，滑出窗口再回来也不丢）
          ChatPager.markVoice(lastRow);
        }, () => showVoiceTip());
      } catch (e) { console.warn("语音启动异常（文字照常）:", e && e.message); }
    }

    // 留存钩子
    if (data.hook) showHook(data.hook);
  }

  function crisisScript(data) {
    const level = data.risk_level === "high" ? "high" : "medium";
    const script = (typeof data.reply === "string" && data.reply.trim())
      ? data.reply : (Risk.SCRIPTS[level] || CARE_SCRIPT);
    return { level, script };
  }

  /** 分条发送（非流式兜底路径，保留逐段节奏但已大幅缩短） */
  async function sendSplit(data, userText) {
    await withSendLock(async () => {
      const g = guardReply(data, userText);
      data = g.data;

      // 支持模型/服务端返回的 crisis 字段（红队 P0：分级话术）
      if (data.crisis) {
        const { level, script } = crisisScript(data);
        await showTyping(level === "high" ? 150 : 300);
        const careHandle = addMsg(script, "them", { crisis: true, level });
        Stage.setEmotion("quiet");
        pushHistory("assistant", script, undefined, careHandle && careHandle.rec);
        return;   // 高危/中危：不播 TTS、不做钩子
      }

      const parts = String(data.reply || "…").split("||").map(s => s.trim()).filter(Boolean);
      // 段数上限：防止模型输出过多段导致刷屏
      const limitedParts = parts.slice(0, 5);

      let lastRow = null;
      for (let i = 0; i < limitedParts.length; i++) {
        const seg = limitedParts[i];
        if (i) await showTyping(typingDelay(seg));   // 首条不再预等
        lastRow = addMsg(seg, "them");
        pushHistory("assistant", seg, undefined, lastRow && lastRow.rec);
      }

      await finalizeReply(data, limitedParts, lastRow, userText);
    });
  }

  /** 流式收尾：以服务端 done 的结构化字段为准，补 history / 表情 / TTS / 记忆 */
  async function finalizeStreamed(data, userText, renderer) {
    await withSendLock(async () => {
      const g = guardReply(data, userText);
      data = g.data;
      // 以最终 reply 校准屏幕（服务端 replace / 人格复检后可能变短）
      renderer.replace(String(data.reply || ""));

      if (data.crisis) {
        const { level, script } = crisisScript(data);
        renderer.replace(script);
        const careHandle = addMsg(script, "them", { crisis: true, level });
        Stage.setEmotion("quiet");
        pushHistory("assistant", script, undefined, careHandle && careHandle.rec);
        return;   // 高危/中危：不播 TTS、不做钩子
      }

      let parts = renderer.parts();
      if (!parts.length) {
        parts = String(data.reply || "…").split("||").map(s => s.trim()).filter(Boolean).slice(0, 5);
        for (const seg of parts) addMsg(seg, "them");
      }
      // w9：流式结束后按渲染槽位的记录句柄回填 id，翻页/滑出窗口后仍能定位
      const handles = renderer.handles ? renderer.handles() : [];
      for (let i = 0; i < parts.length; i++) {
        pushHistory("assistant", parts[i], undefined, handles[i] && handles[i].rec);
      }
      await finalizeReply(data, parts, renderer.last(), userText);
    });
  }

  function showHook(text) {
    const bar = $("hookBar");
    bar.innerHTML = "";
    const chip = document.createElement("span");
    chip.className = "hook-chip";
    chip.textContent = text;   // textContent 本身即安全，再 escapeHtml 会显示成字面实体
    chip.onclick = () => { bar.classList.add("hidden"); send(text); };
    bar.appendChild(chip);
    bar.classList.remove("hidden");
  }

  // ---------- 主动开口（克制版） ----------
  /* 周期说明（AVD 零渲染探针"t≈45s 一次重绘"的来源）：
   * 首次空闲问候的间隔是 60s，从页面 init 起算；探针在页面就绪后静置 10s 才清零，
   * 而 reload→就绪本身约 12–15s，于是清零后 45s 左右正好命中这一条 60s 定时器。
   * 问候本身是产品行为（主动开口），不能删；问候里的 Stage.setEmotion("gentle")
   * 与 TTS 收尾的 lipFrame(0) 只在零动画下各补一帧，且现在由 stage.js 的
   * paintIfStillChanged 判重：开场白/上一次问候已经是 gentle 时不重绘（skip 日志）。
   * 因此它是一次性、有业务语义的唤醒，不是 45s 周期的兜底刷新。 */
  function armIdle() {
    resetIdle();
    if (pings >= 2) return;
    idleTimers.push(setTimeout(async () => {
      if (busy) { armIdle(); return; }   // 用户正在聊：让位，稍后再试（不吞掉这次空闲问候）
      pings++;
      const band = timeBand();
      const lines = {
        凌晨: "还没睡呀？……我陪你待一会儿||睡不着是因为有事，还是单纯刷手机停不下来",
        深夜: "咦，这个点还来，今天过得怎么样呀",
        清晨: "早呀！你居然起得比我还早",
        中午: "吃午饭了没？别又糊弄一顿",
        下午: "忙什么呢，休息一下眼睛嘛",
        晚上: "今天有什么想说的吗，我在呢"
      };
      const text = lines[band] || "在忙吗？我在呢";
      console.log("[probe] idle-greet pings=" + pings);
      try {
        await sendSplit({ reply: text, emotion: "gentle", motion: "Greeting" });
      } catch (e) {
        console.warn("空闲问候失败:", e.message);
      }
      armIdle();   // 排下一次（pings>=2 时自然停止），恢复"60s/150s 各一次"的节奏
    }, pings === 0 ? 60000 : 150000));
  }
  function resetIdle() { idleTimers.forEach(clearTimeout); idleTimers = []; }

  // ---------- 摸头/戳身体反应 ----------
  const POKE_LINES = {
    head: ["干嘛摸我头啦……头发要乱了||……不过，再摸一下也行", "喂喂，把你当朋友才让你摸头的哦"],
    body: ["哇！吓我一跳||怎么突然戳我，想引起我注意就直说嘛", "痒痒痒！住手啦哈哈"]
  };
  let pokeIdx = 0, lastPoke = 0;
  function onPoke(part) {
    const now = Date.now();
    if (now - lastPoke < 2500) return;   // 防连点刷屏
    lastPoke = now;
    const lines = POKE_LINES[part];
    const text = lines[pokeIdx++ % lines.length];
    sendSplit({ reply: text, emotion: part === "head" ? "shy" : "surprised" });
  }

  // ---------- 开场 ----------
  const GREET_KEY = "xiaoman_greet_at";       // 上次开场时间
  const GREET_TAIL_KEY = "xiaoman_greet_tail"; // 上次开场的末条文案（用于内容去重）
  /* 开场白落地耗时构成（PERF_R4 云端 4x 实测 1250ms，测量口径见 scripts/perf_r3.py）：
   *   ~900ms  GREET_WINDOW_MS 固定缓冲（产品语义：给用户先开口的机会，不是在等任何资源）
   *   ~310ms  页面 init 结束前不能注册定时器：DCL 211 → bind 198 → 分页挂载 → IndexedDB 恢复历史
   *   ~0ms    mock/网络：开场白是纯本地文案（G 表 + localStorage），不发 API；测量里 TTS=off
   *   ~30ms   渲染：addMsg + ChatPager 重建窗口 + 贴底强制布局
   * 因此本轮把定时器起点从"init 结束"提前到"页面开始"（performance.now 的 timeOrigin），
   * 并把舞台挂载推迟到开场白落屏之后：落地 ≈ 900ms + 渲染，而不是 init + 900ms。
   * 用户先开口的语义不变：页面打开后 GREET_WINDOW_MS 内发出的消息仍然优先（userInitiated）。 */
  const GREET_WINDOW_MS = 900;
  // 开场真正播出：幂等标记只在这里落盘（被延后/跳过都不会误消费，下次仍会开场）
  function playGreet(reply, toldNew, monthNew) {
    try {
      if (toldNew) localStorage.setItem(TOLD_KEY, String(XIAOMAN_VERSION));
      if (monthNew) localStorage.setItem(MONTH_DONE_KEY, monthNew);
      localStorage.setItem(GREET_KEY, String(Date.now()));
      localStorage.setItem(GREET_TAIL_KEY, reply.split("||").pop());
    } catch (e) {}
    console.log("[probe] greet played ms=" + Math.round(performance.now()));
    // 文本先落屏（sendSplit 的同步段先 addMsg），再放行舞台加载：语音/舞台都不挡开场文本
    sendSplit({ reply, emotion: "gentle", motion: "Greeting" });
    requestStageMount();
  }
  function greet() {
    let lastGreet = 0, tail = "";
    try {
      lastGreet = Number(localStorage.getItem(GREET_KEY) || 0) || 0;
      tail = localStorage.getItem(GREET_TAIL_KEY) || "";
    } catch (e) {}
    // 去重：如果恢复出来的最后一条就是上次开场的末条（说明用户只是刷新/重进、
    // 中间没说过话），就不要再插一条开场——否则气泡会一次次累积。
    // 有待补回复的消息时例外：必须告诉用户"话没丢"。
    if (!loadPending().length && tail && Date.now() - lastGreet < 6 * 3600 * 1000) {
      // 隐藏的打字指示/系统提示不算"最后一条"，否则会误判成没说过话而重复开场；
      // 同时排除打字占位（"正在想…"）：它常驻 #messages 且带 .bubble，会把真正末条挤掉。
      const rows = Array.from(document.querySelectorAll("#messages .msg-row:not(.hidden) .bubble"))
        .filter(b => !b.classList.contains("typing"));
      const lastBubble = rows.length ? rows[rows.length - 1].innerText.trim() : "";
      if (lastBubble && lastBubble.endsWith(tail)) {
        requestStageMount();   // 开场被去重跳过：舞台不能跟着一直等
        return;
      }
    }
    const mem = loadMem();
    const band = timeBand();
    const name = mem["昵称"] ? `${mem["昵称"]}，` : "";
    const G = {
      凌晨: `${name}这个点还没睡？||先别急着说话，我就陪着你。想聊的时候再开口`,
      深夜: `${name}来啦。今天辛苦啦||我是小满，深夜树洞营业中——今天有什么想倒出来的吗`,
      清晨: `早呀${name || "呀"}！一大早就来找我，今天有什么期待的事吗`,
      中午: `${name}中午好呀，吃饭了没`,
      下午: `${name}下午好。忙里偷闲来找我啦`,
      晚上: `${name}晚上好呀。一天过去了，有想说的吗`
    };
    let reply = G[band] || G.深夜;
    // P0-3 升级告知：人格层变更后首次再访主动说（插时段问候与开场引用之间）。
    // 只对有记忆的老用户说——新用户没有"以前"可对比；说完/跳过即登记版本，幂等
    const told = parseInt(localStorage.getItem(TOLD_KEY) || "0", 10) || 0;
    let toldNew = false;
    if (told < XIAOMAN_VERSION) {
      if (Object.values(mem).some(v => v)) reply += `||${UPDATE_TELL}`;
      toldNew = true;   // 标记只在开场真的播出去时才落盘（被跳过就留给下次）
    }
    // P1-1 月度回放：新月首次开场回放上月话题（数据=真实记忆索引；幂等键=month_done，
    // 离线引擎 greet 分支同键写入双端不双播；话题<2 不硬凑——没聊够就不播）
    let monthNew = null;
    try {
      const pm = prevMonthStr(Date.now());
      const md = localStorage.getItem(MONTH_DONE_KEY) || "";
      if (md !== pm) {
        monthNew = pm;
        const topics = monthTopics(pm);
        if (topics.length >= 2) {
          reply += `||翻了翻小本本||上个月你跟我聊过${topics.join("、")}，我都记着呢。这个月接着来`;
        }
      }
    } catch (e) { /* 数据层异常不挡开场 */ }
    // P0-1 再见面开场引用：钉子户里的大事（近事件）优先，其次宠物
    // 不需要严格判定"隔天"——首日铺设完成前（无大事无宠物）自然不会引用
    const event = mem["大事"];
    if (event) {
      reply += `||对了，你上次说${event}——怎么样啦？我一直记着呢`;
    } else if (mem["宠物"]) {
      reply += `||还有，你家${mem["宠物"]}最近乖不乖？`;
    }
    // P1-3 排队提示：还有欠回复的消息时给明确入口（说一句话即触发补话，零 API
    // 依赖——信号好不好都先亮态度：话没丢，人记得）
    try {
      if (loadPending().length) {
        reply += `||对了，你之前跟我说的那几句话我都记着||刚才信号不太好没接上——你随便说句什么，我缓过来就回你`;
      }
    } catch (e) { /* 数据层异常不挡开场 */ }
    /* 缓冲窗口从页面开始（timeOrigin=0）算，而不是从 init 结束算：
     * 历史恢复/分页挂载/启动耗时不该被叠加进开场白；页面打开后 900ms 内用户先发的消息
     * 仍由 userInitiated 优先（语义不变）。慢机上 init 超过 900ms 时 wait=0，但仍排在
     * 历史恢复之后执行，保证消息顺序。 */
    const elapsed = (typeof performance !== "undefined" && performance.now)
      ? performance.now() : GREET_WINDOW_MS;
    const wait = Math.max(0, GREET_WINDOW_MS - Math.round(elapsed));
    setTimeout(() => {
      // 开场窗口内用户先发了消息：不插队，也不丢。
      // 用户那轮还没收尾（busy/sending）→ 记下来，等回复结束后补播；
      // 已经收尾了 → 现在就补，仍排在用户消息/回复之后。
      if (userInitiated) {
        if (busy || sending) {
          pendingGreet = { reply, toldNew, monthNew };
          console.log("[probe] greet deferred user-first");
        } else {
          console.log("[probe] greet played-after-reply");
          playGreet(reply, toldNew, monthNew);
        }
        return;
      }
      playGreet(reply, toldNew, monthNew);
    }, wait);
  }

  // ---------- 抽屉/弹窗：焦点管理 + Esc 关闭 + 退出动画 ----------
  const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
  let returnFocus = null;
  const panelOf = el => el.querySelector(".drawer-panel, .modal-panel") || el;
  const openPanels = () => [...document.querySelectorAll(".drawer:not(.hidden), .modal:not(.hidden)")];

  function openPanel(el, trigger) {
    returnFocus = trigger || document.activeElement;
    el.classList.remove("hidden");
    const panel = panelOf(el);
    if (!panel.hasAttribute("tabindex")) panel.setAttribute("tabindex", "-1");
    panel.focus();
  }

  function closePanel(el) {
    if (el.classList.contains("hidden")) return;
    // 零动画：直接隐藏，不等待退场过渡
    el.classList.add("hidden");
    el.classList.remove("is-closing");
    if (returnFocus && returnFocus.focus) returnFocus.focus();
    returnFocus = null;
  }

  // Esc 关闭最上层；Tab 在面板内循环，避免焦点跑到背后的页面上
  function onKeydown(e) {
    const open = openPanels();
    if (!open.length) return;
    const panel = panelOf(open[open.length - 1]);
    if (e.key === "Escape") { e.preventDefault(); closePanel(open[open.length - 1]); return; }
    if (e.key !== "Tab") return;
    const items = [...panel.querySelectorAll(FOCUSABLE)].filter(x => !x.disabled && x.offsetParent !== null);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (document.activeElement === panel || !panel.contains(document.activeElement)) {
      e.preventDefault(); (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  // ---------- 绑定 ----------
  /** 输入栏状态：随内容自动长高（44→132）、空内容禁用发送、接近上限显示计数 */
  function syncComposer() {
    const input = $("textInput");
    if (!input) return;
    const len = input.value.length;
    const btn = $("sendBtn");
    if (btn) btn.disabled = busy || !input.value.trim();
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight || 44, 132) + "px";
    const cc = $("charCount");
    if (cc) {
      cc.hidden = len < 400;
      cc.textContent = len ? len + "/500" : "";
    }
  }
  function bind() {
    // 统一提交入口：busy 时不吞字（保留输入并提示），避开 IME 合成态误发送
    function submitText() {
      const input = $("textInput");
      const v = input.value;
      if (!v.trim()) return;
      if (busy) { toast("等我把这句说完嘛"); return; }
      input.value = "";
      syncComposer();
      send(v);
    }
    $("sendBtn").onclick = submitText;
    $("textInput").addEventListener("keydown", e => {
      // isComposing / keyCode 229：中文输入法候选确认回车，不算发送；Shift+Enter 换行
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        submitText();
      }
    });
    $("textInput").addEventListener("input", syncComposer);
    syncComposer();
    const micBtn = $("micBtn");
    if (micBtn) micBtn.onclick = () => toast("语音输入接入中，先打字撩她吧");
    const degradeClose = $("degradeClose");
    if (degradeClose) degradeClose.onclick = () => {
      $("degradeBadge").classList.add("hidden");
      try { sessionStorage.setItem("xiaoman_degrade_off", "1"); } catch (e) {}
    };
    const voiceTipClose = $("voiceTipClose");
    if (voiceTipClose) voiceTipClose.onclick = () => {
      $("voiceTip").classList.add("hidden");
      try { sessionStorage.setItem("xiaoman_voice_tip_off", "1"); } catch (e) {}
    };
    // 排队提示：再试一次 / 本次不再提醒
    const pendingRetry = $("pendingRetry");
    if (pendingRetry) pendingRetry.onclick = () => retryPending();
    const pendingClose = $("pendingClose");
    if (pendingClose) pendingClose.onclick = () => {
      $("pendingBar").classList.add("hidden");
      try { sessionStorage.setItem(PENDING_CLOSED_KEY, "1"); } catch (e) {}
    };
    renderPending();
    // 断网/恢复：离线时明确告知"还能看记录"，恢复时把排队提示重新亮出来
    window.addEventListener("offline", () => {
      showDegrade("offline");
      toast("网络断了，之前的记录和小满都还在");
    });
    window.addEventListener("online", () => {
      renderPending(true);
      if (window.__pendingCount && window.__pendingCount() > 0) toast("网络回来了，可以点“再试一次”");
    });
    $("memoryBtn").onclick = () => { renderMem(); openPanel($("memoryDrawer"), $("memoryBtn")); };
    $("memoryClose").onclick = () => closePanel($("memoryDrawer"));
    $("memoryDrawer").querySelector(".drawer-mask").onclick = () => closePanel($("memoryDrawer"));
    $("memoryClear").onclick = () => {
      localStorage.removeItem(MEM_KEY);
      if (window.MemoryStore) MemoryStore.clear();
      // 会话状态一并清掉：清空记忆=重新认识，别让引擎的冷却/去重/引导节奏留幽灵
      if (typeof MockEngine !== "undefined" && typeof MockEngine.resetSession === "function") MockEngine.resetSession(SID);
      renderMem();
      toast("小满失忆了，重新认识一下吧");
    };
    // 首次引导的三个快捷入口（空状态内）
    if ($("quickSay")) $("quickSay").onclick = () => { $("textInput").focus(); };
    if ($("quickRemind")) $("quickRemind").onclick = () => {
      renderMem();
      openPanel($("memoryDrawer"), $("quickRemind"));
      const t = $("reminderText");
      if (t) { t.focus(); t.scrollIntoView({ block: "center" }); }
    };
    if ($("quickMemory")) $("quickMemory").onclick = () => {
      renderMem(); openPanel($("memoryDrawer"), $("quickMemory"));
    };
    // 提示条：重试 / 关闭
    if ($("noticeClose")) $("noticeClose").onclick = hideNotice;
    if ($("noticeRetry")) $("noticeRetry").onclick = () => {
      const act = noticeAction;
      hideNotice();
      if (typeof act === "function") { try { act(); } catch (e) { toast("重试还是没成功，稍后再试一次"); } }
    };
    // ---------- P2-3 友谊卡片（口碑裂变：脱敏聚合数据 → 2D canvas PNG → 分享/保存） ----------
    if (window.FriendCard) {
      $("cardBtn").onclick = () => {
        try {
          FriendCard.render($("cardImg"));
          openPanel($("cardModal"), $("cardBtn"));
        } catch (e) { toast("卡片生成失败了，再试一次？"); }
      };
      $("cardClose").onclick = () => closePanel($("cardModal"));
      $("cardModal").querySelector(".drawer-mask").onclick = () => closePanel($("cardModal"));
      $("cardSave").onclick = async () => {
        const res = await FriendCard.share();
        toast(res === "shared" ? "已呼出分享面板" : "卡片已开始下载，也可以长按图片保存");
      };
    }
    if ($("settingsBtn")) $("settingsBtn").onclick = () => {
      const cfg = API.loadCfg();
      $("cfgMode").value = cfg.mode;
      refreshModeHint();
      $("cfgApi").value = cfg.apiBase || "";
      $("cfgApiKey").value = API.getApiKey();
      $("cfgModel").value = cfg.model || "";
      $("cfgTtsBase").value = cfg.ttsServer || "";
      $("cfgTts").value = cfg.tts || "server";
      $("cfgVoice").value = cfg.voice || "zh-CN-XiaoyiNeural";
      $("cfgRate").value = cfg.ttsRate || "normal";
      $("cfgHeartbeat").checked = cfg.heartbeatOn !== false;
      $("cfgAllowFree").checked = cfg.allowFreePersonal === true;
      updateTtsHint();
      syncAppearancePanel();
      $("cfgTheme").value = (() => { try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { return "auto"; } })();
      $("cfgTestResult").textContent = "";
      openPanel($("settingsModal"), $("settingsBtn"));
    };
    $("settingsClose").onclick = () => closePanel($("settingsModal"));
    $("settingsModal").querySelector(".drawer-mask").onclick = () => closePanel($("settingsModal"));
    [$("cfgMode"), $("cfgApi"), $("cfgModel"), $("cfgTtsBase"), $("cfgTts"), $("cfgVoice"), $("cfgRate")].forEach(el => {
      el.addEventListener("change", () => {
        API.saveCfg({
          mode: $("cfgMode").value,
          apiBase: $("cfgApi").value.trim(),
          model: $("cfgModel").value.trim(),
          ttsBase: $("cfgTtsBase").value.trim(),
          ttsServer: $("cfgTtsBase").value.trim(),   // 远程语音显式启用（默认关闭）
          tts: $("cfgTts").value,
          voice: $("cfgVoice").value,
          ttsRate: $("cfgRate").value
        });
        refreshModeHint();
        updateTtsHint();
        toast("已保存");
      });
    });
    // 「主动问候」开关：真实作用于 Heartbeat.tick（每 tick 现读配置）
    $("cfgHeartbeat").addEventListener("change", () => {
      API.saveCfg({ heartbeatOn: $("cfgHeartbeat").checked });
      toast($("cfgHeartbeat").checked ? "小满会偶尔主动找你" : "已关掉主动问候，提醒照常");
    });
    // 「免费通道」开关：真实作用于 API 路由（安卓免 Key 环境才会改变通道）
    $("cfgAllowFree").addEventListener("change", () => {
      API.saveCfg({ allowFreePersonal: $("cfgAllowFree").checked });
      toast($("cfgAllowFree").checked ? "已允许免费通道承载聊天内容" : "已关闭免费通道（隐私优先）");
    });
    // 外观预设：三档按钮 + 三个独立开关（改任一开关 → 自定义）
    $("apPresets").querySelectorAll("[data-preset]").forEach(btn => {
      btn.addEventListener("click", () => {
        const p = Appearance.setPreset(btn.dataset.preset);
        toast(`外观已切换：${PRESET_LABEL[p.preset]}`);
      });
    });
    $("apFont").addEventListener("change", () => {
      Appearance.setSwitch("font", $("apFont").checked ? "large" : "normal");
      toast($("apFont").checked ? "已切为大字号" : "已切为标准字号");
    });
    $("apMotion").addEventListener("change", () => {
      Appearance.setSwitch("motion", $("apMotion").checked);
      toast($("apMotion").checked ? "已开启动画（仅状态过渡）" : "已关闭所有动画");
    });
    $("apIcons").addEventListener("change", () => {
      Appearance.setSwitch("iconsGray", $("apIcons").checked);
      toast($("apIcons").checked ? "图标已改为灰阶" : "图标已改为彩色");
    });
    syncAppearancePanel();
    // ---------- 倾诉记录导出 / 导入（本地 JSON，不做云同步） ----------
    $("backupExport").onclick = async () => {
      const btn = $("backupExport"), out = $("backupResult");
      btn.disabled = true; out.textContent = "正在整理…";
      try {
        const r = await Backup.exportNow(SID);
        const extra = [];
        if (r.reminders) extra.push(`${r.reminders} 条提醒`);
        if (r.server) extra.push(`服务端会话 ${r.slots} 条记忆槽`);
        else extra.push("服务端会话未取到（已跳过）");
        out.textContent = r.count
          ? `已导出 ${r.count} 条记录 + ${extra.join(" + ")}：${r.path || r.name}`
          : `这台手机上还没有可导出的记录（${extra.join("，")}）`;
        toast(r.count ? "记录已存到本机" : "还没有记录可以导出");
      } catch (e) {
        out.textContent = "导出失败：" + (e.message || "未知原因");
      } finally { btn.disabled = false; }
    };
    $("backupImport").onclick = () => { $("backupFile").click(); };
    $("backupFile").addEventListener("change", async () => {
      const file = $("backupFile").files && $("backupFile").files[0];
      const out = $("backupResult");
      out.textContent = "正在导入…";
      try {
        const text = await Backup.readFile(file);
        const r = await Backup.importNow(text, SID);
        if (!r.ok) { out.textContent = "导入失败：" + r.error; return; }
        const extra = [];
        if (r.reminders) extra.push(`提醒 ${r.reminders} 条`);
        if (r.stats) extra.push("小本本已合并");
        if (r.server && r.server.ok) extra.push(`服务端已还原（${r.server.messages || 0} 条历史）`);
        else if (r.server) extra.push("服务端未连上（跳过）");
        out.textContent = (r.imported
          ? `导入 ${r.imported} 条（跳过重复 ${r.skipped} 条，记忆 ${r.memories} 条`
          : `没有新记录（${r.skipped} 条已存在，记忆 ${r.memories} 条`)
          + (extra.length ? "，" + extra.join("，") : "") + "）";
        // 导入=会话内容整体换血：清掉引擎的旧节奏（最近回复/冷却/引导），
        // 否则刷新后还会按导入前的上下文去重或重复问"怎么称呼你"
        if (typeof MockEngine !== "undefined" && typeof MockEngine.resetSession === "function") MockEngine.resetSession(SID);
        toast("导入完成，马上刷新");
        setTimeout(() => location.reload(), 500);
      } catch (e) {
        out.textContent = "导入失败：" + (e.message || "文件读不出来");
      } finally {
        $("backupFile").value = "";
      }
    });
    // 外观切换即时生效（深色/浅色/跟随系统），不依赖 WebView 重绘时机
    $("cfgTheme").addEventListener("change", () => {
      applyTheme($("cfgTheme").value);
      toast("外观已切换");
    });
    // Key 只进内存，不落 localStorage
    $("cfgApiKey").addEventListener("change", () => {
      API.setApiKey($("cfgApiKey").value.trim());
      toast("Key 仅保存在本次页面内存中");
    });
    $("cfgTestBtn").onclick = async () => {
      const btn = $("cfgTestBtn"), out = $("cfgTestResult");
      btn.disabled = true;
      out.textContent = "测试中…";
      try {
        const r = await API.testConnection({
          mode: $("cfgMode").value,
          apiBase: $("cfgApi").value.trim(),
          apiKey: $("cfgApiKey").value.trim()
        });
        if (r && r.ok) {
          const list = (r.models || []).slice(0, 8).join("、");
          out.textContent = "连接成功：" + (r.note || "可用") + (list ? "｜模型：" + list : "");
          if (r.model && !$("cfgModel").value) {
            $("cfgModel").value = r.model;
            API.saveCfg({ model: r.model });
          }
        } else {
          out.textContent = "连接失败：" + ((r && r.error) || "未知原因") +
            (r && r.status ? `（HTTP ${r.status}）` : "");
        }
      } catch (e) {
        out.textContent = "连接失败：" + e.message;
      } finally {
        btn.disabled = false;
      }
    };
    // 抽屉/弹窗：Esc 关闭 + Tab 焦点循环
    document.addEventListener("keydown", onKeydown);
    // 舞台键盘等价操作：摸头（Live2D 命中区只能点击，键盘走同一反应）
    $("stage").addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") { e.preventDefault(); onPoke("head"); }
    });
    // P2-4 悬浮挂件：收起按钮 + 点挂件展开（widget.js 内部绑定 stage 点击）
    if (window.Widget) Widget.bind();

    // 主 App 可见性心跳：桌宠页据此让位提醒，避免主程序与悬浮窗重复弹气泡
    // 原生 onResume/onPause 调 __appVisibility；页面不可见 90s 后桌宠自动接手
    try {
      const PRESENCE_KEY = "xiaoman_app_visible_v1";
      let appVisible = !document.hidden;
      const touchPresence = () => {
        if (!appVisible) return;
        try { localStorage.setItem(PRESENCE_KEY, String(Date.now())); } catch (e) {}
      };
      window.__appVisibility = (v) => {
        appVisible = !!v;
        try {
          if (appVisible) touchPresence();
          else localStorage.removeItem(PRESENCE_KEY);
        } catch (e) {}
      };
      touchPresence();
      setInterval(touchPresence, 15000);
      document.addEventListener("visibilitychange", () => window.__appVisibility(!document.hidden));
      window.addEventListener("pagehide", () => { try { localStorage.removeItem(PRESENCE_KEY); } catch (e) {} });
    } catch (e) { console.warn("[probe] PRESENCE_FAIL", e.message); }

    // 悬浮小满桌宠（仅 Android 壳内显示：桥上挂了 floatGranted 才露出入口）
    try {
      if (window.AndroidTTS && typeof window.AndroidTTS.floatGranted === "function") {
        $("floatField").hidden = false;
        const PENDING_KEY = "xiaoman_float_pending_v1";
        const startFloat = () => {
          if (window.AndroidTTS.floatGranted()) {
            let ok = false;
            try { ok = !!window.AndroidTTS.floatShow(); } catch (e) { ok = false; }
            toast(ok ? "小满已经在桌面上了——单击互动，双击聊天" : "启动失败，请检查「显示在其他应用上层」权限");
            try { sessionStorage.removeItem(PENDING_KEY); } catch (e) {}
          } else {
            toast("请先允许「显示在其他应用上层」，授权后回来即可开启");
            try { sessionStorage.setItem(PENDING_KEY, "1"); } catch (e) {}
            window.AndroidTTS.floatOpenSettings();
          }
        };
        // 从系统授权页返回时自动续上：只在用户主动点过开启后才触发
        const resumePending = () => {
          let pending = false;
          try { pending = sessionStorage.getItem(PENDING_KEY) === "1"; } catch (e) {}
          if (pending && window.AndroidTTS.floatGranted()) startFloat();
        };
        window.addEventListener("focus", resumePending);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) resumePending(); });
        $("floatOpenBtn").onclick = startFloat;
        $("floatCloseBtn").onclick = () => {
          try { window.AndroidTTS.floatHide(); } catch (e) {}
          try { sessionStorage.removeItem(PENDING_KEY); } catch (e) {}
          toast("悬浮小满已关闭");
        };
      }
    } catch (e) { console.warn("[probe] FLOAT_UI_FAIL", e.message); }
  }

  // ---------- 舞台挂载（非关键资源，等聊天落屏后再说）----------
  let stageAsked = false;
  /** 开场白/首条消息真正落屏后才请求挂载 Live2D（运行时约 900KB + 纹理解码）。
   *  旧实现是在 init 末尾直接 requestIdleCallback(timeout 1200)：4x 降速下这个
   *  timeout 恰好落在开场白 900ms 窗口末尾，舞台的同步初始化与开场白抢同一帧，
   *  把开场白落地时间往后推。现在改成"落屏 → 200ms → idle"，两条路径互不干扰。 */
  function requestStageMount() {
    if (stageAsked) return;
    if (window.FloatMode && FloatMode.on) { stageAsked = true; return; }
    stageAsked = true;
    const run = () => Stage.mount($("stage")).then(() => {
      Stage.setMotionEnabled(Appearance.get().motion);
      console.log("[probe] mount ok ms=" + Math.round(performance.now()));
    }).catch(e => console.warn("[probe] STAGE_FAIL", e.message));
    // 先让这一帧的文本提交/绘制；200ms 内舞台仍显示 CSS 骨架占位，观感无损失
    setTimeout(() => {
      if (typeof requestIdleCallback === "function") requestIdleCallback(run, { timeout: 1200 });
      else setTimeout(run, 600);
    }, 200);
  }

  async function init() {
    // 1) 先把输入/发送绑上：这是"首屏可交互"，绝不能被网络、舞台或存储挡住
    try {
      bind(); console.log("[probe] bind ok ms=" + Math.round(performance.now()));
    } catch (e) { console.error("[probe] BIND_FAIL", e.message); return; }

    // 1.5) 长会话分页窗口：只渲染当前页 + 缓冲（200 轮后 DOM 仍为常数）
    try {
      ChatPager.mount({
        host: $("messages"),
        typing: $("typingRow"),
        scroll: $("chatScroll"),
        build: buildRow,
        loader: loadOlderPage   // IndexedDB：首屏 30 条 → 上翻可回看全部 300 条
      });
      // 状态区（错误卡/旁白行）出现或收起会改变可视高度：只在"跟随最新"时贴底，
      // 用户主动上滑后 pinned=false，不会被强行拉回。
      const stack = $("statusStack");
      if (stack && typeof ResizeObserver === "function") {
        let lastH = -1;
        const ro = new ResizeObserver(() => {
          const h = stack.getBoundingClientRect ? stack.getBoundingClientRect().height : 0;
          if (Math.abs(h - lastH) < 1) return;
          lastH = h;
          if (typeof ChatPager.followBottom === "function") ChatPager.followBottom();
        });
        ro.observe(stack);
      }
    } catch (e) { console.warn("[probe] PAGER_FAIL", e.message); }

    // 2) 服务端默认配置改为非阻塞：慢网/离线时不再拖住首屏
    API.bootstrap().catch(e => console.warn("[probe] BOOTSTRAP_FAIL", e.message));

    // 3) 从 IndexedDB 恢复历史（先于开场白，保证时间顺序；本机路径通常 <10ms）
    try { await restoreHistory(); } catch (e) { console.warn("[probe] HISTORY_FAIL", e.message); }
    syncEmptyState();

    // 4) 舞台是非关键资源（Live2D 运行时约 900KB + 纹理解码）：不再在 init 里抢空闲帧。
    //    触发点见 requestStageMount：开场白/首条消息落屏后 200ms 再进 idle。
    //    悬浮窗迷你模式：跳过 Live2D（双 WebView 实例省内存），布局由 CSS float-mode 接管
    const isFloat = window.FloatMode && FloatMode.on;
    if (isFloat) {
      console.log("[probe] float-mode: stage & widget skipped");
      try { $("stage").style.display = "none"; } catch (e) {}
    } else {
      // 兜底：既没开场（去重跳过/异常）也没发消息时，缓冲窗口后仍把舞台挂上
      setTimeout(requestStageMount, GREET_WINDOW_MS + 600);
    }

    try { greet(); console.log("[probe] greet ok ms=" + Math.round(performance.now())); } catch (e) { console.warn("[probe] GREET_FAIL", e.message); }
    armIdle();

    // 心跳系统：提醒投递 + 朋友式主动问候（多重频控，不打扰）
    if (window.Heartbeat && window.Reminders) {
      try {
        Heartbeat.start(async (data) => {
          if (busy) {
            // 提醒不能被 busy 静默丢弃：抛错让心跳保留待下轮重投；闲聊则直接跳过
            if (data && data.kind === "reminder") throw new Error("busy: 提醒稍后重投");
            return;
          }
          await sendSplit(data, null);
          renderMem();
        }, n => history.slice(-n));
        console.log("[probe] heartbeat ok");
      } catch (e) { console.warn("[probe] HEARTBEAT_FAIL", e.message); }
    }
  }

  return { init, send, onPoke };
})();

window.addEventListener("DOMContentLoaded", () => {
  App.init().catch(e => console.error("[probe] INIT_FAIL", e.message));
});
