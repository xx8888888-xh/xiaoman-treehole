/* stage_idle.test.js · 空闲分级降档状态机单测
 *
 * 不启动浏览器：用 Stage._createIdleGovernor() 注时间戳驱动，
 * 覆盖 活跃→3s 15fps→10s 5fps→30s 1fps、输入立即回满帧、
 * 不可见立即停、恢复不补帧。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const Stage = require("../js/stage.js");

const R = Stage._idleRates;
const T = Stage._idleThresholds;

test("阈值口径：3s→15fps、10s→5fps、30s→1fps，之前保持满帧", () => {
  const g = Stage._createIdleGovernor();
  g.markActivity(0);
  assert.equal(g.rateAt(2999), R.full);
  assert.equal(g.rateAt(T.to15), R.fps15);
  assert.equal(g.rateAt(T.to5 - 1), R.fps15);
  assert.equal(g.rateAt(T.to5), R.fps5);
  assert.equal(g.rateAt(T.to1 - 1), R.fps5);
  assert.equal(g.rateAt(T.to1), R.fps1);
});

test("3s 后按 15fps 门控：只画该画的帧，rAF 回调不逐帧作画", () => {
  const g = Stage._createIdleGovernor();
  g.markActivity(0);
  assert.equal(g.gate(2999).render, true);       // 活跃期每帧照画
  assert.equal(g.rateAt(3000), R.fps15);
  let renders = 0;
  for (let t = 3000; t < 4000; t += 16) if (g.gate(t).render) renders += 1;
  // 1000ms / 66.7ms ≈ 15 帧；允许时间戳取整误差
  assert.ok(renders >= 12 && renders <= 17, "15fps 实际帧数=" + renders);
});

test("10s 后按 5fps 门控：每秒约 5 帧", () => {
  const g = Stage._createIdleGovernor();
  g.markActivity(0);
  assert.equal(g.gate(T.to5).render, true);
  let renders = 0;
  for (let t = T.to5 + 1; t <= T.to5 + 1000; t += 16) if (g.gate(t).render) renders += 1;
  assert.ok(renders >= 4 && renders <= 6, "5fps 实际帧数=" + renders);
});

test("30s 后按 1fps 门控：每秒至多一帧，呼吸仍持续", () => {
  const g = Stage._createIdleGovernor();
  g.markActivity(0);
  assert.equal(g.rateAt(T.to1), R.fps1);
  const shots = [];
  for (let t = T.to1; t <= T.to1 + 3000; t += 16) if (g.gate(t).render) shots.push(t);
  assert.ok(shots.length >= 3 && shots.length <= 4, "1fps 实际帧数=" + shots.length);
  for (let i = 1; i < shots.length; i += 1) {
    assert.ok(shots[i] - shots[i - 1] >= 990, "帧间隔不足 1s：" + (shots[i] - shots[i - 1]));
  }
});

test("用户输入立即恢复满帧，且不补历史帧", () => {
  const g = Stage._createIdleGovernor();
  g.markActivity(0);
  g.gate(20000);
  assert.equal(g.rateAt(20001), R.fps5);
  g.markActivity(20020);                         // 点击/滚动
  assert.equal(g.rateAt(20020), R.full);
  assert.equal(g.gate(20020).render, true);      // 立即补一帧
  assert.equal(g.gate(20036).render, true);      // 之后每帧都画
});

test("低频档迟到一帧：只画一帧，不连续补帧堆积", () => {
  const g = Stage._createIdleGovernor();
  g.markActivity(0);
  g.gate(0);
  assert.equal(g.gate(12000).render, true);
  assert.equal(g.gate(12016).render, false, "不应把错过的帧一次性补回来");
});

test("visibility=hidden 立即停：不排帧，恢复后满帧", () => {
  const g = Stage._createIdleGovernor();
  g.markActivity(0);
  g.hide();
  assert.equal(g.rateAt(100), R.stopped);
  assert.equal(g.gate(100).render, false);
  assert.equal(g.gate(100).waitMs, -1);
  g.show(200);
  assert.equal(g.rateAt(200), R.full);
  assert.equal(g.gate(201).render, true);
});

test("TTS/音频喂帧（含静音段 level=0）是活跃信号", () => {
  Stage.lipFrame(0);
  assert.equal(Stage._idleRate(), R.full);
  assert.ok(Stage._idleState().lastActivity > 0);
});
