/* tts_provider.test.js · provider 接口单测（全部 mock，绝不发声、不开音频/服务）
 * 只校验：接口形状、available 判定、参数映射、降级链、静音开关、stop 幂等。 */
const test = require("node:test");
const assert = require("node:assert/strict");
const TTS = require("../js/tts.js");

/** 造一个假的 window：不含 Audio/AudioContext/fetch 真相，绝不触发真实播放 */
function fakeWin(over = {}) {
  return Object.assign({ performance: { now: () => 0 } }, over);
}

function makeEngine(win, cfg) {
  win.API = { loadCfg: () => cfg || {} };
  return TTS._create(win);
}

test("provider 接口齐备：native/server/browser 各有 available/speak/stop", () => {
  const engine = TTS._create(fakeWin());
  for (const id of ["native", "server", "browser"]) {
    const p = engine.providerObjects[id];
    assert.equal(p.id, id);
    for (const m of ["available", "speak", "stop"]) assert.equal(typeof p[m], "function", `${id}.${m}`);
  }
  assert.deepEqual(engine.providers, ["native", "server", "browser"]);
});

test("provider 缺失时 available()=false：空环境整条链路退到 none", () => {
  const engine = makeEngine(fakeWin(), {});
  const objs = engine.providerObjects;
  assert.equal(objs.native.available(), false);
  assert.equal(objs.browser.available(), false);
  assert.equal(objs.server.available({}), false);
  assert.equal(engine.available(), false);
  assert.deepEqual(engine.chain({}), ["none"]);
  assert.equal(engine.provider({}), "none");
});

test("远程 provider 默认关闭：只选 server 但没配地址 → 不可用", () => {
  const engine = makeEngine(fakeWin());
  const server = engine.providerObjects.server;
  // 旧配置里的默认 ttsBase 不算显式启用
  assert.equal(server.available({ tts: "server", ttsBase: "http://127.0.0.1:8903" }), false);
  // 显式 ttsServer / 注入 XIAOMAN_TTS_SERVER 才启用
  assert.equal(server.available({ tts: "server", ttsServer: "http://127.0.0.1:8903" }), true);
  const injected = makeEngine(fakeWin({ XIAOMAN_TTS_SERVER: "https://tts.example.com/" }));
  assert.equal(injected.providerObjects.server.available({ tts: "off" }), true);
  assert.deepEqual(injected.chain({ tts: "off" }), ["none"], "off 优先于注入");
});

test("链序 native → server → browser；全可用时优先 native", () => {
  const win = fakeWin({
    AndroidTTS: { available: () => true, speakRate: () => true, stop: () => {} },
    speechSynthesis: { speak: () => {}, cancel: () => {} },
    SpeechSynthesisUtterance: function (t) { this.text = t; },
    XIAOMAN_TTS_SERVER: "https://tts.example.com"
  });
  const engine = makeEngine(win, { tts: "server" });
  assert.deepEqual(engine.chain({ tts: "server" }), ["native", "server", "browser"]);
  assert.equal(engine.provider({ tts: "server" }), "native");
  assert.equal(engine.available({ tts: "server" }), true);
});

test("native provider：真桥可用时传入文本 + 语速档（不播放真实音频）", async () => {
  const calls = [];
  const win = fakeWin({
    AndroidTTS: {
      available: () => true,
      speakRate: (text, rate) => { calls.push({ text, rate }); return true; },
      stop: () => calls.push({ stopped: true })
    }
  });
  const engine = makeEngine(win, { tts: "browser", ttsRate: "fast" });
  const frames = [];
  try {
    const res = await engine.speak("你好呀", (lv) => frames.push(lv), () => frames.push("end"));
    assert.deepEqual(res, { spoke: true, provider: "native" });
    const played = calls.filter((c) => c.text);
    assert.equal(played.length, 1);
    assert.equal(played[0].text, "你好呀");
    assert.equal(played[0].rate, 1.18, "fast 档映射 1.18");
    // 原生完成回调穿透：多次回调只结算一次
    win.__ttsEnded();
    win.__ttsEnded();
    assert.equal(frames.filter((f) => f === "end").length, 1, "onEnd 恰好一次");
  } finally {
    engine.stop();   // 清掉假包络 interval，避免留下定时器
  }
  assert.ok(calls.some((c) => c.stopped), "stop 透传到原生桥");
});

test("静音开关：muted 时不出声、available=false、onEnd 仍回调（文字照常）", async () => {
  const calls = [];
  const win = fakeWin({
    AndroidTTS: { available: () => true, speakRate: () => { calls.push("played"); return true; }, stop: () => {} }
  });
  const engine = makeEngine(win, { tts: "browser" });
  engine.setMuted(true);
  assert.equal(engine.isMuted(), true);
  assert.equal(engine.available(), false);
  let ended = 0;
  const res = await engine.speak("这句不该出声", () => {}, () => { ended++; });
  assert.deepEqual(res, { spoke: false, provider: "muted" });
  assert.equal(calls.length, 0, "没有调用任何播放");
  assert.equal(ended, 1);
});

test("远程 provider：POST body 参数正确、文本不进 URL、503 只降级", async () => {
  const seen = [];
  const win = fakeWin({
    fetch: async (url, opts) => {
      seen.push({ url, opts });
      return { ok: false, status: 503, json: async () => ({ error: "provider disabled" }) };
    }
  });
  const engine = makeEngine(win, { tts: "server", ttsServer: "https://tts.example.com/", voice: "zh-CN-YunxiaNeural", ttsRate: "slow" });
  let ended = 0;
  const res = await engine.speak("今天有点累", () => {}, () => { ended++; });
  assert.deepEqual(res, { spoke: false, provider: "none" }, "全链路不可用时静默失败");
  assert.equal(ended, 1);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, "https://tts.example.com/tts");
  assert.equal(seen[0].opts.method, "POST");
  assert.ok(!seen[0].url.includes("今天"), "原文不进查询串");
  const body = JSON.parse(seen[0].opts.body);
  assert.equal(body.text, "今天有点累");
  assert.equal(body.voice, "zh-CN-XiaoyiNeural", "非法音色回默认");
  assert.equal(body.rate, "slow");
});

test("browser provider：语速/语言参数正确，stop 会 cancel（不触碰真实语音）", async () => {
  const spoken = [];
  let canceled = 0;
  function Utt(text) { this.text = text; }
  const win = fakeWin({
    speechSynthesis: { speak: (u) => spoken.push(u), cancel: () => { canceled++; } },
    SpeechSynthesisUtterance: Utt
  });
  const engine = makeEngine(win, { tts: "browser", ttsRate: "slow" });
  let ended = 0;
  try {
    const res = await engine.speak("晚安", () => {}, () => { ended++; });
    assert.deepEqual(res, { spoke: true, provider: "browser" });
    assert.equal(spoken.length, 1);
    assert.equal(spoken[0].lang, "zh-CN");
    assert.equal(spoken[0].rate, 0.86);
    assert.equal(spoken[0].text, "晚安");
    spoken[0].onend();
    spoken[0].onerror();          // 只结算一次
    assert.equal(ended, 1, "onend/onerror 不会重复 onEnd");
  } finally {
    engine.stop();
  }
  assert.equal(canceled >= 1, true);
});

test("stop 幂等、空文本不发声但 onEnd 必到", async () => {
  const engine = makeEngine(fakeWin(), {});
  assert.doesNotThrow(() => { engine.stop(); engine.stop(); });
  let ended = 0;
  const res = await engine.speak("   ", () => {}, () => { ended++; });
  assert.deepEqual(res, { spoke: false, provider: "none" });
  assert.equal(ended, 1);
});

test("包络帧下标：越界钳制在 [0, len-1]", () => {
  const idx = TTS._envelopeIndex;
  assert.equal(idx(-1, 50, 10), 0);
  assert.equal(idx(0, 50, 10), 0);
  assert.equal(idx(0.15, 50, 10), 3);
  assert.equal(idx(99, 50, 10), 9);
  assert.equal(idx(0, 50, 0), 0);
  assert.ok(TTS._breathLevel(0) >= 0.01 && TTS._breathLevel(0) <= 0.1, "呼吸极轻微");
});
