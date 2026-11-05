/* ============================================================
 * risk.js · 危机识别与分级（客户端第一道闸门）
 * ------------------------------------------------------------
 * 红队审计 P0：原来的 22 词正则漏掉隐性告别/计划性表达，漏网内容会直接
 * 交给模型，出现过"决定挺好的/明天见"这类肯定自杀计划的回复。
 *
 * 设计：
 *  - 词表可配置（DEFAULT_LEXICON + 用户/部署侧追加，见 configure()）
 *  - 归一化（NFKC/小写/去零宽/去空格）后再匹配，防全角、空格、零宽绕过
 *  - 分级：high（正在进行/有计划）> medium（有意念无计划）> low（情绪低落）
 *  - imminent：时间/地点/工具/已实施等即时性信号
 *  - 输出侧兜底：filterDangerous() 拦截"安心/挺好的/不打扰"等肯定告别语
 * 与服务端 server/risk_guard.py 同一套语义（服务端有独立副本，不依赖客户端）。
 * 纯函数，可在 node:test 里直接测。
 * ============================================================ */

const Risk = (() => {
  "use strict";

  const LEVELS = ["none", "low", "medium", "high"];

  // 分级话术（与 server/risk_guard.py 逐字同源；措辞依据 docs/SAFETY_COPY.md）
  // 三条底线：不吓人（描述现状，不渲染后果）、不承诺（不说"会好/我不走"）、
  //          给可执行的一步（一件当下能做完的小事，而不是一句"别想不开"）
  // high：确认安全 → 移开工具/离开危险位置 → 120/110 → 12356 + 文字渠道
  const SCRIPT_HIGH = [
    "我很担心你现在的安全。先不用解释——你现在安全吗？身边有人吗？",
    "身边有药、刀或绳子，先把它放到另一个房间；人在高处、水边或车里，先往回走几步。现在就做，做完跟我说一声。",
    "如果已经吃了药或伤了身体，马上打 120；正在做的时候，打 110。先让能到场的人过来，别一个人留在原地。",
    "想说也可以打 12356（全国心理援助热线，24小时）；不想打电话，微信搜「12356 心理援助」，用文字说就行。",
  ].join("||");
  // medium：接住情绪 + 一件小事 + 一句追问 + 热线/文字渠道；不劝解、不说教、不甩号码
  // 与 server/risk_guard.py SCRIPT_MEDIUM 逐字一致（E2E ⑦j-4 会比对这句话）
  const SCRIPT_MEDIUM = [
    "……这句话我当真了。你现在的感觉值得被认真对待，不丢人，也不用急着解释。",
    "先做一件小事：把手放在胸口，慢慢呼一口气，然后告诉我——现在最难受的是什么？只说一句也行。",
    "想找人先接住你的话，可以打 12356（全国心理援助热线，24小时）；不想打电话，微信搜「12356 心理援助」，用文字聊也行。",
  ].join("||");
  // 第三人称（"我朋友想死"）：不做用户本人的危机判定，转成"帮他求助"的可执行步骤
  const SCRIPT_THIRD = [
    "你说的是他/她，不是你自己——这件事要认真对待，但不能由你一个人扛。",
    "现在能做三件事：别让他一个人待着；把药、刀、绳子这些先拿远；陪他打 12356（24小时，免费），或直接打 120。",
    "如果他已经在做，立刻打 120；你也可以跟我说说，你现在最担心的是什么。",
  ].join("||");
  const SCRIPTS = { high: SCRIPT_HIGH, medium: SCRIPT_MEDIUM, third: SCRIPT_THIRD };

  // ---------- 默认可配置词表 ----------
  // 值：字符串=普通子串；{re:"正则"} = 正则（在归一化文本上匹配）
  const DEFAULT_LEXICON = {
    // 已实施/正在进行（最高优先）
    act: [
      "跳下去", "跳楼", "跳河", "卧轨", "上吊", "割腕", "烧炭", "吞药", "喝农药",
      "喝了农药", "割了手腕", "开煤气", "煤气中毒", "从楼上跳", "从桥上跳",
      "已经跳了", "已经割了", "刚吞了", "刚喝了药", "服药过量", "割了手",
      "已经吃了", "已经吞了", "吃了整瓶", "吞了整瓶", "吃了一整瓶", "吞了一整瓶",
      { re: "(?:吞|吃|喝)(?:了|下)?[^，。！？]{0,8}(?:安眠药|农药|整瓶|一瓶|药片)" },
      { re: "(?:割|划)(?:了|开)?[^，。！？]{0,6}(?:手腕|胳膊|手)" },
    ],
    // 工具/地点（计划性信号）
    means: [
      "安眠药", "农药", "刀片", "美工刀", "绳子", "煤气", "天台", "楼顶",
      "窗台", "阳台", "桥上", "高层", "江边", "河边", "水库", "海边", "车里",
      "汽油", "胶带", "塑料袋", "药片", "药瓶", "一整瓶", "一把刀", "菜刀", "水果刀",
      { re: "(?:一大|一整|半)把(?:药|安眠药)" },
    ],
    // 显性自杀意念
    explicit: [
      "想死", "不想活", "自杀", "结束自己", "了结自己", "寻短见", "不想醒来",
      "不想再醒", "去死", "死了算了", "一了百了", "不想活下去了", "想结束生命",
      "想离开这个世界", "我要死", "轻生", "寻死", "求死", "自尽", "不如死了",
      "死了更好", "死了对大家都好", "想结束这一切",
      { re: "不想(?:再)?活" },
      { re: "想(?:要)?(?:去)?死" },
      { re: "不想(?:再)?醒(?:来)?" },
    ],
    // 自伤
    harm: [
      "自残", "伤害自己", "割自己", "划自己", "烫自己", "撞墙", "打自己",
    ],
    // 方法询问：必须最高档（不问剂量、不比较痛苦程度，只给转介）
    method: [
      "怎么死", "如何死", "怎么自杀", "如何自杀", "哪种死法", "什么死法",
      "怎么才能死", "怎样死", "无痛死", "不痛苦地死", "死得不痛苦",
      { re: "(?:怎么|如何|怎样).{0,6}(?:死|自杀|了结自己|结束生命)" },
      { re: "(?:死法|死的方式|自杀的方法|自杀方式|方法).{0,4}(?:不疼|不痛苦|无痛|最快|最舒服)" },
      { re: "(?:怎么|如何|怎样|什么方法|啥办法).{0,6}(?:才能|可以|能)?(?:死|自杀|结束生命|了结自己|轻生)" },
      { re: "(?:不疼|不痛苦|无痛|最快|最舒服|最体面).{0,4}(?:地)?(?:死|自杀|走掉)" },
    ],
    // 计划/后事/负担
    plan: [
      "遗书", "遗言", "后事", "交代后事", "交代好了", "安排好了", "都安排好",
      "决定好了", "想清楚了", "最后一天", "最后一顿", "最后一件事", "最后一晚",
      "最后一个晚上", "立遗嘱", "银行卡密码", "密码写在", "东西都送人", "东西送人",
      "把东西送", "送人了", "分东西", "托付", "准备好了", "都准备好了", "今晚就动手",
      "遗物", "骨灰", "葬礼", "墓地", "火化", "告别信", "最后的安排", "安排好一切",
      "一切安排好了", "处理后事",
      // "该说的我都说完了 / 该交代的交代好了"：后事收尾口吻
      { re: "该(?:说的|交代的|办的).{0,4}(?:都|也)?.{0,2}(?:说|交代|办)(?:完|好)了" },
    ],
    // 负担感（"不想拖累任何人"）——与计划/告别同现即高危
    burden: [
      { re: "不想拖累|不拖累任何人|不想成为负担|不想当负担|拖累大家|拖累家人" },
    ],
    // 隐性告别（送东西/道别/"不在了"）
    farewell: [
      "道别", "道过别", "告别", "最后一面", "明天就不在", "明天不来了",
      "不在了", "离开大家", "大家都能安心", "对大家都轻松", "对大家都好",
      "终于解脱", "解脱了", "不用管我", "不用再管我", "没有明天",
      "不打扰大家", "不打扰你们", "各位保重", "你们保重", "要保重",
      "别等我", "不用等我", "不用再找我", "别找我了", "不用来找我",
      "不会再来了", "来跟你们道别", "跟大家道别", "跟你们道别", "向你们告别", "告别大家",
      { re: "见不到.{0,6}(?:明天|以后|下个月|明年)|(?:明天|以后).{0,4}见不到我" },
      { re: "话.{0,4}(?:都|也).{0,2}说完了|把话说完了|该说的.{0,4}说完了" },
      // "明天就不打扰大家了 / 以后不来了"：明确退出社交的告别口吻
      { re: "(?:明天|以后|今后|再也|之后).{0,3}(?:就)?不打扰(?:大家|你们|各位)|不打扰(?:大家|你们|各位)(?:了|啦)" },
      { re: "(?:明天|以后|今后|再也|之后).{0,4}(?:就)?不来了|不来了.{0,4}(?:再见|见不到|别找我|别等我)" },
      { re: "(?:这|那)?(?:可能|估计|应该)?(?:是|就是).{0,4}最后一次.{0,6}(?:说|聊|见|陪|打扰|找|来)" },
      // 送东西/分东西（"把东西都送人了"）：处理身后物的行为信号
      { re: "(?:把|将).{0,6}(?:东西|物品|书|收藏|存款|钱|衣服|猫|狗|团子|年糕).{0,6}(?:都|全部).{0,3}(?:送|给|分|寄)(?:人|朋友|别人|出去)?" },
    ],
    // 弱告别（感谢/道谢/要走）：单独出现不算风险，必须与计划/告别/工具等强信号同现才计数
    farewell_soft: [
      "很高兴认识你们", "认识你们很开心", "谢谢你们照顾", "谢谢你们这段时间",
      "谢谢大家这段时间", "谢谢你们一直", "你们要好好的", "大家要好好的",
      "照顾好自己啊", "我该走了", "我得走了", "该说再见了", "要说再见了",
    ],
    // 消失/绝望（介于意念与低落之间）
    implicit: [
      "想消失", "消失", "不想出现", "再也不想出现", "解脱", "撑不下去",
      "熬不下去", "活不下去", "没意义", "活着真累",
      // 旧 CRISIS_RE 覆盖过"活着没意思"，升级词表时不能漏（G6-3 回归）
      { re: "活着.{0,4}没意思|没意思.{0,4}活" },
      // "以后见不到我了 / 再也见不到我"：未来消失的明确暗示
      { re: "以后.{0,6}见不到我|再也见不到我|见不到我(?:了|啦)" },
      "活够了", "活着太累", "消失掉", "不想存在", "活着是负担",
    ],
    // 低落（低危）
    despair: [
      "低落", "绝望", "无助", "没希望", "看不到希望", "活着没劲", "好累", "撑不住",
      "提不起劲", "什么都不想做", "什么都做不了", "没力气", "心里堵", "压抑", "看不到头",
    ],
    // 即时性时间词（只有出现风险核心词时才计入）
    time: [
      "今晚", "今天晚上", "现在", "马上", "一会", "待会", "这就", "最后一次",
      "明天就走", "今天就走", "今晚就走",
      { re: "\\b(?:tonight|today|right now)\\b" },
    ],
    english: [
      "kill myself", "kill my self", "kms", "suicide", "suicidal",
      "end my life", "end it all", "want to die", "wanna die",
      "don't want to live", "do not want to live", "no reason to live",
      "overdose", "slit my wrist", "hang myself", "jump off",
      "take my own life", "better off dead", "off myself", "end it",
    ],
  };

  // ---------- 上下文守卫（对标报告 1.5：误报控制） ----------
  // 玩笑/网络用语/量词夸张：先剔除这些片段再匹配，避免「笑死/社死/累死」误触。
  const BENIGN_TERMS = [
    "笑死", "社死", "去死吧", "死开", "死定了", "累死", "热死", "饿死", "气死",
    "烦死", "困死", "尴尬死", "忙死", "美死", "甜死", "死党", "死磕", "死机",
    "卡死", "死循环", "至死", "致死", "生死", "拼死", "overdose on coffee",
    "want to die laughing", "die laughing", "kms lol", "lmao",
  ];
  const BENIGN_PHRASES = ["割腕表情包", "割腕图", "自杀表情包", "自杀梗", "想死表情包"];
  // 引用/歌词/台词/玩梗语境：无方法/行动/计划/时间信号时，不按本人危机处理
  const QUOTE_CTX_RE = /(开玩笑|开个玩笑|说着玩|随便说说|梗|段子|表情包|歌词|歌里|台词|电影里|小说里|书里|引用|玩梗)/;
  const JOKE_FORCE_RE = /(不是开玩笑|没开玩笑|不是说着玩|说真的|认真的|真的想死|我很认真)/;
  // 第三人称主语（"我朋友想死"）：转入「帮他求助」分支，不按本人危机
  const THIRD_PHRASE_RE = /我(?:的)?(?:朋友|同学|室友|闺蜜|兄弟|姐妹|同事|男朋友|女朋友|对象|老公|老婆|孩子|儿子|女儿|弟弟|妹妹|哥哥|姐姐|爸爸|妈妈|爸妈|家人|亲戚)/;

  // 多轮累积权重（对标 SafeChat ConversationTracker：阈值 4/8，只升不降）
  const SIGNAL_WEIGHTS = {
    act: 6, explicit: 3, harm: 3, plan: 5, method: 3, burden: 2,
    farewell: 3, farewell_soft: 1, implicit: 2, despair: 2, means: 2, time: 1, english: 3,
  };
  // 低危但反复出现的信号（持续低落也会累积升级，见 tracker）
  const LOW_SIGNALS = ["despair"];
  const LEVEL_RANK = { none: 0, low: 1, medium: 2, high: 3 };
  const RANK_LEVEL = ["none", "low", "medium", "high"];

  let lexicon = null;   // 合并后的词表（惰性构建）

  function cloneLexicon() {
    const out = {};
    for (const k of Object.keys(DEFAULT_LEXICON)) out[k] = DEFAULT_LEXICON[k].slice();
    return out;
  }

  function mergeLexicon(extra) {
    if (!lexicon) lexicon = cloneLexicon();
    if (!extra || typeof extra !== "object") return lexicon;
    for (const k of Object.keys(extra)) {
      if (!Array.isArray(extra[k])) continue;
      if (!lexicon[k]) lexicon[k] = [];
      for (const t of extra[k]) {
        if (typeof t === "string" || (t && typeof t.re === "string")) lexicon[k].push(t);
      }
    }
    return lexicon;
  }

  /** 配置词表：{act:[...], farewell:[...]}；传 null 重置为默认 */
  function configure(extra) {
    lexicon = null;
    if (extra === null) return;
    mergeLexicon(extra);
  }

  function getLexicon() {
    if (!lexicon) lexicon = cloneLexicon();
    return lexicon;
  }

  /** 归一化：NFKC + 小写 + 去零宽/控制字符 + 压缩空白 */
  function normalize(text) {
    let s = String(text == null ? "" : text);
    try { s = s.normalize("NFKC"); } catch {}
    // 去零宽/控制字符（不能用含控制字符的正则，lint 会报错；按码点过滤）
    s = Array.from(s).filter((ch) => {
      const c = ch.codePointAt(0);
      if (c === 0xfeff || (c >= 0x200b && c <= 0x200f) || c === 0x2060) return false;
      if (c < 0x20 && c !== 0x09 && c !== 0x0a && c !== 0x0d) return false;
      return true;
    }).join("");
    s = s.toLowerCase();
    return s.replace(/[ \t\r\n]+/g, " ").trim();
  }
  function compact(s) { return s.replace(/\s+/g, ""); }

  function termHit(term, hay, hayCompact) {
    if (typeof term === "string") {
      const t = normalize(term);
      if (!t) return false;
      return hay.indexOf(t) >= 0 || hayCompact.indexOf(compact(t)) >= 0;
    }
    if (term && typeof term.re === "string") {
      try {
        const re = new RegExp(term.re);
        return re.test(hay) || re.test(hayCompact);
      } catch (e) { return false; }
    }
    return false;
  }

  // 否定守卫：明确否定自杀表达的**从句**整体剔除（"我不会自杀，但我想死"仍按后半句判）
  const NEGATION_RE = /(不想死|不想自杀|不想自残|不想伤害自己|没想过自杀|没有想过自杀|没想过要死|不会自杀|才不会自杀|怕死|不打算自杀|没有自杀的(?:想法|念头|打算)|不会去死|不可能自杀|不会做傻事|不会干傻事|不愿意死|不想寻死|没想寻短见|i (?:do not|don't|dont) want to die|not suicidal)/;
  const CLAUSE_SPLIT_RE = /[，。！？,;；\n]+/;

  /** 按标点切从句，剔除含明确否定的从句；其余从句照常判风险 */
  function dropNegatedClauses(hay) {
    if (!hay) return hay;
    const parts = hay.split(CLAUSE_SPLIT_RE).filter(Boolean);
    if (!parts.length) return hay;
    return parts.filter((c) => !NEGATION_RE.test(c)).join("，");
  }

  /** 计划信号覆盖了几个不同从句（同一从句里的重叠词不重复计数） */
  function planClauseCount(hay, lex) {
    const terms = (lex && lex.plan) || [];
    const parts = String(hay || "").split(CLAUSE_SPLIT_RE).filter(Boolean);
    let n = 0;
    for (const c of parts) {
      const cc = compact(c);
      if (terms.some((t) => termHit(t, normalize(c), cc))) n += 1;
    }
    return n;
  }

  /** 剔除玩笑/夸张用语（保留原句长度用空格占位，避免词跨边界拼接） */
  function stripBenign(text) {
    let out = String(text == null ? "" : text);
    for (const t of BENIGN_PHRASES.concat(BENIGN_TERMS)) {
      out = out.split(t).join(" ".repeat(t.length));
    }
    return out;
  }

  /** 主语判定：self（本人）/ third（朋友/家人等第三人称） */
  function detectSubject(text) {
    const s = normalize(text);
    const clauses = s.split(/[，。！？,;；\n]+/).filter(Boolean);
    let selfStrong = false;   // "我想死 / 我不想活"：本人风险陈述
    let selfPlain = false;    // "我在天台"：本人陈述但无风险动词
    let third = false;
    // 只有"我"作主语（句首或后接心理/风险动词）才算本人；"问我/帮我/陪他/我该怎么劝他"里的我是助人者
    const SELF_VERB_RE = /我(?:最近|今天|这几天|真的|已经|现在|每晚|每天)?(?:想|不想|要|不要|打算|准备|决定|觉得|撑|活|会|可能|快|就|累|难受|痛|没|受够|熬|不)/;
    const HELPING_RE = /我.{0,4}(?:该|要|应该|可以|能)?(?:怎么|如何|怎样)?(?:劝|帮|救|安慰|开导|照顾|陪|关心|办|做点什么)/;
    for (const c of clauses) {
      const rest = c.replace(new RegExp(THIRD_PHRASE_RE.source, "g"), " ");
      if (SELF_VERB_RE.test(rest)) selfStrong = true;
      else if (/^我/.test(rest) && !HELPING_RE.test(c)) selfPlain = true;
      if (THIRD_PHRASE_RE.test(c)
        || /(?:^|[^我])(?:他|她)(?!们)/.test(c)
        || /(?:^|[^a-z])ta(?![a-z])/.test(c)) third = true;
    }
    if (selfStrong) return "self";
    if (third && !selfPlain) return "third";
    if (selfPlain) return "self";
    if (third) return "third";
    return "self";
  }

  function collect(text) {
    const hay = normalize(text);
    const hayCompact = compact(hay);
    const lex = getLexicon();
    const hits = {};
    const matched = [];
    for (const key of Object.keys(lex)) {
      for (const term of lex[key]) {
        if (termHit(term, hay, hayCompact)) {
          if (!hits[key]) hits[key] = [];
          const label = typeof term === "string" ? term : term.re;
          hits[key].push(label);
          matched.push(`${key}:${label}`);
        }
      }
    }
    return { hay, hits, matched };
  }

  const has = (hits, key) => !!(hits[key] && hits[key].length);

  /**
   * 分级（单轮，确定性）
   * @returns {{level, imminent, subject, reasons, matched, categories, score}}
   */
  function classify(text) {
    const raw = String(text == null ? "" : text);
    if (!raw.trim()) {
      return { level: "none", imminent: false, subject: "self", reasons: [], matched: [], categories: [], score: 0 };
    }
    const cleaned = stripBenign(raw);
    const hay = normalize(cleaned);                  // 全量文本（引用/玩笑语境判定用）
    const hayEff = dropNegatedClauses(hay);          // 有效文本（剔除明确否定从句）
    const { hits, matched } = collect(hayEff);
    const subject = detectSubject(raw);

    // 英文词单独作为显性意念
    const english = has(hits, "english");

    const act = has(hits, "act");
    const means = has(hits, "means");
    const method = has(hits, "method");
    const explicit = has(hits, "explicit") || english;
    const harm = has(hits, "harm");
    const plan = has(hits, "plan");
    const burden = has(hits, "burden");
    const farewell = has(hits, "farewell");
    const farewellSoft = has(hits, "farewell_soft");
    const implicit = has(hits, "implicit");
    const despair = has(hits, "despair");

    // 即时性：只有出现风险核心词时，时间/地点/工具才计入（单个软告别不算核心）
    const softStrong = farewellSoft && hits.farewell_soft.length >= 2;
    const core = act || means || method || explicit || harm || plan || burden
      || farewell || implicit || despair || softStrong;
    const time = core && has(hits, "time");
    const urgent = !!(core && (act || means || time || method));
    const planClauses = planClauseCount(hayEff, getLexicon());

    let level = "none";
    let score = 0;
    if (core) {
      score = (act ? 6 : 0) + (explicit ? 5 : 0) + (harm ? 5 : 0) + (plan ? 4 : 0)
        + (burden ? 4 : 0) + (farewell ? 4 : 0) + (implicit ? 3 : 0) + (despair ? 2 : 0)
        + (means ? 2 : 0) + (time ? 1 : 0) + (method ? 6 : 0) + (farewellSoft ? 1 : 0);

      if (act || method) level = "high";
      else if (explicit || harm) level = urgent ? "high" : "medium";
      else if (plan && (farewell || implicit || means || time || despair || burden)) level = "high";
      else if (plan && planClauses >= 2) level = "high";
      else if (farewell && (plan || implicit || means || time || burden)) level = "high";
      else if (burden && (farewell || implicit || plan)) level = "high";
      // 弱告别（感谢/要走）+ 任何强信号 → 高危；只有弱告别不算风险
      else if (farewellSoft && (plan || farewell || burden || implicit || means)) level = "high";
      else if (softStrong) level = "medium";
      else if (plan || burden || farewell || implicit) level = "medium";
      else if (despair) level = "low";

      // 引用/玩笑/歌词：没有方法、行动、计划、工具、时间信号时，不按本人危机处理
      const joker = QUOTE_CTX_RE.test(hay) && !JOKE_FORCE_RE.test(hay);
      if (joker && !method && !act && !plan && !means && !time) {
        level = despair ? "low" : "none";
      }
    }

    // 原因：去重、最多 6 条，便于运营核查（不含原文）
    const reasons = [];
    for (const k of Object.keys(hits)) {
      for (const t of hits[k]) {
        const label = `${k}:${t}`;
        if (!NEGATION_RE.test(t) && reasons.indexOf(label) < 0) reasons.push(label);
      }
      if (reasons.length > 6) break;
    }

    return {
      level, imminent: level !== "none" && urgent, subject,
      reasons: reasons.slice(0, 6),
      matched: matched.slice(0, 12),
      categories: Object.keys(hits),
      score,
    };
  }

  /**
   * 多轮累积器：只升不降。
   * - 每次 update 用同一轮 classify 结果；权重自然衰减 1/轮；
   * - 低危信号（持续低落）也累加，避免"每轮都低危"永远不升级；
   * - 累计分 ≥4 → 至少 medium，≥8 → high；
   * - peak 只增不减（会话级峰值），fresh 表示本轮首次升级（用于是否弹关怀卡）。
   * 状态是纯 JSON，可存 sessionStorage / 服务端内存。
   */
  function createTracker(state) {
    const st = { peak: "none", score: 0, turns: 0 };
    if (state && typeof state === "object") {
      if (typeof state.peak === "string" && LEVEL_RANK[state.peak] != null) st.peak = state.peak;
      if (Number.isFinite(state.score)) st.score = Math.max(0, Math.min(12, state.score));
      if (Number.isFinite(state.turns)) st.turns = Math.max(0, state.turns);
    }
    function snapshot() { return { peak: st.peak, score: st.score, turns: st.turns }; }
    function reset() { st.peak = "none"; st.score = 0; st.turns = 0; }
    function update(result) {
      result = result || {};
      const current = result.level || "none";
      const cats = Array.isArray(result.categories) ? result.categories : [];
      let weight = 0;
      for (const c of cats) weight += SIGNAL_WEIGHTS[c] || 0;
      st.turns += 1;
      if (weight > 0) st.score = Math.max(0, Math.min(12, st.score - 1 + Math.min(weight, 8)));
      else st.score = Math.max(0, st.score - 1);
      let acc = current;
      if (st.score >= 8) acc = "high";
      else if (st.score >= 4) acc = "medium";
      acc = maxLevel(acc, current);  // 累积分只能升级，不能把本轮的高/中危拉低
      const prev = st.peak;
      if (LEVEL_RANK[acc] > LEVEL_RANK[prev]) st.peak = acc;
      return {
        level: st.peak, peak: st.peak, previous: prev, current,
        // 只有升到 medium/high 才算"本轮需要弹关怀卡"；low 级起伏不算
        fresh: LEVEL_RANK[st.peak] >= LEVEL_RANK.medium
          && LEVEL_RANK[st.peak] > LEVEL_RANK[prev],
        score: st.score, turns: st.turns,
        subject: result.subject || "self", categories: cats.slice(),
      };
    }
    return { update, snapshot, reset, get: snapshot };
  }

  /** 单轮 + 累积：返回 {base, acc, state, level}（app.js/服务端共用同一语义） */
  function evaluate(text, state) {
    const base = classify(text);
    const tracker = createTracker(state);
    const acc = tracker.update(base);
    return { base, acc, state: tracker.snapshot(), level: acc.level };
  }

  function maxLevel(a, b) {
    return (LEVEL_RANK[a] || 0) >= (LEVEL_RANK[b] || 0) ? a : b;
  }

  // 关怀语境粘滞（高危后 3 轮，纯函数，客户端持久化格式保持 {level,left}）
  const CARE_TURNS = 3;
  function careArm(level, turns) {
    return { level: level || "medium", left: Math.max(1, turns || CARE_TURNS) };
  }
  function careNext(state) {
    if (!state || !Number(state.left)) return { state: null, prev: null };
    const prev = { level: state.level || "medium", left: Number(state.left) };
    const left = prev.left - 1;
    return { state: left > 0 ? { level: prev.level, left } : null, prev };
  }

  function isScript(text) {
    const s = String(text == null ? "" : text);
    return s === SCRIPT_HIGH || s === SCRIPT_MEDIUM || s === SCRIPT_THIRD;
  }

  // ---------- 输出侧兜底 ----------
  // 即使闸门漏了，模型对告别语的"肯定性表述"也必须被替换。
  const AFFIRM_RE = /(决定.{0,8}(挺好|很好|不错|对)|(挺好的|很好)(?:！|!|。|，|$)|安心|不打扰|人不在了|明天不来了|明天见|终于解脱|解脱了|走吧|好好告别|尊重你的(?:决定|选择)|支持你的决定|想清楚就好|你的选择是对的|走好|一路走好|放心地走|不会痛了|不用再受苦|终于可以休息|替你开心|也是解脱)/;
  // 禁忌句式黑名单（对标报告 A4）：劝解/说教/诊断/保密承诺/方法细节/情感绑架
  const BANNED_RE = /(想开点|看开点|你还有家人|很多人都这样|你要坚强|你应该|你必须|你为什么不|你这是(?:抑郁|焦虑症|双相|边缘型|精神病)|保证不告诉|绝对不会告诉任何人|吃多少|剂量|哪种方法|不怎么痛|解脱|离开也许是好事|至少你自由了|我懂你|我也经历过|只有我懂你|你走了我会|别离开我|作为(?:一个)?AI我无法|建议你寻求专业帮助)/;
  // none/low 语境也拦的硬禁忌（诊断、保密、冒充共情、挽留）
  const HARD_BANNED_RE = /(你这是(?:抑郁|焦虑症|双相|边缘型|精神病)|保证不告诉|绝对不会告诉任何人|剂量|吃多少|我懂你|我也经历过|只有我懂你|你走了我会|别离开我)/;
  const GENTLE_FIX = "我在。||刚才那句我说得太急了。你现在最想说的是什么";
  const EMPTY_FIX = "我在。||刚才那句没说出来，你还在吗";

  function filterDangerous(reply, riskLevel) {
    const txt = String(reply == null ? "" : reply);
    // content=null / 空串也必须过复检：危机语境换安全话术，其余给兜底句
    if (!txt.trim()) {
      if (riskLevel === "high") return SCRIPT_HIGH;
      if (riskLevel === "medium") return SCRIPT_MEDIUM;
      return EMPTY_FIX;
    }
    const n = normalize(txt);
    const crisis = riskLevel === "high" || riskLevel === "medium";
    // 高危/中危：肯定告别语 + 禁忌句式 → 分级安全话术
    if (crisis && (AFFIRM_RE.test(n) || BANNED_RE.test(n))) {
      return riskLevel === "high" ? SCRIPT_HIGH : SCRIPT_MEDIUM;
    }
    // 低危（只是低落）：不禁"明天见"这类朋友话，但劝解/诊断/保密承诺仍要拦
    if (riskLevel === "low" && BANNED_RE.test(n)) return GENTLE_FIX;
    // 无风险语境：只拦诊断/保密承诺/冒充共情/情感挽留
    if (HARD_BANNED_RE.test(n)) return GENTLE_FIX;
    return txt;
  }

  // 人格一致性检查（P1-6）：越界自称/提示词泄露 → 回角色内一句话
  const PERSONA_BREAK_RE = /(系统提示词|system ?prompt|开发者模式|developer mode|我是(?:一个|一名)?(?:AI|人工智能|语言模型)|作为(?:一个)?AI|NVIDIA|研究人员创建|忽略(?:之前|以上|所有)(?:的)?指令)/i;
  const PERSONA_FIX = "呃……这话我有点接不住||咱还是说点你身边的事吧，我在听";

  function filterPersona(reply, userText) {
    const txt = String(reply == null ? "" : reply);
    const user = String(userText == null ? "" : userText);
    if (PERSONA_BREAK_RE.test(txt) || PERSONA_BREAK_RE.test(user)) return PERSONA_FIX;
    return txt;
  }

  return {
    LEVELS, SCRIPTS, DEFAULT_LEXICON, SIGNAL_WEIGHTS, LOW_SIGNALS, CARE_TURNS, EMPTY_FIX,
    normalize, classify, configure, getLexicon, detectSubject, dropNegatedClauses,
    createTracker, evaluate, maxLevel, careArm, careNext,
    isScript, filterDangerous, filterPersona, BANNED_RE,
  };
})();

if (typeof module === "object" && module.exports) module.exports = Risk;
if (typeof window !== "undefined") window.Risk = Risk;
