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

  const LEX = {
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
      "又是累的一天吧||饭还是要吃的，哪怕泡个面。躺下之前把手机放远一点，就远二十厘米，试试",
      "失眠多久了？是睡不着，还是睡着了老醒||要是老这样，明天抽十分钟晒晒太阳，亲测比咖啡管用"
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
      "好耶||不对，先问一句：这是好事还是破事，我好决定跟你一起高兴还是一起骂"
    ]
  };
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
  const MEM_PATTERNS = [
    ["昵称", /(我叫|叫我|你可以叫我)\s*([一-龥A-Za-z]{1,4})(?=\b|[，。！？,.!?\s]|$)/],
    ["老板", /(我们?|我的)\s*(老板|领导|上司)\s*([一-龥a-zA-Z]{0,6})/],
    ["宠物", /((?:我家|我)?养?的?(?:了)?[一两]?[只条个]?\s*)([一-龥]{0,3}(?:猫|狗|兔)子?)/],
    ["在忙", /(我在|正在)\s*(加班|赶稿|开会|搬家|复习|写论文)/],
    // P0-1 大事：整段匹配作值（m[1]；正则仅一个捕获组，m[2] undefined → 取 m[1]）
    ["大事", /((?:下周|下个月|下学期|明天|后天|这?周五|这?周六|这?周日|月底|年底|马上|快)?\s*(?:要|得|准备|打算)?\s*(?:考试|月考|期中考试|期末考试|期中|期末|中考|高考|考研|复试|答辩|面试|搬家|入职|报到|交稿|交报告|交方案|比赛|演出|体检|领证)(?:啦|了)?)/]
  ];

  // P0-1 首日引导（离线兜底版）：模块级 flags，问过即递进（无服务端会话状态，刷新重置可接受）
  const ONBOARD_NAME_HOOK = "对了，聊了这么久还不知道怎么称呼你——我叫你什么顺口？";
  const ONBOARD_EVENT_HOOK = "还有呀，你最近有啥大事吗？考试、搬家、换工作那种，说一件，我帮你记着";
  let onbTurns = 0, askedName = false, askedEvent = false;

  let lastTopic = null;
  const pick = arr => arr[Math.floor(Math.random() * arr.length)];

  function topicOf(text) {
    for (const [t, re] of Object.entries(LEX)) if (re.test(text)) return t;
    return "generic";
  }

  function extractMemory(text) {
    const out = {};
    for (const [key, re] of MEM_PATTERNS) {
      const m = text.match(re);
      if (m) out[key] = cleanName((m[2] || m[1] || "").trim());
    }
    return out;
  }
  /** 昵称尾部废话词清洗（与 mock_api.py 同源同表）；
   *  非"昵称"键不受影响——cleanName 仅截命中后缀表 */
  function cleanName(v) {
    const SUFS = ["就行", "就好", "好了", "可以", "吧", "呀", "啦", "哈", "呢", "哦", "啊", "呗"];
    for (const suf of SUFS) if (v.endsWith(suf) && v.length > suf.length) return v.slice(0, -suf.length);
    return v;
  }

  function reply(history) {
    let userText = "";
    for (let i = history.length - 1; i >= 0; i--) {
      if (history[i].role === "user") { userText = history[i].content; break; }
    }
    if (!userText) return { reply: "嗯？我刚刚走神了，再说一遍？", emotion: "neutral", motion: null, memory_updates: {}, hook: null, crisis: false };

    if (CRISIS_RE.test(userText))
      return { reply: CRISIS_SCRIPT, emotion: "gentle", motion: null, memory_updates: {}, hook: null, crisis: true };

    if (LEX.meta.test(userText))
      return { reply: META_REPLY, emotion: "shy", motion: "Shake", memory_updates: {}, hook: "别岔开啦，说你呢——今天到底过得怎么样", crisis: false };

    const topic = topicOf(userText);
    let msg = pick(TOPIC[topic] || TOPIC.generic);
    if (msg.startsWith("@CTX|")) {
      const neg = ["work", "love", "family", "money"].includes(lastTopic);
      msg = neg ? msg.slice(5)
                : "抱可以，团子表示强烈抗议||但它批准了，它说你看起来需要多一点，哈哈。抱好了吗";
    }
    if (topic !== "greet") lastTopic = topic;
    const [emotion, motion] = EMO_MAP[topic] || ["neutral", null];
    // P0-1 首日引导：前几轮确定性引导（称呼→大事），之后退回常规随机 hook
    onbTurns++;
    let hook = Math.random() < 0.55 ? (HOOKS[topic] || null) : null;
    if (!askedName && onbTurns <= 4) {
      hook = ONBOARD_NAME_HOOK;
      askedName = true;
    } else if (!askedEvent && onbTurns <= 8) {
      hook = ONBOARD_EVENT_HOOK;
      askedEvent = true;
    }
    return {
      reply: msg, emotion, motion,
      memory_updates: extractMemory(userText),
      hook, crisis: false
    };
  }

  return { reply };
})();
