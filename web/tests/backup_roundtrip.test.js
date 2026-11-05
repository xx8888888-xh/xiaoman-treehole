/* backup_roundtrip.test.js · W16 备份 v3：提醒 / 小本本 / 服务端会话（记忆槽）
 * 覆盖：导出对象结构、v2 老备份兼容、导出→解析 round-trip 逐项一致、
 * 脏数据（非法提醒/负数计数/坏槽）被清洗、容量上限。
 * 只测纯函数；真实浏览器 round-trip 见 scripts/test_roundtrip.py。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const Backup = require("../js/backup.js");

const reminders = [
  { id: "r1", text: "八点吃药", at: 1770000000000, done: false, createdAt: 1769990000000, updatedAt: 0 },
  { id: "r2", text: "给妈妈打电话", at: 1770003600000, done: true, doneAt: 1770004000000, createdAt: 1769990000000, updatedAt: 0 },
];

const stats = {
  msgs: 12, haha: 3, night: 4,
  days: ["2026-03-01", "2026-03-02"],
  perDay: { "2026-03-01": 5, "2026-03-02": 7 },
  emoji: { "🥺": 4, "😭": 2 },
};

const server = {
  session_id: "s-1",
  state: { memories: { "昵称": "阿秋", "宠物": "年糕" }, turns: 5, crisis_count: 1, last_topic: "加班" },
  messages: [
    { role: "user", content: "我叫阿秋", at: 1000 },
    { role: "assistant", content: "阿秋，你好", at: 1001 },
  ],
  slots: { version: 1, slots: [
    { id: "sl1", key: "宠物", value: "年糕", kind: "pet", emotion: 1, confidence: 0.8, source: "chat", created: 1000, updated: 1001, last_used: 0, uses: 2 },
  ] },
};

test("buildBackup v3：提醒 / 统计 / 服务端会话都进备份", () => {
  const b = Backup.buildBackup({ sid: "s-1", messages: [{ role: "user", content: "在吗", at: 1 }], reminders, stats, server });
  assert.equal(b.version, 3);
  assert.equal(b.reminders.length, 2);
  assert.equal(b.reminders[0].text, "八点吃药");
  assert.equal(b.reminders[0].at, 1770000000000);
  assert.equal(b.stats.msgs, 12);
  assert.deepEqual(b.stats.perDay, { "2026-03-01": 5, "2026-03-02": 7 });
  assert.equal(b.server.state.memories["宠物"], "年糕");
  assert.equal(b.server.slots.slots.length, 1);
  assert.equal(b.server.slots.slots[0].uses, 2);
});

test("round-trip：导出 → 解析，提醒时刻 / 统计计数 / 记忆槽逐项一致", () => {
  const b = Backup.buildBackup({ sid: "s-1", messages: [], reminders, stats, server });
  const r = Backup.parseBackup(JSON.stringify(b));
  assert.equal(r.ok, true);
  assert.deepEqual(r.data.reminders.map(x => [x.id, x.text, x.at, x.done]), reminders.map(x => [x.id, x.text, x.at, x.done]));
  assert.deepEqual(r.data.stats, stats);
  assert.deepEqual(r.data.server.state.memories, server.state.memories);
  assert.equal(r.data.server.messages.length, 2);
  const slot = r.data.server.slots.slots[0];
  assert.equal(slot.key, "宠物");
  assert.equal(slot.value, "年糕");
  assert.equal(slot.uses, 2);
  assert.equal(slot.created, 1000);
});

test("v2 老备份照样能导入：缺的字段补空，不报错", () => {
  const old = { app: Backup.APP_TAG, version: 2, sid: "s-old", messages: [{ role: "user", content: "旧备份", at: 5 }] };
  const r = Backup.parseBackup(JSON.stringify(old));
  assert.equal(r.ok, true);
  assert.equal(r.data.version, 3);
  assert.deepEqual(r.data.reminders, []);
  assert.equal(r.data.stats, null);
  assert.equal(r.data.server, null);
});

test("脏数据被清洗：非法提醒 / 负数计数 / 空槽值都丢掉", () => {
  const b = Backup.buildBackup({
    reminders: [
      { text: "", at: 100 },
      { text: "没有时间", at: "abc" },
      { text: "正常", at: 200 },
    ],
    stats: { msgs: -5, haha: "2", perDay: { "2026-03-01": -1, "2026-03-02": 3 }, emoji: { "🙂": 1 } },
    server: { state: { memories: { "": "x", "昵称": "  " } }, messages: [{ role: "system", content: "x" }], slots: { slots: [{ value: "" }] } },
  });
  assert.equal(b.reminders.length, 1);
  assert.equal(b.reminders[0].text, "正常");
  assert.equal(b.stats.msgs, 0);
  assert.equal(b.stats.haha, 2);
  assert.deepEqual(b.stats.perDay, { "2026-03-02": 3 });
  assert.deepEqual(b.server.state.memories, {});
  assert.equal(b.server.messages.length, 0);
  assert.equal(b.server.slots.slots.length, 0);
});

test("服务端消息最多留 200 条，取最近的", () => {
  const many = Array.from({ length: 260 }, (_, i) => ({ role: "user", content: "m" + i, at: i }));
  const b = Backup.buildBackup({ server: { messages: many } });
  assert.equal(b.server.messages.length, 200);
  assert.equal(b.server.messages[199].content, "m259");
});

test("提醒最多留 200 条，服务端不可达时 server 为 null（备份仍成立）", () => {
  const many = Array.from({ length: 220 }, (_, i) => ({ text: "t" + i, at: 1000 + i }));
  const b = Backup.buildBackup({ messages: [], reminders: many, server: null });
  assert.equal(b.reminders.length, 200);
  assert.equal(b.server, null);
  const r = Backup.parseBackup(JSON.stringify(b));
  assert.equal(r.ok, true);
});
