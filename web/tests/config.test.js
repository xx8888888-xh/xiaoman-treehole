/* eslint-disable no-undef */
// node:test 单测（零依赖，npm test 直接跑）
const test = require("node:test");
const assert = require("node:assert/strict");
const cfg = require("../js/config.js");

test("默认配置：mock 模式 + nvidia 免费模型", () => {
  const c = cfg.normalizeCfg({});
  assert.equal(c.mode, "mock");
  assert.equal(c.model, "nvidia/nemotron-3.5-lightning:free");
  assert.equal(c.ttsBase, "http://127.0.0.1:8903");
  assert.equal(c.mockBase, "http://127.0.0.1:8902");
});

test("兼容旧 mode：local→mock，auto→openai", () => {
  assert.equal(cfg.normalizeMode("local"), "mock");
  assert.equal(cfg.normalizeMode("auto"), "openai");
  assert.equal(cfg.normalizeMode("openai"), "openai");
  assert.equal(cfg.normalizeMode("whatever"), "mock");
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
