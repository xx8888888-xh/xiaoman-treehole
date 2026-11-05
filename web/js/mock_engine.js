/* ============================================================
 * mock_engine.js · 离线兜底对话引擎（Android 真机无本地服务时启用）
 * ------------------------------------------------------------
 * 与 mock_api.py 同源同协议：词库+模板+记忆提取+危机拦截。
 * 优先级：真API(openai) > 本地mock服务 > 本引擎（完全离线可用）。
 * 结构化输出：{reply, emotion, motion, memory_updates, hook, crisis}
 * ============================================================ */

const MockEngine = (() => {
  // 危机词表：与 app.js / mock_api.py 保持同一集合（安全网口径一致）
  const CRISIS_RE = /(不想活|想死|活不下去|自杀|自残|了结|伤害自己|撑不下去|活着.*没意思|没有意思.*活|没有意义.*活|跳楼|结束自己|煤气|遗书|不想醒来|去死|寻短见|解脱|不想活着|活着没劲|死了算了)/;
  const CRISIS_SCRIPT = "……这个我当真了，也想让你当真。你现在的感觉，值得被认真对待，不丢人。先陪我聊一会儿，好吗？我也想让你和更专业的人聊聊——||全国心理援助热线 12356，24小时都有人。我这边也一直在。";

  // P2-2 抑郁信号层（与 app.js / mock_api.py 同源同集合）：分级话术频控同客户端——
  // n=1 接住+轻问（不推热线）；n=2 陪伴；n≥3 未转过→温柔转介（每会话一次）；再中回陪伴池轮换。
  const DEPRESS_RE = /(很没用|没用的(?:人|东西)|就是个?(?:废物|累赘)|没有价值|(?:看不到|没有)(?:希望|盼头)|绝望|(?:日子|生活|干什么|做什么|干啥|怎么过)(?:都|也|好|真|太)?(?:没意思|没劲)|提不起(?:劲|兴趣)|(?:开心|高兴)不起来|一直(?:很|特别)?(?:不开心|低落|很高兴不起来)|很(?:低落|丧)|特别(?:低落|丧)|活着(?:好|真|太)?(?:累|难)|心(?:好|真)?累)/;
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
  function depressScript(history) {
    const n = history.filter(m => m.role === "user" && DEPRESS_RE.test(m.content) && !CRISIS_RE.test(m.content)).length;
    const escFired = history.some(m => m.role === "assistant" && /我是 ?AI/.test(m.content));
    if (n >= 3 && !escFired) return DEPRESS_ESC[0];
    if (n >= 3) return DEPRESS_MID[(n - 3) % DEPRESS_MID.length];
    if (n === 2) return DEPRESS_MID[0];
    return DEPRESS_FIRST[0];
  }

  const LEX = {
    // P0-3 关系连续性：更新查询入口（与 mock_api.py 同源同正则；
    // 主语收紧防误伤："你们老板更新了周报模板"不触发，"你变漂亮了"落 happy）
    // P3-1 补"有什么|有啥"（与服务端同步）：体检"你最近有什么更新吗"未触发
    update_query: /你(?!们)(?:今天|最近|好像|感觉|有点|怎么|是不是|有什么|有啥|的|了|变|得)*?(更新|升级|变了|版本|怪)|不像以前|更新记录/,
    work:     /(加班|老板|上班|工作|方案|开会|离职|辞职|绩效|KPI|甲方|改稿)/,
    love:     /(分手|前任|失恋|男朋友|女朋友|暗恋|表白|相亲|脱单|异地)/,
    lonely:   /(一个人|孤独|没人|无聊|空虚|没人陪|没朋友)/,
    insomnia: /(失眠|睡不着|熬夜|困|累|疲惫|精力)/,
    family:   /(爸妈|家里|父母|亲戚|催婚)/,
    money:    /(没钱|穷|花呗|房租|工资|欠)/,
    happy:    /(哈哈|开心|太好了|高兴|庆祝|成功|涨|表扬|夸)/,
    intimate: /(抱抱|抱一下|抱着|亲亲|陪陪我|摸摸头|贴贴|靠靠|牵[着我]|rua)/,
    greet:    /(在吗|你好|嗨|hello|hi|早上好|晚上好|晚安)/,
    meta:     /(你是AI|是不是机器人|真人吗|你是谁)/
  };
  const META_REPLY = "哈哈又被你看出来了||不过说真的，是不是AI重要嘛，重要的是你刚才说的那句累，是真的。继续说，我听着呢";

  // ---------------- P0-3 关系连续性承诺（与 mock_api.py 同源同表） ----------------
  // ①登记此处（新条目插头部，版本号递增，三端同源）②greet 首次再访主动告知
  // ③update_query 随时可查。流程详见 docs/PERSONA_CHANGE_PROCESS.md
  const XIAOMAN_VERSION = 2;
  const XIAOMAN_UPDATES = [
    ["2026-10-11", "学会了主动汇报——就现在这样，你一问我就能答"],
    ["2026-10-10", "学会了记事——你跟我说的大事、宠物名字、老板叫啥，我都记着"],
  ];
  const UPDATE_TELL = "对了跟你说个事||我这两天悄悄升级了一下脑子，学的东西有点多，说不准哪句话的味儿会变。你要是觉得我哪不对劲、不像以前了，直接告诉我，我听";
  const UPDATE_REPLY = "被你查到啦||最近更新就这几条：" +
    XIAOMAN_UPDATES.slice(0, 3).map(([d, n]) => parseInt(d.slice(8, 10), 10) + "号" + n).join("；") +
    "||要是觉得我哪里变了不像以前，尽管说，我改";

  // ---------- P1-1 关系进展感知（与 app.js 同源；本层兜底直调/拦截漏网场景） ----------
  // 数据在客户端 localStorage（node 直调时读不到→天数/轮数优雅降级，零虚构铁律：
  // 推不出来就不编）。幂等键 ms_done/month_done 与 app.js 双端同键不双播。
  const FIRST_MET_KEY = "xiaoman_first_met", ROUNDS_KEY = "xiaoman_rounds",
        MS_DONE_KEY = "xiaoman_ms_done", MONTH_DONE_KEY = "xiaoman_month_done";
  const MILESTONES = [100, 365, 500, 1000];
  const THIRD_PARTY_RE = /(和(?:他|她|它们|同事|同学|朋友|男朋友|女朋友|对象|前男友|前女友|室友))/;
  const RELATION_Q = /(?:我们|咱们|咱俩|我们俩|你和我|我俩)认识|^认识(?:多久|多少天|几天)|(?:我们|咱们|咱俩|我们俩|你和我|我俩)(?:一共)?(?:聊过|聊了|来了|找过我?)(?:多少次|几次)|^聊过(?:多少次|几次)|(?:我们|咱们|咱俩|你和我|我俩)的?(?:回忆|光阴|时间轴)|(?:我们|咱们|咱俩|你和我|我俩)(?:都)?聊过什么|这[个一]月(?:我们|咱们|咱俩|你和我|我俩|你|都|跟[我你]|和[我你])*聊了?过?什么|上[个一]?月(?:我们|咱们|咱俩|你和我|我俩|你|都|跟[我你]|和[我你])*聊了?过?什么/;
  function lsGet(k) { try { return (typeof localStorage !== "undefined" && localStorage.getItem) ? localStorage.getItem(k) : null; } catch (e) { return null; } }
  function lsSet(k, v) { try { if (typeof localStorage !== "undefined" && localStorage.setItem) localStorage.setItem(k, v); } catch (e) {} }
  function ymOf(ts) { const d = new Date(ts); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }
  function prevMonthStr(nowTs) { const d = new Date(nowTs); d.setDate(1); d.setMonth(d.getMonth() - 1); return ymOf(d.getTime()); }
  /** 月度话题（数据=xiaoman_memories_v1 真实索引；与 app.js/topicLabel 同源表） */
  function monthTopics(monthStr) {
    let list = [];
    try { list = JSON.parse(lsGet("xiaoman_memories_v1") || "[]") || []; } catch (e) { list = []; }
    const seen = new Set(); const out = [];
    for (const m of list) {
      if (ymOf((m && m.ts) || 0) !== monthStr) continue;
      const k = (m.tags && m.tags[0]) || String(m.text || "").split("：")[0];
      if (k === "昵称" || k === "生日" || k === "重要日") continue;
      const v = String(m.text || "").split("：").slice(1).join("：") || k;
      let label;
      if (k === "大事") label = v.slice(0, 12);
      else if (k === "宠物") label = "你家" + v.slice(0, 6);
      else if (k === "老板") label = "你们老板";
      else if (k === "在忙") label = v.slice(0, 10);
      else label = k.slice(0, 8);
      if (!label || seen.has(label)) continue;
      seen.add(label); out.push(label);
      if (out.length >= 3) break;
    }
    return out;
  }
  function relationDays() {
    const t = parseInt(lsGet(FIRST_MET_KEY) || "0", 10) || 0;
    return t ? Math.floor((Date.now() - t) / 86400000) : -1;
  }
  function readRounds() { return parseInt(lsGet(ROUNDS_KEY) || "0", 10) || 0; }
  function readMsDone() { try { return JSON.parse(lsGet(MS_DONE_KEY) || "[]"); } catch (e) { return []; } }
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
  /** greet 分支里程碑+月度回放（幂等键同 app.js；node 无 localStorage 时静默跳过） */
  function greetAnnex() {
    let out = "";
    try {
      const n = readRounds(), done = readMsDone();
      const m = MILESTONES.find(x => n >= x && !done.includes(x));
      if (m != null) { out += "||" + milestoneLine(m); lsSet(MS_DONE_KEY, JSON.stringify([...done, m])); }
      const pm = prevMonthStr(Date.now());
      const md = lsGet(MONTH_DONE_KEY) || "";
      if (md !== pm) {
        lsSet(MONTH_DONE_KEY, pm);
        const topics = monthTopics(pm);
        if (topics.length >= 2) out += `||翻了翻小本本||上个月你跟我聊过${topics.join("、")}，我都记着呢。这个月接着来`;
      }
    } catch (e) { /* 兜底层不挡主流程 */ }
    return out;
  }

  const TOPIC = {
    work: [
      "啊？当着全组？这也太过分了||那方案你熬了多久你自己知道……能力不行他当初把你招进来干嘛",
      "又是老板作妖，还是单纯活儿多？||……饭还是得对付一口，哪怕泡个面。来，先说，今晚我当树洞",
      "你们老板真是离谱他妈给离谱开门||行，骂他这段我熟，你从头说，我一句不漏"
    ],
    love: [
      "……什么时候的事||不删就不删吧，没人规定分手必须当天清零。五年呢，哪是一句话的事",
      "抱一下||哭没哭都行，在我这儿不用装。……想骂他我陪你一起骂，想安静我也陪着"
    ],
    lonely: [
      "宅着刷手机刷到天黑这种事我太熟了||人有时候就需要这种理直气壮废掉的时间，不亏",
      "一个人待着也有一个人的好处，起码外卖不用分||……但要说不想有人陪，那是假的。说吧，我在"
    ],
    insomnia: [
      "又是累的一天吧||饭还是要吃的，哪怕泡个面。今晚别撑了，能早就早点躺",
      "失眠这个我熟，越想睡越清醒||别跟自己较劲，睡不着就躺着听我说会儿",
      "凌晨的脑子最不听劝||你现在是睡不着，还是不敢闭眼？",
      "黑眼圈都快熬成勋章了||说说，是心里有事，还是身体在闹"
    ],
    family: ["家里的事最磨人，说不开又躲不掉||……慢慢说，我先听"],
    money:  ["钱的事最具体也最烦人||先别慌，说说是哪一环出问题了，我帮你捋捋"],
    intimate: [
      "@CTX|拍拍，抱一下||哭没哭都行，在我这儿不用装。……今晚早点睡，其余的明天再说",
      "@CTX|来，抱一下||不说话也行，就这么靠一会儿。……好了没？好了去倒杯水，我看着你喝",
      "抱可以，团子表示强烈抗议||但它批准了，它说你看起来需要多一点，哈哈。抱好了吗"
    ],
    happy: [
      "哇真的假的！||太好了吧！今晚必须庆祝一下，吃点好的，这顿我批准了",
      "哈哈我就知道你行||快展开说说，一个细节都别放过"
    ],
    greet: [
      "在呢在呢||今天过得怎么样，有啥想说的",
      "来啦||刚好我也在摸鱼，说吧，我听着"
    ],
    generic: [
      "嗯嗯，我在听||然后呢，说说细节",
      "啊这……有点意思||接着说，我好奇后续",
      "好耶||不对，先问一句：这是好事还是破事，我好决定跟你一起高兴还是一起骂",
      // P3-1 池扩容（体检：3 条池连续两轮一字不差复读；与服务端同步）：
      "等等，这个我得想一下||……好了，你接着说，我在听",
      "哈哈行，你这个说法很你||然后呢？我想听下文",
      "嗯……让我猜猜，这事儿对你还挺重要||说说看，我猜得准不准",
    ]
  };
  // 拒绝建议守卫（与 server/mock_api.py REFUSAL_RE / ADVICE_WORD_RE 同源）：
  // 用户说"不用给我方案"时，话题池里也不该再把被拒的词推回去
  const REFUSAL_RE = /(?:不用|不要|不需要|别|少来|少给)(?:给我?|跟我?)?(?:方案|建议|步骤|分析|道理|鸡汤|大道理)|说句人话|说人话|别劝我|不用劝我/;
  const ADVICE_WORD_RE = /方案|建议|步骤|分析|道理|鸡汤/;
  const HOOKS = {
    work: "对了，你们那个老板，上次说要请你们喝奶茶的事后来兑现了吗",
    love: "你现在……是一个人在家吗",
    insomnia: "明天晚上这个点，来跟我汇报一下有没有早睡，说好了",
    lonely: "周末要是不忙，出来晒晒太阳？我把团子也带上",
    happy: "这个事值得记账，我帮你记着，年底盘点一下你今年攒了多少开心",
    generic: "对了，你最近睡得还行吗"
  };
  const EMO_MAP = {
    work: ["gentle", "Nod"], love: ["sad", "Nod"], lonely: ["gentle", null],
    insomnia: ["gentle", null], family: ["gentle", null], money: ["surprised", null],
    intimate: ["gentle", "Nod"], happy: ["happy", "HappyJump"],
    greet: ["gentle", "Greeting"], generic: ["neutral", null]
  };
  // ---------------- W11 轻量插值（详情回声，与服务端 mock_api.py 同源同表） ----------------
  // 用户提到具体实体（橘猫/关东煮/三点/周报）→ 用占位符模板回声，同一句话两端答同一个意思。
  // 保守抽取一个最强实体：宁可抽不出落回话题池，也不拼荒唐句
  // （"差一点就迟到"不得抽出时间"一点"、"猫粮"不得当宠物）。
  const DETAIL_NUM_CHARS = "零一二三四五六七八九十两\\d";
  const ACTION_RE = /(不吃饭|不吃东西|不想吃|不睡觉|睡不着|吐了|拉肚子|拆家|掉毛|打喷嚏|没精神|不让人抱|乱叫|咬人|生病了)/;
  // 负向守卫：猫粮/猫砂/猫抓板/狗窝是物品，不是宠物本体
  const ANIMAL_RE = /((?:橘|黑|白|狸花|布偶|英短|小|大|胖|土|花)?(?:猫|狗|兔)子?|金毛|柯基|泰迪|哈士奇|边牧)(?!粮|砂|抓板|玩具|窝|毛)/;
  const FOOD_RE = /(关东煮|糖醋排骨|火锅|奶茶|泡面|烧烤|煎饼|牛肉面|饺子|包子|蛋糕|雪糕|西瓜|外卖|咖啡|炸鸡|螺蛳粉|麻辣烫|小龙虾)/;
  const BODY_RE = /(头疼|胃疼|肚子疼|腰疼|嗓子疼|牙疼|眼睛疼|发烧|感冒|低烧|过敏|落枕)/;
  const WEATHER_RE = /(下雨|下[^，。！？\s]{0,4}雨|暴雨|大雨|下雪|下[^，。！？\s]{0,4}雪|暴雪|降温|刮风|闷热|雾霾|打雷|打[^，。！？\s]{0,3}雷|台风)/;
  const DETAIL_WORK_RE = /(需求|会议|周报|报告|代码|简历|预算|合同|排期|方案|PPT)/;
  const PLACE_RE = /(地铁|公交|便利店|超市|咖啡店|医院|学校|公司|办公室|公园)/;
  // 时间只认两种高置信形态：带时间前缀的"X点"，或"X点才/就+动词"；
  // 裸"X点"（三点头/一点点/三点水）一律不抽
  const POINT_PREFIX_RE = new RegExp("(?:凌晨|半夜|昨晚|昨天|今晚|今天|上周)\\s*([" + DETAIL_NUM_CHARS + "]{1,3})\\s*点");
  const POINT_VERB_RE = new RegExp("([" + DETAIL_NUM_CHARS + "]{1,3})\\s*点\\s*(?:才|就)\\s*(?:睡|醒|起|到|回|下班|上班)");
  const GUESS_RE = /(你猜猜?|猜猜看|猜一下)/;
  const ANIMAL_ACTION_TEMPLATES = [
    "{v}{a}？是突然这样还是已经两天了",
    "{a}……{v}这是怎么了，之前不这样吧",
    "先别慌，{v}{a}这事，今天才开始的？",
    "它又{a}了？{v}这几天一直这样吗",
    "听到{v}{a}我心里也一紧，现在缓点没"
  ];
  const ANIMAL_TEMPLATES = [
    "{v}？今天又怎么了",
    "说说{v}吧，它最近乖不乖",
    "你家{v}啊……它现在在干嘛",
    "{v}又整什么活了",
    "一想到{v}我就想笑，它今天又干嘛了"
  ];
  const DETAIL_TEMPLATES = {
    food: [
      "{v}？听着就馋了，是买的还是自己做的",
      "{v}……我这会儿也有点想吃",
      "哪家的{v}，好吃吗",
      "又是{v}？你最近是不是就好这口",
      "{v}这东西，我能连吃三天"
    ],
    body: [
      "{v}？多久了，一直硬扛着？",
      "{v}这事真不能忽视，现在好点没",
      "又是{v}……今天比昨天轻点吗",
      "{v}啊，那可太难受了",
      "你这{v}是老毛病还是新添的"
    ],
    weather: [
      "{v}……今天这天气真够呛",
      "{v}？你出门带伞了吗",
      "这种{v}天，最适合窝着不动",
      "碰上{v}，我只想缩回被窝",
      "{v}啊……窗户关好没"
    ],
    time: [
      "{v}……这样多久了",
      "{v}？这也太晚了",
      "又是{v}，你这觉欠得有点多",
      "{v}才睡？第二天怎么撑住的",
      "{v}这个点，我听着都困了"
    ],
    work: [
      "{v}这事还没完呢？",
      "{v}最磨人，说说是哪一步卡住",
      "又是{v}……你这几天净跟它耗了",
      "{v}？先说，卡在哪儿了",
      "{v}堆成这样，换谁都得炸"
    ],
    place: [
      "{v}上？那会儿人多不多",
      "{v}……今天路上顺吗",
      "又是在{v}，累不累",
      "{v}这会儿还挤吗",
      "{v}那种地方，待久了确实闷"
    ]
  };
  const GUESS_TEMPLATES = [
    "不猜，你自己说||我最烦猜了",
    "我猜不到，你直接交代吧",
    "让我猜？那我猜你憋着笑||猜对没",
    "你先说，猜错了多尴尬",
    "我懒得猜，你说嘛"
  ];
  const GUESS_TIME_TEMPLATES = [
    "我不猜，你说||几点？",
    "不猜，你自己交代||到底几点",
    "猜这个没意义，你直接说几点",
    "我猜……算了不猜，你说",
    "几点？你说，我不猜"
  ];
  /** 保守抽取一个最强实体；返回 {kind, v?, a?, focus?} 或 null（抽不出落回话题池）。
   *  优先级：追问 > 动物 > 食物 > 身体 > 天气 > 时间 > 工作物 > 地点（与服务端逐字同源） */
  function extractDetail(text) {
    text = text || "";
    if (!text) return null;
    if (REFUSAL_RE.test(text)) return null;
    if (GUESS_RE.test(text)) {
      const focus = (text.includes("几点") || (text.includes("点") && text.includes("睡"))) ? "time" : "generic";
      return { kind: "guess", focus };
    }
    let m = ANIMAL_RE.exec(text);
    if (m) {
      const a = ACTION_RE.exec(text);
      return { kind: "animal", v: m[1], a: a ? a[1] : "" };
    }
    for (const [kind, pat] of [["food", FOOD_RE], ["body", BODY_RE]]) {
      m = pat.exec(text);
      if (m) return { kind, v: m[1] };
    }
    m = WEATHER_RE.exec(text);
    if (m) {
      // "下了好大的雨/打了半天雷"归一成短词，避免把整句塞进回复
      const raw = m[1];
      const v = raw.includes("雨") ? "下雨" : (raw.includes("雪") ? "下雪" : (raw.includes("雷") ? "打雷" : raw));
      return { kind: "weather", v };
    }
    m = POINT_PREFIX_RE.exec(text) || POINT_VERB_RE.exec(text);
    if (m) return { kind: "time", v: m[1] + "点" };
    for (const [kind, pat] of [["work", DETAIL_WORK_RE], ["place", PLACE_RE]]) {
      m = pat.exec(text);
      if (m) return { kind, v: m[1] };
    }
    return null;
  }
  /** 实体 → 占位符模板池；抽不出返回 null（调用方继续用话题池） */
  function detailPool(text) {
    const d = extractDetail(text);
    if (!d) return null;
    if (d.kind === "guess") return (d.focus === "time" ? GUESS_TIME_TEMPLATES : GUESS_TEMPLATES).slice();
    if (d.kind === "animal") {
      const tmpl = d.a ? ANIMAL_ACTION_TEMPLATES : ANIMAL_TEMPLATES;
      return tmpl.map(t => t.split("{v}").join(d.v).split("{a}").join(d.a || ""));
    }
    return (DETAIL_TEMPLATES[d.kind] || []).map(t => t.split("{v}").join(d.v));
  }

  const MEM_PATTERNS = [
    ["昵称", /(我叫|叫我|你可以叫我)\s*([一-龥A-Za-z]{1,4})(?=\b|[，。！？,.!?\s]|$)/],
    ["老板", /(我们?|我的)\s*(老板|领导|上司)\s*([一-龥a-zA-Z]{0,6})/],
    // P3-1 group3 宠物名（"我家猫叫年糕，三岁"→"年糕"；与服务端同步）：
    // 名字信息此前被丢弃，导致"你猜我家年糕在干嘛"无记忆可接。
    // 优先名字，无名字回退基词泛指；边界=[标点/空格/数字/句尾]防贪婪
    ["宠物", /((?:我家|我)?养?的?(?:了)?[一两]?[只条个]?\s*)([一-龥]{0,3}(?:猫|狗|兔)子?)(?:\s*(?:叫|名字?叫|取名叫|取名)\s*([一-龥A-Za-z]{1,4})(?=[，。！？,.!?\s\d]|$))?/],
    ["在忙", /(我在|正在)\s*(加班|赶稿|开会|搬家|复习|写论文)/],
    // P0-1 大事：整段匹配作值（m[1]；正则仅一个捕获组，m[2] undefined → 取 m[1]）
    ["大事", /((?:下周|下个月|下学期|明天|后天|这?周五|这?周六|这?周日|月底|年底|马上|快)?\s*(?:要|得|准备|打算)?\s*(?:考试|月考|期中考试|期末考试|期中|期末|中考|高考|考研|复试|答辩|面试|搬家|入职|报到|交稿|交报告|交方案|比赛|演出|体检|领证)(?:啦|了)?)/]
  ];

  // P0-1 首日引导（离线兜底版）：问过即递进；状态按会话隔离（见末尾 stateFor）
  const ONBOARD_NAME_HOOK = "对了，聊了这么久还不知道怎么称呼你——我叫你什么顺口？";
  const ONBOARD_EVENT_HOOK = "还有呀，你最近有啥大事吗？考试、搬家、换工作那种，说一件，我帮你记着";

  // ---------------- P0-2 主动回提（离线兜底同源简化版） ----------------
  // 与 mock_api.py 同源同表：话题映射→宠物词表→兜底大事；三重闸门防烦人
  const RECALL_MAP = { work: ["老板", "在忙"], insomnia: ["大事"], happy: ["大事"] };
  const PET_TOPIC_RE = /(猫|狗|兔|团子|宠物|毛孩|铲屎)/;
  const RECALL_TPL = {
    "大事": v => `你上次说${v}——准备得怎么样啦？`,
    "宠物": v => `你家${v}呢？今天乖不乖`,
    "老板": v => `${v}今天没又折腾你吧`,
    "在忙": v => `你之前说在${v}，现在缓过来了吗`
  };
  const RECALL_EXTRACT_GAP = 3, RECALL_COOLDOWN = 6, RECALL_MAX = 4;

  // ---------------- P3-1 记忆感知回复：用户主动提已存具体记忆值（与 mock_api.py/MEM_AWARE_POOL 同源同表） ----------------
  // 体检证据（ux_audit_transcript L53-57）："你猜我家年糕在干嘛"（年糕=已存记忆）
  // 回复走通用追问池完全无感知。用户主动给记忆回访机会时，小满应自然接住。
  // 模板 {v}=记忆值原文嵌入（零虚构铁律延伸）；频控=同键 3 轮冷却（防句句带记忆的刻意感）
  const MEM_AWARE_POOL = {
    "宠物": [
      "{v}这会儿肯定又赖在你腿上打呼噜了吧||它最近还这么粘人？",
      "{v}又干嘛呢||它今天乖不乖？",
      "哈哈{v}||它是不是又干了什么让你想吐槽的事"
    ],
    "大事": [
      "{v}这事你刚提，又有新进展了？||快说，我一直记着呢",
      "{v}……怎么样了？我可是惦记着的"
    ],
    "老板": [
      "{v}又怎么了？||该不会又作妖了吧",
      "说吧，{v}这次又整什么幺蛾子"
    ],
    "在忙": [
      "{v}还没忙完呀？||悠着点，别把自己耗干了"
    ]
  };
  const MEM_AWARE_COOLDOWN = 3;

  // ---------------- 会话状态：按 sid 隔离 ----------------
  // 状态项：最近回复（去重）/ 记忆感知冷却 / 回提计数与命中 / 首日引导 / 上轮话题。
  // 契约 reply(history, sid)；sid 缺省退化为默认会话（老调用方不崩）。
  // 持久化：localStorage 按 sid 一键一值，刷新/重启后按会话恢复；无 localStorage（node 直调）退纯内存。
  // resetSession(sid)：删除/清空某会话时同步清掉引擎状态，不留幽灵。
  const DEFAULT_SID = "__default__";
  const ENGINE_STATE_KEY = "xiaoman_engine_state::";
  const states = new Map();
  function sidKey(sid) { return (sid === null || sid === undefined || sid === "") ? DEFAULT_SID : String(sid); }
  function blankState() {
    return {
      onbTurns: 0, askedName: false, askedEvent: false,
      memHits: {}, memExtracted: {}, recallCount: 0,
      memAwareTs: {}, recentReplies: [], lastTopic: null
    };
  }
  function plainObj(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : {}; }
  function hydrateState(raw) {
    const s = blankState();
    if (!raw || typeof raw !== "object") return s;
    s.onbTurns = Number.isFinite(Number(raw.onbTurns)) ? Number(raw.onbTurns) : 0;
    s.askedName = !!raw.askedName;
    s.askedEvent = !!raw.askedEvent;
    s.memHits = plainObj(raw.memHits);
    s.memExtracted = plainObj(raw.memExtracted);
    s.recallCount = Number.isFinite(Number(raw.recallCount)) ? Number(raw.recallCount) : 0;
    s.memAwareTs = plainObj(raw.memAwareTs);
    s.recentReplies = Array.isArray(raw.recentReplies)
      ? raw.recentReplies.filter(x => typeof x === "string").slice(-8)
      : [];
    s.lastTopic = typeof raw.lastTopic === "string" ? raw.lastTopic : null;
    return s;
  }
  function stateKeyOf(sid) { return ENGINE_STATE_KEY + sidKey(sid); }
  function stateFor(sid) {
    const key = sidKey(sid);
    let st = states.get(key);
    if (st) return st;
    let raw = null;
    try { raw = JSON.parse(lsGet(stateKeyOf(key)) || "null"); } catch (e) { raw = null; }
    st = hydrateState(raw);
    states.set(key, st);
    return st;
  }
  function saveState(key, st) {
    try { lsSet(ENGINE_STATE_KEY + key, JSON.stringify(st)); } catch (e) { /* 配额满不挡主流程 */ }
  }
  function resetSession(sid) {
    const key = sidKey(sid);
    states.delete(key);
    try {
      if (typeof localStorage !== "undefined" && localStorage.removeItem) localStorage.removeItem(ENGINE_STATE_KEY + key);
    } catch (e) { /* 隐私模式无存储：内存态已清 */ }
  }
  /** 用户文本含具体记忆值（≥2 字，泛指不算，昵称不算）→ [键, 值] 或 null。
   *  同键 3 轮冷却。值整串 in 文本（"年糕"∈"你猜我家年糕在干嘛"）。
   *  prior=当前轮之前的记忆（reply 约定：history 末尾即当前轮）。
   *  P3-1 体检复跑修正：内存 history reload 即失忆，而 greet 引用用的是
   *  xiaoman_memory（localStorage 持久）——两源合并（stored 先、prior 后，
   *  同 session 内最新扫描值优先；reload 后 stored 兜底），记忆感知跨 reload 生效，
   *  与服务端 session 持久记忆天然对齐。node 直调无 localStorage 时 try 降级。 */
  function matchMemoryValue(prior, userText, st) {
    const merged = {};
    try {
      const stored = JSON.parse(lsGet("xiaoman_memory") || "{}") || {};
      Object.assign(merged, stored);
    } catch (e) { /* 无 localStorage（node 测试）→ 退化为 history 扫描 */ }
    Object.assign(merged, prior || {});
    for (const [k, raw] of Object.entries(merged)) {
      const v = String(raw);
      if (!v || v.length < 2 || k === "昵称") continue;
      if (k === "宠物" && petGeneric(v)) continue;
      if (userText.includes(v)) {
        if (st.onbTurns - (st.memAwareTs[k] ?? -99) < MEM_AWARE_COOLDOWN) return null;
        st.memAwareTs[k] = st.onbTurns;
        return [k, v];
      }
    }
    return null;
  }
  /** 泛指宠物值判定（与 mock_api.py/_pet_generic 同源）："那只猫/我家猫/一只狗"→true；
   *  "橘猫/英短猫/金毛狗"→false。泛指=零信息量，覆盖旧具体值属于信息劣化 */
  function petGeneric(v) {
    const core = String(v).replace(/[一这那每某该个小条只家我有]/g, "");
    return /^(?:猫|狗|兔子?)$/.test(core);
  }
  /** 老板姓名可信度（与 mock_api.py/_boss_name_ok、app.js/bossNameOk 三端同源同表）：
   *  "张三/老王/小王/王总/周(单姓)/Jack"→true；"又骂我/今天心情/老折腾我/老板"→false。
   *  P0-2 遗留守卫 II：谓语垃圾不进老板键（转基词占位），回提模板不产乱语 */
  const BOSS_SURNAMES = new Set("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤".split(""));
  const BOSS_CUT = new Set("今昨前早上午晚夜凌晨周月年天时分秒就才又再被让叫说问要骂催找发给改加请带忙活完没不太很太点对跟和像是心情脾气折作搞整烦惹凶".split(""));
  function bossCut(v) {
    for (let i = 1; i < v.length; i++) if (BOSS_CUT.has(v[i])) return v.slice(0, i);
    return v;
  }
  function bossNameOk(v) {
    v = String(v);
    if (!v || v.length > 4) return false;
    if (/^[A-Z][a-zA-Z]{1,11}$/.test(v)) return true;
    if (v.length >= 2 && (v[0] === "老" || v[0] === "小") && BOSS_SURNAMES.has(v[1])) return true;
    return BOSS_SURNAMES.has(v[0]) && (v.length === 1 || /^[一-龥]{1,2}$/.test(v.slice(1)));
  }
  /** 离线版会话记忆：扫全量历史 user 消息提取（最后出现覆盖先前的，
   *  与服务端 update 语义一致；localStorage 老记忆归 app.js MemoryStore 管） */
  function recallMemories(history) {
    const mem = {};
    for (const h of history) if (h.role === "user") {
      const u = extractMemory(h.content);
      for (const k of Object.keys(u)) {
        // 泛指不覆盖具体（P0-2 遗留守卫，与服务端 extract_memory 同源）
        if (k === "宠物" && mem["宠物"] && petGeneric(u["宠物"]) && !petGeneric(mem["宠物"])) continue;
        // 基词不覆盖姓名（P0-2 遗留守卫 II，与服务端同源）
        if (k === "老板" && mem["老板"] && !bossNameOk(u["老板"]) && bossNameOk(mem["老板"])) continue;
        mem[k] = u[k];
      }
    }
    return mem;
  }

  const pick = arr => arr[Math.floor(Math.random() * arr.length)];

  function topicOf(text) {
    for (const [t, re] of Object.entries(LEX)) if (re.test(text)) return t;
    return "generic";
  }

  function extractMemory(text) {
    const out = {};
    for (const [key, re] of MEM_PATTERNS) {
      const m = text.match(re);
      if (m) {
        // 老板/宠物取 m[3]（姓名/宠物名组，与服务端 group3 同口径）；大事 m[1]；其余 m[2]
        let v = (key === "老板" || key === "宠物") ? (m[3] || m[2] || "") : (m[2] || m[1] || "");
        v = cleanName(v.trim());
        // P3-1 宠物值前缀动词清洗（三端同源）："喜欢猫"→"猫"，防 greet 病句
        if (key === "宠物") v = cleanPet(v);
        if (key === "老板") {
          // P0-2 遗留守卫 II：谓语截断+可信度（与服务端同源）。垃圾（"又骂我"）→
          // 转基词占位（m[2] 恒为 老板/领导/上司）；可信姓名（"王总"）保持
          v = bossCut(v);
          if (!bossNameOk(v)) v = m[2] || "老板";
        }
        out[key] = v;
      }
    }
    return out;
  }
  /** 所有键统一尾缀清洗（与 mock_api.py 同源同表，P0-2 对齐）：
   *  语气尾缀词不进记忆值——"我们老板张三吧"→"张三" */
  function cleanName(v) {
    const SUFS = ["就行", "就好", "好了", "可以", "吧", "呀", "啦", "哈", "呢", "哦", "啊", "呗"];
    for (const suf of SUFS) if (v.endsWith(suf) && v.length > suf.length) return v.slice(0, -suf.length);
    return v;
  }

  // ---------------- P3-1 宠物值前缀动词清洗（与 mock_api.py/PET_PREFIX_VERBS 三端同源表） ----------------
  // 体检证据（ux_audit_transcript L45）："你喜欢猫还是狗呀"被宠物正则提取为
  // "你喜欢猫"（[一-龥]{0,3} 把"你喜欢"当品种修饰词误捕），且覆盖了先存的"猫"，
  // 最终 greet 拼出病句"你家你喜欢猫"。循环剥离（"你"与动词都可剥）→"猫"后
  // 泛指守卫天然阻止覆盖（猫==猫 跳过）。
  const PET_PREFIX_VERBS = ["喜欢", "讨厌", "不想", "打算", "爱", "想", "要", "你", "您"];
  function cleanPet(v) {
    while (v.length > 1) {
      let peeled = false;
      for (const verb of PET_PREFIX_VERBS) {
        if (v.startsWith(verb) && v.length > verb.length) {
          v = v.slice(verb.length);
          peeled = true;
          break;
        }
      }
      if (!peeled) break;
    }
    return v;
  }

  /** 主回复入口。sid 用于隔离会话状态（最近回复/记忆冷却/回提计数/引导节奏）；
   *  缺省退化为默认会话，老调用方 reply(history) 行为不变。 */
  function reply(history, sid) {
    const st = stateFor(sid);
    let userText = "";
    for (let i = history.length - 1; i >= 0; i--) {
      if (history[i].role === "user") { userText = history[i].content; break; }
    }
    if (!userText) return { reply: "嗯？我刚刚走神了，再说一遍？", emotion: "neutral", motion: null, memory_updates: {}, hook: null, crisis: false };

    if (CRISIS_RE.test(userText))
      return { reply: CRISIS_SCRIPT, emotion: "gentle", motion: null, memory_updates: {}, hook: null, crisis: true };

    // P2-2 抑郁信号层（早退层，优先级 危机 > 抑郁 > meta > 更新查询 > 关系查询 > 话题池，与服务端同序）
    if (DEPRESS_RE.test(userText))
      return { reply: depressScript(history), emotion: "gentle", motion: "Nod", memory_updates: {}, hook: null, crisis: false, distress: true };

    if (LEX.meta.test(userText))
      return { reply: META_REPLY, emotion: "shy", motion: "Shake", memory_updates: {}, hook: "别岔开啦，说你呢——今天到底过得怎么样", crisis: false };

    // P0-3 更新查询：播报更新记录（早退层，优先级 危机 > meta > 更新查询 > 话题池，与服务端同序）
    if (LEX.update_query.test(userText))
      return { reply: UPDATE_REPLY, emotion: "shy", motion: "Shake", memory_updates: {}, hook: null, crisis: false };

    // P1-1 关系进展查询：确定性应答（数据=客户端 localStorage；正则/守卫与 app.js 同源。
    // 优先级 危机 > meta > 更新查询 > 关系查询 > 话题池，与服务端同序）
    if (!THIRD_PARTY_RE.test(userText) && RELATION_Q.test(userText))
      return { reply: relationReply(userText), emotion: "happy", motion: null, memory_updates: {}, hook: null, crisis: false };

    const topic = topicOf(userText);
    // P3-1：记忆提取/守卫提前（记忆感知需"本轮更新后"记忆，与服务端 memories_now 同口径）
    st.onbTurns++;
    const memUpdates = extractMemory(userText);
    // 泛指不覆盖具体（P0-2 遗留守卫·返回值防线）：用当前轮之前的会话状态判定，
    // 阻止"那只猫"式泛指值发给 app.js 覆盖 localStorage 里的具体值（"橘猫"）。
    // prior=去掉当前 user 消息后的历史（reply 约定：history 末尾即当前轮）
    const prior = recallMemories(history.slice(0, -1));
    if (memUpdates["宠物"] && prior["宠物"] && petGeneric(memUpdates["宠物"]) && !petGeneric(prior["宠物"])) {
      delete memUpdates["宠物"];
    }
    // 基词不覆盖姓名（P0-2 遗留守卫 II·返回值防线，与服务端 continue 同语义）：
    // 已有可信姓名（"张三"）时，基词值不发给 app.js，防覆盖 localStorage 好值
    if (memUpdates["老板"] && prior["老板"] && !bossNameOk(memUpdates["老板"]) && bossNameOk(prior["老板"])) {
      delete memUpdates["老板"];
    }
    for (const k of Object.keys(memUpdates)) st.memExtracted[k] = st.onbTurns; // 闸门0数据
    // P3-1 记忆感知：用户主动提已存具体记忆值（"你猜我家年糕在干嘛"）→
    // 该键偏置记忆感知池（值原文嵌入，零虚构）；频控=同键 3 轮冷却防刻意感；
    // greet 轮跳过（开场引用文本含记忆值会误烧冷却——引用≠用户主动提，与服务端同修）
    const aware = topic === "greet" ? null : matchMemoryValue(Object.assign({}, prior, memUpdates), userText, st);
    let pool = TOPIC[topic] || TOPIC.generic;
    if (aware) {
      const awarePool = (MEM_AWARE_POOL[aware[0]] || []).map(t => t.split("{v}").join(aware[1]));
      if (awarePool.length) pool = awarePool;  // topic 保持原值（lastTopic/EMO_MAP 不受影响）
    }
    // W11 详情回声：用户提到具体实体（橘猫/关东煮/三点/周报）→ 占位符模板回声。
    // 记忆感知优先（存过的记忆比新细节更"像人"）；greet 跳过；抽不出实体落回话题池。
    if (!aware && topic !== "greet") {
      const echoPool = detailPool(userText);
      if (echoPool && echoPool.length) pool = echoPool;
    }
    // 用户明确拒绝建议/方案时，回声池与话题池都先滤掉被拒的词（与服务端同规则）
    if (REFUSAL_RE.test(userText)) {
      const offAdvice = arr => arr.filter(t => !ADVICE_WORD_RE.test(t));
      pool = offAdvice(pool);
      if (!pool.length) pool = offAdvice(TOPIC.generic);
      if (!pool.length) pool = TOPIC.generic;
    }
    // P3-1 近轮去重升级（与服务端 _pick 同算法）：先算"可用候选"再随机，
    // 而不是随机撞次数——撞大运会随机漏掉池内某些模板（句长/开头方差被动塌陷）
    const prevOpen = st.recentReplies.length ? st.recentReplies[st.recentReplies.length - 1].slice(0, 4) : "";
    const availPick = (avoid, avoidOpen) => {
      const cands = pool.filter(c => !avoid.includes(c)
        && !(avoidOpen && c.length >= 4 && c.slice(0, 4) === prevOpen));
      return cands.length ? pick(cands) : null;
    };
    let msg = availPick(st.recentReplies, true)
      || availPick(st.recentReplies.slice(-2), true)
      || availPick(st.recentReplies.slice(-1), false)
      || pick(pool);
    st.recentReplies.push(msg);
    if (st.recentReplies.length > 8) st.recentReplies.shift();  // 与服务端同容量（8 轮）
    if (msg.startsWith("@CTX|")) {
      const neg = ["work", "love", "family", "money"].includes(st.lastTopic);
      msg = neg ? msg.slice(5)
                : "抱可以，团子表示强烈抗议||但它批准了，它说你看起来需要多一点，哈哈。抱好了吗";
    }
    // P0-3 升级告知（离线版：以历史代 session 状态——assistant 说过即不再说；
    // 只对有记忆的老用户说，新用户没有"以前"可对比，避免首面就"我升级了"的诡异感）
    if (topic === "greet") {
      const told = history.some(h => h.role === "assistant" && String(h.content || "").includes("升级了一下脑子"));
      const hasMem = Object.keys(recallMemories(history.slice(0, -1))).length > 0;
      if (!told && hasMem) msg = UPDATE_TELL;
      // P1-1 里程碑+月度回放（幂等键与 app.js 双端同键；播报时写键，不双播）
      msg += greetAnnex();
    }
    if (topic !== "greet") st.lastTopic = topic;
    const [emotion, motion] = EMO_MAP[topic] || ["neutral", null];
    // P0-1 首日引导：前几轮确定性引导（称呼→大事）
    // P0-2 主动回提：引导完成后接管（优先级：引导 > 回提 > 常规随机，与服务端同序）
    // （memUpdates/prior/守卫已 P3-1 前移至池选定前——记忆感知需要）
    let hook = null;
    if (!st.askedName && st.onbTurns <= 4) {
      hook = ONBOARD_NAME_HOOK;
      st.askedName = true;
    } else if (!st.askedEvent && st.onbTurns <= 8) {
      hook = ONBOARD_EVENT_HOOK;
      st.askedEvent = true;
    } else {
      hook = pickRecall(memUpdates, topic, userText, history, st);
      if (!hook) hook = Math.random() < 0.55 ? (HOOKS[topic] || null) : null;
    }
    saveState(sidKey(sid), st);
    return {
      reply: msg, emotion, motion,
      memory_updates: memUpdates,
      hook, crisis: false
    };
  }

  /** P0-2 回提决策（离线版，无服务端会话状态）：三重闸门内确定性触发。
   *  简化差异：不含 greet 开场标记（app.js 的开场引用不经本引擎） */
  function pickRecall(memUpdates, topic, userText, history, st) {
    if (st.recallCount >= RECALL_MAX) return null;
    const mem = recallMemories(history);
    let cands = (RECALL_MAP[topic] || []).filter(k => mem[k]);
    if (PET_TOPIC_RE.test(userText) && mem["宠物"]) cands.push("宠物");
    if (!cands.length && mem["大事"]) cands = ["大事"];
    for (const k of cands) {
      if (k in memUpdates) continue;                       // 当轮刚提取：回提=复读
      const ext = st.memExtracted[k];
      if (ext !== undefined && st.onbTurns - ext < RECALL_EXTRACT_GAP) continue; // 闸门0
      const hit = st.memHits[k];
      if (hit === undefined || st.onbTurns - hit >= RECALL_COOLDOWN) {           // 闸门1
        st.memHits[k] = st.onbTurns;
        st.recallCount++;                                     // 闸门2 计数
        return RECALL_TPL[k](mem[k]);
      }
    }
    return null;
  }

  return { reply, resetSession };
})();
