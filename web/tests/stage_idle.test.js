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

/* ---------- 零动画：空刷新抑制 ------------------------------------------------
 * AVD 探针在 idle_minimal 里记录到 t≈45s 的 303,309px / 14 次 drawImage，
 * 来源是 60s 空闲问候重复 setEmotion("gentle") + TTS 收尾 lipFrame(0)。
 * 零动画下这两处画面本来就没有变化，签名相同就不该重绘。
 */
function fakePuppet(emotion) {
  const p = {
    emotion: emotion || "smile",
    eye: { l: "open", r: "open" },
    mouthFrame: "closed",
    blink: { phase: "open" },
    params: { lipSync: 0, headTilt: 0 },
    paints: 0,
    fit() {},
    render() { p.paints += 1; },
    setAutoIdle() {},
    setParam(name, v) { p.params[name] = v; },
    setEmotion(name) { p.emotion = name; },
    playMotion() {}
  };
  return p;
}

test("零动画：表情未变不重绘（60s 空闲问候与开场同为 gentle）", () => {
  const p = fakePuppet("smile");          // 开场已把 gentle→smile 画在屏上
  const undo = Stage._stillForTest(p, false);
  try {
    Stage.setEmotion("gentle");           // 同为 smile：空刷新
    assert.equal(p.paints, 0, "同表情不应重绘");
    Stage.setEmotion("gentle");
    assert.equal(p.paints, 0, "重复空刷新仍不应重绘");
  } finally { undo(); }
});

test("零动画：表情真的变化只重绘一次，之后再调同值不重绘", () => {
  const p = fakePuppet("smile");
  const undo = Stage._stillForTest(p, false);
  try {
    Stage.setEmotion("sad");              // smile → neutral
    assert.equal(p.paints, 1, "表情变化应重绘一次");
    Stage.setEmotion("sad");
    assert.equal(p.paints, 1, "同值重复调用不应再画");
    Stage.setEmotion("shy");              // neutral → shy
    assert.equal(p.paints, 2, "再次变化只加一帧");
  } finally { undo(); }
});

test("零动画：TTS 收尾不再喂 0 时，静默 180ms 自动合嘴", async () => {
  const p = fakePuppet("smile");
  const undo = Stage._stillForTest(p, false);
  try {
    Stage.lipFrame(0.6);
    const paints = p.paints;
    await new Promise((r) => setTimeout(r, 260));
    assert.equal(p.params.lipSync, 0, "最后一帧包络后必须自动合嘴，不能定格在张嘴");
    assert.equal(p.paints, paints + 1, "合嘴要定格一帧");
  } finally { undo(); }
});

test("零动画：说话时按 ≤15fps 把口型推进引擎，收尾立即合嘴", () => {
  const p = fakePuppet("smile");
  p.params.lipSync = 0.6;                 // 动画期留下的口型
  const undo = Stage._stillForTest(p, false);
  try {
    Stage.lipFrame(0.53);
    assert.equal(p.paints, 1, "首帧口型必须画出来（默认极简预设下也要动）");
    assert.equal(Stage._lipStats().paints, 1, "验收口径：真正画出的说话帧 = 1");
    assert.ok(p.params.lipSync > 0.3, `口型目标要进引擎参数，实际 ${p.params.lipSync}`);
    Stage.lipFrame(0.53);                 // 第 2 帧（计数 2）：未到每 4 帧一画 → 不重绘
    assert.equal(p.paints, 1, "零动画口型按每 4 帧画 1 帧节流（60fps→~15fps）");
    assert.equal(Stage._lipStats().paints, 1, "被节流跳过的喂入帧不进货真帧计数");
    Stage.lipFrame(0);
    assert.equal(p.paints, 2, "说话结束立即合嘴重绘一次，不定格在张嘴");
    assert.equal(Stage._lipStats().paints, 1, "合嘴帧不算\"说话帧\"（只统计张嘴帧）");
    assert.equal(p.params.lipSync, 0);
  } finally { undo(); }
});

test("冻结帧签名：表情/眼睛/嘴型/眨眼相位任一变化都算不同帧", () => {
  const a = fakePuppet("smile");
  const b = fakePuppet("smile");
  assert.equal(Stage._stillFrameSig(a), Stage._stillFrameSig(b));
  b.emotion = "neutral";
  assert.notEqual(Stage._stillFrameSig(a), Stage._stillFrameSig(b));
  const c = fakePuppet("smile");
  c.blink = { phase: "half_down" };
  assert.notEqual(Stage._stillFrameSig(a), Stage._stillFrameSig(c));
});
