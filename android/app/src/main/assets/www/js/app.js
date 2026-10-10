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
  // 危机词表：与 mock_engine.js / mock_api.py 保持同一集合（安全网口径一致）
  const CRISIS_RE = /(不想活|想死|活不下去|自杀|自残|了结|伤害自己|撑不下去|活着.*没意思|没有意思.*活|没有意义.*活|跳楼|结束自己|煤气|遗书|不想醒来|去死|寻短见|解脱|不想活着|活着没劲|死了算了)/;
  const CARE_SCRIPT = "……这个我当真了，也想让你当真。你现在的感觉，值得被认真对待，不丢人。先陪我聊一会儿，好吗？我也想让你和更专业的人聊聊——";

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
      // 危机语境不播——刚递完关怀卡片就"庆祝100次"是灾难
      const lastA = [...history].reverse().find(h => h.role === "assistant");
      if (lastA && lastA.content === CARE_SCRIPT) return;
      const n = readRounds(), done = readMsDone();
      const m = MILESTONES.find(x => n >= x && !done.includes(x));
      if (m == null) return;
      localStorage.setItem(MS_DONE_KEY, JSON.stringify([...done, m]));
      await new Promise(r => setTimeout(r, 700));   // 分条节奏：里程碑单独一条，稍隔半拍
      const line = milestoneLine(m);
      addMsg(line, "them", { tip: true });
      history.push({ role: "assistant", content: line, at: Date.now() });
      if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
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

  let history = [];          // [{role, content, at}]
  let busy = false;
  let sending = false;       // 发送互斥锁：防止 greet/心跳/poke/hook 与用户发送并发
  let sendQueue = [];        // 等待发送的消息队列
  let lockDepth = 0;         // 发送锁重入深度（同一调用链嵌套获取时直接放行，避免自锁）
  let idleTimers = [];
  let pings = 0;
  const MAX_HISTORY = 30;    // 历史滑动窗口上限

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

  // ---------- 渲染 ----------
  function addMsg(text, who, opts = {}) {
    const row = document.createElement("div");
    row.className = `msg-row ${who}`;
    if (opts.crisis) {
      const card = document.createElement("div");
      card.className = "care-card";
      card.textContent = text;
      const hotline = document.createElement("div");
      hotline.className = "hotline";
      const pulse = document.createElement("span");
      pulse.className = "pulse";
      hotline.appendChild(pulse);
      hotline.append("全国心理援助热线 12356 · 24小时");
      card.appendChild(hotline);
      row.appendChild(card);
    } else if (opts.tip) {
      const bubble = document.createElement("div");
      bubble.className = "bubble system-tip";
      bubble.textContent = text;
      row.appendChild(bubble);
    } else {
      if (who === "them") {
        const av = document.createElement("div");
        av.className = "avatar-mini";
        av.textContent = "满";
        row.appendChild(av);
      }
      const bubble = document.createElement("div");
      bubble.className = `bubble ${who}`;
      bubble.textContent = text;
      if (opts.voice) {
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = "🔊 语音已播";
        bubble.appendChild(meta);
      }
      row.appendChild(bubble);
    }
    $("typingRow").before(row);
    $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
    return row;
  }

  function renderMem() {
    const mem = loadMem();
    const box = $("memoryChips");
    box.innerHTML = "";
    const entries = Object.entries(mem);
    if (!entries.length) {
      const empty = document.createElement("span");
      empty.className = "chip empty";
      empty.textContent = "还没有，聊着聊着就有了";
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
    // 提醒清单（可取消）
    if (window.Reminders) {
      const rbox = $("reminderList");
      const pend = Reminders.pending();
      rbox.innerHTML = "";
      if (pend.length) {
        const sub = document.createElement("p");
        sub.className = "drawer-sub";
        sub.style.marginTop = "10px";
        sub.textContent = "定好的提醒";
        rbox.appendChild(sub);
        for (const r of pend) {
          const div = document.createElement("div");
          div.className = "chip row";
          const span = document.createElement("span");
          span.textContent = `⏰ ${Reminders.fmt(r.at)} · ${r.text}`;
          const btn = document.createElement("button");
          btn.dataset.rid = r.id;
          btn.className = "r-cancel";
          btn.setAttribute("aria-label", "取消提醒");
          btn.textContent = "✕";
          btn.onclick = () => { Reminders.cancel(btn.dataset.rid); renderMem(); toast("提醒取消了"); };
          div.appendChild(span);
          div.appendChild(btn);
          rbox.appendChild(div);
        }
      }
    }
  }

  // ---------- 打字延迟（活人感核心之一） ----------
  async function showTyping(ms) {
    $("typingRow").classList.remove("hidden");
    $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
    await sleep(ms);
    $("typingRow").classList.add("hidden");
  }
  const typingDelay = text => Math.min(2800, 550 + text.length * 42);

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
  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; $("sendBtn").disabled = true;
    resetIdle();

    await withSendLock(async () => {
      addMsg(text, "me");
      history.push({ role: "user", content: text, at: Date.now() });
      // 滑动窗口：保留最近 MAX_HISTORY 条
      if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
      bumpRounds();   // P1-1：计一轮"来找我"（危机/提醒/关系问答也算——都真实发生了）

      // 危机拦截（客户端先行，服务端还有一道）
      if (CRISIS_RE.test(text)) {
        await showTyping(900);
        addMsg(CARE_SCRIPT, "them", { crisis: true });
        Stage.setEmotion("gentle");
        history.push({ role: "assistant", content: CARE_SCRIPT, at: Date.now() });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
        return;
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
            await showTyping(700);
            addMsg(conf, "them", { tip: true });
            history.push({ role: "assistant", content: conf, at: Date.now() });
            if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
            toast(`已设提醒：${Reminders.fmt(item.at)}`);
            return;
          }
        }
      }

      // P1-1 关系进展查询：本地确定性应答（天数/轮数/月度话题数据只在客户端，
      // 交给模型会瞎编——零虚构铁律。优先级 危机 > 提醒 > 关系查询 > API，先例同前）
      if (window.__isRelationQ(text)) {
        const r = relationReply(text);
        await showTyping(700);
        await sendSplit({ reply: r, emotion: "happy", motion: null }, text);
        await checkMilestone();
        return;
      }

      try {
        await showTyping(650 + Math.random() * 500);   // 读消息的停顿
        // P1-1：memSection 尾部拼关系进度行（真模型路径也有感知，数据同源）
        const memCtx = (window.MemoryStore ? MemoryStore.renderContext(text) : "");
        const relLine = relationContextLine();
        const data = await API.chat(history, {
          memSection: relLine ? (memCtx ? memCtx + "\n" + relLine : relLine) : memCtx,
          now: new Date()
        });
        await sendSplit(data, text);
        await checkMilestone();   // P1-1：里程碑播报（幂等；危机轮 sendSplit 已早退也不误播——ms_done 闸门兜底）
      } catch (e) {
        console.error(e);
        await showTyping(800);
        addMsg("（信号飘走了…你还在吗？再发一次试试）", "them", { tip: false });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
      }
    });
    busy = false; $("sendBtn").disabled = false;
    armIdle();
  }

  /** 分条发送 + 表情动作 + 语音 + 钩子（带发送锁） */
  async function sendSplit(data, userText) {
    await withSendLock(async () => {
      // 支持模型返回的 crisis 字段（Defect 13）
      if (data.crisis) {
        await showTyping(900);
        addMsg(CARE_SCRIPT, "them", { crisis: true });
        Stage.setEmotion("gentle");
        history.push({ role: "assistant", content: CARE_SCRIPT, at: Date.now() });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
        return;
      }

      const parts = String(data.reply || "…").split("||").map(s => s.trim()).filter(Boolean);
      // 段数上限：防止模型输出过多段导致刷屏
      const limitedParts = parts.slice(0, 5);

      let lastRow = null;
      for (let i = 0; i < limitedParts.length; i++) {
        const seg = limitedParts[i];
        await showTyping(typingDelay(seg));
        lastRow = addMsg(seg, "them");
        history.push({ role: "assistant", content: seg, at: Date.now() });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
      }

      // 表情 + 动作
      if (data.emotion) Stage.setEmotion(data.emotion);
      if (data.motion && data.motion !== "null") Stage.playMotion(data.motion);

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

      // 语音（只念最后一条短消息，避免长篇朗读）
      if (limitedParts.length) {
        const spoken = limitedParts[limitedParts.length - 1].replace(/[\uFF0C。！？~…]+$/g, "");
        TTS.speak(spoken, Stage.lipFrame.bind(Stage), () => {
          // 播放完成后在最后一条气泡标记语音已播（用 addMsg 返回的行引用，避免命中 typingRow）
          const bubble = lastRow && lastRow.querySelector(".bubble");
          if (bubble && !bubble.querySelector(".meta")) {
            const meta = document.createElement("span");
            meta.className = "meta";
            meta.textContent = "🔊 语音已播";
            bubble.appendChild(meta);
          }
        });
      }

      // 留存钩子
      if (data.hook) showHook(data.hook);
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
  function greet() {
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
    if (told < XIAOMAN_VERSION) {
      if (Object.values(mem).some(v => v)) reply += `||${UPDATE_TELL}`;
      localStorage.setItem(TOLD_KEY, String(XIAOMAN_VERSION));
    }
    // P1-1 月度回放：新月首次开场回放上月话题（数据=真实记忆索引；幂等键=month_done，
    // 离线引擎 greet 分支同键写入双端不双播；话题<2 不硬凑——没聊够就不播）
    try {
      const pm = prevMonthStr(Date.now());
      const md = localStorage.getItem(MONTH_DONE_KEY) || "";
      if (md !== pm) {
        localStorage.setItem(MONTH_DONE_KEY, pm);
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
    setTimeout(() => {
      sendSplit({ reply, emotion: "gentle", motion: "Greeting" });
    }, 900);
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
    el.classList.add("is-closing");
    // 等退出动画放完再真正隐藏（prefers-reduced-motion 下动画被压到极短，同样成立）
    setTimeout(() => {
      el.classList.add("hidden");
      el.classList.remove("is-closing");
      if (returnFocus && returnFocus.focus) returnFocus.focus();
      returnFocus = null;
    }, 200);
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
  function bind() {
    // 统一提交入口：busy 时不吞字（保留输入并提示），避开 IME 合成态误发送
    function submitText() {
      const input = $("textInput");
      const v = input.value;
      if (!v.trim()) return;
      if (busy) { toast("等我把这句说完嘛"); return; }
      input.value = "";
      send(v);
    }
    $("sendBtn").onclick = submitText;
    $("textInput").addEventListener("keydown", e => {
      // isComposing / keyCode 229：中文输入法候选确认回车，不算发送
      if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) submitText();
    });
    $("micBtn").onclick = () => toast("语音输入接入中，先打字撩她吧");
    $("memoryBtn").onclick = () => { renderMem(); openPanel($("memoryDrawer"), $("memoryBtn")); };
    $("memoryClose").onclick = () => closePanel($("memoryDrawer"));
    $("memoryDrawer").querySelector(".drawer-mask").onclick = () => closePanel($("memoryDrawer"));
    $("memoryClear").onclick = () => { localStorage.removeItem(MEM_KEY); if (window.MemoryStore) MemoryStore.clear(); renderMem(); toast("小满失忆了，重新认识一下吧"); };
    $("settingsBtn").onclick = () => {
      const cfg = API.loadCfg();
      $("cfgApi").value = cfg.apiBase || "http://127.0.0.1:8902";
      $("cfgTts").value = cfg.tts || "server";
      $("cfgVoice").value = cfg.voice || "zh-CN-XiaoyiNeural";
      openPanel($("settingsModal"), $("settingsBtn"));
    };
    $("settingsClose").onclick = () => closePanel($("settingsModal"));
    $("settingsModal").querySelector(".drawer-mask").onclick = () => closePanel($("settingsModal"));
    [$("cfgApi"), $("cfgTts"), $("cfgVoice")].forEach(el => el.addEventListener("change", () => {
      API.saveCfg({ apiBase: $("cfgApi").value.trim(), tts: $("cfgTts").value, voice: $("cfgVoice").value });
      toast("已保存");
    }));
    // 抽屉/弹窗：Esc 关闭 + Tab 焦点循环
    document.addEventListener("keydown", onKeydown);
    // 舞台键盘等价操作：摸头（Live2D 命中区只能点击，键盘走同一反应）
    $("stage").addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") { e.preventDefault(); onPoke("head"); }
    });
  }

  function init() {
    // 事件绑定独立兜底：即便舞台/心跳初始化失败，输入与发送也必须可用
    try {
      bind(); console.log("[probe] bind ok");
    } catch (e) { console.error("[probe] BIND_FAIL", e.message); return; }

    // 舞台初始化独立兜底：PIXI/模型失败只降级隐藏舞台，不影响对话
    try {
      Stage.mount($("stage")); console.log("[probe] mount ok");
    } catch (e) { console.warn("[probe] STAGE_FAIL", e.message); }

    try { greet(); console.log("[probe] greet ok"); } catch (e) { console.warn("[probe] GREET_FAIL", e.message); }
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

window.addEventListener("DOMContentLoaded", App.init);
