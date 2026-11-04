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

  const api = { DEFAULTS, MODES, RATES, normalizeMode, normalizeCfg, stripSecret, maskKey, trimUrl, apiRoot };

  root.XMConfig = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
