/* eslint-disable no-undef */
// 倾诉记录导出/导入：纯函数单测（node:test，零依赖）
const test = require("node:test");
const assert = require("node:assert/strict");
const Backup = require("../js/backup.js");

test("导出对象：带应用标识、版本、消息与记忆", () => {
  const b = Backup.buildBackup({
    sid: "s-1",
    messages: [{ role: "user", content: "今天有点累", at: 1700000000000 }],
    memories: [{ text: "怕黑", tags: ["情绪"], kind: "pin" }],
  });
  assert.equal(b.app, Backup.APP_TAG);
  assert.equal(b.version, Backup.VERSION);
  assert.equal(b.sid, "s-1");
  assert.equal(b.messages.length, 1);
  assert.deepEqual(b.memories[0], { text: "怕黑", tags: ["情绪"], kind: "pin", ts: 0 });
  assert.match(b.exportedAt, /^\d{4}-\d{2}-\d{2}T/);
});

test("导出消息会清洗：非法 role / 空内容 / 超长内容都被处理", () => {
  const b = Backup.buildBackup({
    messages: [
      { role: "system", content: "hi", at: 1 },
      { role: "user", content: "   ", at: 2 },
      { role: "assistant", content: "x".repeat(2000), at: 3 },
    ],
  });
  assert.equal(b.messages.length, 1);
  assert.equal(b.messages[0].role, "assistant");
  assert.equal(b.messages[0].content.length, 1000);
});

test("parseBackup：坏 JSON / 不是本应用 / 没有消息都给出人话错误", () => {
  assert.equal(Backup.parseBackup("not json").ok, false);
  assert.match(Backup.parseBackup("not json").error, /JSON/);
  assert.equal(Backup.parseBackup(JSON.stringify({ app: "other" })).ok, false);
  assert.match(Backup.parseBackup(JSON.stringify({ app: "other" })).error, /小满树洞/);
  assert.equal(Backup.parseBackup(JSON.stringify({ app: Backup.APP_TAG })).ok, false);
  assert.match(Backup.parseBackup(JSON.stringify({ app: Backup.APP_TAG })).error, /聊天记录/);
});

test("parseBackup：合法备份可往返（round-trip）", () => {
  const b = Backup.buildBackup({
    sid: "s-2",
    messages: [{ role: "user", content: "在吗", at: 1700000001000 }],
    memories: [{ text: "喜欢猫" }],
  });
  const r = Backup.parseBackup(JSON.stringify(b));
  assert.equal(r.ok, true);
  assert.equal(r.data.messages[0].content, "在吗");
  assert.equal(r.data.memories[0].text, "喜欢猫");
});

test("mergeMessages：按 role+时间+内容去重、按时间升序、超限留最新", () => {
  const existing = [{ role: "user", content: "旧话", at: 100 }];
  const incoming = [
    { role: "user", content: "旧话", at: 100 },     // 重复
    { role: "assistant", content: "晚点回", at: 300 },
    { role: "user", content: "早点", at: 200 },
  ];
  const out = Backup.mergeMessages(existing, incoming, 10);
  assert.equal(out.length, 2);
  assert.deepEqual(out.map(m => m.at), [200, 300]);

  const capped = Backup.mergeMessages([], [
    { role: "user", content: "a", at: 1 },
    { role: "user", content: "b", at: 2 },
    { role: "user", content: "c", at: 3 },
  ], 2);
  assert.deepEqual(capped.map(m => m.content), ["b", "c"]);
});

test("mergeMessages：输入脏数据不炸，坏消息被丢弃", () => {
  const out = Backup.mergeMessages([], [null, 5, { role: "user" }, { role: "user", content: "ok" }], 10);
  assert.equal(out.length, 1);
  assert.equal(out[0].content, "ok");
});

test("危机命中记录：随备份导出，可合并去重（红队 P0 验收）", () => {
  const b = Backup.buildBackup({
    messages: [{ role: "user", content: "在吗", at: 1 }],
    riskEvents: [
      { at: 1700000000000, level: "high", imminent: true, reasons: ["act:跳下去"], text: "我在天台" },
      { at: 1700000000500, level: "medium", reasons: ["explicit:不想活"], text: "不想活了" },
      { at: 1, level: "bogus" },   // 非法等级 → low，保留可核查
    ],
  });
  assert.equal(b.riskEvents.length, 3);
  assert.equal(b.riskEvents[0].level, "high");
  assert.equal(b.riskEvents[2].level, "low");

  const merged = Backup.mergeRiskEvents(
    [{ at: 1700000000000, level: "high", text: "我在天台" }],
    b.riskEvents, 50);
  assert.equal(merged.length, 2);   // 重复那条不再写入
  assert.deepEqual(merged.map(e => e.at), [1, 1700000000500]);

  const r = Backup.parseBackup(JSON.stringify(b));
  assert.equal(r.ok, true);
  assert.equal(r.data.riskEvents.length, 3);
});

test("filename：带日期且以 .json 结尾", () => {
  const name = Backup.filename(Date.parse("2026-11-04T09:05:00"));
  assert.equal(name, "xiaoman-backup-20261104-0905.json");
});
