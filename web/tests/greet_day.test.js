/* greet_day.test.js · 开场问候"同日重入不重复 / 跨日正常出现"的双向断言 */
const test = require("node:test");
const assert = require("node:assert");
const GreetDay = require("../js/greet_day.js");

// 东八区（UTC+8）时间 → 毫秒时间戳
const CST = (y, m, d, hh = 0, mm = 0, ss = 0) => Date.UTC(y, m - 1, d, hh - 8, mm, ss);

test("日期边界按东八区 00:00 切，而不是 UTC", () => {
  // 2026-11-07 00:00:00 CST = 2026-11-06 16:00 UTC
  assert.equal(GreetDay.dayKey(CST(2026, 11, 7, 0, 0, 0), 480), "2026-11-07");
  // 同一时刻若按 UTC 切日会落到前一天——这正是不能用 toISOString() 的原因
  assert.equal(GreetDay.dayKey(CST(2026, 11, 7, 0, 0, 0), 0), "2026-11-06");
  // 东八区当天最后一秒 / 次日第一秒
  assert.equal(GreetDay.dayKey(CST(2026, 11, 7, 23, 59, 59), 480), "2026-11-07");
  assert.equal(GreetDay.dayKey(CST(2026, 11, 8, 0, 0, 0), 480), "2026-11-08");
});

test("当天首次启动：要问候（不能把首次一起禁掉）", () => {
  assert.equal(GreetDay.shouldGreet("", "2026-11-07"), true, "从未问候过 = 当天首次");
  assert.deepEqual(GreetDay.decide("", "2026-11-07", false), { full: true, greeting: true, play: true });
  assert.deepEqual(GreetDay.decide("2026-11-06", "2026-11-07", false), { full: true, greeting: true, play: true });
});

test("同一天内启动两次：第二次不追加问候", () => {
  // 第一次启动后落盘 lastDay=今天；第二次启动同一日期 → 不播
  assert.equal(GreetDay.shouldGreet("2026-11-07", "2026-11-07"), false);
  assert.deepEqual(GreetDay.decide("2026-11-07", "2026-11-07", false), { full: false, greeting: false, play: false });
});

test("同日重入但有欠回复：只播\"话没丢\"，不叠加时段问候", () => {
  assert.deepEqual(GreetDay.decide("2026-11-07", "2026-11-07", true), { full: false, greeting: false, play: true });
});

test("跨天启动：照常问候", () => {
  assert.equal(GreetDay.shouldGreet("2026-11-07", "2026-11-08"), true);
  assert.deepEqual(GreetDay.decide("2026-11-07", "2026-11-08", false), { full: true, greeting: true, play: true });
});
