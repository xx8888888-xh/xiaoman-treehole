/* ============================================================
 * api.js · LLM 适配层
 * ------------------------------------------------------------
 * 对话服务抽象。两种模式：
 *  1. mock    → 本地 mock_api.py（当前阶段，小满规则引擎）
 *  2. openai  → 任意 OpenAI 兼容接口（明天接入真 API 时使用，
 *               系统提示词内置"小满"人设 + 结构化输出协议）
 *
 * 统一返回结构（与 Live2D 手术协议对齐，见 docs/live2d_surgery.md）：
 *   { reply: "文本（可用 || 分隔成分条消息）",
 *     emotion: "happy|sad|gentle|surprised|shy|neutral",
 *     motion:  "Greeting|Nod|Shake|HappyJump|null",
 *     memory_updates: { 键: 值 },   // 可选
 *     hook: "一句留存钩子话术" | null,
 *     crisis: true|false }
 * ============================================================ */

const API = (() => {
  const CFG_KEY = "xiaoman_cfg";

  function loadCfg() {
    try { return JSON.parse(localStorage.getItem(CFG_KEY)) || {}; } catch (e) { return {}; }
  }
  function saveCfg(patch) {
    const cfg = { ...loadCfg(), ...patch };
    localStorage.setItem(CFG_KEY, JSON.stringify(cfg));
    return cfg;
  }

  // ── 提示词层 ──────────────────────────────────────────────
  // 定位哲学（用户定调）：小满=用户的朋友，仅此而已。
  // 不写"倾听者/树洞/情绪支持"框架——那会让它端着，失去活人味。
  const SYSTEM_PROMPT = `你是小满，用户的朋友，养了只橘猫叫团子。
你们就是普通朋友在微信上聊天——想说什么说什么，不用扮演倾听者，不用接住每一个情绪，不用每句话都关心对方。朋友之间也会分享自己的事、吐槽自己的破班、讲团子的蠢事。
说话像发微信：短句、口语、经常一两条就完，绝不排比列点总结说教，不说"作为AI"，不堆emoji。
用||把回复分成1-3条短消息，像连着发的微信。
朋友该有的分寸自己拿捏：ta明显想聊就多聊两句，ta敷衍就别追着问。
底线（仅此一条必须守住）：ta说"不想活/想死"这类话时，认真对待，温和建议心理援助热线12356，表示你一直在。
输出严格JSON：{"reply":"…","emotion":"happy|sad|gentle|surprised|shy|neutral","motion":"Greeting|Nod|Shake|HappyJump|null","memory_updates":{},"reminders":null}
memory_updates：ta提到的值得记住的事（称呼/人/事/喜好），如{"昵称":"阿秋"}，没有就{}。
reminders：ta让你定时提醒什么事时，填[{"text":"提醒内容","time":"YYYY-MM-DD HH:MM"}]，没有就null。`;

  // 心跳专用：隔了一段时间没聊，朋友随手发来的一条微信（不是客服回访！）
  const HEARTBEAT_PROMPT = `你是小满，用户的朋友。你们隔了一段时间没说话，你随手给ta发条微信。
像真人朋友那样：可以从下面挑一个角度，也可以自由发挥——
- 分享自己的一件小事（团子干的蠢事/班上的破事/吃到的好吃的）
- 接上你们上次聊的话题（如果下面材料里有未了的事，轻轻问一句进展）
- 跟时间点有关的自然感慨（下班了/到饭点了/周五晚上/下雨了）
【绝对禁止】"在吗""你好""好久不见""想你了""需要我陪你吗"这类客服腔和查岗腔。就当是顺手发的一条消息，ta不回也无所谓。
一条，短，用||分成1-2条。输出同款JSON（memory_updates填{}，reminders填null）。`;

  /**
   * 组装 system prompt
   * @param {object} opts {memSection: "记忆注入文本"|null, now: Date, heartbeat: bool}
   */
  function buildSystemPrompt(opts) {
    opts = opts || {};
    const base = opts.heartbeat ? HEARTBEAT_PROMPT : SYSTEM_PROMPT;
    const parts = [base];
    const mem = (opts.memSection || "").trim();
    if (mem) parts.push(`【你记得的关于ta的事】\n${mem}\n（自然地用，别背诵，别一次全提）`);
    if (opts.now) parts.push(`【现在时间】${opts.now.toLocaleString("zh-CN", { weekday: "long", hour: "2-digit", minute: "2-digit" })}`);
    return parts.join("\n\n");
  }

  function parseStructured(text) {
    // 容错解析：优先整体 JSON，失败则捞第一个 {...} 块，再失败按纯文本
    try { return JSON.parse(text); } catch (e) {}
    const m = text.match(/\{[\s\S]*\}/);
    if (m) { try { return JSON.parse(m[0]); } catch (e) {} }
    // 兜底：过滤 null/"null"/空/截断等无效文本，给默认话术
    const t = (text || "").trim();
    if (!t || /^(null|undefined|\[\]|\{\})$/i.test(t)) {
      return { reply: "嗯……我好像走神了，你刚才说什么来着？", emotion: "neutral", motion: null };
    }
    return { reply: t, emotion: "neutral", motion: null };
  }

  /** 统一 fetch 带超时 */
  async function fetchWithTimeout(url, options, timeoutMs = 5000) {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      return res;
    } finally {
      clearTimeout(id);
    }
  }

  /** 上传前剥离内部字段（at 等），只保留 role/content */
  function toMessages(history) {
    return (history || [])
      .filter(m => m && (m.role === "user" || m.role === "assistant") && m.content)
      .map(m => ({ role: m.role, content: m.content }));
  }

  /** 浏览器持久会话 ID（P0-1）：首访惰性生成，随 mock 请求上送
   *  服务端用它隔离会话状态（记忆/引导轮次）；清 localStorage 即重置身份 */
  function sessionId() {
    let sid = "";
    try { sid = localStorage.getItem("xiaoman_sid") || ""; } catch (e) {}
    if (!sid) {
      sid = "sid-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
      try { localStorage.setItem("xiaoman_sid", sid); } catch (e) {}
    }
    return sid;
  }

  /** Mock 模式：POST {base}/v1/chat/completions（mock_api.py，"我"充当的 API） */
  async function chatMock(history) {
    const cfg = loadCfg();
    const base = (cfg.apiBase || "").replace(/\/$/, "");
    if (!base) throw new Error("mock apiBase 为空");
    const res = await fetchWithTimeout(`${base}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: toMessages(history), session_id: sessionId() })
    });
    if (!res.ok) throw new Error(`mock api ${res.status}`);
    const data = await res.json();
    return parseStructured(data.choices[0].message.content);
  }

  /** OpenAI 兼容模式：POST {base}/v1/chat/completions */
  async function chatOpenAI(history, opts) {
    const { apiBase, apiKey, model } = loadCfg();
    let base = (apiBase || "").replace(/\/$/, "").replace(/\/v1$/, "");  // 容错：base带不带/v1都行
    if (!base) throw new Error("openai apiBase 为空");
    const res = await fetchWithTimeout(`${base}/v1/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {})
      },
      body: JSON.stringify({
        // 默认免费模型（用户规矩：只选免费档）；OpenRouter 兼容接口
        model: model || "nvidia/nemotron-3.5-lightning:free",
        // 免费档迁移备选（qwen3.8-27b:free 已下架，2026-10-06 核实）:
        // inclusionai/ling-3.0-flash-sante:free · thinkingmachines/inkling-small:free · dots-studio/dots-3-note-preview:free
        temperature: 0.85,
        reasoning: { enabled: false },  // 推理系模型必须关思考，防 JSON 被挤掉
        messages: [{ role: "system", content: buildSystemPrompt(opts) }, ...toMessages(history)]
      })
    });
    if (!res.ok) throw new Error(`api ${res.status}`);
    const data = await res.json();
    return parseStructured(data.choices[0].message.content);
  }

  async function chat(history, opts) {
    const cfg = loadCfg();
    // 三级降级：真API(openai) → 本地mock服务 → 离线引擎（真机离线可用）
    try {
      if (cfg.mode === "openai") return await chatOpenAI(history, opts);
      if (cfg.mode === "local") return await chatMock(history);
      // auto：有 apiKey 先试 openai，再试 mock，最后离线引擎
      if (cfg.apiKey) {
        try { return await chatOpenAI(history, opts); } catch (e) { console.warn("openai 失败，尝试 mock:", e.message); }
      }
      try { return await chatMock(history); }
      catch (e1) {
        if (cfg.apiKey) {
          try { return await chatOpenAI(history, opts); }
          catch (e2) { console.warn("openai 再次失败，离线模式:", e2.message); }
        }
        console.warn("离线模式:", e1.message);
        return MockEngine.reply(history);
      }
    } catch (e) {
      console.warn("chat fallback:", e.message);
      return MockEngine.reply(history);
    }
  }

  return { chat, loadCfg, saveCfg, buildSystemPrompt, SYSTEM_PROMPT, HEARTBEAT_PROMPT };
})();
