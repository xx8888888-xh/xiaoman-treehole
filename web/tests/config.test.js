/* eslint-disable no-undef */
// node:test 单测（零依赖，npm test 直接跑）
const test = require("node:test");
const assert = require("node:assert/strict");
const cfg = require("../js/config.js");

test("默认配置：openai 模式（P1-2：mock 只作兜底）+ nvidia 免费模型", () => {
  const c = cfg.normalizeCfg({});
  assert.equal(c.mode, "openai");
  assert.equal(c.model, "nvidia/nemotron-3.5-lightning:free");
  assert.equal(c.ttsBase, "http://127.0.0.1:8903");
  assert.equal(c.mockBase, "http://127.0.0.1:8902");
});

test("兼容旧 mode：local→mock，auto→openai", () => {
  assert.equal(cfg.normalizeMode("local"), "mock");
  assert.equal(cfg.normalizeMode("auto"), "openai");
  assert.equal(cfg.normalizeMode("openai"), "openai");
  assert.equal(cfg.normalizeMode("whatever"), "openai");
});

test("apiKey 永不进入持久化配置", () => {
  const c = cfg.normalizeCfg({ apiKey: "sk-secret" });
  assert.equal("apiKey" in c, false);
  assert.deepEqual(cfg.stripSecret({ apiKey: "sk-secret", mode: "openai" }), { mode: "openai" });
});

test("地址归一：去尾斜杠、apiRoot 去掉 /v1", () => {
  const c = cfg.normalizeCfg({ apiBase: "https://api.kilo.ai/api/gateway///" });
  assert.equal(c.apiBase, "https://api.kilo.ai/api/gateway");
  assert.equal(cfg.apiRoot("https://openrouter.ai/api/v1"), "https://openrouter.ai/api");
  assert.equal(cfg.apiRoot("https://api.kilo.ai/api/gateway/"), "https://api.kilo.ai/api/gateway");
  assert.equal(cfg.trimUrl("http://127.0.0.1:8903/"), "http://127.0.0.1:8903");
});

test("非法 tts 取值回退 server，voice 去空格", () => {
  const c = cfg.normalizeCfg({ tts: "boom", voice: "  zh-CN-XiaoxiaoNeural  " });
  assert.equal(c.tts, "server");
  assert.equal(c.voice, "zh-CN-XiaoxiaoNeural");
});

test("语速档位：默认 normal，非法值回退 normal，合法值保留", () => {
  assert.equal(cfg.normalizeCfg({}).ttsRate, "normal");
  assert.equal(cfg.normalizeCfg({ ttsRate: "fast" }).ttsRate, "fast");
  assert.equal(cfg.normalizeCfg({ ttsRate: "slow" }).ttsRate, "slow");
  assert.equal(cfg.normalizeCfg({ ttsRate: "turbo" }).ttsRate, "normal");
  assert.deepEqual(cfg.RATES, ["slow", "normal", "fast"]);
});

test("maskKey 不泄露完整 key", () => {
  assert.equal(cfg.maskKey(""), "");
  assert.equal(cfg.maskKey("short"), "*****");
  assert.equal(cfg.maskKey("sk-1234567890abcd"), "sk-***abcd");
});

test("patch 合并保留已有字段（模拟保存单项）", () => {
  const saved = cfg.normalizeCfg({ mode: "openai", apiBase: "https://openrouter.ai/api/v1" });
  const merged = cfg.normalizeCfg({ tts: "off" }, saved);
  assert.equal(merged.mode, "openai");
  assert.equal(merged.apiBase, "https://openrouter.ai/api/v1");
  assert.equal(merged.tts, "off");
});

test("设置页当前实际模式：默认在线，区分原生桥/服务端代理", () => {
  assert.equal(cfg.normalizeCfg({}).mode, "openai");
  assert.match(cfg.describeMode("openai", { native: true }), /在线免费模型（原生桥直连）/);
  assert.match(cfg.describeMode("openai", { native: false }), /在线模型（服务端代理）/);
  assert.match(cfg.describeMode("mock", { native: true }), /本机离线规则引擎/);
  assert.match(cfg.describeMode("mock", { native: false }), /本地 Mock 服务/);
  assert.match(cfg.describeMode("offline", {}), /纯离线规则引擎（不联网）/);
});

test("设置页当前实际模式：显示自动降级后的真实通道", () => {
  assert.match(cfg.describeMode("openai", { native: true, lastRoute: "native" }), /当前实际：在线免费模型/);
  assert.doesNotMatch(cfg.describeMode("openai", { native: true, lastRoute: "native" }), /降级/);

  const degraded = cfg.describeMode("openai", { native: true, lastRoute: "mock" });
  assert.match(degraded, /当前实际：本机离线规则引擎/);
  assert.match(degraded, /配置为在线免费模型/);
  assert.match(degraded, /已自动降级/);

  const offline = cfg.describeMode("openai", { native: false, lastRoute: "offline" });
  assert.match(offline, /当前实际：纯离线规则引擎/);
  assert.match(offline, /已自动降级/);
});
