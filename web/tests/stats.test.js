/* stats.test.js · 小本本：跨会话保留、按本地日切、图表数据窗口 */
const test = require("node:test");
const assert = require("node:assert/strict");

global.window = globalThis;
const store = new Map();
global.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
function freshStats() {
  delete require.cache[require.resolve("../js/stats.js")];
  return require("../js/stats.js");
}
const Stats = freshStats();

test("累计数字真的落盘，重新加载模块后仍在（模拟重启 App）", () => {
  store.clear();
  Stats.bump("今天有点累", new Date("2026-03-01T22:10:00").getTime());
  Stats.bump("哈哈哈笑死", new Date("2026-03-01T22:11:00").getTime());
  assert.equal(Stats.snapshot().msgs, 2);
  const reloaded = freshStats();
  assert.equal(reloaded.snapshot().msgs, 2);
  const raw = JSON.parse(store.get(reloaded.KEY));
  assert.equal(raw.msgs, 2);
  assert.ok(raw.perDay["2026-03-01"] >= 2, "按天计数也要落盘");
});

test("日期边界：23:59 与次日 00:01 分属两天，不并到一天", () => {
  store.clear();
  const night = new Date("2026-03-01T23:59:00").getTime();
  const next = new Date("2026-03-02T00:01:00").getTime();
  Stats.bump("睡前一句", night);
  Stats.bump("凌晨一句", next);
  const raw = JSON.parse(store.get(Stats.KEY));
  assert.equal(raw.perDay["2026-03-01"], 1);
  assert.equal(raw.perDay["2026-03-02"], 1);
  assert.equal(Stats.streak(night), 1);
  assert.equal(Stats.streak(next), 2, "跨天连续说话要算连续打卡");
  assert.equal(Stats.snapshot().night, 2, "23:59 与 00:01 都算深夜");
});

test("最近 7 天窗口：缺数据补 0、顺序从旧到新、今天在最后", () => {
  store.clear();
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  Stats.bump("a", today.getTime());
  Stats.bump("b", today.getTime());
  const y2 = new Date(today.getTime());
  y2.setDate(y2.getDate() - 2);
  Stats.bump("c", y2.getTime());
  const days = Stats.daily(7, today.getTime());
  assert.equal(days.length, 7);
  assert.equal(days[6].key, Stats.dayKey(today.getTime()));
  assert.equal(days[6].n, 2);
  assert.equal(days[4].n, 1);
  assert.equal(days[5].n, 0, "空的一天必须是 0，而不是沿用前一天");
  assert.match(days[6].date, /^\d{1,2}-\d{2}$/);
});

test("连续打卡：今天没开口时从昨天算，不把记录清零", () => {
  store.clear();
  const today = new Date();
  today.setHours(10, 0, 0, 0);
  for (let i = 1; i <= 3; i++) {
    const d = new Date(today.getTime());
    d.setDate(d.getDate() - i);
    Stats.bump("day" + i, d.getTime());
  }
  assert.equal(Stats.streak(today.getTime()), 3, "昨天起连续 3 天");
  Stats.bump("today", today.getTime());
  assert.equal(Stats.streak(today.getTime()), 4);
});

test("招牌 emoji 与笑出声：按真实内容统计", () => {
  store.clear();
  Stats.bump("哈哈哈今天太离谱了 😂😂");
  Stats.bump("又笑死 😂");
  Stats.bump("普通一句");
  const s = Stats.snapshot();
  assert.equal(s.haha, 2);
  assert.equal(s.emoji, "😂");
  assert.equal(s.emojiN, 3, "同一条里最多取 5 个，但要累计");
});

test("旧数据兼容：没有 perDay 时用 days 折算，不清空历史", () => {
  store.clear();
  const legacy = { msgs: 5, haha: 1, night: 1, days: ["Sun Mar 01 2026", "Sat Feb 28 2026"] };
  store.set(Stats.KEY, JSON.stringify(legacy));
  const s = Stats.snapshot();
  assert.equal(s.msgs, 5);
  assert.equal(s.days, 2);
  const days = Stats.daily(7, new Date("2026-03-01T12:00:00").getTime());
  assert.equal(days[6].n, 1, "旧数据折算成当天 1 条");
  assert.equal(days[5].n, 1, "前一天也应折算出来");
});

test("清空小本本：只清统计，不碰聊天/提醒键", () => {
  store.clear();
  store.set("xiaoman_chat_v1", "keep-me");
  Stats.bump("一句话");
  Stats.clear();
  assert.equal(store.has(Stats.KEY), false);
  assert.equal(store.get("xiaoman_chat_v1"), "keep-me");
});
