/* tts.js 包络关键帧单测：口型不依赖实时 analyser（MuMu 上 analyser RMS 恒 0）。 */
const test = require("node:test");
const assert = require("node:assert/strict");
const TTS = require("../js/tts.js");

const { _envelopeLevels: envelopeLevels, _frameMs: FRAME_MS } = TTS;

function makePcm(total, fn) {
  const ch = new Float32Array(total);
  for (let i = 0; i < total; i++) ch[i] = fn(i, total);
  return ch;
}

test("帧长默认 50ms（20Hz，足够 15/30fps 舞台）", () => {
  assert.equal(FRAME_MS, 50);
});

test("静音 PCM 全 0：无音频时嘴不动（不随机张嘴）", () => {
  const ch = makePcm(8000, () => 0);
  const levels = envelopeLevels(ch, 400); // 50ms @ 8kHz
  assert.equal(levels.length, 20);
  assert.deepEqual(Array.from(levels), Array(20).fill(0));
});

test("前半段正弦、后半段静音：包络峰值 1，静音段回 0", () => {
  const ch = makePcm(8000, (i) => (i < 4000 ? Math.sin((2 * Math.PI * 220 * i) / 8000) : 0));
  const levels = envelopeLevels(ch, 400);
  const head = Array.from(levels.slice(0, 5));
  const tail = Array.from(levels.slice(-5));
  for (const v of head) assert.ok(v > 0.6 && v <= 1, `有声段 ${v}`);
  for (const v of tail) assert.ok(v < 0.001, `静音段 ${v}`);
  assert.equal(Math.max(...levels), 1); // 归一化到峰值的最大值恰为 1
});

test("渐强音量 → 包络单调上升；帧数与时长成正比", () => {
  const ch = makePcm(4000, (i, total) => Math.sin(i / 10) * (i / total));
  const levels = envelopeLevels(ch, 400); // 10 帧
  assert.equal(levels.length, 10);
  assert.ok(levels[9] > levels[0], "后段应更响");
  const half = envelopeLevels(makePcm(2000, (i) => Math.sin(i / 5)), 400);
  assert.equal(half.length, 5);
});

test("空/超短 PCM 不崩：至少 1 帧", () => {
  assert.equal(envelopeLevels(new Float32Array(0), 400).length, 1);
  assert.equal(envelopeLevels(new Float32Array(10), 400).length, 1);
});
