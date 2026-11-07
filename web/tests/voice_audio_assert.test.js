/* voice_audio_assert.test.js · 无 TTS 环境下的音频断言（node 层，零模拟器）
 *
 * 覆盖用户要求的三件事：
 *   1) 有音频输出（真的播放了 blob，不是「speak 返回 true」就算数）
 *   2) 时长合理（与文本时间线同口径，防止只播半个字）
 *   3) 未中途截断（短于下限 / 播一半没声了）
 * 并明确：
 *   - 「有音频但全是静音」必须判红，不许算通过；
 *   - 「环境没有 TTS」必须判失败并保留降级路径，不许当 skip 通过。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const TTS = require("../js/tts.js");
const probe = require("./voice_probe.js");
const VoiceTip = require("../js/voice_tip.js");

const TEXT = "今晚也辛苦啦，先喝口水，慢慢来。";

function memStore(init) {
  const m = Object.assign({}, init || {});
  return {
    getItem: (k) => (k in m ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    removeItem: (k) => { delete m[k]; },
  };
}

/**
 * 可控假 TTS：fetch 返回真实 WAV 字节；Audio 播放并推进 currentTime；
 * AudioContext.decodeAudioData 解出同一份 PCM，让包络真的非零。
 */
function fakeWin(opts) {
  const o = opts || {};
  const state = {
    audios: [], raf: [], urls: new Map(), seq: 0,
    played: 0, clockMs: 0, periods: [],
  };
  const win = {
    performance: { now: () => state.clockMs },
    requestAnimationFrame: (fn) => { state.raf.push(fn); return state.raf.length; },
    cancelAnimationFrame: () => { state.raf.length = 0; },
    URL: {
      createObjectURL: (blob) => {
        const u = `blob:test-${++state.seq}`;
        state.urls.set(u, blob && blob.wav ? blob.wav : null);
        return u;
      },
      revokeObjectURL: (u) => { state.urls.delete(u); },
    },
  };
  if (o.wav || o.httpStatus) win.XIAOMAN_TTS_SERVER = "http://tts.test";
  win.fetch = async (url) => {
    const u = String(url);
    if (u.startsWith("http")) {
      const status = o.httpStatus || 200;
      if (status !== 200) {
        return { ok: false, status, json: async () => ({ error: "provider disabled" }) };
      }
      return { ok: true, status: 200, blob: async () => ({ wav: o.wav }) };
    }
    const wav = state.urls.get(u);
    if (!wav) return { ok: false, status: 404 };
    return { ok: true, status: 200, arrayBuffer: async () => wav };
  };
  win.Audio = function (src) {
    const a = this;
    a.src = src || "";
    a.currentTime = 0;
    a.readyState = 0;
    a.duration = 0;
    a.paused = true;
    state.audios.push(a);
    a.play = () => {
      state.played++;
      if (o.playReject) return Promise.reject(new Error("play blocked"));
      if (o.stall) return Promise.resolve();          // 请求发出但一直没开播 → 触发超时降级
      a.paused = false;
      a.readyState = 4;
      const wav = state.urls.get(a.src);
      a.duration = wav ? probe.decodeWav(wav).durationMs / 1000 : 0;
      setTimeout(() => { if (a.onplaying) a.onplaying(); }, 0);
      return Promise.resolve();
    };
    a.pause = () => { a.paused = true; };
  };
  if (o.decode !== false) {
    win.AudioContext = function () {
      this.decodeAudioData = async (buf) => {
        const d = probe.decodeWav(buf);
        return {
          sampleRate: d.sampleRate,
          duration: d.durationMs / 1000,
          getChannelData: () => d.samples,
        };
      };
      this.close = () => {};
    };
  }
  return { win, state };
}

const flush = async (n) => {
  for (let i = 0; i < (n || 4); i++) await Promise.resolve();
};

function pump(state, audio, durationMs, stepMs) {
  const step = stepMs || 50;
  let guard = 0;
  while (state.raf.length && audio.currentTime < durationMs / 1000 && guard++ < 4000) {
    state.clockMs += step;
    audio.currentTime = Math.min(durationMs / 1000, audio.currentTime + step / 1000);
    const q = state.raf.splice(0);
    for (const fn of q) fn();
  }
  audio.currentTime = durationMs / 1000;
  if (audio.onended) audio.onended();
}

test("假 TTS：真的播放了音频、包络非静音、时长合理、未截断", async () => {
  const exp = probe.expectedMs(TEXT);
  const wav = probe.buildWav({ durationMs: exp, voicedMs: exp, freq: 380, amplitude: 0.6 });
  const { win, state } = fakeWin({ wav });
  const engine = TTS._create(win);

  let ended = null;
  const res = await engine.speak(TEXT, () => {}, (ok) => { ended = ok; });
  assert.equal(res.spoke, true);
  assert.equal(res.provider, "server", "必须走真音频路（server），而不是只返回 true");
  await flush();
  await new Promise((r) => setTimeout(r, 5));   // 让 onplaying 回调落地
  assert.equal(state.played, 1, "audio.play() 必须真的被调用");

  const audio = state.audios[state.audios.length - 1];
  assert.match(audio.src, /^blob:/, "播放源必须是取回来的音频 blob");
  pump(state, audio, exp);
  await flush(2);
  assert.equal(ended, true);

  const timing = engine._lastTiming();
  assert.ok(timing.frames > 0, "播放期间必须有口型帧");
  assert.ok(timing.envFrames > 0, "必须真的解码出 PCM 包络（不是呼吸兜底）");
  assert.ok(timing.firstNonZeroAt > 0, "必须观测到非静音包络帧");

  const j = probe.judge(wav, TEXT);
  assert.equal(j.nonzero, true, "非静音");
  assert.equal(j.durationOk, true, "时长与文本时间线同口径");
  assert.equal(j.truncated, false, "没有中途截断");
  assert.equal(j.ok, true);

  assert.ok(audio.currentTime >= audio.duration - 0.001, "必须播到结尾");
  assert.equal(engine._lastFailure(), null, "成功不该留下失败记录");
});

test("静音 PCM（播放成功但没声音）：断言必须判红", async () => {
  const exp = probe.expectedMs(TEXT);
  const wav = probe.buildWav({ durationMs: exp, silence: true });
  const { win, state } = fakeWin({ wav });
  const engine = TTS._create(win);

  let ended = null;
  const res = await engine.speak(TEXT, () => {}, (ok) => { ended = ok; });
  assert.equal(res.spoke, true);
  await flush();
  await new Promise((r) => setTimeout(r, 5));
  pump(state, state.audios[state.audios.length - 1], exp);
  await flush(2);

  assert.equal(ended, true, "设备确实播完了");
  // 关键：客户端听不出「有没有声」，离线 PCM 判定必须把它抓住
  const j = probe.judge(wav, TEXT);
  assert.equal(j.ok, false, "全是静音不能算通过");
  assert.equal(j.nonzero, false);
  assert.ok(j.reasons.includes("silent"));
});

test("中途截断（只给了 40% 时长）：断言必须判红", async () => {
  const exp = probe.expectedMs(TEXT);
  const wav = probe.buildWav({ durationMs: Math.round(exp * 0.4), voicedMs: Math.round(exp * 0.4) });
  const j = probe.judge(wav, TEXT);
  assert.equal(j.ok, false);
  assert.equal(j.truncated, true);
  assert.ok(j.reasons.includes("truncated"));
});

test("中途截断（总时长够但后半段没声）：断言必须判红", () => {
  const exp = probe.expectedMs(TEXT);
  const wav = probe.buildWav({ durationMs: exp, voicedMs: Math.round(exp * 0.4) });
  const j = probe.judge(wav, TEXT);
  assert.equal(j.truncated, true, "播到一半没声必须算截断");
  assert.equal(j.ok, false);
});

test("无 TTS 环境：判失败（不 skip），文字与失败日志仍成立", async () => {
  const { win } = fakeWin({});          // 没有任何 provider
  const engine = TTS._create(win);

  let silentRes = null;
  const res = await engine.speakSafe(TEXT, () => {}, () => {}, (r) => { silentRes = r; });
  assert.equal(res.spoke, false, "环境没有 TTS 必须判失败，不能当通过");
  assert.equal(res.provider, "none");
  assert.equal(silentRes && silentRes.provider, "none", "onSilent 必须被通知（提示条路径不变）");

  const f = engine._lastFailure();
  assert.ok(f, "失败必须留下诊断");
  assert.equal(f.error, "no_provider");
  assert.equal(f.textLen, Array.from(TEXT).length);
  assert.match(f.textHash, /^[0-9a-f]{8}$/);

  // 降级路径：提示条照常出现，日志里能按原因/长度桶聚合
  const store = memStore();
  const tip = VoiceTip.create({ el: null, store, now: () => 9, setTimeout: () => 1, clearTimeout: () => {} });
  tip.show("start_failed", f);
  const e = tip.log()[0];
  assert.equal(e.reason, "start_failed");
  assert.equal(e.detail.error, "no_provider");
  assert.equal(e.detail.bucket, "short");
  assert.equal(tip.stats().byError.no_provider, 1);
});

test("服务端 503（算力/provider 不足）：记录 http 码并降级，不冒充成功", async () => {
  const { win, state } = fakeWin({ httpStatus: 503 });
  const engine = TTS._create(win);
  let ended = null;
  const res = await engine.speak(TEXT, () => {}, (ok) => { ended = ok; });
  assert.equal(res.spoke, false);
  assert.equal(ended, false, "没出声就不能回调 onEnd(true)");
  assert.equal(state.played, 0, "拿不到音频就不该创建播放");
  const f = engine._lastFailure();
  assert.equal(f.error, "http_503");
  assert.equal(f.httpStatus, 503);
  assert.equal(f.provider, "server");
});

test("服务端拿到音频但迟迟不开播：超时判失败并走降级（start_timeout 可见）", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const exp = probe.expectedMs(TEXT);
  const wav = probe.buildWav({ durationMs: exp });
  const { win, state } = fakeWin({ wav, stall: true });
  const engine = TTS._create(win);

  let ended = null;
  const res = await engine.speak(TEXT, () => {}, (ok) => { ended = ok; });
  assert.equal(res.spoke, true, "音频已拿到，server 接手播放");
  assert.equal(state.played, 1);

  // 第一跳：包络路 7s 未开播 → 判超时 → 纯播放再试
  t.mock.timers.tick(7000);
  await flush();
  // 第二跳：纯播放同样未开播 → 7s 再超时 → 无 browser 兜底 → onEnd(false)
  t.mock.timers.tick(7000);
  await flush();
  assert.equal(ended, false, "超时必须明确「没播成」");

  const f = engine._lastFailure();
  assert.equal(f.error, "start_timeout");
  assert.equal(f.timedOut, true);
  assert.ok(f.attempts.length >= 2, "两跳尝试都要留痕");
});
