/* ============================================================
 * scripts/test_data_roundtrip.test.js · w19 数据完整性往返测试
 * ------------------------------------------------------------
 * 验收口径：导出 → 写真实 JSON 文件 → 清空本机 → 读回文件导入 →
 * 对「提醒 / 小本本统计 / 闹钟设定」逐字段比对，必须 100% 还原。
 * 另含：v2/v1 老备份迁移、脏数据降级、删除消息后的统计回退、
 * 原生编排脚本（xm_host_bootstrap.js）在 merge/update/clearDone 后重推提醒表。
 *
 * 运行：node --test scripts/test_data_roundtrip.test.js
 * ============================================================ */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");

/* ---------- 真实文件与存储（测试环境里唯一的内存物是 localStorage 本身） ---------- */

function makeStorage() {
  const map = new Map();
  return {
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: k => map.delete(k),
    clear: () => map.clear(),
    key: i => Array.from(map.keys())[i] ?? null,
    get length() { return map.size; },
    _dump: () => Object.fromEntries(map),
  };
}

const storage = makeStorage();
global.window = globalThis;
global.localStorage = storage;

const Backup = require(path.join(ROOT, "web/js/backup.js"));
const Stats = require(path.join(ROOT, "web/js/stats.js"));
const Reminders = require(path.join(ROOT, "web/js/reminders.js"));

function tmpFile(name, obj) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "treehole-w19-"));
  const file = path.join(dir, name);
  if (obj !== undefined) fs.writeFileSync(file, JSON.stringify(obj, null, 2), "utf8");
  return file;
}

/** 真实落盘的 ChatStore：接口与 web/js/store.js 对齐，读写都是磁盘文件 */
function fileChatStore(file) {
  const read = () => {
    try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch (e) { return []; }
  };
  const write = list => fs.writeFileSync(file, JSON.stringify(list, null, 2), "utf8");
  return {
    MAX_KEEP: 300,
    async list(sid, limit) { return read().slice(-Math.max(1, limit || 300)); },
    async append(sid, msg) { const list = read(); list.push(msg); write(list); },
    async prune(sid, keep) { write(read().slice(-Math.max(1, keep || 300))); },
    _read: read,
  };
}

/* ---------- 逐字段比对 ---------- */

const REMINDER_FIELDS = ["id", "text", "at", "done", "doneAt", "createdAt", "updatedAt"];
const STATS_FIELDS = ["msgs", "haha", "night"];

function assertReminderFields(want, got, where) {
  for (const f of REMINDER_FIELDS) {
    assert.equal(typeof got[f], typeof want[f], `${where}.${f} 类型不一致`);
    assert.equal(got[f], want[f], `${where}.${f} 不一致`);
  }
}

function assertStatsFields(want, got) {
  for (const f of STATS_FIELDS) {
    assert.equal(typeof got[f], typeof want[f], `stats.${f} 类型不一致`);
    assert.equal(got[f], want[f], `stats.${f} 不一致`);
  }
  assert.deepEqual(got.days, want.days, "stats.days 不一致");
  assert.deepEqual(got.perDay, want.perDay, "stats.perDay 不一致");
  assert.deepEqual(got.emoji, want.emoji, "stats.emoji 不一致");
}

/* ---------- 1. 三项块都在包里 ---------- */

test("导出包 v3：提醒 / 闹钟设定 / 统计 / 服务端会话四块齐全", () => {
  const b = Backup.buildBackup({
    messages: [{ role: "user", content: "在吗", at: 1 }],
    reminders: [{ id: "r1", text: "吃药", at: 1700000000000, done: false, createdAt: 1699999999000 }],
    alarm: { notifications: true, exactAlarm: true, ignoringBattery: false, pendingReminders: 1, capturedAt: 1700000000000 },
    stats: { msgs: 3, haha: 1, night: 1, days: ["Fri Nov 06 2026"], perDay: { "2026-11-06": 3 }, emoji: { "😭": 1 } },
    server: { session_id: "s1", state: { memories: { 昵称: "小满" }, turns: 2 }, messages: [], slots: { version: 1, slots: [{ key: "宠物", value: "年糕", kind: "pin" }] } },
  });
  assert.equal(b.version, 3);
  assert.equal(b.reminders.length, 1);
  assert.deepEqual(b.alarm, {
    notifications: true, exactAlarm: true, ignoringBattery: false, pending: 1, capturedAt: 1700000000000,
  });
  assert.equal(b.stats.msgs, 3);
  assert.equal(b.server.state.memories["昵称"], "小满");
  assert.equal(b.server.slots.slots.length, 1);
});

/* ---------- 2. 三块完整往返（真实 JSON 文件） ---------- */

test("往返 100%：提醒 + 统计导出落盘 → 清空 → 导入 → 逐字段一致", async () => {
  storage.clear();

  // 用真实 API 造数据（提醒走 add/markDone，统计走 bump）
  const r1 = Reminders.add("明早八点吃药", new Date("2026-11-06T08:00:00"));
  const r2 = Reminders.add("下午三点开会", new Date("2026-11-06T15:00:00"));
  Reminders.markDone([r1.id]);
  Stats.bump("今天有点累 😭", new Date("2026-11-05T23:10:00").getTime());
  Stats.bump("哈哈哈笑死 233", new Date("2026-11-05T23:11:00").getTime());
  Stats.bump("晚安 🌙", new Date("2026-11-06T02:10:00").getTime());
  Stats.bump("早", new Date("2026-11-06T08:30:00").getTime());
  Stats.bump("早 ☀", new Date("2026-11-06T08:31:00").getTime());

  const seedReminders = JSON.parse(JSON.stringify(Reminders.all()));
  const seedStats = JSON.parse(JSON.stringify(Stats.raw()));
  const alarmSeed = { notifications: true, exactAlarm: true, ignoringBattery: true, pendingReminders: 1, capturedAt: 1700000000000 };

  const backup = Backup.buildBackup({
    sid: "w19-roundtrip",
    messages: [{ role: "user", content: "在吗", at: 1 }],
    reminders: seedReminders,
    alarm: alarmSeed,
    stats: seedStats,
  });

  // 导出：真实文件
  const file = tmpFile("backup.json", backup);
  const text = fs.readFileSync(file, "utf8");
  assert.ok(text.length > 0);
  assert.match(text, /"reminders"/);
  assert.match(text, /"alarm"/);
  assert.match(text, /"stats"/);

  // 清空：本机数据全没
  storage.clear();
  assert.deepEqual(Reminders.all(), []);
  assert.deepEqual(Stats.raw(), {});

  // 导入：读真实文件 → 合并
  const parsed = Backup.parseBackup(text);
  assert.equal(parsed.ok, true);
  Reminders.merge(parsed.data.reminders);
  Stats.merge(parsed.data.stats);

  // 逐字段比对
  const gotReminders = JSON.parse(JSON.stringify(Reminders.all()));
  assert.equal(gotReminders.length, seedReminders.length, "提醒条数不一致");
  for (let i = 0; i < seedReminders.length; i++) {
    assertReminderFields(seedReminders[i], gotReminders.find(r => r.id === seedReminders[i].id), `reminders[${i}]`);
  }

  assertStatsFields(seedStats, JSON.parse(JSON.stringify(Stats.raw())));

  // 包里的闹钟设定随文件保留（不可还原的权限只记录，不改本机）
  assert.deepEqual(parsed.data.alarm, { notifications: true, exactAlarm: true, ignoringBattery: true, pending: 1, capturedAt: 1700000000000 });
  assert.equal(parsed.data.alarm.pending, 1);
  assert.equal(parsed.data.alarm.notifications, true);
  assert.equal(parsed.data.alarm.exactAlarm, true);
  assert.equal(parsed.data.alarm.ignoringBattery, true);
  assert.equal(parsed.data.alarm.capturedAt, alarmSeed.capturedAt);
});

/* ---------- 3. 重复导入不翻倍、不漂移 ---------- */

test("同一份备份导入两次：提醒不重复、统计不翻倍", async () => {
  storage.clear();
  Reminders.add("喝水", new Date("2026-11-06T10:00:00"));
  Stats.bump("哈哈", new Date("2026-11-05T20:00:00").getTime());
  const seedReminders = JSON.parse(JSON.stringify(Reminders.all()));
  const seedStats = JSON.parse(JSON.stringify(Stats.raw()));
  const backup = Backup.buildBackup({ messages: [], reminders: seedReminders, stats: seedStats });
  const text = JSON.stringify(backup);

  storage.clear();
  Reminders.merge(Backup.parseBackup(text).data.reminders);
  Stats.merge(Backup.parseBackup(text).data.stats);
  const firstRem = Reminders.all().length;
  const firstStats = Stats.raw();
  Reminders.merge(Backup.parseBackup(text).data.reminders);
  Stats.merge(Backup.parseBackup(text).data.stats);

  assert.equal(Reminders.all().length, firstRem, "第二次导入不应新增提醒");
  assert.deepEqual(Stats.raw(), firstStats, "第二次导入不应改变统计");
});

/* ---------- 4. 老备份迁移 ---------- */

test("v2 老备份（无提醒/统计/闹钟/服务端）可迁移，导入不崩", async () => {
  const legacy = {
    app: Backup.APP_TAG,
    version: 2,
    exportedAt: "2026-03-01T10:00:00.000Z",
    sid: "s-old",
    messages: [{ role: "user", content: "旧消息", at: 1700000000000 }],
    memories: ["讨厌下雨"],
    riskEvents: [{ at: 1700000000100, level: "medium", reasons: [], text: "累" }],
  };
  const file = tmpFile("backup-v2.json", legacy);
  const parsed = Backup.parseBackup(fs.readFileSync(file, "utf8"));

  assert.equal(parsed.ok, true);
  assert.equal(parsed.fromVersion, 2);
  assert.equal(parsed.migrated, true);
  assert.equal(parsed.data.messages.length, 1);
  assert.deepEqual(parsed.data.reminders, []);
  assert.equal(parsed.data.alarm, null);
  assert.equal(parsed.data.stats, null);

  // 走真实 importNow（消息落盘到真实文件）
  const msgFile = tmpFile("messages.json", []);
  global.window.ChatStore = fileChatStore(msgFile);
  const res = await Backup.importNow(fs.readFileSync(file, "utf8"), "s-old");
  assert.equal(res.ok, true);
  assert.equal(res.imported, 1);
  assert.equal(res.reminders, 0);
  assert.equal(res.alarm, null);
  assert.equal(JSON.parse(fs.readFileSync(msgFile, "utf8")).length, 1, "老备份消息应写进会话");
});

test("v1 草稿（消息在 chats 里）可迁移；脏数据块降级不崩", async () => {
  const v1 = {
    app: Backup.APP_TAG, version: 1, sid: "s1",
    chats: [{ role: "user", content: "老格式", at: 5 }],
    reminders: "not-an-array", stats: 42, server: "bad",
  };
  const parsed = Backup.parseBackup(JSON.stringify(v1));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.data.messages.length, 1);
  assert.deepEqual(parsed.data.reminders, []);
  assert.equal(parsed.data.stats, null);
  assert.equal(parsed.data.server, null);

  const brokenRem = Backup.buildBackup({ reminders: [{ text: "", at: 1 }, { text: "ok", at: -1 }, null] });
  assert.deepEqual(brokenRem.reminders, [], "缺文案/时间非法的提醒不能进包");
});

/* ---------- 5. 删除消息 → 统计回退，再导入可还原 ---------- */

test("删除单条消息后统计不留残影；备份导入后回到原值", () => {
  storage.clear();
  const at = new Date("2026-11-05T23:30:00").getTime();
  Stats.bump("哈哈哈笑死 😂", at);
  Stats.bump("普通一句", at + 1000);
  const before = JSON.parse(JSON.stringify(Stats.raw()));
  Stats.unbump("哈哈哈笑死 😂", at);
  const after = JSON.parse(JSON.stringify(Stats.raw()));

  assert.equal(after.msgs, before.msgs - 1);
  assert.equal(after.haha, 0, "删掉唯一一条笑消息，haha 必须归零");
  assert.equal(after.emoji["😂"], undefined, "这条消息带的 emoji 计数要一起删掉");
  assert.equal(after.perDay[Stats.dayKey(at)], before.perDay[Stats.dayKey(at)] - 1);

  const backup = JSON.parse(JSON.stringify(Backup.buildBackup({ stats: before })));
  Stats.merge(backup.stats);
  assertStatsFields(before, JSON.parse(JSON.stringify(Stats.raw())));

  const empty = Backup.buildBackup({ stats: { msgs: 0, haha: 0, night: 0, days: [], perDay: {}, emoji: {} } });
  assert.equal(empty.stats.msgs, 0, "清空后的统计仍能正常序列化（图表拿 0 而不是 NaN 源）");
});

/* ---------- 6. importNow：导入后重推原生闹钟 ---------- */

test("importNow 合并提醒后重推原生闹钟（readAlarm 也进包）", async () => {
  storage.clear();
  const msgFile = tmpFile("messages2.json", []);
  global.window.ChatStore = fileChatStore(msgFile);
  const pushes = [];
  global.window.AndroidHost = {
    status: () => JSON.stringify({ notifications: true, exactAlarm: false, ignoringBattery: true, pendingReminders: 2 }),
    syncReminders: json => { pushes.push(json); return JSON.stringify({ ok: true, pendingReminders: 2 }); },
  };

  const backup = Backup.buildBackup({
    messages: [{ role: "user", content: "在吗", at: 10 }],
    reminders: [{ id: "r9", text: "吃药", at: 1700000009000, done: false, createdAt: 1700000000000 }],
  });
  const res = await Backup.importNow(JSON.stringify(backup), "s-r");
  assert.equal(res.ok, true);
  assert.equal(res.imported, 1);
  assert.equal(res.reminders, 1);
  assert.equal(res.remindersUpdated, 0);

  assert.equal(pushes.length, 1, "导入后必须推一次提醒表给原生");
  const pushed = JSON.parse(pushes[0]);
  assert.equal(pushed.length, 1);
  assert.equal(pushed[0].id, "r9");
  assert.equal(res.alarm.pendingReminders, 2);

  const alarm = Backup.readAlarm();
  assert.equal(alarm.notifications, true);
  assert.equal(alarm.exactAlarm, false);
  assert.equal(alarm.ignoringBattery, true);
  assert.equal(alarm.pending, 2);
  assert.equal(typeof alarm.capturedAt, "number");

  delete global.window.AndroidHost;
});

/* ---------- 7. 原生编排脚本：merge/update/clearDone 后必须重推 ---------- */

test("xm_host_bootstrap：merge / update / clearDone 都会把提醒表推给原生", () => {
  const source = fs.readFileSync(path.join(ROOT, "android/app/src/main/res/raw/xm_host_bootstrap.js"), "utf8");
  const localMap = new Map();
  const sandboxLocalStorage = {
    getItem: k => (localMap.has(k) ? localMap.get(k) : null),
    setItem: (k, v) => localMap.set(k, String(v)),
    removeItem: k => localMap.delete(k),
  };
  const handlers = {};
  const timers = [];
  const doc = {
    hidden: false, visibilityState: "visible",
    addEventListener() {}, dispatchEvent() {},
  };
  const pushes = [];
  const sandbox = {
    console, JSON, Date, Math, Object, Array, Set, Map, String, Number, Boolean, Error,
    Promise, RegExp, isFinite, parseInt, parseFloat,
    Event: class Event { constructor(t) { this.type = t; } },
    localStorage: sandboxLocalStorage,
    sessionStorage: sandboxLocalStorage,
    document: doc,
    setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = (name, fn) => { (handlers[name] = handlers[name] || []).push(fn); };
  sandbox.AndroidHost = {
    syncReminders: json => { pushes.push(JSON.parse(json)); return JSON.stringify({ ok: true, pendingReminders: pushes.length }); },
    drainDelivered: () => "[]",
    getHeartbeatIntervalMs: () => 60000,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);

  // 真实 reminders.js（同一份源码）跑在这个沙箱里
  vm.runInContext(fs.readFileSync(path.join(ROOT, "web/js/reminders.js"), "utf8"), sandbox, { filename: "reminders.js" });
  vm.runInContext(source, sandbox, { filename: "xm_host_bootstrap.js" });

  // 触发注入脚本的 load 兜底（等价于页面加载完成）
  (handlers.load || []).forEach(fn => fn());
  const base = pushes.length;
  assert.ok(base >= 1, "加载时应先把当前表推给原生");

  const R = sandbox.window.Reminders;
  const before = R.all().length;

  const item = R.add("吃药", new Date("2026-11-06T08:00:00"));
  assert.ok(pushes.length > base, "add 后要推送");
  const afterAdd = pushes.length;

  R.update(item.id, { text: "吃药（饭后）" });
  assert.ok(pushes.length > afterAdd, "update 后要推送");
  assert.equal(pushes[pushes.length - 1][0].text, "吃药（饭后）");

  const merged = R.merge([{ id: "r-b", text: "开会", at: 1700000010000, done: false }]);
  assert.equal(merged.added, 1);
  assert.ok(pushes.length > afterAdd + 1, "merge（备份导入）后要推送");
  assert.equal(pushes[pushes.length - 1].length, before + 2);

  R.markDone([item.id]);
  R.clearDone();
  assert.ok(pushes.length >= 5, "clearDone 后也要推送");
  assert.equal(pushes[pushes.length - 1].length, before + 1);
});
