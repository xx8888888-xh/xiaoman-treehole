/* live2d_lip.test.js · 口型纯函数单测（音频包络驱动；无音频静止；口型不受装饰动效开关影响） */
const test = require("node:test");
const assert = require("node:assert/strict");
const Stage = require("../js/stage.js");

test("无音频（level<0）口型完全静止", () => {
  assert.equal(Stage._lipFor(-1, true), 0);
  assert.equal(Stage._lipFor(undefined, true), 0);
  assert.equal(Stage._lipFor(Number.NaN, true), 0);
  Stage.lipFrame(-1);
  assert.deepEqual(Stage._lipState(), { lipOpen: 0, lipHold: 0 });
});

test("包络 level 0（音频静音段）也闭嘴，不留缝", () => {
  assert.equal(Stage._lipFor(0, true), 0);
  Stage.lipFrame(0);
  assert.deepEqual(Stage._lipState(), { lipOpen: 0, lipHold: 0 });
});

test("极简模式（motion off）口型不被装饰动效开关归零", () => {
  Stage.setMotionEnabled(false);
  assert.ok(Stage._lipFor(0.05) > 0, "有包络就必须张嘴：口型是说话的事实反馈");
  assert.ok(Math.abs(Stage._lipFor(0.5) - (0.04 + 0.5 * 0.55)) < 1e-9);
  Stage.lipFrame(0.3);
  assert.ok(Stage._lipState().lipOpen > 0.2, "零动画下 lipOpen 仍要跟随包络");
  Stage.lipFrame(0);
  assert.deepEqual(Stage._lipState(), { lipOpen: 0, lipHold: 0 });
  Stage.setMotionEnabled(true);
});

test("有音频时嘴张开：幅度随包络增大并封顶 0.59", () => {
  const a = Stage._lipFor(0.2, true);
  const b = Stage._lipFor(0.6, true);
  assert.ok(a > 0 && a < b, `0.2→${a} < 0.6→${b}`);
  assert.ok(Math.abs(b - (0.04 + 0.6 * 0.55)) < 1e-9);
  assert.ok(Math.abs(Stage._lipFor(1, true) - 0.59) < 1e-9);
  assert.ok(Math.abs(Stage._lipFor(2, true) - 0.59) < 1e-9, "level 超界仍封顶");
  Stage.lipFrame(0.6);
  assert.equal(Stage._lipState().lipHold, 6, "有声音时保持若干帧");
});
