/* time_divider.js 单测：分隔条的克制规则（分组、跨天、异常时间戳）
 * 全部用本地时间构造，避免依赖运行机器的时区/日期。 */
const test = require("node:test");
const assert = require("node:assert/strict");
const TD = require("../js/time_divider.js");

/** 本地时间 -> 毫秒 */
const D = (y, m, d, hh, mm, ss = 0) => new Date(y, m - 1, d, hh, mm, ss, 0).getTime();

test("会话第一条不插分隔：没有上一条就没有参照", () => {
  assert.equal(TD.label(null, D(2026, 11, 5, 14, 20)), null);
  assert.equal(TD.label(undefined, D(2026, 11, 5, 14, 20)), null);
});

test("同一天：同一分钟内/4分59秒内的连续消息不重复插分隔", () => {
  // 同一分钟
  assert.equal(TD.label(D(2026, 11, 5, 14, 20, 0), D(2026, 11, 5, 14, 20, 40)), null);
  // 4 分 59 秒：仍然算"同一段对话"
  assert.equal(TD.label(D(2026, 11, 5, 14, 15, 1), D(2026, 11, 5, 14, 20, 0)), null);
});

test("同一天：间隔满 5 分钟只给时刻（HH:MM，补零）", () => {
  assert.equal(TD.label(D(2026, 11, 5, 14, 15, 0), D(2026, 11, 5, 14, 20, 0)), "14:20");
  assert.equal(TD.label(D(2026, 11, 5, 9, 0, 0), D(2026, 11, 5, 9, 5, 0)), "09:05");
  assert.equal(TD.label(D(2026, 11, 5, 0, 0, 0), D(2026, 11, 5, 0, 5, 0)), "00:05");
});

test("跨天：今天 / 昨天 / 同年月日 / 跨年月日 + 时刻", () => {
  const now = D(2026, 11, 5, 14, 20);
  assert.equal(TD.label(D(2026, 11, 4, 23, 50), D(2026, 11, 5, 14, 20), now), "今天 14:20");
  assert.equal(TD.label(D(2026, 11, 3, 20, 0), D(2026, 11, 4, 9, 5), now), "昨天 09:05");
  assert.equal(TD.label(D(2026, 10, 1, 20, 0), D(2026, 11, 3, 8, 12), now), "11月3日 08:12");
  assert.equal(TD.label(D(2025, 12, 1, 20, 0), D(2025, 12, 3, 8, 12), now), "2025年12月3日 08:12");
});

test("跨天优先于 5 分钟规则：23:58 → 00:03 也要给出日期", () => {
  const now = D(2026, 11, 5, 12, 0);
  assert.equal(TD.label(D(2026, 11, 4, 23, 58), D(2026, 11, 5, 0, 3), now), "今天 00:03");
  assert.equal(TD.label(D(2026, 11, 3, 23, 58), D(2026, 11, 4, 0, 3), now), "昨天 00:03");
});

test("异常/缺失/倒退的时间戳宁缺勿错", () => {
  assert.equal(TD.label(D(2026, 11, 5, 14, 20), Number.NaN), null);
  assert.equal(TD.label("abc", D(2026, 11, 5, 14, 20)), null);
  assert.equal(TD.label(null, D(2026, 11, 5, 14, 20)), null);
  // 时间倒退（旧数据/时钟回拨）：不加分隔
  assert.equal(TD.label(D(2026, 11, 5, 14, 20), D(2026, 11, 5, 14, 10)), null);
  assert.equal(TD.label(D(2026, 11, 5, 14, 20), D(2026, 11, 4, 8, 0)), null);
});

test("dateLabel 只依赖 now：同一天/昨天/同年/跨年", () => {
  const now = D(2026, 11, 5, 14, 20);
  assert.equal(TD.dateLabel(D(2026, 11, 5, 1, 0), now), "今天");
  assert.equal(TD.dateLabel(D(2026, 11, 4, 1, 0), now), "昨天");
  assert.equal(TD.dateLabel(D(2026, 9, 3, 1, 0), now), "9月3日");
  assert.equal(TD.dateLabel(D(2025, 12, 3, 1, 0), now), "2025年12月3日");
});
