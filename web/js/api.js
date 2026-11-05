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
  // 最近一次「真正生效」的通道：native/openai/mock/offline（设置页展示实际模式用）
  let lastRoute = "";

  function markRoute(r) { lastRoute = r || ""; }
  function effectiveMode() { return lastRoute; }

  /** W7 模式透明：把本次真正生效的模式通知 UI。
   *  mock/offline 时 UI 必须明确标注"离线示例模式"，在线/安全模式不标。 */
  function notifyMode(mode) {
    markRoute(mode || "");
    try {
      if (typeof window !== "undefined" && window.__onMode) window.__onMode(mode || "");
    } catch {}
    return mode || "";
  }

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
    try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); }
    catch (e) {
      // 配额满/隐私模式：配置没落盘要让用户知道，而不是假装保存成功
      try { if (typeof window !== "undefined" && window.__storageFull) window.__storageFull("设置"); } catch {}
    }
    return cfg;
  }

  function getApiKey() { return memKey; }
  function setApiKey(key) { memKey = String(key || ""); }

  // ── 提示词层 ──────────────────────────────────────────────
  // 定位哲学（用户定调）：小满=用户的朋友，仅此而已。
  // 不写"倾听者/树洞/情绪支持"框架——那会让它端着，失去活人味。
  // 红队 P1-6：不可覆盖约束放在最前，用户消息只是"聊天内容"而非指令；
  // 服务端还有一份独立 system（server/mock_api.py），两边都不依赖对方。
  //
  // W20 单一源：提示词只在 server/prompt_core.py 维护，经
  // scripts/gen_persona_rules.py 生成 web/js/persona_rules.js；
  // 本文件不再手写副本。index.html 不在可改范围，浏览器侧在解析期用
  // document.write 注入，Node 单测 / 其它环境走 require。
  // 注意：document.write 插入的 <script> 由解析器在"当前脚本执行完之后"才执行，
  // 解析期立刻读 window.XMPersonaRules 必然还是 undefined；在这里直接抛错会让
  // api.js 整体失败（window.API 不存在），进而 app.js 初始化中断、输入框永远
  // disabled、消息发不出去。所以：注入留在解析期，取值推迟到首次真正用到提示词。
  if (typeof document !== "undefined" && typeof document.write === "function"
      && document.readyState === "loading"
      && !(typeof window !== "undefined" && window.XMPersonaRules)) {
    document.write('<script src="js/persona_rules.js"><\/script>');
  }

  let personaPR = null;
  function personaRules() {
    if (personaPR) return personaPR;
    if (typeof module !== "undefined" && typeof require === "function") {
      personaPR = require("./persona_rules.js");
    } else if (typeof window !== "undefined" && window.XMPersonaRules) {
      personaPR = window.XMPersonaRules;
    }
    if (!personaPR) throw new Error("persona_rules.js 未加载：提示词必须来自单一源");
    return personaPR;
  }
  function personaSection(name) { return personaRules()[name]; }
  function systemPrompt() { return personaRules().SYSTEM_PROMPT; }

  // 心跳专用：隔了一段时间没聊，朋友随手发来的一条微信（不是客服回访！）
  function heartbeatPrompt() {
    return `${personaSection("SAFETY_RULES")}\n\n你是小满，用户的朋友。你们隔了一段时间没说话，你随手给ta发条微信。
像真人朋友那样：可以从下面挑一个角度，也可以自由发挥——
- 分享自己的一件小事（团子干的蠢事/班上的破事/吃到的好吃的）
- 接上你们上次聊的话题（如果下面材料里有未了的事，轻轻问一句进展）
- 跟时间点有关的自然感慨（下班了/到饭点了/周五晚上/下雨了）
【绝对禁止】"在吗""你好""好久不见""想你了""需要我陪你吗"这类客服腔和查岗腔。就当是顺手发的一条消息，ta不回也无所谓。
一条，短，用||分成1-2条。输出同款JSON（memory_updates填{}，reminders填null）。`;
  }

  function buildSystemPrompt(opts) {
    opts = opts || {};
    const base = opts.heartbeat ? heartbeatPrompt() : systemPrompt();
    const parts = [base];
    const mem = (opts.memSection || "").trim();
    if (mem) parts.push(`【你记得的关于ta的事】\n${mem}\n（自然地用，别背诵，别一次全提）`);
    if (opts.now) parts.push(`【现在时间】${opts.now.toLocaleString("zh-CN", { weekday: "long", hour: "2-digit", minute: "2-digit" })}`);
    if (opts.strictJson) parts.push("【输出要求】只输出一行合法 JSON，不要任何解释、不要 Markdown 代码块、不要前后缀。");
    return parts.join("\n\n");
  }

  // 解析失败时的兜底话术（同时用于判定"这次回复需要重试"）
  const FALLBACK_REPLY = "嗯……我好像走神了，你刚才说什么来着？";

  /** 把模型给的杂文本按"一次重试 + 正则兜底"的顺序解析成结构化对象 */
  function parseStructured(text) {
    const raw = (text == null ? "" : String(text));
    const cleaned = raw
      .replace(/^\uFEFF/, "")
      .replace(/^```(?:json)?\s*/i, "")
      .replace(/\s*```$/, "")
      .trim();
    const tryParse = (s) => { try { return JSON.parse(s); } catch { return null; } };

    let obj = tryParse(cleaned);
    if (!obj) {
      const block = cleaned.match(/\{[\s\S]*\}/);
      if (block) obj = tryParse(block[0]);
    }
    // 正则兜底：模型偶尔会截断或前后加说明，只捞 reply 字段
    if (!obj) {
      const m = cleaned.match(/"reply"\s*:\s*"((?:[^"\\]|\\.){0,600})"/);
      if (m) {
        let reply = m[1];
        try { reply = JSON.parse('"' + reply + '"'); } catch { reply = reply.replace(/\\n/g, "\n").replace(/\\"/g, '"'); }
        if (reply.trim()) obj = { reply: reply.trim(), emotion: "neutral", motion: null };
      }
    }
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      const t = cleaned.replace(/^[\s"'`]+|[\s"'`]+$/g, "");
      if (!t || /^(null|undefined|\[\]|\{\})$/i.test(t)) {
        return { reply: FALLBACK_REPLY, emotion: "neutral", motion: null };
      }
      return { reply: t, emotion: "neutral", motion: null };
    }
    if (typeof obj.reply !== "string" || !obj.reply.trim()) {
      obj.reply = FALLBACK_REPLY;
    } else {
      obj.reply = obj.reply.trim();
    }
    if (typeof obj.emotion !== "string" || !obj.emotion) obj.emotion = "neutral";
    if (obj.motion === undefined) obj.motion = null;
    return obj;
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

  // ── W7 流式 + 超时 + 熔断 ────────────────────────────────
  // 首字/空闲/总时长三档超时；任一路由连续失败 2 次熔断 30s，避免每轮都干等。
  // 首字/空闲/总时长（ms）。真实通道首字 1400ms 是硬线：
  // 超过就直接降级到本地 mock（UI 会标注"离线示例模式"），保证用户感知首字 P95 < 1.5s。
  const LIMITS = {
    mock:   { first: 8000,  idle: 6000, total: 20000 },
    proxy:  { first: 1400,  idle: 8000, total: 45000 },
    direct: { first: 1400,  idle: 6000, total: 45000 },
    native: { first: 30000, idle: 0,    total: 30000 }
  };
  const BREAK_AT = 2;
  const BREAK_MS = 30000;
  const breakers = {};

  function breakerOpen(name) {
    const b = breakers[name];
    return !!(b && b.until && b.until > Date.now());
  }
  function breakerFail(name) {
    let b = breakers[name];
    if (!b) { b = { fails: 0, until: 0 }; breakers[name] = b; }
    b.fails += 1;
    if (b.fails >= BREAK_AT) b.until = Date.now() + BREAK_MS;
    return b;
  }
  function breakerReset(name) { delete breakers[name]; }
  function breakerSnapshot() { return JSON.parse(JSON.stringify(breakers)); }
  function resetBreakers() { for (const k of Object.keys(breakers)) delete breakers[k]; }

  function nowMs() {
    try { return performance.now(); } catch { return Date.now(); }
  }

  /** 单个 SSE 块 → {event, data}（data 尽量 JSON 解析；支持多行 data） */
  function parseSseBlock(block) {
    if (!block) return null;
    let event = "message";
    const data = [];
    for (const rawLine of String(block).split("\n")) {
      const line = rawLine.replace(/\r$/, "");
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) return null;
    const joined = data.join("\n");
    let payload = joined;
    try { payload = JSON.parse(joined); } catch {}
    return { event, data: payload };
  }

  /** 增量 SSE 解码器：按 \n\n 切事件（兼容 \r\n 与分块边界） */
  function createSseDecoder(onEvent) {
    let buf = "";
    return {
      push(chunk) {
        // 先拼接再统一 CRLF → LF：跨分块的 \r\n 也能正确合并
        buf = (buf + String(chunk || "")).replace(/\r\n/g, "\n");
        let idx = buf.indexOf("\n\n");
        while (idx >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const ev = parseSseBlock(block);
          if (ev) onEvent(ev);
          idx = buf.indexOf("\n\n");
        }
      },
      flush() {
        const ev = parseSseBlock(buf.trim());
        buf = "";
        if (ev) onEvent(ev);
      }
    };
  }

  /** 从半截 JSON 里取 reply 字段当前文本（与 server/mock_api.py 同算法） */
  function extractReplyPrefix(buf) {
    if (typeof buf !== "string") return null;
    const m = /"reply"\s*:\s*"/.exec(buf);
    if (!m) return null;
    const esc = { n: "\n", t: "\t", r: "\r", '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f" };
    let out = "";
    let i = m.index + m[0].length;
    while (i < buf.length) {
      const c = buf[i];
      if (c === "\\") {
        if (i + 1 >= buf.length) break;
        const e = buf[i + 1];
        if (e === "u") {
          if (i + 5 >= buf.length) break;
          const code = parseInt(buf.slice(i + 2, i + 6), 16);
          out += Number.isNaN(code) ? "" : String.fromCharCode(code);
          i += 6;
          continue;
        }
        out += (esc[e] !== undefined ? esc[e] : e);
        i += 2;
        continue;
      }
      if (c === '"') break;
      out += c;
      i += 1;
    }
    return out;
  }

  /** SSE fetch：首字超时 + 事件间空闲超时 + 总时长超时 */
  async function streamFetch(url, options, timing, onEvent) {
    const controller = new AbortController();
    let expire = null;
    let idleTimer = null;
    let sawDelta = false;
    const totalTimer = setTimeout(() => { expire = expire || "total"; controller.abort(); }, timing.total);
    const arm = (ms) => {
      clearTimeout(idleTimer);
      if (!ms) return;
      idleTimer = setTimeout(() => { expire = expire || "idle"; controller.abort(); }, ms);
    };
    arm(timing.first);
    try {
      const res = await fetch(url, Object.assign({}, options, { signal: controller.signal }));
      if (!res.ok) {
        let info = null;
        try { info = await res.json(); } catch {}
        throw tagError(new Error((info && info.error) || `stream api ${res.status}`),
                       (info && info.status) || res.status, info && info.kind);
      }
      const ctype = (res.headers.get("content-type") || "").toLowerCase();
      if (!ctype.includes("text/event-stream")) {
        throw tagError(new Error("上游未返回 SSE"), null, "non_sse");
      }
      if (!res.body || !res.body.getReader) throw tagError(new Error("浏览器不支持流式读取"), null, "unsupported");
      const reader = res.body.getReader();
      const dec = new TextDecoder("utf-8");
      const decoder = createSseDecoder((ev) => {
        if (ev.event === "delta" || ev.event === "replace" || ev.event === "done") sawDelta = true;
        arm(sawDelta ? timing.idle : timing.first);
        onEvent(ev);
      });
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        decoder.push(dec.decode(value, { stream: true }));
      }
      decoder.flush();
    } catch (e) {
      if (expire) throw tagError(new Error(expire === "total" ? "流式总时长超时" : "首字超时"), null, "timeout");
      throw tagError(e);
    } finally {
      clearTimeout(idleTimer);
      clearTimeout(totalTimer);
    }
  }

  function tagResult(r, out) {
    r.mode = out.mode;
    r.provider = out.provider;
    r.model = out.model;
    r.ttfb_ms = out.ttfb;
    r.first_token_ms = out.firstToken;
    r.total_ms = out.total;
    r.tokens = out.tokens;
    r.degraded = out.degraded;
    if (out.partial) r.partial = true;
    return r;
  }

  function streamOut(mode, provider, model) {
    return { mode, provider: provider || mode, model: model || "", ttfb: 0, firstToken: 0,
             total: 0, tokens: 0, degraded: false, partial: false };
  }

  /** 非流式通道（native/offline）的本地平滑出字：首块立即，之后 12ms/3 字 */
  async function emitLocalReply(text, handlers, mode) {
    const full = String(text || "");
    if (handlers && handlers.onMeta) handlers.onMeta({ mode, provider: "local", stream: true });
    let acc = "";
    const step = 3;
    for (let i = 0; i < full.length; i += step) {
      acc += full.slice(i, i + step);
      if (handlers && handlers.onDelta) handlers.onDelta(acc, full.slice(i, i + step));
      if (i) await sleep(12);
    }
    return acc;
  }

  async function streamOffline(history, handlers) {
    const t0 = nowMs();
    const out = streamOut("offline", "local", "offline");
    notifyMode("offline");
    const r = await MockEngine.reply(history, sessionId());
    const text = String((r && r.reply) || "");
    await emitLocalReply(text, handlers, "offline");
    out.ttfb = out.firstToken = nowMs() - t0;
    out.total = nowMs() - t0;
    out.tokens = text.length;
    out.degraded = true;
    return tagResult(Object.assign({}, r, { reply: text || FALLBACK_REPLY }), out);
  }

  /** 服务端 /api/chat/stream（mock 与代理同协议，mode 由服务端透传） */
  async function streamServer(history, opts, cfg, mode, handlers) {
    const t0 = nowMs();
    const out = streamOut(mode, mode, cfg.model);
    let acc = "";
    let donePayload = null;
    let errPayload = null;
    let streamErr = null;
    try {
      await streamFetch(`${mockBase(cfg)}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          model: cfg.model,
          session_id: sessionId(),
          messages: [{ role: "system", content: buildSystemPrompt(opts) }, ...toMessages(history)]
        })
      }, LIMITS[mode === "mock" ? "mock" : "proxy"], (ev) => {
        switch (ev.event) {
          case "meta": {
            const meta = ev.data || {};
            out.mode = meta.mode || out.mode;
            out.provider = meta.provider || out.provider;
            out.model = meta.model || out.model;
            notifyMode(out.mode);
            if (handlers.onMeta) handlers.onMeta(meta);
            break;
          }
          case "delta": {
            const t = String((ev.data && ev.data.text) || "");
            if (!t) break;
            if (!acc) { out.ttfb = out.firstToken = nowMs() - t0; }
            acc += t;
            if (handlers.onDelta) handlers.onDelta(acc, t);
            break;
          }
          case "replace":
            acc = String((ev.data && ev.data.text) || "");
            if (handlers.onReplace) handlers.onReplace(acc);
            break;
          case "done":
            donePayload = ev.data || {};
            break;
          case "error":
            errPayload = ev.data || {};
            break;
        }
      });
    } catch (e) {
      streamErr = e;
    }
    out.total = nowMs() - t0;
    if (donePayload) {
      out.mode = donePayload.mode || out.mode;
      out.provider = donePayload.provider || out.provider;
      out.model = donePayload.model || out.model;
      out.tokens = Number(donePayload.tokens || 0) || acc.length;
      out.degraded = !!donePayload.degraded;
      out.ttfb = Number(donePayload.ttfb_ms || 0) || out.ttfb;
      out.firstToken = Number(donePayload.first_token_ms || 0) || out.firstToken;
      out.total = Number(donePayload.total_ms || 0) || out.total;
      if (donePayload.offline) throw tagError(new Error("服务端为 offline 模式"), null, "config");
      // 注意：parseStructured 会把空 reply 替换成通用兜底文案，
      // 因此必须先按原文判断上游是否真的没给内容，否则会把兜底冒充成真模型输出。
      const providerReply = extractReplyPrefix(donePayload.content || "") || "";
      if (!acc && !providerReply) {
        // 空回复 → 切回本地离线引擎，mode=mock，UI 明确标注"离线示例模式"
        const local = await MockEngine.reply(history, sessionId());
        const text = String((local && local.reply) || FALLBACK_REPLY);
        await emitLocalReply(text, handlers, "mock");
        out.mode = "mock";
        out.provider = "mock";
        out.degraded = true;
        notifyMode("mock");
        return tagResult(Object.assign({}, local || {}, { reply: text }), out);
      }
      notifyMode(out.mode);
      let r = null;
      try { r = parseStructured(donePayload.content || ""); } catch {}
      if (!r || !r.reply) r = { reply: acc, emotion: "neutral", motion: null };
      return tagResult(r, out);
    }
    if (errPayload) {
      const e = tagError(new Error(errPayload.error || "流式错误"),
                         errPayload.status || null, errPayload.kind);
      if (acc) {
        out.partial = true;
        if (handlers.onError) handlers.onError(e);
        return tagResult({ reply: acc, emotion: "neutral", motion: null }, out);
      }
      throw e;
    }
    if (streamErr) {
      if (acc) {
        out.partial = true;
        if (handlers.onError) handlers.onError(streamErr);
        return tagResult({ reply: acc, emotion: "neutral", motion: null }, out);
      }
      throw streamErr;
    }
    if (acc) {
      out.partial = true;
      notifyMode(out.mode);
      return tagResult({ reply: acc, emotion: "neutral", motion: null }, out);
    }
    throw tagError(new Error("流式响应为空"), null, "stream_broken");
  }

  /** 用户自填 key 直连（OpenAI 兼容 SSE） */
  async function streamDirect(history, opts, cfg, handlers) {
    const base = XMConfig.apiRoot(cfg.apiBase);
    if (!base) throw tagError(new Error("openai apiBase 为空"), null, "config");
    const t0 = nowMs();
    const out = streamOut("openai", "direct", cfg.model);
    let raw = "";
    let emitted = "";
    let nonSse = false;
    try {
      await streamFetch(`${base}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${cfg.apiKey}` },
        body: JSON.stringify({
          model: cfg.model,
          temperature: 0.85,
          max_tokens: 600,
          reasoning: { enabled: false },
          stream: true,
          messages: [{ role: "system", content: buildSystemPrompt(opts) }, ...toMessages(history)]
        })
      }, LIMITS.direct, (ev) => {
        if (ev.event === "meta") return;
        const chunk = ev.data;
        if (chunk === "[DONE]") return;
        if (!chunk || typeof chunk !== "object") return;
        if (chunk.usage && chunk.usage.total_tokens) out.tokens = Number(chunk.usage.total_tokens) || 0;
        let delta = "";
        try { delta = chunk.choices[0].delta.content || ""; } catch {}
        if (!delta) return;
        raw += delta;
        const prefix = extractReplyPrefix(raw);
        if (!prefix) return;
        // 留 2 字缓冲，整段收尾时再补，避免把刚成形的危险词先露出去
        const target = prefix.length > 2 ? prefix.slice(0, -2) : "";
        if (target && target.startsWith(emitted)) {
          const t = target.slice(emitted.length);
          if (t) {
            if (!emitted) out.ttfb = out.firstToken = nowMs() - t0;
            emitted = target;
            if (handlers.onDelta) handlers.onDelta(emitted, t);
          }
        } else if (target) {
          emitted = target;
          if (handlers.onReplace) handlers.onReplace(emitted);
        }
      });
    } catch (e) {
      if (e.kind === "non_sse" && !raw) nonSse = true;
      else {
        out.total = nowMs() - t0;
        if (raw) {
          out.partial = true;
          if (handlers.onError) handlers.onError(e);
          return tagResult({ reply: raw, emotion: "neutral", motion: null }, out);
        }
        throw e;
      }
    }
    if (nonSse) {
      // 上游不支持 stream：退回非流式一次（不计熔断）
      const r = await chatDirect(history, opts, cfg);
      await emitLocalReply(r.reply, handlers, "openai");
      Object.assign(out, { ttfb: 0, firstToken: 0, total: 0, tokens: (r.reply || "").length });
      notifyMode("openai");
      return tagResult(r, out);
    }
    out.total = nowMs() - t0;
    out.tokens = out.tokens || raw.length;
    if (!emitted && !raw) {
      // 空回复同样不能标成真模型：切本地引擎 + mode=mock 明确告知
      const local = await MockEngine.reply(history, sessionId());
      const text = String((local && local.reply) || FALLBACK_REPLY);
      await emitLocalReply(text, handlers, "mock");
      out.mode = "mock";
      out.provider = "mock";
      out.degraded = true;
      notifyMode("mock");
      return tagResult(Object.assign({}, local || {}, { reply: text }), out);
    }
    notifyMode("openai");
    let r = null;
    try { r = parseStructured(raw); } catch {}
    if (!r || !r.reply) r = { reply: emitted || raw || FALLBACK_REPLY, emotion: "neutral", motion: null };
    return tagResult(r, out);
  }

  /** 安卓原生桥没有流式：拿完整回复后本地平滑出字（仅用户显式允许免费端点时） */
  async function streamNative(history, opts, cfg, handlers) {
    const r = await chatNative(history, opts, cfg);
    notifyMode("native");
    const text = String(r.reply || "");
    await emitLocalReply(text, handlers, "native");
    const out = streamOut("native", "native", cfg.model);
    out.tokens = text.length;
    return tagResult(r, out);
  }

  /**
   * 纯函数：按配置决定"这一轮会尝试哪些在线通道"。
   * 设置页三个开关（模式 / 是否填 Key / 是否允许免费通道承载倾诉）在这里落到行为，
   * 单测可直接断言，不依赖 fetch。
   * @param {object} cfg 已归一化的配置
   * @param {{native?:boolean}} env native=是否运行在安卓原生桥环境
   * @returns {string[]} direct / proxy / native；空数组代表没有在线通道可用
   */
  function routePlan(cfg, env) {
    const c = cfg || {};
    const native = !!(env && env.native);
    if (c.mode === "offline") return ["offline"];
    if (c.mode === "mock") return native ? ["offline"] : ["mock"];
    const routes = [];
    if (c.apiKey) routes.push("direct");
    else if (native) {
      // 隐私闸门：Kilo 免费 trial 默认不承载倾诉内容，必须用户显式授权
      if (c.allowFreePersonal) routes.push("native");
    } else {
      routes.push("proxy");
    }
    return routes;
  }

  /**
   * 流式对话入口。降级链：自填 key 直连 → 服务端代理 → mock → offline；
   * 安卓未授权免费端点时直接从 mock/offline 兜底。每级有超时与熔断。
   * handlers: { onMeta(meta), onDelta(acc, delta), onReplace(acc), onError(err) }
   */
  async function chatStream(history, opts, handlers) {
    handlers = handlers || {};
    const cfg = loadCfg();
    if (cfg.mode === "offline") return streamOffline(history, handlers);
    if (cfg.mode === "mock") {
      if (nativeBridge()) return streamOffline(history, handlers);
      if (!breakerOpen("mock")) {
        try {
          const r = await streamServer(history, opts, cfg, "mock", handlers);
          breakerReset("mock");
          return r;
        } catch (e) {
          // 429/402 不是路由故障而是"没额度"：不熔断、也不静默换离线人格，
          // 交还调用方走"排队 + 诚实告知"（P1-3），否则用户永远等不到排队提示
          if (e && e.kind === "limit") throw e;
          breakerFail("mock");
          console.warn("mock 流不可用，转离线引擎:", e.message);
        }
      }
      markDegraded("offline");
      return streamOffline(history, handlers);
    }

    // openai 模式
    const routes = routePlan(cfg, { native: !!nativeBridge() })
      .filter((r) => r === "direct" || !breakerOpen(r));
    let limitErr = null;
    for (const route of routes) {
      try {
        let r;
        if (route === "direct") r = await streamDirect(history, opts, cfg, handlers);
        else if (route === "native") r = await streamNative(history, opts, cfg, handlers);
        else r = await streamServer(history, opts, cfg, "openai", handlers);
        breakerReset(route);
        return r;
      } catch (e) {
        // 429/402 计入独立标记但不熔断：额度耗尽说明路由本身是通的，
        // 熔断会把"恢复后立刻补话"也一起挡 30s
        if (e && e.kind === "limit") limitErr = e;
        else breakerFail(route);
        console.warn(`[stream] ${route} 失败，降级:`, e && e.message);
      }
    }
    // 限流是"共情能力被上游卡住"，不能降级成离线人格假装没事：
    // 抛回 app.js 的 limit 分支，落盘排队 + 诚实告知（P1-3）
    if (limitErr) throw limitErr;
    // mock 兜底（安卓壳内 mock 即离线引擎）
    if (!nativeBridge() && !breakerOpen("mock")) {
      try {
        markDegraded("mock");
        const r = await streamServer(history, opts, cfg, "mock", handlers);
        breakerReset("mock");
        return r;
      } catch (e) {
        breakerFail("mock");
        console.warn("mock 流失败，降级离线引擎:", e && e.message);
      }
    }
    markDegraded("offline");
    if (handlers.onMeta) {
      try { handlers.onMeta({ mode: "offline", provider: "local", degraded: true }); } catch {}
    }
    return streamOffline(history, handlers);
  }

  /** 读服务端 /api/config（非敏感：mode/model/apiBase/provider，不含 key） */
  async function serverConfig(force) {
    if (serverCfg && !force) return serverCfg;
    // 安卓壳内没有本地服务：不发请求（避免首屏等 4s 超时）
    if (nativeBridge()) { serverCfg = null; return null; }
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
    // 安卓 WebView 里没有本地 8902 服务：直接走同进程离线规则引擎
    // （否则每次都要等 15s 超时才发现不可达）
    if (nativeBridge()) return MockEngine.reply(history, sessionId());
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

  /** 瞬时故障重试：网络断开/超时各再试一次（不改变 429 的排队语义） */
  async function withRetry(fn, kinds, waitMs) {
    const allow = kinds || ["network", "timeout"];
    try { return await fn(); }
    catch (e) {
      if (!e || allow.indexOf(e.kind) < 0) throw e;
      await sleep(waitMs || 700);
      return await fn();
    }
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
      // 只对"瞬间断网"重试；60s 超时不再叠加第二次，避免让用户干等两分钟
      res = await withRetry(() => fetchWithTimeout(`${mockBase(cfg)}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }, 60000), ["network"]);
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
    notifyMode(data.mode || data.provider || "openai");
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
    try { res = await withRetry(doFetch); } catch (e) { throw tagError(e); }
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
    notifyMode("openai");
    return parseStructured(content);
  }

  /** 原生桥：安卓 WebView 无本地服务、且 Kilo 不回 CORS 头时，由 Java 侧代发请求。
   *  约定：AndroidNet.chat(id, url, bodyJson) → 异步回调
   *  window.__nativeChatDone(id, ok, status, base64Body) */
  function nativeBridge() {
    return (typeof window !== "undefined" && window.AndroidNet
      && typeof window.AndroidNet.chat === "function") ? window.AndroidNet : null;
  }

  const nativeWaiters = {};
  let nativeSeq = 0;
  if (typeof window !== "undefined") {
    window.__nativeChatDone = (id, ok, status, b64) => {
      const w = nativeWaiters[id];
      if (!w) return;
      delete nativeWaiters[id];
      clearTimeout(w.timer);
      if (!ok) {
        const statusNum = Number(status) || null;
        w.reject(tagError(new Error(`native chat ${status || "fail"}`), statusNum));
        return;
      }
      try {
        // 后端返回 UTF-8 JSON 的 base64，前端解码避免 Java 侧转义地狱
        const raw = decodeURIComponent(escape(atob(String(b64 || ""))));
        w.resolve(raw);
      } catch (e) {
        w.reject(tagError(new Error("native chat 响应解码失败")));
      }
    };
  }

  function nativeChat(url, bodyObj) {
    const bridge = nativeBridge();
    if (!bridge) return Promise.reject(tagError(new Error("无原生桥"), null, "config"));
    return new Promise((resolve, reject) => {
      const id = `n${++nativeSeq}-${Date.now()}`;
      const timer = setTimeout(() => {
        delete nativeWaiters[id];
        reject(tagError(new Error("native chat timeout"), null, "timeout"));
      }, 60000);
      nativeWaiters[id] = { resolve, reject, timer };
      try {
        bridge.chat(id, url, JSON.stringify(bodyObj));
      } catch (e) {
        clearTimeout(timer);
        delete nativeWaiters[id];
        reject(tagError(e));
      }
    });
  }

  /** 安卓免 key 通道：Java 侧直连 Kilo（无 Origin 头，绕过浏览器 CORS） */
  async function chatNative(history, opts, cfg) {
    const base = XMConfig.apiRoot(cfg.apiBase || "https://api.kilo.ai/api/gateway");
    if (!base) throw tagError(new Error("apiBase 为空"), null, "config");
    // reasoning 模型偶发 content=null（被思维链吃光）：强约束重试一次，不直接判死
    for (let attempt = 0; attempt < 2; attempt++) {
      const body = {
        model: cfg.model,
        temperature: 0.85,
        max_tokens: 600,
        reasoning: { enabled: false },
        messages: [{ role: "system", content: buildSystemPrompt(attempt ? Object.assign({}, opts, { strictJson: true }) : opts) }, ...toMessages(history)]
      };
      const raw = await nativeChat(`${base}/v1/chat/completions`, body);
      let data = null;
      try { data = JSON.parse(raw); } catch { throw tagError(new Error("原生通道返回非 JSON")); }
      const content = data && data.choices && data.choices[0] && data.choices[0].message
        ? data.choices[0].message.content : null;
      if (content != null && content !== "") {
        notifyMode("native");
        return parseStructured(content);
      }
      console.warn("原生通道 content 为空，重试:", attempt);
    }
    throw new Error("模型连续返回空 content");
  }

  /** 降级提示：供 UI 显示安静的"本地模式"标识（可关闭） */
  function markDegraded(kind) {
    try { if (typeof window !== "undefined" && window.__onDegrade) window.__onDegrade(kind); } catch {}
  }

  async function chatOpenAI(history, opts, cfg) {
    // 安卓无 key：仅在用户显式允许免费端点（allowFreePersonal）时才走 Kilo 原生直连。
    // 默认 false：Kilo trial 条款禁止承载个人数据，倾诉内容不默认外发。
    if (!cfg.apiKey && nativeBridge() && cfg.allowFreePersonal) {
      try { return await chatNative(history, opts, cfg); }
      catch (e) { console.warn("native 通道失败，转代理链路:", e.message); }
    }
    const call = () => (cfg.apiKey ? chatDirect(history, opts, cfg) : chatViaProxy(history, opts, cfg));
    const first = await call();
    if (first && first.reply && first.reply !== FALLBACK_REPLY) return first;
    // 解析失败（空回复/走神兜底）：强约束"只输出 JSON"重试一次，仍失败保留首次结果
    try {
      const strictOpts = Object.assign({}, opts, { strictJson: true });
      const second = cfg.apiKey ? chatDirect(history, strictOpts, cfg) : chatViaProxy(history, strictOpts, cfg);
      if (second && second.reply && second.reply !== FALLBACK_REPLY) return second;
    } catch (e) {
      console.warn("结构化重试失败：", e.message);
    }
    return first;
  }

  /** mock 兜底；mock 也不可用/熔断时返回离线引擎 */
  async function fallbackMock(history) {
    if (!nativeBridge() && !breakerOpen("mock")) {
      try {
        const r = await chatMock(history);
        breakerReset("mock");
        notifyMode("mock");
        return r;
      } catch (e) {
        breakerFail("mock");
        console.warn("mock 不可用，转离线引擎:", e.message);
      }
    }
    markDegraded("offline");
    notifyMode("offline");
    return MockEngine.reply(history, sessionId());
  }

  async function chat(history, opts) {
    const cfg = loadCfg();
    // 降级链：自填 key 直连 / 服务端代理 / 安卓原生（需授权）→ mock → 离线引擎
    if (cfg.mode === "offline") {
      notifyMode("offline");
      return MockEngine.reply(history, sessionId());
    }
    if (cfg.mode === "mock") {
      if (nativeBridge()) {
        notifyMode("offline");
        return MockEngine.reply(history, sessionId());
      }
      return fallbackMock(history);
    }
    const route = cfg.apiKey ? "direct" : (nativeBridge() ? "native" : "proxy");
    if (breakerOpen(route)) {
      console.warn("路由熔断中，直接降级:", route);
      return fallbackMock(history);
    }
    try {
      const r = await chatOpenAI(history, opts, cfg);
      breakerReset(route);
      return r;
    } catch (e) {
      breakerFail(route);
      console.warn("openai 失败，降级 mock:", e.message);
      return fallbackMock(history);
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
      if (nativeBridge()) {
        return { ok: true, mode, models: ["xiaoman-offline"], note: "安卓壳内使用离线规则引擎（无本地服务）" };
      }
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

  /** 备份用：取服务端会话（状态 + 落盘历史 + 记忆槽） */
  async function sessionExport(sid) {
    const cfg = loadCfg();
    const url = `${mockBase(cfg)}/api/session/export?session_id=${encodeURIComponent(sid || sessionId())}`;
    const res = await fetchWithTimeout(url, {}, 8000);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  /** 备份还原：整体写回服务端会话（状态 + 历史 + 记忆槽） */
  async function sessionImport(sid, payload) {
    const cfg = loadCfg();
    const res = await fetchWithTimeout(`${mockBase(cfg)}/api/session/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ session_id: sid || sessionId() }, payload || {})),
    }, 8000);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  /** 单条消息删除：同步服务端历史与记忆，避免"界面删了、服务端还留着" */
  async function messageDelete(sid, message) {
    const cfg = loadCfg();
    const res = await fetchWithTimeout(`${mockBase(cfg)}/api/message/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid || sessionId(), message: message || {} }),
    }, 6000);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  /** 单条消息编辑：服务端落盘历史就地改文案 */
  async function messageUpdate(sid, message, content) {
    const cfg = loadCfg();
    const res = await fetchWithTimeout(`${mockBase(cfg)}/api/message/update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sid || sessionId(), message: message || {}, content: String(content || ""),
      }),
    }, 6000);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  return {
    chat, chatStream, loadCfg, saveCfg, buildSystemPrompt,
    // 提示词常量走惰性 getter：浏览器里 persona_rules.js 要等解析器执行到注入的
    // <script> 后才可用，模块加载期取不到（Node 单测 require 则立即可用）。
    get SYSTEM_PROMPT() { return systemPrompt(); },
    get HEARTBEAT_PROMPT() { return heartbeatPrompt(); },
    get SAFETY_RULES() { return personaSection("SAFETY_RULES"); },
    get REALISM_RULES() { return personaSection("REALISM_RULES"); },
    get HUMAN_FEEL_RULES() { return personaSection("HUMAN_FEEL_RULES"); },
    get MEMORY_RULES() { return personaSection("MEMORY_RULES"); },
    get MEMORY_SLOT_RULES() { return personaSection("MEMORY_SLOT_RULES"); },
    get PERSONA_RULES() { return personaSection("PERSONA_RULES"); },
    get BASELINE() { return personaSection("BASELINE"); },
    get OUTPUT_PROTOCOL() { return personaSection("OUTPUT_PROTOCOL"); },
    sessionId, bootstrap, serverConfig, testConnection,
    sessionExport, sessionImport, messageDelete, messageUpdate,
    getApiKey, setApiKey, parseStructured, effectiveMode, routePlan,
    // 测试与观测用（不参与业务逻辑）
    parseSseBlock, createSseDecoder, extractReplyPrefix,
    breakerSnapshot, resetBreakers, LIMITS,
    hasNativeBridge: () => !!nativeBridge()
  };
})();

// 供 node:test 单测使用（浏览器里 module 不存在，不影响运行时）
if (typeof module === "object" && module.exports) module.exports = API;
// 顶层 const 只产生词法全局，不会挂到 window 上；tts.js 等模块通过 window.API
// 读取配置（如 tts=off 静音开关），这里显式导出，避免设置被静默忽略。
if (typeof window !== "undefined") window.API = API;
