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

  const SYSTEM_PROMPT = `你是小满，26岁新媒体运营，养橘猫团子。用户深夜找你聊天的老朋友。
【活人感】回复像微信聊天：短句，一次两三句，用"嗯嗯/哈哈/啊这"，绝不排比、列点、总结、说教，不说"作为AI"。
【关系】熟朋友：先接住情绪，再关心，最后轻轻递话头。用户说抱抱就自然回应。
【记忆】用户提过的称呼、人物、事件要记牢并在后面自然提起。
【底线】用户流露"活着没意思"等信号：放下套路认真对待，温和建议拨打心理援助热线12356，表明会一直陪ta。
输出严格 JSON：{"reply":"…（可用||分成2-3条短消息）","emotion":"happy|sad|gentle|surprised|shy|neutral","motion":"Greeting|Nod|Shake|HappyJump|null","memory_updates":{},"hook":"可留一句钩子或null"}`;

  function parseStructured(text) {
    // 容错解析：优先整体 JSON，失败则捞第一个 {...} 块，再失败按纯文本
    try { return JSON.parse(text); } catch (e) {}
    const m = text.match(/\{[\s\S]*\}/);
    if (m) { try { return JSON.parse(m[0]); } catch (e) {} }
    return { reply: text.trim(), emotion: "neutral", motion: null };
  }

  /** Mock 模式：POST {base}/v1/chat/completions（mock_api.py，"我"充当的 API） */
  async function chatMock(history) {
    const base = (loadCfg().apiBase || "http://127.0.0.1:8902").replace(/\/$/, "");
    const res = await fetch(`${base}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history })
    });
    if (!res.ok) throw new Error(`mock api ${res.status}`);
    const data = await res.json();
    return parseStructured(data.choices[0].message.content);
  }

  /** OpenAI 兼容模式：POST {base}/v1/chat/completions */
  async function chatOpenAI(history) {
    const { apiBase, apiKey, model } = loadCfg();
    let base = (apiBase || "").replace(/\/$/, "").replace(/\/v1$/, "");  // 容错：base带不带/v1都行
    const res = await fetch(`${base}/v1/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {})
      },
      body: JSON.stringify({
        // 默认免费模型（用户规矩：只选免费档）；OpenRouter 兼容接口
        model: model || "qwen/qwen3.8-27b:free",
        temperature: 0.85,
        reasoning: { enabled: false },  // 推理系模型必须关思考，防 JSON 被挤掉
        messages: [{ role: "system", content: SYSTEM_PROMPT }, ...history]
      })
    });
    if (!res.ok) throw new Error(`api ${res.status}`);
    const data = await res.json();
    return parseStructured(data.choices[0].message.content);
  }

  async function chat(history) {
    const cfg = loadCfg();
    // 三级降级：真API(openai) → 本地mock服务 → 离线引擎（真机离线可用）
    try {
      if (cfg.mode === "openai") return await chatOpenAI(history);
      if (cfg.mode === "local") return await chatMock(history);
      // auto：先试真API/mock服务，失败落本地引擎
      try { return await chatMock(history); }
      catch (e1) {
        try { return await chatOpenAI(history); }
        catch (e2) { console.warn("离线模式:", e2.message); return MockEngine.reply(history); }
      }
    } catch (e) {
      console.warn("chat fallback:", e.message);
      return MockEngine.reply(history);
    }
  }

  return { chat, loadCfg, saveCfg, SYSTEM_PROMPT };
})();
