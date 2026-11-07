/* trunc_log.test.js · 段数截断必须留下可查记录（改前静默 / 改后有记录） */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const TruncLog = require("../js/trunc_log.js");

function memStore() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
  };
}

const NOW = () => 1234;

test("未截断：内容原样返回，不写记录", () => {
  const store = memStore();
  const parts = ["一", "二", "三", "四", "五"];
  const kept = TruncLog.limitParts(parts, 8, "sendSplit", { store, now: NOW });
  assert.deepEqual(kept, parts);
  assert.deepEqual(TruncLog.readLog({ store }), []);
});

test("截断：返回前 cap 段，并写一条含段数/上限/被丢段数的记录", () => {
  const store = memStore();
  const parts = ["一", "二", "三", "四", "五", "六", "七"];
  const kept = TruncLog.limitParts(parts, 5, "sendSplit", { store, now: NOW });
  assert.deepEqual(kept, ["一", "二", "三", "四", "五"]);
  const log = TruncLog.readLog({ store });
  assert.equal(log.length, 1, "发生截断必须留下一条记录");
  assert.equal(log[0].kind, "sendSplit");
  assert.equal(log[0].total, 7);
  assert.equal(log[0].cap, 5);
  assert.equal(log[0].droppedN, 2);
  assert.equal(log[0].at, 1234);
});

test("对照：旧的 slice(0,cap) 静默丢内容，新的 limitParts 有记录", () => {
  const store = memStore();
  const parts = ["一", "二", "三", "四", "五", "六", "七", "八", "九"];
  // 改前行为：纯 slice，没有任何记录
  const before = parts.slice(0, 5);
  assert.equal(before.length, 5);
  assert.equal(TruncLog.readLog({ store }).length, 0, "改前：丢了 4 段但日志为空");
  // 改后行为：同样的上限，额外留下一条可查记录
  TruncLog.limitParts(parts, 5, "sendSplit", { store, now: NOW });
  const log = TruncLog.readLog({ store });
  assert.equal(log.length, 1, "改后：同样丢 4 段，但有 1 条记录");
  assert.equal(log[0].total, 9);
  assert.equal(log[0].droppedN, 4);
});

test("空段不算内容丢失：只有末尾分隔符时不写记录", () => {
  const store = memStore();
  const parts = ["一", "二", "三", "四", "五", "", "  "];
  const kept = TruncLog.limitParts(parts, 5, "stream", { store, now: NOW });
  assert.equal(kept.length, 5);
  assert.deepEqual(TruncLog.readLog({ store }), []);
});

test("脱敏：被丢段只留前 6 字，数字/邮箱/英文名打码，不落全文", () => {
  const store = memStore();
  const parts = ["一", "二", "三", "四", "五",
    "我的手机号13800138000你记一下", "abc@example.com", "Alice"];
  TruncLog.limitParts(parts, 5, "sendSplit", { store, now: NOW });
  const log = TruncLog.readLog({ store });
  assert.equal(log.length, 1);
  assert.equal(log[0].dropped[0], "我的手机号*");
  assert.equal(log[0].dropped[1], "******");
  assert.equal(log[0].dropped[2], "*****");
  for (const s of log[0].dropped) {
    assert.ok(Array.from(s).length <= TruncLog.PREVIEW_CHARS, "预览不得超过 6 字");
  }
  const raw = store.getItem(TruncLog.KEY_LOG);
  assert.equal(raw.includes("13800138000"), false, "完整号码不得落盘");
  assert.equal(raw.includes("abc@example.com"), false, "完整邮箱不得落盘");
});

test("流式同一 seq 反复截断：upsert 只留最后/最大的一条", () => {
  const store = memStore();
  const seq = TruncLog.nextSeq();
  for (const total of [6, 7, 8, 9]) {
    const parts = Array.from({ length: total }, (_, i) => `段${i + 1}`);
    TruncLog.record({
      kind: "stream", seq, total: parts.length, cap: 5,
      droppedN: parts.slice(5).filter((s) => s.trim()).length,
      dropped: parts.slice(5),
    }, { store, now: NOW });
  }
  const log = TruncLog.readLog({ store });
  assert.equal(log.length, 1, "同一条回复只留一条记录");
  assert.equal(log[0].total, 9, "记录保留最大段数");
});

test("日志最多 20 条，旧记录被挤出", () => {
  const store = memStore();
  for (let i = 0; i < 25; i++) {
    TruncLog.limitParts(["a", "b", "c", "d", "e", "f"], 5, `k${i}`, { store, now: NOW });
  }
  const log = TruncLog.readLog({ store });
  assert.equal(log.length, TruncLog.MAX_LOG);
  assert.equal(log[log.length - 1].kind, "k24");
});

test("app.js 接线：两处 slice(0, n) 截断都必须走 TruncLog", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "js", "app.js"), "utf8");
  assert.match(app, /const limitedParts = TruncLog\.limitParts\(parts, cap, "sendSplit"\)/);
  assert.match(app, /TruncLog\.record\(\s*\{\s*kind: "stream", seq: truncSeq,/, "流式槽位截断必须记录");
  assert.match(app, /window\.__truncLog = \(\) => TruncLog\.readLog\(\)/, "E2E 探针必须暴露");
});

test("index.html 先加载 trunc_log.js，再加载 app.js", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const mod = html.indexOf("js/trunc_log.js");
  const app = html.indexOf("js/app.js");
  assert.ok(mod > 0 && app > 0 && mod < app, "trunc_log.js 必须在 app.js 之前");
});
