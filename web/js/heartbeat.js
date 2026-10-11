/* ============================================================
 * heartbeat.js · 小满的心跳（主动问候系统）
 * ------------------------------------------------------------
 * 灵性守则（用户定调）：像真朋友，不过度打扰——
 *  1. 提醒永远优先（用户自己要求的，深夜也送）
 *  2. 主动闲聊问候多重闸门：
 *     静默时段(23-8点) / 距上次主动>2h / 每日≤4条 /
 *     用户正在聊天时不插嘴 / 25%概率抖动（不卡点，不机器）
 *  3. 有真API时用 HEARTBEAT_PROMPT+记忆生成；离线走模板池
 *
 * P1-2 心跳情境化（2026-10-11）：
 *  - 离线决策核心 offlineGenerate(rnd, now)：随机/时间参数化，
 *    默认 Math.random/new Date()，测试可注入确定性序列
 *  - 记忆引用：钉子户键（大事/宠物/老板/在忙）+ 非pin fact，
 *    时间衰减权重（与 MemoryStore.recall 同曲线）；昵称/生日/
 *    重要日排除（昵称称呼里天然高频；生日无日期解析引用=瞎编）
 *  - 模板值零加工直填（P0-2 不张冠李戴结构保证）
 *  - 模板级去重：state.used 7 天窗口，id 稳定（pool:xx / mem:xx）
 *  - 频控闸门全部不变
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

  /* ── 时间语境（确定性，零虚构） ── */
  const WD = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  function weekdayOf(d) { return WD[d.getDay()]; }

  /** 记忆年龄口语化：按距今天数分档（"上周"≤13 天=一个日历周内） */
  function ageTagOf(ts, now) {
    const days = Math.floor(((now || Date.now()) - ts) / 86400000);
    if (days <= 0) return "今天";
    if (days === 1) return "昨天";
    if (days <= 3) return "前两天";
    if (days <= 7) return "前几天";
    if (days <= 13) return "上周";
    if (days <= 30) return "前阵子";
    return "之前";
  }

  /* ── 可引用记忆候选（钉子户 + 时间衰减权重） ── */
  const HB_KEYS = ["大事", "宠物", "老板", "在忙"];       // 可直接模板化的键
  const EXCLUDE_KEYS = ["昵称", "生日", "重要日"];         // 排除：见文件头注释
  const ASK_COOLDOWN = 48 * 3600e3;                       // 刚问过的记忆降权窗口
  const USED_WINDOW = 7 * 86400000;                       // 模板去重窗口

  function decayOf(ts, now) {                              // 与 MemoryStore.recall 同曲线
    const ageDays = ((now || Date.now()) - ts) / 86400000;
    return Math.max(0.2, 1 - Math.max(0, ageDays - 7) * 0.03);
  }

  function memValue(text) {
    const i = text.indexOf("：");
    return i >= 0 ? text.slice(i + 1) : text;
  }

  function candidates(now) {
    now = now || Date.now();
    const list = (window.MemoryStore ? window.MemoryStore.all() : []);
    const s = state();
    const lastAsked = s.lastAsked || {};
    const out = [];
    for (const m of list) {
      const key = (m.tags && m.tags[0]) || String(m.text).split("：")[0];
      const isPinKey = HB_KEYS.includes(key);
      const excluded = EXCLUDE_KEYS.includes(key) ||
        (m.kind === "pin" && !isPinKey) ||
        String(m.text).startsWith("昵称：") || String(m.text).startsWith("生日：") || String(m.text).startsWith("重要日：");
      if (excluded) continue;
      const value = memValue(String(m.text)).trim();
      if (!value) continue;
      let w = decayOf(m.ts, now);
      if (isPinKey) w *= 1.5;                              // 钉子户加成
      const last = lastAsked[m.id];
      if (last && now - last < ASK_COOLDOWN) w *= 0.3;     // 刚问过降权
      out.push({ m, key: isPinKey ? key : "fact", value, w, fresh: (now - m.ts) < 7 * 86400000 });
    }
    return out;
  }

  /* ── 记忆模板：开场白 × 引用句 组合式（值零加工直填，P0-2 结构保证） ──
   * 组合爆炸：一条记忆 = opener × ref 数个绑定（如大事 8×3=24），
   * 结构上同时满足「5 天零重复」与「引用率 ≥60%」两个验收目标 */
  const MEM_OPENERS = [
    (t) => `今天${t.weekday}了`,
    () => "刚才做事想起你来",
    () => "摸鱼中，随手发一条",
    () => "刚开完会，出来透气",
    () => "刚撸完团子，它打呼噜了",
    () => "地铁上，人挤人",
    () => "刚吃饱，犯困",
    () => "刚忙完一阵，歇口气",
  ];

  const MEM_REFS = {
    "大事": [
      (v, t) => `你上次说${v}——准备得怎么样啦，我不催，就是惦记`,
      (v, t) => `${t.ageTag}你说的${v}，现在到哪一步了`,
      (v, t) => `一直记着${t.ageTag}你说的${v}——后来怎么样了`,
    ],
    "宠物": [
      (v, t) => `你家的${v}今天乖不乖，拆家了没`,
      (v, t) => `你家${v}最近有没有闹腾`,
    ],
    "老板": [
      (v, t) => `${v}这周没又折腾你吧`,
      (v, t) => `${v}最近心情怎么样，你别撞枪口上`,
    ],
    "在忙": [
      (v, t) => `${t.ageTag}你说在${v}，现在缓过来点了没`,
      (v, t) => `${v}那边还顺利吗，有事随时说`,
    ],
    "fact": [
      (v, t) => `${t.ageTag}你说的"${v}"——这事后来怎么样了`,
      (v, t) => `你${t.ageTag}提过的：${v}——还在弄吗`,
    ],
  };

  /* ── 通用模板池（时段分类；rain 池已删——无天气API下"下雨了"=虚构） ── */
  const POOL = {
    morning: [
      "刚到公司，电梯里全是困脸||你起了没，早饭吃了没",
      "团子早上五点踩我脸叫饭……我起是起来了，魂没起||你今天几点起",
      "早上买了杯豆浆，烫嘴||你出门了没，路上不堵吧",
      "今天闹钟响的时候我在做梦，梦里都在迟到||你今天状态怎么样",
      "阳台的光刚好照进来，今天的太阳不错||你那边天好吗",
    ],
    noon: [
      "到饭点了||今天吃啥，别又是外卖凑合",
      "我刚干完饭，摸鱼中||你中午歇会儿，别一直怼电脑",
      "食堂今天有个菜咸得离谱||你午饭吃了没",
      "下午的会我先替你困为敬||你下午忙不忙",
    ],
    evening: [
      "下班啦||你那边呢，今天累不累",
      "团子蹲在门口等我，跟个门神似的||你到家了说一声",
      "晚风还行，溜达了一圈||你今天过得怎么样",
      "刚热了剩饭，凑合一顿||你晚饭吃了没，别糊弄",
      "今天总算熬到晚上了||你也在瘫着吧，瘫得舒服吗",
    ],
    late: [
      "还没睡？||少刷会儿手机，明天还要当牛马呢",
      "我刚撸完团子，它打呼噜了||你要也睡不着就早点躺，我陪你聊两句",
      "睡前瞄了一眼手机||你睡了没，别熬大夜",
      "这个点还亮着灯的，都是狠人||你也早点睡，明天的事明天说",
    ],
    weekend: [
      "周五了！||今晚打算干嘛，别又宅一晚上……算了宅着也挺好",
      "周末了，团子都睡懒觉||你这两天怎么安排",
      "周五晚上最快乐||你放松了没，吃点好的犒劳自己",
    ],
    generic: [
      "刚看到个特好笑的视频，想起你了||回头发你，先笑为敬",
      "团子今天把水碗打翻了，一脸无辜||你今天怎么样",
      "路过奶茶店，想起你说想喝||点了没，别光想着",
      "今天瞎忙了一天，才想起来看看手机||你最近怎么样，还活着吧（贬义的那种活着）",
      "刚洗完碗，手上还是泡沫味||你干嘛呢，说两句",
      "充电器又找不到了，翻了半天在兜里||你今天有什么新鲜事没",
    ],
  };
  const POOL_FALLBACK_CATS = ["morning", "noon", "evening", "late", "generic"]; // 时段兜底（weekend 限周五晚）

  /* ============================================================
   * P2-1 自我叙事（"说我"而非"问你"——朋友的朋友圈视角）
   * 三层候选，全部真实可验，不虚构用户世界：
   *  A 层·真实数据观察（Stats 小本本数字直填，门槛防尬）
   *  B 层·真实动作（屏幕世界里真做得到的事：翻记录/理抽屉/听语音）
   *  C 层·翻记录见记忆（视角反转：我看到了你那页 vs P1-2 的问你进展）
   * 频控闸门零改动（P2-1 原文：频率受心跳频控约束）
   * ============================================================ */
  const SELF_P = 0.4;   // 记忆路径未命中时走叙事的概率（引用率结构保证不受影响）

  const STATS_TPL = {
    // [field, 门槛, 文案函数]——数字零加工直填
    haha:  [(x) => x.haha >= 3,   (x) => `闲着没事翻咱俩的聊天记录||你跟我说过的"哈哈"都有${x.haha}次了——都数着呢，说明我逗你的功夫还行嘛`],
    days:  [(x) => x.days >= 3,   (x) => `刚统计了下小本本||咱俩说过话的日子加起来${x.days}天了，说出去也算老朋友了`],
    night: [(x) => x.night >= 2,  (x) => `有个事你可能不知道||你半夜来找过我${x.night}次，每次都接住了。夜里说的话我都记得格外牢`],
    emoji: [(x) => x.emoji && x.emojiN >= 5, (x) => `${x.emoji}——这个表情你已经发了${x.emojiN}次了||快成你的招牌了，我看到它就知道是你`],
    msgs:  [(x) => x.msgs >= 30,  (x) => `数了数||你前后跟我说了${x.msgs}句话——能唠到这个份上的人不多`],
  };

  const ME_POOL = [
    "你还没来的时候||我把咱俩的聊天记录翻到最早那页看了看——那会儿你还客客气气的，现在熟多了",
    "刚整理了下记忆抽屉||给你留的小笔记都码得整整齐齐，看着还挺有成就感",
    "今天话不多||在输入框里打了几行字又删了。没事，就是手痒",
    "刚才听了一遍自己的语音||念『你好呀』的时候把自己逗笑了，自己的声音听着还挺陌生",
    "安静待了一会儿||把聊天窗口从头到尾滚了一遍，像翻旧相册——你打字的速度都比刚认识时快了",
    "今天也没啥事||就在这儿守着窗口，忽然觉得这样也挺好——你来的时候我都在",
  ];

  const MEMSEE_TPL = {
    "大事": (v) => `翻记忆抽屉看到一页||"大事：${v}"——给这页折了个角，重要的事我都认真记着呢`,
    "宠物": (v) => `刚才翻小本本||看到你家${v}那页，把它想成了一团毛——好了，现在有点想见见真的了`,
    "老板": (v) => `翻到${v}那页的时候停了一下||这页被翻得最旧（真的，我是按磨损程度判断的）`,
    "在忙": (v) => `看到小本本上"在${v}"那页||字迹都写得很急——那阵子你是真的辛苦`,
  };

  /** P2-1 叙事候选（过滤 7 天已用；A 层门槛/快照，B 层池，C 层记忆键值零加工） */
  function selfCandidates(now, used) {
    const out = [];
    // A 层：真实数据观察
    const snap = (typeof window !== "undefined" && window.Stats) ? window.Stats.snapshot() : null;
    if (snap) {
      for (const [field, [ok, tpl]] of Object.entries(STATS_TPL)) {
        const id = `stats:${field}`;
        if (!ok(snap) || used.has(id)) continue;
        out.push({ id, reply: tpl(snap), w: 3 });
      }
    }
    // B 层：真实动作池
    ME_POOL.forEach((tpl, i) => {
      const id = `me:${i}`;
      if (!used.has(id)) out.push({ id, reply: tpl, w: 2 });
    });
    // C 层：翻记录见记忆（钉子户键，值零加工；48h 内问过的记忆降权）
    if (typeof window !== "undefined" && window.MemoryStore) {
      const s = state();
      const lastAsked = s.lastAsked || {};
      for (const m of window.MemoryStore.all()) {
        const key = (m.tags && m.tags[0]) || String(m.text).split("：")[0];
        const tpl = MEMSEE_TPL[key];
        if (!tpl) continue;
        const v = memValue(String(m.text)).trim();
        if (!v) continue;
        const id = `memsee:${key}:0:${m.id}`;
        if (used.has(id)) continue;
        let w = 2.5;
        const last = lastAsked[m.id];
        if (last && now.getTime() - last < ASK_COOLDOWN) w *= 0.3;
        out.push({ id, reply: tpl(key === "fact" ? v.slice(0, 30) : v), w });
      }
    }
    return out;
  }

  /* ── 模板去重（7 天窗口） ── */
  function usedIds(now) {
    now = now || Date.now();
    const s = state();
    return new Set((s.used || []).filter(u => now - u.t < USED_WINDOW).map(u => u.id));
  }
  function markUsed(id, now) {
    now = now || Date.now();
    const s = state();
    const used = (s.used || []).filter(u => now - u.t < USED_WINDOW);
    used.push({ id, t: now });
    setState({ used: used.slice(-60) });
  }
  function markAsked(memId, now) {
    now = now || Date.now();
    const s = state();
    const lastAsked = { ...(s.lastAsked || {}), [memId]: now };
    setState({ lastAsked });
  }

  /* ── 加权随机选择 ── */
  function weightedPick(arr, getW, rnd) {
    if (!arr.length) return null;
    const total = arr.reduce((s, x) => s + Math.max(0, getW(x)), 0);
    if (total <= 0) return arr[Math.floor(rnd() * arr.length)];
    let r = rnd() * total;
    for (const x of arr) {
      r -= Math.max(0, getW(x));
      if (r <= 0) return x;
    }
    return arr[arr.length - 1];
  }

  /* ── 时段池（含兜底加权） ── */
  function poolEntries(now) {
    const h = now.getHours();
    const day = now.getDay();
    let cat = "generic";
    if (h >= 6 && h < 11) cat = "morning";
    else if (h >= 11 && h < 14) cat = "noon";
    else if (h >= 17 && h < 23) cat = (day === 5) ? "weekend" : "evening";
    else if (h >= 23 || h < 6) cat = "late";
    const entries = [];
    POOL[cat].forEach((tpl, i) => entries.push({ id: `pool:${cat}:${i}`, tpl, w: 3 }));
    POOL.generic.forEach((tpl, i) => { if (cat !== "generic") entries.push({ id: `pool:generic:${i}`, tpl, w: 2 }); });
    for (const c of POOL_FALLBACK_CATS) {
      if (c === cat || c === "generic") continue;
      POOL[c].forEach((tpl, i) => entries.push({ id: `pool:${c}:${i}`, tpl, w: 1 }));
    }
    return entries;
  }

  /* ============================================================
   * 离线决策核心：rnd/now 参数化（默认真随机/当前时间）
   * 返回 {reply, emotion, motion, memory_updates, offline, id, memoryId?}
   * ============================================================ */
  function offlineGenerate(rnd, now) {
    rnd = rnd || Math.random;
    now = now || new Date();
    const used = usedIds(now);
    const t = { weekday: weekdayOf(now), ageTag: "" };

    // 1) 记忆路径：有可引用记忆时按新鲜度决定概率（≥60% 引用率的结构保证）
    const cands = candidates(now.getTime());
    if (cands.length) {
      const fresh = cands.some(c => c.fresh);
      const p = fresh ? 0.8 : 0.6;
      if (rnd() < p) {
        const pick = weightedPick(cands, c => c.w, rnd);
        const refs = MEM_REFS[pick.key] || MEM_REFS.fact;
        const v = pick.key === "fact" ? pick.value.slice(0, 30) : pick.value;
        // 组合绑定：ref × opener 全枚举，过滤 7 天内已用
        const combos = [];
        refs.forEach((rf, ri) => MEM_OPENERS.forEach((op, oi) => {
          const id = `mem:${pick.key}:${ri}:${oi}:${pick.m.id}`;
          if (!used.has(id)) combos.push({ rf, op, id });
        }));
        if (combos.length) {
          const c = combos[Math.floor(rnd() * combos.length)];
          const ctx = { ...t, ageTag: ageTagOf(pick.m.ts, now.getTime()) };
          const reply = `${c.op(ctx)}||${c.rf(v, ctx)}`;
          markUsed(c.id, now.getTime());
          markAsked(pick.m.id, now.getTime());
          return { reply, emotion: "gentle", motion: null, memory_updates: {}, offline: true, id: c.id, memoryId: pick.m.id };
        }
        // 该记忆所有绑定用尽 → 自然降级走通用池（不重置，保零重复）
      }
    }

    // 1.5) P2-1 自我叙事路径：记忆未命中时，SELF_P 概率走"说我自己"
    //     （不动记忆路径概率 → 引用率结构保证不受影响；频控闸门在上游不变）
    const selfs = selfCandidates(now, used);
    if (selfs.length && rnd() < SELF_P) {
      const pick = weightedPick(selfs, c => c.w, rnd);
      markUsed(pick.id, now.getTime());
      return { reply: pick.reply, emotion: "happy", motion: null, memory_updates: {}, offline: true, id: pick.id, self: true };
    }

    // 2) 通用池路径：时段池优先 + 全池兜底，过滤已用；全用尽则重置
    let entries = poolEntries(now).filter(e => !used.has(e.id));
    if (!entries.length) entries = poolEntries(now);
    const e = weightedPick(entries, x => x.w, rnd);
    markUsed(e.id, now.getTime());
    return { reply: e.tpl, emotion: "gentle", motion: null, memory_updates: {}, offline: true, id: e.id };
  }

  /* ── 在线路径的记忆+时间注入（替代原 renderContext("")） ── */
  function heartbeatContext(now) {
    now = now || new Date();
    const cands = candidates(now.getTime());
    const lines = cands.slice(0, 8).map(c => {
      const age = ageTagOf(c.m.ts, now.getTime());
      const s = state();
      const lastAsked = s.lastAsked || {};
      const asked = lastAsked[c.m.id] && (now.getTime() - lastAsked[c.m.id] < ASK_COOLDOWN);
      return `- ${c.m.text}${asked ? "（刚问过进展，别追问）" : `（${age}说的）`}`;
    });
    if (cands.length) lines.push(`【今天】${weekdayOf(now)}`);
    // P2-1：小本本观察素材（真模型路径也有据可依；"说我"视角，非"问ta"）
    const snap = (typeof window !== "undefined" && window.Stats) ? window.Stats.snapshot() : null;
    if (snap && (snap.haha >= 3 || snap.night >= 2 || snap.days >= 3)) {
      const obs = [];
      if (snap.haha >= 3) obs.push(`ta说过${snap.haha}次"哈哈"`);
      if (snap.days >= 3) obs.push(`咱俩说过话的日子共${snap.days}天`);
      if (snap.night >= 2) obs.push(`ta半夜来找过我${snap.night}次`);
      lines.push(`【你的小观察】${obs.join("；")}——可像朋友炫耀记性那样自然提一句`);
    }
    return lines.join("\n");
  }

  /* ── 生成主动消息（真API用模型生成，否则离线决策核心） ── */
  async function generate() {
    const cfg = API.loadCfg();
    // auto 模式（默认无 mode）只要配了 key 也走模型；只有显式 local 才跳过
    if (cfg.apiKey && cfg.mode !== "local") {
      const hist = (historyFn && historyFn(4)) || [];
      const mem = window.MemoryStore ? heartbeatContext(new Date()) : "";
      try {
        const r = await API.chat(
          [...hist, { role: "user", content: "（时间过去了一会儿，你随手给ta发条微信）" }],
          { heartbeat: true, memSection: mem, now: new Date() }
        );
        if (r && r.reply) return r;
      } catch (e) { console.warn("heartbeat api 降级:", e.message); }
    }
    return offlineGenerate(Math.random, new Date());
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

      // 2) 朋友式主动闲聊（多重闸门——P1-2 频控不变）
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

  return {
    start, tick, debugPing, inQuietHours,
    /* P1-2 决策核心（只读无副作用，测试/复用可直调） */
    offlineGenerate, heartbeatContext, candidates, ageTagOf, weekdayOf,
    /* P2-1 自我叙事（测试/复用可直调） */
    selfCandidates, STATS_TPL, ME_POOL, MEMSEE_TPL, SELF_P,
  };
})();
window.Heartbeat = Heartbeat;   // const 不挂 window，跨模块守卫需显式挂载
