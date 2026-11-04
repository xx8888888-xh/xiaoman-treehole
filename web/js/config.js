/* ============================================================
 * config.js · 前端配置模型（纯函数，可在 node:test 里直接测）
 * ------------------------------------------------------------
 * 约定：
 *  - 持久化配置里永远不出现 apiKey（明文不落 localStorage）
 *  - mode 三选一：openai / mock / offline；历史值 local→mock、auto→openai
 *  - 地址统一去尾斜杠；/v1 由调用方按需拼接
 * ============================================================ */
(function (root) {
  "use strict";

  const DEFAULTS = {
    // 红队 P1-2：全新安装默认走真模型（安卓原生桥 / 服务端代理）；
    // mock 只在离线/降级时兜底，避免"以为是 AI 其实是规则表"
    mode: "openai",
    apiBase: "",
    model: "nvidia/nemotron-3.5-lightning:free",
    ttsBase: "http://127.0.0.1:8903",
    ttsServer: "",         // 远程语音显式地址：默认空 = provider 关闭（不请求任何外部服务）
    mockBase: "http://127.0.0.1:8902",
    tts: "server",
    voice: "zh-CN-XiaoyiNeural",
    ttsRate: "normal",     // 语速档位：slow / normal / fast（跨启动持久化）
    // W7 隐私开关：Kilo 等免费 trial 条款禁止承载个人倾诉数据。
    // 默认 false → 安卓免 key 通道不发请求，直接 mock/offline 安全兜底。
    allowFreePersonal: false,
    heartbeatOn: true,   // 主动问候总开关（设置页可关；关掉后只保留用户自己定的提醒）
    apiKey: ""            // 仅内存使用，normalizeCfg 会把它丢弃
  };

  const MODES = ["openai", "mock", "offline"];
  const RATES = ["slow", "normal", "fast"];

  function normalizeMode(mode) {
    if (mode === "local") return "mock";     // 兼容旧配置
    if (mode === "auto") return "openai";    // 兼容旧配置
    return MODES.indexOf(mode) >= 0 ? mode : DEFAULTS.mode;
  }

  function trimUrl(v) {
    return String(v == null ? "" : v).trim().replace(/\/+$/, "");
  }

  function apiRoot(base) {
    const b = trimUrl(base);
    return b.endsWith("/v1") ? b.slice(0, -3) : b;
  }

  function normalizeCfg(patch, base) {
    const raw = Object.assign({}, base || {}, patch || {});
    const cfg = Object.assign({}, DEFAULTS);
    cfg.mode = normalizeMode(raw.mode || DEFAULTS.mode);
    cfg.apiBase = trimUrl(raw.apiBase);
    cfg.model = String(raw.model || DEFAULTS.model).trim();
    cfg.ttsBase = trimUrl(raw.ttsBase || DEFAULTS.ttsBase);
    cfg.ttsServer = trimUrl(raw.ttsServer || "");
    cfg.mockBase = trimUrl(raw.mockBase || DEFAULTS.mockBase);
    cfg.tts = ["server", "browser", "off"].indexOf(raw.tts) >= 0 ? raw.tts : DEFAULTS.tts;
    cfg.voice = String(raw.voice || DEFAULTS.voice).trim();
    cfg.ttsRate = RATES.indexOf(raw.ttsRate) >= 0 ? raw.ttsRate : DEFAULTS.ttsRate;
    cfg.allowFreePersonal = raw.allowFreePersonal === true;
    cfg.heartbeatOn = raw.heartbeatOn !== false;   // 缺省开；只有显式 false 才关
    // 密钥永不进入持久化配置
    delete cfg.apiKey;
    return cfg;
  }

  function stripSecret(obj) {
    const out = Object.assign({}, obj || {});
    delete out.apiKey;
    return out;
  }

  function maskKey(key) {
    const k = String(key || "");
    if (!k) return "";
    return k.length <= 8 ? "*".repeat(k.length) : `${k.slice(0, 3)}***${k.slice(-4)}`;
  }

  /**
   * 设置页「当前实际模式」文案（纯函数，便于单测）。
   * @param {string} mode 已保存的配置模式
   * @param {{native?:boolean, lastRoute?:string}} opts
   *        native：是否在安卓原生桥环境；lastRoute：最近一次真正生效的通道
   *        （openai/native/mock/offline，由 API.effectiveMode() 提供）
   */
  function describeMode(mode, opts) {
    const o = opts || {};
    const configured = normalizeMode(mode || DEFAULTS.mode);
    const route = o.lastRoute ? String(o.lastRoute) : "";
    const native = !!o.native;

    const label = m => {
      if (m === "offline") return "纯离线规则引擎（不联网）";
      if (m === "mock") return native ? "本机离线规则引擎（安卓壳内无本地服务）" : "本地 Mock 服务";
      return native ? "在线免费模型（原生桥直连）" : "在线模型（服务端代理）";
    };

    const configuredLabel = label(configured);
    // lastRoute=openai/native 都属于「在线模型」；mock/offline 单独判断
    if (route) {
      const routeLabel = label(route === "native" ? "openai" : route);
      const same = (route === "native" || route === "openai")
        ? configuredLabel === label("openai")
        : route === configured;
      if (!same) {
        return `当前实际：${routeLabel}（配置为${configuredLabel}，已自动降级）`;
      }
      return `当前实际：${routeLabel}`;
    }
    return `当前实际：${configuredLabel}`;
  }

  /**
   * 设置页「语音实际会怎么发声」文案（纯函数，便于单测）。
   * 关键：用户选的服务端语音只是**偏好**，真正发声还要看环境有没有该后端。
   * @param {string} tts 配置值：server / browser / off
   * @param {{server?:boolean, native?:boolean, browser?:boolean}} opts 环境可用性
   * @returns {{mode:string, chain:string[], actual:string, text:string}}
   */
  function describeTts(tts, opts) {
    const o = opts || {};
    const mode = ["server", "browser", "off"].indexOf(tts) >= 0 ? tts : DEFAULTS.tts;
    const label = {
      server: "服务端语音",
      native: "安卓系统语音",
      browser: "浏览器本机语音",
      none: "无可用的语音后端",
    };
    if (mode === "off") {
      return { mode, chain: ["none"], actual: "none", text: "语音已关闭：只显示文字，不会出声" };
    }
    const prefer = mode === "server" ? ["server", "native", "browser"] : ["browser", "native"];
    const avail = { server: !!o.server, native: !!o.native, browser: !!o.browser };
    const chain = prefer.filter((id) => avail[id]);
    const actual = chain[0] || "none";
    if (actual === "none") {
      return { mode, chain, actual, text: "当前没有可用的语音后端：文字照常，不会出声" };
    }
    let text = `实际发声：${label[actual]}`;
    if (mode === "server" && actual !== "server") {
      text += "（服务端语音地址为空或不可用，已自动用本机语音）";
    }
    if (mode === "browser" && actual === "native") {
      text += "（当前环境没有浏览器语音，已用安卓系统语音）";
    }
    return { mode, chain, actual, text };
  }

  /** 音色只在服务端语音真正发声时才有意义（本机 TTS 用系统音色） */
  function voiceApplies(tts, actual) {
    return (tts || DEFAULTS.tts) === "server" && actual === "server";
  }

  const api = { DEFAULTS, MODES, RATES, normalizeMode, normalizeCfg, stripSecret, maskKey, trimUrl, apiRoot, describeMode, describeTts, voiceApplies };


  root.XMConfig = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
