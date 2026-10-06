/* ============================================================
 * heartbeat.js · 小满的心跳（主动问候系统）
 * ------------------------------------------------------------
 * 灵性守则（用户定调）：像真朋友，不过度打扰——
 *  1. 提醒永远优先（用户自己要求的，深夜也送）
 *  2. 主动闲聊问候多重闸门：
 *     静默时段(23-8点) / 距上次主动>2h / 每日≤4条 /
 *     用户正在聊天时不插嘴 / 25%概率抖动（不卡点，不机器）
 *  3. 有真API时用 HEARTBEAT_PROMPT+记忆生成；离线走模板池
 * ============================================================ */

const Heartbeat = (() => {
  const STATE_KEY = "xiaoman_heartbeat_state";
  const QUIET = [23, 8];        // 23:00-08:00 静默
  const GAP_MS = 2 * 3600e3;    // 两次主动间隔下限
  const DAILY_MAX = 4;          // 每日主动闲聊上限
  const CHANCE = 0.25;          // 每个合格 tick 的触发概率

  let timer = null;
  let pushFn = null, historyFn = null;

  function state() {
    try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch (e) { return {}; }
  }
  function setState(patch) {
    const s = { ...state(), ...patch };
    localStorage.setItem(STATE_KEY, JSON.stringify(s));
    return s;
  }

  function inQuietHours(d) {
    const h = d.getHours();
    return h >= QUIET[0] || h < QUIET[1];
  }

  /* ── 离线模板池：像随手发的微信 ── */
  const POOL = {
    morning: [
      "刚到公司，电梯里全是困脸||你起了没，早饭吃了没",
      "团子早上五点踩我脸叫饭……我起是起来了，魂没起||你今天几点起",
    ],
    noon: [
      "到饭点了||今天吃啥，别又是外卖凑合",
      "我刚干完饭，摸鱼中||你中午歇会儿，别一直怼电脑",
    ],
    evening: [
      "下班啦||你那边呢，今天累不累",
      "团子蹲在门口等我，跟个门神似的||你到家了说一声",
    ],
    late: [
      "还没睡？||少刷会儿手机，明天还要当牛马呢",
      "我刚撸完团子，它打呼噜了||你要也睡不着就早点躺，我陪你聊两句",
    ],
    rain: [
      "外面下雨了，哗哗的||你那边下没下，出门带伞没",
    ],
    weekend: [
      "周五了！||今晚打算干嘛，别又宅一晚上……算了宅着也挺好",
    ],
    generic: [
      "刚看到个特好笑的视频，想起你了||回头发你，先笑为敬",
      "团子今天把水碗打翻了，一脸无辜||你今天怎么样",
      "路过奶茶店，想起你说想喝||点了没，别光想着",
    ],
  };

  function pickOffline() {
    const h = new Date().getHours();
    const day = new Date().getDay();
    let pool = POOL.generic;
    if (h >= 6 && h < 11) pool = POOL.morning;
    else if (h >= 11 && h < 14) pool = POOL.noon;
    else if (h >= 17 && h < 23) pool = day === 5 ? POOL.weekend : POOL.evening;
    else if (h >= 23 || h < 6) pool = POOL.late;
    return pool[Math.floor(Math.random() * pool.length)];
  }

  /* ── 生成主动消息（真API用模型生成，否则离线模板池） ── */
  async function generate() {
    const cfg = API.loadCfg();
    // auto 模式（默认无 mode）只要配了 key 也走模型；只有显式 local 才跳过
    if (cfg.apiKey && cfg.mode !== "local") {
      const hist = (historyFn && historyFn(4)) || [];
      const mem = window.MemoryStore ? MemoryStore.renderContext("") : "";
      try {
        const r = await API.chat(
          [...hist, { role: "user", content: "（时间过去了一会儿，你随手给ta发条微信）" }],
          { heartbeat: true, memSection: mem, now: new Date() }
        );
        if (r && r.reply) return r;
      } catch (e) { console.warn("heartbeat api 降级:", e.message); }
    }
    // 离线模板池 + 记忆追访（有记忆时 40% 概率接上上次的话题）
    if (window.MemoryStore && Math.random() < 0.4) {
      const mems = MemoryStore.all().filter(m => m.kind !== "pin");
      if (mems.length) {
        const m = mems[Math.floor(Math.random() * mems.length)];
        const kw = m.text.replace(/^[^：]+：/, "");
        return { reply: `对了，${kw.slice(0, 14)}——后来怎么样了||突然想起来问问`, emotion: "gentle", motion: null, memory_updates: {}, offline: true };
      }
    }
    const reply = pickOffline();
    return { reply, emotion: "gentle", motion: null, memory_updates: {}, offline: true };
  }

  /* ── tick：每分钟检查 ── */
  let running = false;    // 防重入：上一轮未返回时不再并发（避免同 tick 双投/绕过闸门）
  async function tick() {
    if (!pushFn || running) return;
    running = true;
    try {
      // 1) 提醒（用户自己定的，静默时段也送）
      const fired = Reminders.due();
      for (const r of fired) {
        // 先尝试投递，失败则放回未完成队列（不标记 done）
        try {
          await pushFn({
            reply: `（提醒时间到）${r.text}||我说到做到的，别赖`,
            emotion: "gentle", motion: null, kind: "reminder"
          });
          // 投递成功，标记 done
          Reminders.markDone([r.id]);
        } catch (e) {
          // 投递失败：不标记 done，下一轮重试
          console.warn("提醒投递失败，下一轮重试:", e.message);
        }
      }
      if (fired.length) return;   // 送完提醒这个 tick 不再闲聊

      // 2) 朋友式主动闲聊（多重闸门）
      const cfg = API.loadCfg();
      if (cfg.heartbeatOn === false) return;
      if (inQuietHours(new Date())) return;

      const s = state();
      const today = new Date().toDateString();
      if (s.dateKey !== today) Object.assign(s, { dateKey: today, count: 0 });
      if (Date.now() - (s.lastAt || 0) < GAP_MS) return;
      if ((s.count || 0) >= DAILY_MAX) return;
      const recent = historyFn ? historyFn(1) : [];
      if (recent.length) {
        const last = recent[0];
        if (last && Date.now() - (last.at || 0) < 10 * 60e3) return;  // 正在聊天，别插嘴
      }
      if (Math.random() > CHANCE) return;

      const msg = await generate();
      try {
        await pushFn({ ...msg, kind: "ping" });
      } catch (e) { console.warn("心跳投递失败:", e.message); return; }
      setState({ lastAt: Date.now(), dateKey: today, count: (s.count || 0) + 1 });
    } finally {
      running = false;
    }
  }

  /** 启动：pushFn(结构化消息) historyFn(n)取最近n条聊天 */
  function start(push, history) {
    pushFn = push; historyFn = history;
    if (timer) clearInterval(timer);
    timer = setInterval(tick, 60e3);
    return () => clearInterval(timer);
  }

  /** 仅供测试：无视闸门立即生成一条 */
  function debugPing() {
    return generate();
  }

  return { start, tick, debugPing, inQuietHours };
})();
window.Heartbeat = Heartbeat;   // const 不挂 window，跨模块守卫需显式挂载
