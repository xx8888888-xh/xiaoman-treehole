/* reminders.test.js · 提醒的完整 CRUD 与"触发后留痕"（纯 localStorage，不联网） */
const test = require("node:test");
const assert = require("node:assert/strict");

global.window = globalThis;
const store = new Map();
global.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
const Reminders = require("../js/reminders.js");

test("新建：文案/时间/createdAt 都落库，且同毫秒两条 id 不撞", () => {
  store.clear();
  const realNow = Date.now;
  Date.now = () => 1700000000000;
  try {
    const a = Reminders.add("喝水", new Date(Date.now() + 60e3));
    const b = Reminders.add("给妈妈打电话", new Date(Date.now() + 120e3));
    assert.ok(a && b);
    assert.notEqual(a.id, b.id, "同毫秒创建也不能互相覆盖/误删");
    const saved = JSON.parse(store.get(Reminders.KEY));
    assert.equal(saved.length, 2);
    assert.equal(saved[0].text, "喝水");
    assert.equal(saved[0].createdAt, 1700000000000);
  } finally {
    Date.now = realNow;
  }
});

test("即将到来：按时间升序，不含已完成项", () => {
  store.clear();
  const now = Date.now();
  Reminders.add("晚一点的", new Date(now + 90e3));
  Reminders.add("早一点的", new Date(now + 30e3));
  const late = Reminders.add("已经触发", new Date(now - 10e3));
  Reminders.markDone([late.id]);
  const pend = Reminders.pending();
  assert.equal(pend.length, 2);
  assert.equal(pend[0].text, "早一点的");
  assert.equal(pend[1].text, "晚一点的");
});

test("编辑：改文案/改时间；空文案或非法时间被拒绝，原值不动", () => {
  store.clear();
  const it = Reminders.add("旧文案", new Date(Date.now() + 60e3));
  const later = new Date(Date.now() + 3600e3);
  const up = Reminders.update(it.id, { text: " 新文案 ", at: later });
  assert.ok(up);
  assert.equal(up.text, "新文案");
  assert.equal(up.at, later.getTime());
  // 空文案
  assert.equal(Reminders.update(it.id, { text: "   " }), null);
  assert.equal(Reminders.pending()[0].text, "新文案", "非法编辑不能把原值改坏");
  // 非法时间
  assert.equal(Reminders.update(it.id, { at: "不是时间" }), null);
  // 已完成项不可编辑
  Reminders.markDone([it.id]);
  assert.equal(Reminders.update(it.id, { text: "改不动" }), null);
});

test("已完成历史：记录 doneAt、最近的在前、可清空", () => {
  store.clear();
  const a = Reminders.add("第一条", new Date(Date.now() - 60e3));
  const b = Reminders.add("第二条", new Date(Date.now() - 30e3));
  Reminders.markDone([a.id]);
  const realNow = Date.now;
  Date.now = () => realNow() + 10e3;
  try { Reminders.markDone([b.id]); } finally { Date.now = realNow; }
  const hist = Reminders.history(10);
  assert.equal(hist.length, 2);
  assert.equal(hist[0].text, "第二条", "最近触发/完成的排前面");
  assert.ok(hist[0].doneAt && hist[1].doneAt);
  Reminders.clearDone();
  assert.equal(Reminders.history(10).length, 0);
  assert.equal(Reminders.pending().length, 0);
});

test("取消：只删指定 id，不影响其它提醒", () => {
  store.clear();
  const a = Reminders.add("留下", new Date(Date.now() + 60e3));
  const b = Reminders.add("删掉", new Date(Date.now() + 60e3));
  Reminders.cancel(b.id);
  assert.deepEqual(Reminders.pending().map(r => r.id), [a.id]);
});

test("存储满：不抛异常、提醒仍返回，并通知 UI 一次", () => {
  store.clear();
  let warned = 0;
  window.__storageFull = () => { warned++; };
  const orig = localStorage.setItem;
  localStorage.setItem = () => { const e = new Error("quota"); e.name = "QuotaExceededError"; throw e; };
  try {
    const item = Reminders.add("写不进去也要告诉我", new Date(Date.now() + 60e3));
    assert.ok(item, "写入失败也不能崩掉输入流程");
    assert.equal(warned, 1);
  } finally {
    localStorage.setItem = orig;
    delete window.__storageFull;
  }
});

test("时间解析与展示：8点/今晚/明天等常见说法仍可用", () => {
  const base = new Date();
  base.setHours(9, 0, 0, 0);
  const at8 = Reminders.parseTime("早上8点", base);
  assert.ok(at8, "早上8点应解析出时间");
  assert.equal(at8.getHours(), 8);
  const t = new Date(Date.now() + 200 * 60e3);
  const label = Reminders.fmt(t.getTime());
  assert.match(label, /今天|明天|\d+月\d+日/);
  assert.match(Reminders.fmtFull(t.getTime()), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
});
