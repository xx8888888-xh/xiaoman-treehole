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
    mockBase: "http://127.0.0.1:8902",
    tts: "server",
    voice: "zh-CN-XiaoyiNeural",
    ttsRate: "normal",     // 语速档位：slow / normal / fast（跨启动持久化）
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
    cfg.mockBase = trimUrl(raw.mockBase || DEFAULTS.mockBase);
    cfg.tts = ["server", "browser", "off"].indexOf(raw.tts) >= 0 ? raw.tts : DEFAULTS.tts;
    cfg.voice = String(raw.voice || DEFAULTS.voice).trim();
    cfg.ttsRate = RATES.indexOf(raw.ttsRate) >= 0 ? raw.ttsRate : DEFAULTS.ttsRate;
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

  const api = { DEFAULTS, MODES, RATES, normalizeMode, normalizeCfg, stripSecret, maskKey, trimUrl, apiRoot, describeMode };


  root.XMConfig = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
