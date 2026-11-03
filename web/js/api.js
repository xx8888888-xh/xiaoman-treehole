/* ============================================================
 * api.js · LLM 适配层
 * ------------------------------------------------------------
 * 三种模式（设置页可切换）：
 *   mock    → 本地 mock_api.py（规则引擎，零依赖）
 *   openai  → 任意 OpenAI 兼容接口；无本地 key 时走服务端代理
 *             （key 只存在于服务端环境变量，浏览器拿不到）
 *   offline → 纯离线规则引擎（MockEngine），完全不联网
 *
 * 降级链：真 API（Kilo 免 key / OpenRouter 带 key）→ 本地 mock → 离线引擎。
 * 429/402 视为配额限额：先降一级（mock），mock 也不可用才上抛，避免人格偷换。
 *
 * 统一返回结构（见 docs/live2d_surgery.md）：
 *   { reply, emotion, motion, memory_updates, hook, crisis }
 * ============================================================ */

// biome-ignore lint/correctness/noUnusedVariables: API 作为全局对象被 index.html 其余脚本消费
const API = (() => {
  const CFG_KEY = "xiaoman_cfg";
  const SID_KEY = "xiaoman_sid";
  const MOCK_DEFAULT = "http://127.0.0.1:8902";

  // 密钥只驻留内存，不写 localStorage；刷新页面需重新输入（或由服务端环境变量提供）
  let memKey = "";
  let serverCfg = null;

  function rawCfg() {
    try { return JSON.parse(localStorage.getItem(CFG_KEY)) || {}; } catch { return {}; }
  }

  function loadCfg() {
    const raw = rawCfg();
    // 兼容旧版本：曾把 apiKey 明文存在 localStorage —— 迁移到内存并立刻抹掉
    if (raw.apiKey) {
      memKey = String(raw.apiKey);
      try {
        localStorage.setItem(CFG_KEY, JSON.stringify(XMConfig.stripSecret(raw)));
      } catch {}
    }
    const cfg = XMConfig.normalizeCfg(raw);
    cfg.apiKey = memKey;   // 仅本次调用可见，saveCfg 不会持久化它
    return cfg;
  }

  function saveCfg(patch) {
    const incoming = Object.assign({}, patch || {});
    if ("apiKey" in incoming) {
      memKey = String(incoming.apiKey || "");
      delete incoming.apiKey;
    }
    const cfg = XMConfig.normalizeCfg(incoming, rawCfg());
    try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); } catch {}
    return cfg;
  }

  function getApiKey() { return memKey; }
  function setApiKey(key) { memKey = String(key || ""); }

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
    try { return JSON.parse(text); } catch {}
    const m = (text || "").match(/\{[\s\S]*\}/);
    if (m) { try { return JSON.parse(m[0]); } catch {} }
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
      return await fetch(url, Object.assign({}, options, { signal: controller.signal }));
    } finally {
      clearTimeout(id);
    }
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  /** 上传前剥离内部字段（at 等），只保留 role/content */
  function toMessages(history) {
    return (history || [])
      .filter(m => m && (m.role === "user" || m.role === "assistant") && m.content)
      .map(m => ({ role: m.role, content: m.content }));
  }

  /** 浏览器持久会话 ID：首访惰性生成；IndexedDB 与服务端落盘共用同一个 sid */
  function sessionId() {
    let sid = "";
    try { sid = localStorage.getItem(SID_KEY) || ""; } catch {}
    if (!sid) {
      sid = `sid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      try { localStorage.setItem(SID_KEY, sid); } catch {}
    }
    return sid;
  }

  function mockBase(cfg) {
    return cfg?.mockBase || MOCK_DEFAULT;
  }

  function tagError(e, status, kindHint) {
    const err = (e instanceof Error) ? e : new Error(String(e));
    if (!err.kind) {
      if (kindHint) err.kind = kindHint;
      else if (status === 429 || status === 402) err.kind = "limit";
      else if (status != null) err.kind = "http";
      else if (err.name === "AbortError") err.kind = "timeout";
      else err.kind = "network";
    }
    if (status != null) err.status = status;
    return err;
  }

  /** 读服务端 /api/config（非敏感：mode/model/apiBase/provider，不含 key） */
  async function serverConfig(force) {
    if (serverCfg && !force) return serverCfg;
    const cfg = loadCfg();
    try {
      const res = await fetchWithTimeout(`${mockBase(cfg)}/api/config`, {}, 4000);
      serverCfg = res.ok ? await res.json() : null;
    } catch {
      serverCfg = null;
    }
    return serverCfg;
  }

  /** 首启引导：服务端 env 作为默认值（不覆盖用户已保存的配置） */
  async function bootstrap() {
    const info = await serverConfig(true);
    if (!info) return null;
    const raw = rawCfg();
    const patch = {};
    if (!raw.mode && info.mode) patch.mode = info.mode;
    if (!raw.apiBase && info.apiBase) patch.apiBase = info.apiBase;
    if (!raw.model && info.model) patch.model = info.model;
    if (!raw.ttsBase && info.ttsBase) patch.ttsBase = info.ttsBase;
    if (Object.keys(patch).length) saveCfg(patch);
    return info;
  }

  /** Mock 模式：POST {mockBase}/v1/chat/completions */
  async function chatMock(history) {
    const cfg = loadCfg();
    const base = mockBase(cfg);
    let res;
    try {
      res = await fetchWithTimeout(`${base}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: toMessages(history), session_id: sessionId() })
      }, 15000);
    } catch (e) { throw tagError(e); }
    if (!res.ok) throw tagError(new Error(`mock api ${res.status}`), res.status);
    const data = await res.json();
    return parseStructured(data.choices[0].message.content);
  }

  function httpHint(status) {
    if (status === 429 || status === 402) return "免费池限流/额度用尽（429/402），可稍后重试或切换 mock";
    if (status === 401) return "Key 无效或未授权（401）";
    if (status === 403) return "无权限（403）";
    if (status === 404) return "接口或模型不存在（404）";
    return `HTTP ${status}`;
  }

  /** 服务端代理：key 只在服务端，浏览器不接触 */
  async function chatViaProxy(history, opts, cfg) {
    const body = {
      mode: "openai",
      model: cfg.model,
      session_id: sessionId(),
      messages: [{ role: "system", content: buildSystemPrompt(opts) }, ...toMessages(history)]
    };
    let res;
    try {
      res = await fetchWithTimeout(`${mockBase(cfg)}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }, 60000);
    } catch (e) { throw tagError(e); }
    let data = null;
    try { data = await res.json(); } catch {}
    if (!res.ok) {
      // 服务端已对 429 做过一次退避重试，这里直接按类型上抛
      throw tagError(new Error(data?.error || `proxy api ${res.status}`),
                     data?.status || res.status, data?.kind);
    }
    if (data?.offline) throw tagError(new Error("服务端为 offline 模式"), null, "config");
    if (data?.error) throw tagError(new Error(data.error), data.status || null, data.kind);
    if (!data?.content) throw new Error("代理返回 content 为空");
    return parseStructured(data.content);
  }

  /** 直连 OpenAI 兼容接口（仅当用户在本页输入了 key） */
  async function chatDirect(history, opts, cfg) {
    const key = cfg.apiKey;
    const base = XMConfig.apiRoot(cfg.apiBase);
    if (!base) throw tagError(new Error("openai apiBase 为空"), null, "config");
    const body = {
      model: cfg.model,
      temperature: 0.85,
      max_tokens: 600,                 // 推理模型开思考时 <512 会被 reasoning 吃光
      reasoning: { enabled: false },
      messages: [{ role: "system", content: buildSystemPrompt(opts) }, ...toMessages(history)]
    };
    const doFetch = () => fetchWithTimeout(`${base}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify(body)
    }, 45000);

    let res;
    try { res = await doFetch(); } catch (e) { throw tagError(e); }
    if (res.status === 429) {
      // 免费池限流：退避重试一次
      await sleep(1500);
      try { res = await doFetch(); } catch (e) { throw tagError(e); }
    }
    if (!res.ok) throw tagError(new Error(`api ${res.status}: ${httpHint(res.status)}`), res.status);
    const data = await res.json();
    const content = data && data.choices && data.choices[0] && data.choices[0].message
      ? data.choices[0].message.content : null;
    if (content == null) throw new Error("模型返回 content 为 null（可能被 reasoning 吃光）");
    return parseStructured(content);
  }

  async function chatOpenAI(history, opts, cfg) {
    if (cfg.apiKey) return chatDirect(history, opts, cfg);
    return chatViaProxy(history, opts, cfg);
  }

  async function chat(history, opts) {
    const cfg = loadCfg();
    // 降级链：openai（Kilo 免 key / OpenRouter key）→ mock → 离线引擎
    if (cfg.mode === "offline") return MockEngine.reply(history);
    if (cfg.mode === "mock") {
      try { return await chatMock(history); }
      catch (e) {
        console.warn("mock 不可用，转离线引擎:", e.message);
        return MockEngine.reply(history);
      }
    }
    try {
      return await chatOpenAI(history, opts, cfg);
    } catch (e) {
      if (e.kind === "limit") {
        // 限流是确定性的：先降一级到本地 mock；mock 也不可用才上抛排队 UX
        try { return await chatMock(history); }
        catch (e1) {
          console.warn("限流且 mock 不可用:", e1.message);
          throw e;
        }
      }
      console.warn("openai 失败，降级 mock:", e.message);
      try { return await chatMock(history); }
      catch (e2) {
        console.warn("mock 失败，降级离线引擎:", e2.message);
        return MockEngine.reply(history);
      }
    }
  }

  /**
   * 测试连接：设置页按钮调用。
   * 返回 { ok, mode, models?, note?, error?, status?, provider? }
   */
  async function testConnection(override) {
    const cfg = loadCfg();
    const o = override || {};
    const mode = XMConfig.normalizeMode(o.mode || cfg.mode);
    const base = XMConfig.apiRoot(o.apiBase != null ? o.apiBase : cfg.apiBase);
    const key = o.apiKey != null ? o.apiKey : memKey;
    const mb = mockBase(cfg);

    if (mode === "offline") {
      return { ok: true, mode, models: [], note: "纯离线规则引擎可用（不联网）" };
    }
    if (mode === "mock") {
      try {
        const res = await fetchWithTimeout(`${mb}/health`, {}, 4000);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const h = await res.json();
        return { ok: true, mode, models: ["xiaoman-mock"],
                 note: `本地 mock 在线：${mb}（mode=${h.mode || "mock"}）` };
      } catch (e) {
        return { ok: false, mode, error: `本地 mock 不可达：${e.message}` };
      }
    }

    // openai：有 key → 直连测 /v1/models；无 key → 服务端代理（Kilo 免 key）
    if (key && base) {
      try {
        const res = await fetchWithTimeout(`${base}/v1/models`, {
          headers: { Authorization: `Bearer ${key}` }
        }, 12000);
        if (!res.ok) return { ok: false, mode, status: res.status, error: httpHint(res.status) };
        const j = await res.json();
        const ids = ((j && j.data) || []).map(m => m && m.id).filter(Boolean);
        return { ok: true, mode, provider: "direct", models: ids.slice(0, 80),
                 note: `直连 ${base}，共 ${ids.length} 个模型` };
      } catch (e) {
        return { ok: false, mode, error: `直连失败：${e.message}` };
      }
    }

    try {
      const res = await fetchWithTimeout(`${mb}/api/models`, {}, 20000);
      const j = await res.json();
      return Object.assign({ mode, source: "server" }, j);
    } catch (e) {
      return { ok: false, mode, error: `服务端不可达：${e.message}` };
    }
  }

  return {
    chat, loadCfg, saveCfg, buildSystemPrompt, SYSTEM_PROMPT, HEARTBEAT_PROMPT,
    sessionId, bootstrap, serverConfig, testConnection,
    getApiKey, setApiKey, parseStructured
  };
})();
