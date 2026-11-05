// node:test 单测：LLM 适配层的结构化解析与提示词约束（零依赖）
const test = require("node:test");
const assert = require("node:assert/strict");
const API = require("../js/api.js");

const FALLBACK = "嗯……我好像走神了，你刚才说什么来着？";

test("标准 JSON 直接解析", () => {
  const r = API.parseStructured('{"reply":"今天咋啦？","emotion":"gentle","motion":"Nod"}');
  assert.equal(r.reply, "今天咋啦？");
  assert.equal(r.emotion, "gentle");
  assert.equal(r.motion, "Nod");
});

test("Markdown 代码块包裹也能解析", () => {
  const r = API.parseStructured('```json\n{"reply":"喝豆浆了吗","emotion":"neutral"}\n```');
  assert.equal(r.reply, "喝豆浆了吗");
});

test("前后带说明文字时捞 JSON 块", () => {
  const r = API.parseStructured('好的，这是回复：{"reply":"嗯嗯","emotion":"happy"} 希望有帮助');
  assert.equal(r.reply, "嗯嗯");
  assert.equal(r.emotion, "happy");
});

test("JSON 被截断时用正则兜底捞 reply", () => {
  const r = API.parseStructured('{"reply":"今天先别熬夜了","emotion":"gentle","memor');
  assert.equal(r.reply, "今天先别熬夜了");
  assert.equal(r.emotion, "neutral");
});

test("reply 含转义引号时正确还原", () => {
  const r = API.parseStructured('{"reply":"他说\\"算了\\"，然后就走了"}');
  assert.equal(r.reply, '他说"算了"，然后就走了');
});

test("纯文本回复退化为 reply", () => {
  const r = API.parseStructured("有点累就歇会儿吧");
  assert.equal(r.reply, "有点累就歇会儿吧");
  assert.equal(r.emotion, "neutral");
});

test("null / 空串 / [] 一律走兜底话术且不返回 null", () => {
  for (const bad of [null, "", "   ", "null", "undefined", "[]", "{}"]) {
    const r = API.parseStructured(bad);
    assert.equal(r.reply, FALLBACK);
    assert.equal(r.emotion, "neutral");
    assert.ok(r.reply.length > 0);
  }
});

test("reply 为空白字符串时也走兜底", () => {
  const r = API.parseStructured('{"reply":"   ","emotion":"sad"}');
  assert.equal(r.reply, FALLBACK);
});

test("system prompt 含长度与跑题硬约束", () => {
  assert.match(API.SYSTEM_PROMPT, /长度硬约束/);
  assert.match(API.SYSTEM_PROMPT, /跑题硬约束/);
  assert.match(API.SYSTEM_PROMPT, /30 个字/);
});

test("system prompt 带真人感硬约束与长期记忆槽（W20 客户端同步）", () => {
  assert.match(API.SYSTEM_PROMPT, /【真人感硬约束/);
  assert.match(API.SYSTEM_PROMPT, /默认不给建议/);
  assert.match(API.SYSTEM_PROMPT, /允许说不知道/);
  assert.match(API.SYSTEM_PROMPT, /允许不好笑/);
  assert.match(API.SYSTEM_PROMPT, /拒绝要落地/);
  assert.match(API.SYSTEM_PROMPT, /记忆像人回忆/);
  assert.match(API.SYSTEM_PROMPT, /【长期记忆槽的用法】/);
  assert.match(API.SYSTEM_PROMPT, /忘了\/别记\/删掉这个/);
  assert.equal(API.HUMAN_FEEL_RULES, require("../js/persona_rules.js").HUMAN_FEEL_RULES);
});

test("心跳提示词仍带安全三条", () => {
  assert.ok(API.HEARTBEAT_PROMPT.indexOf(API.SAFETY_RULES) === 0);
  assert.match(API.HEARTBEAT_PROMPT, /12356/);
});

test("strictJson 追加输出约束", () => {
  const p = API.buildSystemPrompt({ strictJson: true });
  assert.match(p, /只输出一行合法 JSON/);
  const plain = API.buildSystemPrompt({});
  assert.doesNotMatch(plain, /只输出一行合法 JSON/);
});

test("buildSystemPrompt 注入记忆与时间不破坏基础提示", () => {
  const now = new Date("2026-11-04T22:30:00");
  const p = API.buildSystemPrompt({ memSection: "昵称：阿秋", now });
  assert.match(p, /你记得的关于ta的事/);
  assert.match(p, /昵称：阿秋/);
  assert.match(p, /现在时间/);
  assert.ok(p.indexOf(API.SYSTEM_PROMPT) === 0);
});
