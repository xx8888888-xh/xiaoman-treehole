/* tts_provider.test.js · provider 接口单测（全部 mock，绝不发声、不开音频/服务）
 * 只校验：接口形状、available 判定、参数映射、降级链、静音开关、stop 幂等。 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
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

test("链序跟随设置：server 优先服务端、browser 优先本机（全可用时按选择走）", () => {
  const win = fakeWin({
    AndroidTTS: { available: () => true, speakRate: () => true, stop: () => {} },
    speechSynthesis: { speak: () => {}, cancel: () => {} },
    SpeechSynthesisUtterance: function (t) { this.text = t; },
    XIAOMAN_TTS_SERVER: "https://tts.example.com"
  });
  const engine = makeEngine(win, { tts: "server" });
  // 审计修复：旧版永远先 native，设置页的"服务端语音"形同虚设
  assert.deepEqual(engine.chain({ tts: "server" }), ["server", "native", "browser"]);
  assert.equal(engine.provider({ tts: "server" }), "server");
  assert.equal(engine.available({ tts: "server" }), true);
  // 选"本机语音"时浏览器语音优先；没有浏览器语音则落安卓系统语音
  assert.deepEqual(engine.chain({ tts: "browser" }), ["browser", "native"]);
  assert.equal(engine.provider({ tts: "browser" }), "browser");
  // 未配置服务端地址：server 偏好自动落到本机，不阻塞出声
  const localOnly = makeEngine(fakeWin({
    AndroidTTS: { available: () => true, speakRate: () => true, stop: () => {} },
    speechSynthesis: { speak: () => {}, cancel: () => {} },
    SpeechSynthesisUtterance: function (t) { this.text = t; }
  }), { tts: "server", ttsServer: "" });
  assert.deepEqual(localOnly.chain({ tts: "server", ttsServer: "" }), ["native", "browser"]);
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
  const endedOk = [];
  try {
    const res = await engine.speak("晚安", () => {}, (ok) => { ended++; endedOk.push(ok); });
    assert.deepEqual(res, { spoke: true, provider: "browser" });
    assert.equal(spoken.length, 1);
    assert.equal(spoken[0].lang, "zh-CN");
    assert.equal(spoken[0].rate, 0.86);
    assert.equal(spoken[0].text, "晚安");
    spoken[0].onend();
    spoken[0].onerror();          // 只结算一次
    assert.equal(ended, 1, "onend/onerror 不会重复 onEnd");
    assert.deepEqual(endedOk, [true], "真出声了：onEnd(true) 才允许打「语音已播」");
  } finally {
    engine.stop();
  }
  assert.equal(canceled >= 1, true);
});

test("tts=off：不出声，且不得把气泡标成「语音已播」（状态与文案一致）", async () => {
  // 回归用例：AVD 上 provider=off 时气泡仍显示「🔊 语音已播」。
  // 标记由 onEnd 写入（app.js: ChatPager.markVoice），所以 off 分支必须不回调 onEnd。
  const calls = [];
  const win = fakeWin({
    AndroidTTS: { available: () => true, speakRate: () => { calls.push("played"); return true; }, stop: () => {} },
    speechSynthesis: { speak: () => calls.push("browser"), cancel: () => {} },
    SpeechSynthesisUtterance: function (t) { this.text = t; }
  });
  const engine = makeEngine(win, { tts: "off" });
  const bubble = { spoken: false };            // 模拟一条消息记录
  let ended = 0;
  const res = await engine.speak("这句不该出声", () => {},
    () => { ended++; bubble.spoken = true; });
  assert.deepEqual(res, { spoke: false, provider: "off" }, "off 如实返回，不伪装成 none");
  assert.equal(calls.length, 0, "任何 provider 都不该被调用");
  assert.equal(ended, 0, "onEnd 不得回调");
  assert.equal(bubble.spoken, false, "界面不得出现「🔊 语音已播」");
  assert.equal(engine.provider({ tts: "off" }), "off", "设置页应显示 off 而非 none");
  assert.equal(engine.available({ tts: "off" }), false);
});

test("真实页面：API 是全局 const（不在 window 上）时，状态也必须读到配置", () => {
  // 回归：api.js 的 API 是顶层 const，浏览器里 window.API 恒为 undefined。
  // 只查 W.API 会让真机 provider 永远是 none，与设置页选的 tts 不一致。
  const saved = globalThis.API;
  globalThis.API = { loadCfg: () => ({ tts: "off" }) };
  const engine = TTS._create(fakeWin());   // 刻意不注入 win.API，模拟真实浏览器
  try {
    assert.equal(engine.provider(), "off", "设置页显示 off 而不是 none");
    assert.equal(engine.available(), false);
    assert.deepEqual(engine.chain(), ["none"]);
  } finally {
    if (saved === undefined) delete globalThis.API; else globalThis.API = saved;
  }
});

test("stop 幂等、空文本不发声但 onEnd 必到", async () => {
  const engine = makeEngine(fakeWin(), {});
  assert.doesNotThrow(() => { engine.stop(); engine.stop(); });
  let ended = 0;
  const res = await engine.speak("   ", () => {}, () => { ended++; });
  assert.deepEqual(res, { spoke: false, provider: "none" });
  assert.equal(ended, 1);
});

test("服务端音频拿到了但设备播不出来：onEnd(false) 且只一次（不得打「语音已播」）", async () => {
  // 回归（云端 AVD 实测）：server 返回 200，但设备侧 play() reject（无音频设备/
  // 自动播放被拦）→ 旧实现仍然 onEnd()，气泡被打成「🔊 语音已播」，
  // 与同屏的「语音暂时没法出声」提示自相矛盾。onEnd 必须带 ok 事实。
  const calls = [];
  const win = fakeWin({
    fetch: async () => ({ ok: true, blob: async () => ({ type: "audio/wav" }) }),
    URL: { createObjectURL: () => "blob:fake", revokeObjectURL: () => {} },
    Audio: function FakeAudio() {
      this.currentTime = 0; this.readyState = 0;
      this.play = () => Promise.reject(new Error("no audio device"));
      this.pause = () => {};
    }
  });
  const engine = makeEngine(win, { tts: "server", ttsServer: "http://127.0.0.1:8903" });
  const res = await engine.speak("今天有点累", () => {}, (ok) => { calls.push(ok); });
  assert.deepEqual(res, { spoke: true, provider: "server" }, "server 接管了播放（已拿到音频）");
  await new Promise((r) => setTimeout(r, 30));   // 等两段播放降级链走完
  assert.deepEqual(calls, [false], "只结算一次，且明确 ok=false");
  assert.equal(calls.includes(true), false, "没出声绝不能报成功");
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

// ---------- speakSafe：文字优先，语音失败绝不反过来影响文本/发送 ----------
test("speakSafe：语音回调/链路抛错时永不 reject，只如实报告没出声", async () => {
  // 真实崩溃点：口型回调（stage 被卸载/绑定丢失）、或 provider 内部异常。
  // 文字早已落屏，这类异常必须止步于语音层，不能冒泡到 send()/sendSplit() 的失败分支。
  const engine = makeEngine(fakeWin(), { tts: "off" });
  const silent = [];
  const res = await engine.speakSafe("你好", () => { throw new Error("lip boom"); },
    () => {}, r => silent.push(r));
  assert.deepEqual(res, { spoke: false, provider: "error" });
  assert.equal(silent.length, 1, "静默提示回调一次");
  assert.equal(silent[0].spoke, false);
});

test("speakSafe：tts=off 仍只静默收尾，不回调 onEnd（气泡不标「已播」）", async () => {
  const engine = makeEngine(fakeWin(), { tts: "off" });
  const seen = [];
  const res = await engine.speakSafe("这句不该出声", () => {},
    () => seen.push("end"), r => seen.push(r.provider));
  assert.deepEqual(res, { spoke: false, provider: "off" });
  assert.deepEqual(seen, ["off"], "off 只给静默提示，不得回调 onEnd");
});

test("app.js：先渲染文字，语音统一走 speakSafe（不 await、不阻塞发送）", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const app = fs.readFileSync(path.join(__dirname, "..", "js", "app.js"), "utf8");
  assert.equal(/TTS\.speak\(/.test(app), false, "app.js 不得直接调用会抛的 TTS.speak");
  assert.match(app, /TTS\.speakSafe\(/, "统一走 speakSafe（内部吞掉同步/异步失败）");
  const split = app.slice(app.indexOf("async function sendSplit"),
    app.indexOf("function finalizeStreamed"));
  assert.ok(split.indexOf('addMsg(seg, "them")') < split.indexOf("await finalizeReply"),
    "分条发送必须先把文字落屏，再进 finalizeReply（语音/舞台都在文字之后）");
});

test("延迟探针入口 _lastTiming 在引擎作用域内可取（回归：曾 ReferenceError）", () => {
  // 真机 CI 里 TTS._lastTiming() 抛 ReferenceError: lastTiming is not defined，
  // 因为旧的导出写在 IIFE 外层作用域；单测此前没调用过这个入口。
  const engine = TTS._create(fakeWin());
  assert.equal(typeof engine._lastTiming, "function");
  assert.equal(engine._lastTiming(), null, "未播放时应为 null");
});

test("CSP 必须放行 blob: 到 connect-src（否则包络解码 fetch 被拦，口型永远只有呼吸）", () => {
  // 云端 Chromium 实测 envError="Failed to fetch"、envFrames=0：
  // computeEnvelope() 对 createObjectURL 的 blob: URL 再 fetch 一次，
  // 而 CSP connect-src 只写了 'self' http: https: ws: wss: —— blob: 被拦。
  // 结果：服务端语音能出声，但口型一直走呼吸兜底（首音节包络帧永远不出现）。
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const m = html.match(/Content-Security-Policy" content="([^"]+)"/);
  assert.ok(m, "index.html 缺少 CSP meta");
  const csp = m[1];
  for (const dir of ["connect-src", "media-src"]) {
    const part = csp.split(";").map((s) => s.trim()).find((s) => s.startsWith(dir));
    assert.ok(part, `CSP 缺少 ${dir}`);
    assert.match(part, /\bblob:/, `${dir} 未放行 blob:（${part}）`);
  }
});
