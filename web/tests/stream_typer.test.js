const test = require("node:test");
const assert = require("node:assert");

const StreamTyper = require("../js/stream_typer.js");

/** 假时钟 + 手动帧队列：节奏是纯时间函数，不依赖真实 rAF */
function harness() {
  let t = 0;
  const pending = new Set();
  const calls = [];
  const typer = StreamTyper.create({
    now: () => t,
    raf: (fn) => { const h = { fn }; pending.add(h); return h; },
    cancel: (h) => pending.delete(h),
    onChange: (text) => calls.push({ n: text.length, at: t }),
  });
  function step(ms) {
    t += ms;
    const before = calls.length;
    const due = [...pending];
    pending.clear();
    for (const h of due) h.fn();
    return calls.length - before;
  }
  return { typer, step, calls, pending, now: () => t };
}

const LONG = "长".repeat(240);

/* ---------- 1. 短回复：一次落下，不进逐字队列 ---------- */
test("短回复 ≤SHORT_CHARS 字：同步整段落屏，不排帧", () => {
  const h = harness();
  const text = "今天先别急着想明天的事";
  assert.ok(text.length <= StreamTyper.SHORT_CHARS);
  h.typer.push(text);
  assert.deepEqual(h.calls.map((c) => c.n), [text.length]);
  assert.equal(h.pending.size, 0, "短文本不该留下待执行的 rAF");
  assert.equal(h.typer.stats().short, true);
});

test("短回复增长（模拟网络分块）仍是逐次整段落屏，每次只回调一次", () => {
  const h = harness();
  h.typer.push("你");
  h.typer.push("你还好");
  h.typer.push("你还好吗");
  assert.deepEqual(h.calls.map((c) => c.n), [1, 3, 4]);
  assert.equal(h.pending.size, 0);
});

/* ---------- 2. 长回复：可见的逐字过程，且总时长有界 ---------- */
test("长回复：首帧只给一小段，之后单调增长，0.6–0.9s 内补齐", () => {
  const h = harness();
  h.typer.push(LONG);
  assert.equal(h.pending.size, 1, "长文本应进入逐字队列");
  h.step(16);
  const first = h.calls[h.calls.length - 1].n;
  assert.ok(first > 0 && first < LONG.length * 0.2, `首帧 ${first} 应明显少于全文`);
  let last = first;
  let frames = 0;
  while (h.pending.size && frames < 200) {
    h.step(16);
    frames += 1;
    const n = h.calls[h.calls.length - 1].n;
    assert.ok(n >= last, "显示长度不能回退");
    last = n;
  }
  assert.equal(last, LONG.length, "最终必须补齐全文");
  const elapsed = h.calls[h.calls.length - 1].at;
  assert.ok(elapsed >= 600 && elapsed <= 900, `长回复总时长应在 0.6–0.9s，实际 ${elapsed}ms`);
});

test("长回复逐帧增长：每个时间点只写一次 DOM（不会一帧回调多次）", () => {
  const h = harness();
  h.typer.push(LONG);
  for (let i = 0; i < 10; i++) {
    const emitted = h.step(16);
    assert.ok(emitted <= 1, "一帧最多一次重排");
  }
  assert.ok(h.typer.shown() > 0);
});

test("长回复与短回复的节奏差：同样 400ms 里短文本已完成，长文本仍在出字", () => {
  const h = harness();
  h.typer.push("早点睡");
  assert.equal(h.typer.shown(), 3);
  const g = harness();
  g.typer.push(LONG);
  for (let i = 0; i < 25; i++) g.step(16);   // 400ms，按真实 rAF 逐帧推进
  assert.ok(g.typer.shown() < LONG.length, "长文本 400ms 时不该整段铺完");
  assert.ok(g.typer.shown() > LONG.length * 0.2, "长文本 400ms 时也不该还没动静");
});

/* ---------- 3. flush：流式结束立刻定稿（TTS/history 不等动画） ---------- */
test("flush 立刻补齐并停帧；之后不再回调", () => {
  const h = harness();
  h.typer.push(LONG);
  h.step(32);
  const mid = h.typer.shown();
  assert.ok(mid < LONG.length);
  h.typer.flush();
  assert.equal(h.typer.shown(), LONG.length);
  assert.equal(h.pending.size, 0);
  const count = h.calls.length;
  h.step(500);
  assert.equal(h.calls.length, count, "flush 后不该再有渲染回调");
});

test("finishSoon：收尾欠账按帧补齐，不一次性瞬移半段", () => {
  const h = harness();
  h.typer.push(LONG);
  for (let i = 0; i < 8; i++) h.step(16);   // 约 130ms，屏幕明显落后
  const mid = h.typer.shown();
  assert.ok(mid > 0 && mid < LONG.length * 0.5);
  h.typer.finishSoon(160);
  assert.equal(h.typer.shown(), mid, "finishSoon 本身不该瞬移");
  let frames = 0;
  let last = mid;
  let maxStep = 0;
  while (h.pending.size && frames < 60) {
    h.step(16);
    frames += 1;
    const n = h.typer.shown();
    assert.ok(n >= last, "显示长度不能回退");
    maxStep = Math.max(maxStep, n - last);
    last = n;
  }
  assert.equal(last, LONG.length, "收尾必须补齐全文");
  const elapsed = frames * 16;
  assert.ok(elapsed >= 60 && elapsed <= 350, `欠账应在有限窗口补齐，实际 ${elapsed}ms`);
  assert.ok(maxStep <= 14, `逐帧步进不能是整段瞬移，实际 ${maxStep} 字/帧`);
});

/* ---------- 4. 速度曲线：按字数自适应，有上限 ---------- */
test("speedFor：字数越多越快，封顶 MAX_CPS；短文本为 Infinity", () => {
  assert.equal(StreamTyper.speedFor(StreamTyper.SHORT_CHARS), Infinity);
  const a = StreamTyper.speedFor(60);
  const b = StreamTyper.speedFor(160);
  const c = StreamTyper.speedFor(600);
  assert.ok(a < b && b < c, "速度应随字数单调上升");
  assert.equal(c, StreamTyper.MAX_CPS);
  assert.ok(b > 0);
});

test("重复 push 同一目标不重复排帧", () => {
  const h = harness();
  h.typer.push(LONG);
  h.typer.push(LONG);
  assert.equal(h.pending.size, 1);
});

/* ---------- 5. 交接：短句前缀已上屏，跨过阈值不能回缩 ---------- */
test("initial：从已显示字数续播，跨过短句阈值后不回缩", () => {
  let t = 0;
  const calls = [];
  const pendingSet = new Set();
  const typer = StreamTyper.create({
    initial: 24,
    now: () => t,
    raf: (fn) => { const h = { fn }; pendingSet.add(h); return h; },
    cancel: (h) => pendingSet.delete(h),
    onChange: (s) => calls.push(s.length),
  });
  typer.push(LONG);
  for (let i = 0; i < 6; i++) {
    t += 16;
    const due = [...pendingSet];
    pendingSet.clear();
    for (const h of due) h.fn();
  }
  assert.ok(calls.length >= 1);
  assert.ok(calls.every((n) => n >= 24), "已上屏的前缀不允许被抹掉");
  assert.ok(typer.shown() > 24);
});
