/* live2d_lipsync.test.js · 口型与中文语音/文本节奏对齐的纯函数量化
 *
 * 不启动浏览器、不跑 TTS：
 *  - 用中文断句时间线（190ms/字 + 标点停顿）合成一段"音节包络" PCM；
 *  - 走真实管线：TTS._envelopeLevels → 20Hz 帧 → 播放 tick 平滑 →
 *    Stage._lipFor（7 档口型目标）；
 *  - 与 ground truth（同一断句时间线）做互相关，输出滞后毫秒与相关系数。
 *
 * 旧实现（native/browser 无 PCM）只喂 0.05~0.09 的呼吸，相关系数≈0，
 * 这份测试把"没有音节对齐"变成可量化事实，而不是感觉。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const TTS = require("../js/tts.js");
const Stage = require("../js/stage.js");

const ROOT = path.join(__dirname, "..");
const manifest = JSON.parse(
  fs.readFileSync(path.join(ROOT, "assets", "live2d", "original", "manifest.json"), "utf8")
);
const PUPPET_SRC = fs.readFileSync(path.join(ROOT, "js", "vendor", "puppet.js"), "utf8");

const SR = 16000;
const FRAME_MS = 50;
const TICK_MS = 1000 / 60;          // 舞台 rAF（60fps）
const SPOKEN = "先别急着总结今天，喝口水，坐一会儿。";

/** 合成 PCM：按断句时间线，每个音节是一段 220Hz 正弦，标点/字间留空 */
function synthPcm(plan) {
  const total = plan.total + 200;
  const pcm = new Float32Array(Math.ceil((total / 1000) * SR));
  for (const s of plan.syllables) {
    const a = Math.floor((s.start / 1000) * SR);
    const b = Math.min(pcm.length, Math.floor(((s.start + s.dur) / 1000) * SR));
    for (let i = a; i < b; i++) pcm[i] = 0.8 * Math.sin((2 * Math.PI * 220 * i) / SR);
  }
  return pcm;
}

/** 真实播放路径：20Hz 包络帧 + 60fps tick 的 0.62/0.38 平滑 → lipFor */
function runEnvelopePath(plan, levels, frameMs) {
  const out = [];
  let last = 0;
  for (let t = 0; t <= plan.total + 300; t += TICK_MS) {
    const idx = TTS._envelopeIndex(t / 1000, frameMs, levels.length);
    const target = levels[idx] || 0;
    last = last * 0.62 + target * 0.38;
    out.push({ t, v: Stage._lipFor(last, true) });
  }
  return out;
}

/** 文本路径：50ms 喂一次 speechLevel，舞台按 rAF 读取并保持 */
function runTextPath(plan, rate) {
  const out = [];
  let fed = 0;
  let level = 0;
  let hold = 0;
  let lip = 0;
  for (let t = 0; t <= plan.total + 300; t += TICK_MS) {
    if (t >= fed) {
      level = TTS._speechLevel(plan, t);
      lip = Stage._lipFor(level, true);
      hold = lip > 0 ? 6 : 0;
      fed += FRAME_MS;
    }
    // 与 stage.js startLoop 同口径：按 60fps 帧当量递减保持量
    if (hold > 0) hold = Math.max(0, hold - (TICK_MS / 1000) * 60);
    out.push({ t, v: hold > 0 ? lip : 0 });
  }
  return out;
}

/** 旧实现：无 PCM 时只喂呼吸，嘴几乎恒定 */
function runBreathPath(plan) {
  const out = [];
  for (let t = 0; t <= plan.total + 300; t += TICK_MS) {
    out.push({ t, v: Stage._lipFor(TTS._breathLevel(t), true) });
  }
  return out;
}

/** ground truth：文本时间线的音节包络（未经过任何平滑） */
function truthLevels(plan) {
  const out = [];
  for (let t = 0; t <= plan.total + 300; t += TICK_MS) {
    out.push({ t, v: TTS._speechLevel(plan, t) });
  }
  return out;
}

function pearson(a, b) {
  const n = Math.min(a.length, b.length);
  let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
  ma /= n; mb /= n;
  let sab = 0, sa = 0, sb = 0;
  for (let i = 0; i < n; i++) {
    const da = a[i] - ma, db = b[i] - mb;
    sab += da * db; sa += da * da; sb += db * db;
  }
  return sab / Math.sqrt(Math.max(1e-12, sa * sb));
}

/** 互相关找滞后：正数 = 嘴比声音晚 */
function bestLag(out, truth, maxLagMs = 300, stepMs = 5) {
  const byT = new Map(truth.map((f) => [Math.round(f.t), f.v]));
  const at = (t) => byT.get(Math.round(t)) || 0;
  let best = { lag: 0, r: -2 };
  for (let lag = -maxLagMs; lag <= maxLagMs; lag += stepMs) {
    const a = out.map((f) => f.v);
    const b = out.map((f) => at(f.t - lag));
    const r = pearson(a, b);
    if (r > best.r) best = { lag, r };
  }
  return { lag: best.lag, r: Math.round(best.r * 1000) / 1000 };
}

/** 逐音节峰值滞后（p50/p95），用于报告"最长拖多少" */
function peakLagStats(out, plan) {
  const lags = [];
  for (const s of plan.syllables) {
    const target = s.start + s.dur * 0.45;
    let best = { d: 1e9, v: -1 };
    for (const f of out) {
      // 窗口只到本音节结束：再往后就会把下一个字的峰算进上一个字
      if (f.t < s.start - 30 || f.t > s.start + s.dur + 25) continue;
      if (f.v > best.v) best = { d: f.t - target, v: f.v };
    }
    if (best.v > 0) lags.push(Math.abs(best.d));
  }
  lags.sort((x, y) => x - y);
  const pick = (q) => lags[Math.min(lags.length - 1, Math.floor(lags.length * q))] || 0;
  return { n: lags.length, p50: Math.round(pick(0.5)), p95: Math.round(pick(0.95)) };
}

const PLAN = TTS._speechPlan(SPOKEN, 1.02);
const PCM = synthPcm(PLAN);
const LEVELS = TTS._envelopeLevels(PCM, Math.round((SR * FRAME_MS) / 1000));

test("分层契约不动：口型 7 档、眨眼 215ms（≈216ms 契约）、呼吸 0.55/0.45", () => {
  assert.equal(manifest.layers.mouthLevels.length, 7);
  const blink = [...PUPPET_SRC.matchAll(/this\.blink\.t > (0\.\d+)/g)]
    .map((m) => Number(m[1])
    );
  const blinkMs = Math.round(blink.reduce((a, b) => a + b, 0) * 1000);
  assert.equal(blinkMs, 215, "眨眼状态机总时长变了：" + blinkMs + "ms");
  assert.match(PUPPET_SRC, /bodySway \|\| 0\) \* 0\.55/);
  assert.match(PUPPET_SRC, /gazeX \|\| 0\) \* 0\.45/);
});

test("中文断句时间线：标点有停顿，字与字不重叠", () => {
  assert.ok(PLAN.syllables.length >= 15, "断句音节数异常");
  for (let i = 1; i < PLAN.syllables.length; i++) {
    const gap = PLAN.syllables[i].start - (PLAN.syllables[i - 1].start + PLAN.syllables[i - 1].dur);
    assert.ok(gap >= 0, "音节重叠");
  }
  // 任一处标点应带来 ≥150ms 的空档（逗号 180ms/1.02 ≈176ms）
  let maxGap = 0;
  for (let i = 1; i < PLAN.syllables.length; i++) {
    maxGap = Math.max(maxGap, PLAN.syllables[i].start - (PLAN.syllables[i - 1].start + PLAN.syllables[i - 1].dur));
  }
  assert.ok(maxGap >= 150, "标点停顿太短：" + maxGap + "ms");
});

test("服务端音频路径：包络驱动与断句节奏相关，滞后 ≤60ms", () => {
  const out = runEnvelopePath(PLAN, LEVELS, FRAME_MS);
  const truth = truthLevels(PLAN);
  const lag = bestLag(out, truth);
  const peaks = peakLagStats(out, PLAN);
  console.log(`[lipsync] envelope path lag=${lag.lag}ms r=${lag.r} peak p50=${peaks.p50} p95=${peaks.p95} (n=${peaks.n})`);
  assert.ok(lag.r >= 0.72, "包络与断句节奏相关性不足：" + lag.r);
  assert.ok(Math.abs(lag.lag) <= 60, "整体滞后过大：" + lag.lag + "ms");
  assert.ok(peaks.p95 <= 90, "逐音节峰值滞后 p95 过大：" + peaks.p95 + "ms");
});

test("文本路径（native/browser 无 PCM）：跟着断句起伏，滞后 ≤60ms", () => {
  const out = runTextPath(PLAN, 1.02);
  const truth = truthLevels(PLAN);
  const lag = bestLag(out, truth);
  const peaks = peakLagStats(out, PLAN);
  console.log(`[lipsync] text path lag=${lag.lag}ms r=${lag.r} peak p50=${peaks.p50} p95=${peaks.p95} (n=${peaks.n})`);
  assert.ok(lag.r >= 0.8, "文本口型与断句节奏相关性不足：" + lag.r);
  assert.ok(Math.abs(lag.lag) <= 60, "整体滞后过大：" + lag.lag + "ms");
  assert.ok(peaks.p95 <= 90, "逐音节峰值滞后 p95 过大：" + peaks.p95 + "ms");
});

test("旧呼吸路径没有音节信息：相关度≈0，嘴几乎恒定", () => {
  const out = runBreathPath(PLAN);
  const truth = truthLevels(PLAN);
  const lag = bestLag(out, truth);
  const vs = out.map((f) => f.v);
  const min = Math.min(...vs), max = Math.max(...vs);
  console.log(`[lipsync] breath path r=${lag.r} mouth range=${min.toFixed(3)}~${max.toFixed(3)}`);
  assert.ok(lag.r <= 0.2, "呼吸值意外与音节相关：" + lag.r);
  assert.ok(max - min <= 0.06, "呼吸张嘴幅度异常：" + (max - min));
});

test("文本语速偏差：误差随时长线性累计（诚实的上限）", () => {
  // 实际语速比估算慢 15% 时，第 n 个字滞后 0.15*n*190ms
  const n = PLAN.syllables.length;
  const worstMs = Math.round(0.15 * n * TTS._charMs);
  console.log(`[lipsync] rate mismatch ±15% → 句末累计 ${worstMs}ms（${n} 字）`);
  assert.ok(worstMs > 300 && worstMs < 900, "模型口径异常：" + worstMs);
});
