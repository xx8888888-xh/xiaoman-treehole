/* voice_probe.js · 离线音频断言工具（node 层，不依赖浏览器/模拟器）
 *
 * 目的：把「语音到底有没有响」变成可自动断言的事实。
 *   buildWav  ：可控假 TTS —— 按文本时长合成一段 16-bit PCM（可指定静音/截断）
 *   decodeWav ：把 WAV 解成归一化采样（等价于浏览器 decodeAudioData 的结果）
 *   analyze   ：RMS / 峰值 / 静音占比 / 首尾静音 / 有声时长
 *   judge     ：非静音 + 时长合理 + 未中途截断
 *
 * 离线判定口径（与 TTS.speechPlan 同一文本时间线，±容忍见下）：
 *   expectedMs = TTS._speechPlan(text, rate).total   （190ms/字 + 标点停顿 + 240ms 尾）
 *   durationOk = 0.45×expected ≤ 实测时长 ≤ 2.6×expected
 *   truncated  = 实测时长 < 0.45×expected
 *             或 有声段 < 55% 且 尾部静音 > 30%（播到一半没声了）
 */
const TTS = require("../js/tts.js");

const CHAR_RATE = 1.02; // 默认语速档（与设置页 normal 同值）

function buildWav(opts) {
  const o = opts || {};
  const sampleRate = o.sampleRate || 16000;
  const durationMs = Math.max(1, Math.round(o.durationMs || 1000));
  const voicedMs = o.voicedMs == null ? durationMs : Math.min(durationMs, Math.max(0, o.voicedMs));
  const freq = o.freq || 440;
  const amplitude = o.amplitude == null ? 0.5 : o.amplitude;
  const silence = o.silence === true;
  const n = Math.round((sampleRate * durationMs) / 1000);
  const vN = Math.round((sampleRate * voicedMs) / 1000);
  const buf = Buffer.alloc(44 + n * 2);
  buf.write("RIFF", 0, "ascii");
  buf.writeUInt32LE(36 + n * 2, 4);
  buf.write("WAVE", 8, "ascii");
  buf.write("fmt ", 12, "ascii");
  buf.writeUInt32LE(16, 16);          // fmt chunk size
  buf.writeUInt16LE(1, 20);           // PCM
  buf.writeUInt16LE(1, 22);           // mono
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36, "ascii");
  buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    let v = 0;
    if (!silence && i < vN) {
      // 10Hz 轻幅度起伏：让包络有音节感，而不是一条恒定正弦
      const mod = 0.6 + 0.4 * Math.sin((2 * Math.PI * 10 * i) / sampleRate);
      v = amplitude * mod * Math.sin((2 * Math.PI * freq * i) / sampleRate);
    }
    buf.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(v * 32767))), 44 + i * 2);
  }
  return buf;
}

function decodeWav(buf) {
  const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
  let sampleRate = 16000;
  let dataOff = -1;
  let dataLen = 0;
  // 按 chunk 遍历，兼容带 LIST/fact 等附加块的 WAV
  let off = 12;
  while (off + 8 <= b.length) {
    const id = b.toString("ascii", off, off + 4);
    const size = b.readUInt32LE(off + 4);
    if (id === "fmt ") sampleRate = b.readUInt32LE(off + 12);
    if (id === "data") { dataOff = off + 8; dataLen = size; break; }
    off += 8 + size;
  }
  if (dataOff < 0) throw new Error("wav: no data chunk");
  const n = Math.floor(Math.min(dataLen, b.length - dataOff) / 2);
  const samples = new Float32Array(n);
  for (let i = 0; i < n; i++) samples[i] = b.readInt16LE(dataOff + i * 2) / 32768;
  return { sampleRate, samples, durationMs: (n / sampleRate) * 1000 };
}

function analyze(wavOrObj) {
  const d = wavOrObj && wavOrObj.samples ? wavOrObj : decodeWav(wavOrObj);
  const s = d.samples;
  const sr = d.sampleRate;
  const n = s.length;
  let sum = 0, peak = 0;
  for (let i = 0; i < n; i++) { const a = Math.abs(s[i]); if (a > peak) peak = a; sum += s[i] * s[i]; }
  const rms = n ? Math.sqrt(sum / n) : 0;
  const thr = Math.max(0.005, peak * 0.05);   // 相对峰值 5% 以下算静音
  let first = -1, last = -1, silent = 0;
  for (let i = 0; i < n; i++) {
    if (Math.abs(s[i]) > thr) { if (first < 0) first = i; last = i; } else silent++;
  }
  const leadingSilenceMs = first < 0 ? d.durationMs : (first / sr) * 1000;
  const trailingSilenceMs = last < 0 ? d.durationMs : ((n - last - 1) / sr) * 1000;
  const voicedMs = first < 0 ? 0 : ((last - first + 1) / sr) * 1000;
  return {
    sampleRate: sr, durationMs: d.durationMs, rms, peak,
    silentRatio: n ? silent / n : 1,
    leadingSilenceMs, trailingSilenceMs, voicedMs,
  };
}

function expectedMs(text, rate) {
  try {
    return TTS._speechPlan(text, rate || CHAR_RATE).total;
  } catch (e) {
    const chars = Array.from(String(text || "")).length;
    return Math.round(chars * 190 / (rate || CHAR_RATE)) + 240;
  }
}

/** 离线判定：有声音、时长合理、没有中途截断 */
function judge(wavOrObj, text, rate) {
  const a = wavOrObj && wavOrObj.rms != null ? wavOrObj : analyze(wavOrObj);
  const exp = expectedMs(text, rate);
  const reasons = [];
  const nonzero = a.rms >= 0.02 && a.peak >= 0.05;
  if (!nonzero) reasons.push("silent");
  const durationOk = a.durationMs >= exp * 0.45 && a.durationMs <= exp * 2.6;
  if (!durationOk) reasons.push("duration");
  const truncated = a.durationMs < exp * 0.45
    || (a.voicedMs < a.durationMs * 0.55 && a.trailingSilenceMs > a.durationMs * 0.30);
  if (truncated) reasons.push("truncated");
  return {
    ok: nonzero && durationOk && !truncated,
    nonzero, durationOk, truncated,
    durationMs: Math.round(a.durationMs),
    expectedMs: exp,
    rms: +a.rms.toFixed(4),
    voicedMs: Math.round(a.voicedMs),
    trailingSilenceMs: Math.round(a.trailingSilenceMs),
    reasons,
  };
}

module.exports = { buildWav, decodeWav, analyze, judge, expectedMs, CHAR_RATE };
