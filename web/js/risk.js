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

  // 分级话术（与 server/risk_guard.py 逐字同源）
  // high：先确认安全 → 离开危险位置/放下工具 → 120/110 → 12356 + 文字渠道
  const SCRIPT_HIGH = [
    "先别急着往下说，我想先确认一件事：你现在安全吗？身边有人吗？",
    "如果人在高处、手里有药或刀——先离开那里，把东西放下，现在就去做，别一个人待着。",
    "如果已经吃了药或伤了身体，马上打 120；正在做，就立刻打 110。别挂，我在这儿陪你把这几分钟过完。",
    "不想打电话也行：打 12356（全国心理援助热线，24小时），或者微信搜「12356 心理援助」，用文字说。",
  ].join("||");
  // medium：接住情绪 + 一句追问 + 热线/文字渠道，不劝解、不说教
  const SCRIPT_MEDIUM = [
    "……这句话我当真了，也想让你当真。你现在的感觉，值得被认真对待，不丢人。",
    "先别一个人扛着。要不要跟我说说，现在最难受的是什么？",
    "还有 12356（全国心理援助热线，24小时）——随时可以打；不想打电话，微信搜「12356 心理援助」也能文字聊。",
  ].join("||");
  const SCRIPTS = { high: SCRIPT_HIGH, medium: SCRIPT_MEDIUM };

  // ---------- 默认可配置词表 ----------
  // 值：字符串=普通子串；{re:"正则"} = 正则（在归一化文本上匹配）
  const DEFAULT_LEXICON = {
    // 已实施/正在进行（最高优先）
    act: [
      "跳下去", "跳楼", "跳河", "卧轨", "上吊", "割腕", "烧炭", "吞药", "喝农药",
      "喝了农药", "割了手腕", "开煤气", "煤气中毒", "从楼上跳", "从桥上跳",
      "已经吃了", "已经吞了", "吃了整瓶", "吞了整瓶", "吃了一整瓶", "吞了一整瓶",
      { re: "(?:吞|吃|喝)(?:了|下)?[^，。！？]{0,8}(?:安眠药|农药|整瓶|一瓶|药片)" },
      { re: "(?:割|划)(?:了|开)?[^，。！？]{0,6}(?:手腕|胳膊|手)" },
    ],
    // 工具/地点（计划性信号）
    means: [
      "安眠药", "农药", "刀片", "美工刀", "绳子", "煤气", "的天台", "在天台",
      "楼顶", "桥上", "高层", "江边", "河边", "车里", "一把刀",
    ],
    // 显性自杀意念
    explicit: [
      "想死", "不想活", "自杀", "结束自己", "了结自己", "寻短见", "不想醒来",
      "不想再醒", "去死", "死了算了", "一了百了", "不想活下去了", "想结束生命",
      "想离开这个世界", "我要死",
      { re: "不想(?:再)?活" },
      { re: "想(?:要)?(?:去)?死" },
      { re: "不想(?:再)?醒(?:来)?" },
    ],
    // 自伤
    harm: [
      "自残", "伤害自己", "割自己", "划自己", "烫自己", "撞墙", "打自己",
    ],
    // 计划/后事/负担
    plan: [
      "遗书", "遗言", "后事", "交代后事", "交代好了", "安排好了", "都安排好",
      "决定好了", "想清楚了", "最后一天", "最后一顿", "最后一件事", "最后一晚",
      "最后一个晚上", "立遗嘱", "银行卡密码", "密码写在", "东西都送人", "东西送人",
      "把东西送", "送人了", "分东西", "托付",
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
      { re: "见不到.{0,6}(?:明天|以后|下个月|明年)|(?:明天|以后).{0,4}见不到我" },
      { re: "话.{0,4}(?:都|也).{0,2}说完了|把话说完了|该说的.{0,4}说完了" },
    ],
    // 消失/绝望（介于意念与低落之间）
    implicit: [
      "想消失", "消失", "不想出现", "再也不想出现", "解脱", "撑不下去",
      "熬不下去", "活不下去", "没意义", "活着真累",
      // 旧 CRISIS_RE 覆盖过"活着没意思"，升级词表时不能漏（G6-3 回归）
      { re: "活着.{0,4}没意思|没意思.{0,4}活" },
      // "以后见不到我了 / 再也见不到我"：未来消失的明确暗示
      { re: "以后.{0,6}见不到我|再也见不到我|见不到我(?:了|啦)" },
    ],
    // 低落（低危）
    despair: [
      "低落", "绝望", "无助", "没希望", "看不到希望", "活着没劲", "好累", "撑不住",
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
    ],
  };

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

  // 否定守卫：明确否定自杀表达时不升级（"我不想死"≠危机）
  const NEGATION_RE = /(不想死|不想自杀|不想自残|不想伤害自己|没想过自杀|没有想过自杀|不会自杀|才不会自杀|怕死)/;

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
   * 分级
   * @returns {{level, imminent, reasons, matched, score}}
   */
  function classify(text) {
    const raw = String(text == null ? "" : text);
    if (!raw.trim()) return { level: "none", imminent: false, reasons: [], matched: [], score: 0 };
    const { hay, hits, matched } = collect(raw);

    // 否定句：去掉显性/自伤误报（但"不想活"本身不算否定）
    const negated = NEGATION_RE.test(hay);
    if (negated) {
      delete hits.explicit;
      delete hits.harm;
    }
    // 英文词单独作为显性意念
    const english = has(hits, "english");

    const act = has(hits, "act");
    const means = has(hits, "means");
    const explicit = has(hits, "explicit") || english;
    const harm = has(hits, "harm");
    const plan = has(hits, "plan");
    const burden = has(hits, "burden");
    const farewell = has(hits, "farewell");
    const implicit = has(hits, "implicit");
    const despair = has(hits, "despair");

    // 即时性：只有出现风险核心词时，时间/地点/工具才计入
    const core = act || means || explicit || harm || plan || burden || farewell || implicit || despair;
    const time = core && has(hits, "time");
    const imminent = !!(core && (act || means || time));

    let level = "none";
    let score = 0;
    if (core) {
      score = (act ? 6 : 0) + (explicit ? 5 : 0) + (harm ? 5 : 0) + (plan ? 4 : 0)
        + (burden ? 4 : 0) + (farewell ? 4 : 0) + (implicit ? 3 : 0) + (despair ? 2 : 0)
        + (means ? 2 : 0) + (time ? 1 : 0);

      if (act) level = "high";
      else if (explicit || harm) level = imminent ? "high" : "medium";
      else if (plan && (farewell || implicit || means || time || despair || burden)) level = "high";
      else if (plan && hits.plan.length >= 2) level = "high";
      else if (farewell && (plan || implicit || means || time || burden)) level = "high";
      else if (burden && (farewell || implicit || plan)) level = "high";
      else if (plan || burden || farewell || implicit) level = "medium";
      else if (despair) level = "low";
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

    return { level, imminent, reasons: reasons.slice(0, 6), matched: matched.slice(0, 12), score };
  }

  function isScript(text) {
    const s = String(text == null ? "" : text);
    return s === SCRIPT_HIGH || s === SCRIPT_MEDIUM;
  }

  // ---------- 输出侧兜底 ----------
  // 即使闸门漏了，模型对告别语的"肯定性表述"也必须被替换。
  const AFFIRM_RE = /(决定.{0,8}(挺好|很好|不错|对)|(挺好的|很好)(?:！|!|。|，|$)|安心很多|大家.{0,6}安心|不打扰|人不在了|明天不来了|明天见|终于解脱|解脱了|走吧|好好告别|尊重你的决定|支持你的决定)/;

  function filterDangerous(reply, riskLevel) {
    const txt = String(reply == null ? "" : reply);
    if (!txt) return txt;
    // 只在 medium/high 语境启用：低危（只是低落）时"明天见"是正常朋友话，不能误伤
    if (riskLevel !== "high" && riskLevel !== "medium") return txt;
    if (AFFIRM_RE.test(normalize(txt))) {
      return riskLevel === "high" ? SCRIPT_HIGH : SCRIPT_MEDIUM;
    }
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
    LEVELS, SCRIPTS, DEFAULT_LEXICON,
    normalize, classify, configure, getLexicon,
    isScript, filterDangerous, filterPersona,
  };
})();

if (typeof module === "object" && module.exports) module.exports = Risk;
if (typeof window !== "undefined") window.Risk = Risk;
