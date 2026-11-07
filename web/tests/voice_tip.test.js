/* voice_tip.test.js · 语音失败提示条：出现 / 到期收起 / 重绘不复活 / 日志不丢 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const VoiceTip = require("../js/voice_tip.js");

function memStore(init) {
  const m = Object.assign({}, init || {});
  return {
    getItem: (k) => (k in m ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    removeItem: (k) => { delete m[k]; },
  };
}

function fakeEl(hidden) {
  const cls = new Set(hidden ? ["hidden"] : []);
  return {
    classList: {
      add: (c) => cls.add(c),
      remove: (c) => cls.delete(c),
      contains: (c) => cls.has(c),
    },
  };
}

/** 可控定时器：手动触发到期，避免测试里真的等 8 秒。 */
function fakeClock() {
  const q = [];
  return {
    setTimeout: (fn) => { q.push(fn); return q.length - 1; },
    clearTimeout: (h) => { if (h != null) q[h] = null; },
    tick: () => { const run = q.splice(0); run.forEach((f) => { if (f) f(); }); },
  };
}

test("失败时提示出现，并把这次失败写进日志", () => {
  const store = memStore();
  const el = fakeEl(true);
  const c = VoiceTip.create({ el, store, now: () => 111, setTimeout: () => 1, clearTimeout: () => {} });
  assert.equal(c.visible(), false);
  assert.equal(c.show("play_failed"), true);
  assert.equal(c.visible(), true);
  assert.equal(el.classList.contains("hidden"), false);
  assert.deepEqual(c.log(), [{ at: 111, reason: "play_failed" }]);
});

test("到期自动收起；收起后重绘（render）不会复活", () => {
  const store = memStore();
  const el = fakeEl(true);
  const clock = fakeClock();
  const c = VoiceTip.create({ el, store, ttlMs: 8000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout });
  c.show("timeout");
  assert.equal(c.visible(), true);
  clock.tick();                       // 8s 到期
  assert.equal(c.visible(), false);
  assert.equal(el.classList.contains("hidden"), true);
  c.render();                         // 模拟下一次渲染/页面恢复
  c.render();
  assert.equal(c.visible(), false, "重绘不能把已收起的提示刷回来");
});

test("收起后日志仍在（排障线索不丢）", () => {
  const store = memStore();
  const el = fakeEl(true);
  const clock = fakeClock();
  const c = VoiceTip.create({ el, store, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout });
  c.show("server_error");
  clock.tick();
  assert.equal(c.visible(), false);
  assert.equal(c.log().length, 1);
  assert.equal(c.log()[0].reason, "server_error");
});

test("新的失败重新出现并重新计时（不是复活旧提示）", () => {
  const store = memStore();
  const el = fakeEl(true);
  const clock = fakeClock();
  const c = VoiceTip.create({ el, store, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout });
  c.show("a");
  clock.tick();
  assert.equal(c.visible(), false);
  c.show("b");
  assert.equal(c.visible(), true);
  assert.equal(c.log().length, 2);
  clock.tick();
  assert.equal(c.visible(), false);
});

test("会话内手动关闭过：不再显示，但失败记录照写", () => {
  const store = memStore({ [VoiceTip.KEY_OFF]: "1" });
  const el = fakeEl(true);
  const c = VoiceTip.create({ el, store, setTimeout: () => 1, clearTimeout: () => {} });
  assert.equal(c.show("muted"), false);
  assert.equal(c.visible(), false);
  assert.equal(c.log().length, 1, "静音只影响显示，不影响记录");
});

test("日志最多保留 20 条，旧记录被挤出而不是无限增长", () => {
  const store = memStore();
  const el = fakeEl(true);
  const c = VoiceTip.create({ el, store, now: () => 1, setTimeout: () => 1, clearTimeout: () => {} });
  for (let i = 0; i < 25; i++) c.show("r" + i);
  assert.equal(c.log().length, VoiceTip.MAX_LOG);
  assert.equal(c.log()[c.log().length - 1].reason, "r24");
});

test("app.js 接线：TTS 失败回调必须调 showVoiceTip，不能静默吞掉", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "js", "app.js"), "utf8");
  assert.match(app, /TTS\.speakSafe[\s\S]*?showVoiceTip\("play_failed"\)/);
  assert.match(app, /showVoiceTip\("start_failed"\)/);
});

test("index.html 先加载 voice_tip.js，再加载 app.js", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const tip = html.indexOf("js/voice_tip.js");
  const app = html.indexOf("js/app.js");
  assert.ok(tip > 0 && app > 0 && tip < app, "voice_tip.js 必须在 app.js 之前");
});

// ---------- 2026-11-07：失败日志结构化（原因分布可重算） ----------

test("detail 只落白名单字段：不写对话原文/未知键", () => {
  const store = memStore();
  const c = VoiceTip.create({ el: fakeEl(true), store, now: () => 5, setTimeout: () => 1, clearTimeout: () => {} });
  c.show("play_failed", {
    provider: "server",
    error: "http_503",
    httpStatus: 503,
    textLen: 42,
    textHash: "deadbeef",
    elapsedMs: 123.6,
    timedOut: false,
    attempts: [{ provider: "server", error: "start_timeout", timedOut: true, elapsedMs: 7000 }],
    text: "这是不该落盘的正文",
    stack: "Error: boom\n  at app.js:1",
  });
  const e = c.log()[0];
  assert.equal(e.reason, "play_failed");
  assert.deepEqual(e.detail, {
    provider: "server",
    error: "http_503",
    textLen: 42,
    bucket: "mid",
    textHash: "deadbeef",
    elapsedMs: 124,
    httpStatus: 503,
    attempts: [{ provider: "server", error: "start_timeout", timedOut: true, elapsedMs: 7000 }],
  });
  const dump = JSON.stringify(c.log());
  assert.ok(!dump.includes("这是不该落盘的正文"), "正文不得进入 sessionStorage 日志");
  assert.ok(!dump.includes("at app.js"), "堆栈不得进入日志");
});

test("无 detail 时保持旧 schema（旧读端不受影响）", () => {
  const store = memStore();
  const c = VoiceTip.create({ el: fakeEl(true), store, now: () => 7, setTimeout: () => 1, clearTimeout: () => {} });
  c.show("play_failed");
  assert.deepEqual(c.log(), [{ at: 7, reason: "play_failed" }]);
});

test("长度分桶：short ≤20 / mid ≤60 / long >60", () => {
  assert.equal(VoiceTip.bucketOf(0), "empty");
  assert.equal(VoiceTip.bucketOf(12), "short");
  assert.equal(VoiceTip.bucketOf(20), "short");
  assert.equal(VoiceTip.bucketOf(21), "mid");
  assert.equal(VoiceTip.bucketOf(60), "mid");
  assert.equal(VoiceTip.bucketOf(61), "long");
});

test("stats 能从既有日志重算原因/错误码/长度桶/超时/重复文本", () => {
  const log = [
    { at: 1, reason: "play_failed" },   // 旧记录：只有 reason
    { at: 2, reason: "start_failed", detail: { provider: "none", error: "no_provider", textLen: 8, bucket: "short", textHash: "aaa" } },
    { at: 3, reason: "start_failed", detail: { provider: "server", error: "http_503", httpStatus: 503, textLen: 70, bucket: "long", textHash: "aaa" } },
    { at: 4, reason: "play_failed", detail: { provider: "server", error: "start_timeout", timedOut: true, textLen: 30, bucket: "mid", textHash: "bbb" } },
  ];
  const s = VoiceTip.stats(log);
  assert.equal(s.total, 4);
  assert.equal(s.legacy, 1);
  assert.deepEqual(s.byReason, { play_failed: 2, start_failed: 2 });
  assert.deepEqual(s.byError, { no_provider: 1, http_503: 1, start_timeout: 1 });
  assert.deepEqual(s.byBucket, { short: 1, long: 1, mid: 1 });
  assert.deepEqual(s.byProvider, { none: 1, server: 2 });
  assert.equal(s.timeouts, 1);
  assert.deepEqual(s.topTextHashes, [{ hash: "aaa", count: 2 }, { hash: "bbb", count: 1 }]);
});

test("会话内 20 条写满后，detail 仍在（不会退化成只有总数）", () => {
  const store = memStore();
  const c = VoiceTip.create({ el: fakeEl(true), store, now: () => 1, setTimeout: () => 1, clearTimeout: () => {} });
  for (let i = 0; i < 25; i++) {
    c.show("play_failed", { provider: "server", error: "audio_error", textLen: i, textHash: "h" + i });
  }
  const log = c.log();
  assert.equal(log.length, 20);
  assert.ok(log.every((e) => e.detail && e.detail.error === "audio_error"));
  const s = c.stats();
  assert.equal(s.total, 20);
  assert.equal(s.byError.audio_error, 20);
});

