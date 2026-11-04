/* settings_behavior.test.js · 「设置开关 → 真实行为」链路断言
 *
 * 目的（评审要求）：不允许只把开关存进 localStorage 就算完，
 * 每个开关都要能证明它改变了运行时的行为（语音后端顺序 / 路由规划 /
 * 心跳是否主动发声）。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const XMConfig = require("../js/config.js");
const API = require("../js/api.js");
const TTS = require("../js/tts.js");

global.window = globalThis;   // 让 classic script 能挂 window.X

test("默认配置：主动问候开、免费通道关、语音偏好服务端", () => {
  const cfg = XMConfig.normalizeCfg({});
  assert.equal(cfg.heartbeatOn, true, "默认愿意被主动问候");
  assert.equal(cfg.allowFreePersonal, false, "默认不把倾诉内容交给免费额度");
  assert.equal(cfg.tts, "server");
});

test("开关归一化：只有显式 false 关闭主动问候；免费通道严谨布尔", () => {
  assert.equal(XMConfig.normalizeCfg({ heartbeatOn: false }).heartbeatOn, false);
  assert.equal(XMConfig.normalizeCfg({ heartbeatOn: undefined }).heartbeatOn, true);
  assert.equal(XMConfig.normalizeCfg({ allowFreePersonal: "yes" }).allowFreePersonal, false);
  assert.equal(XMConfig.normalizeCfg({ allowFreePersonal: true }).allowFreePersonal, true);
});

test("语音选择 → 真实 provider 顺序（server 优先 / browser 优先 / 关闭）", () => {
  const c = XMConfig.describeTts("server", { server: true, native: true, browser: true });
  assert.deepEqual(c.chain, ["server", "native", "browser"]);
  assert.equal(c.actual, "server");
  assert.equal(XMConfig.voiceApplies("server", c.actual), true);

  const noServer = XMConfig.describeTts("server", { server: false, native: true, browser: true });
  assert.equal(noServer.actual, "native");
  assert.match(noServer.text, /自动用本机语音/);
  assert.equal(XMConfig.voiceApplies("server", noServer.actual), false, "音色此时不生效");

  const local = XMConfig.describeTts("browser", { server: true, native: true, browser: true });
  assert.equal(local.actual, "browser");
  assert.deepEqual(local.chain, ["browser", "native"]);

  const off = XMConfig.describeTts("off", { server: true, native: true, browser: true });
  assert.deepEqual(off.chain, ["none"]);
  assert.equal(off.actual, "none");

  // 与运行时 TTS 引擎同一套语义（不是两套并行逻辑）
  const engine = TTS._create(Object.assign({ performance: { now: () => 0 } }, {
    AndroidTTS: { available: () => true, speakRate: () => true, stop: () => {} },
    speechSynthesis: { speak: () => {}, cancel: () => {} },
    SpeechSynthesisUtterance: (t) => ({ text: t }),
    XIAOMAN_TTS_SERVER: "https://tts.example.com"
  }));
  assert.deepEqual(engine.chain({ tts: "server" }), c.chain);
  assert.deepEqual(engine.chain({ tts: "browser" }), local.chain);
});

test("对话模式 / 免费通道开关 → 真实路由规划（不需要真网络）", () => {
  // 纯离线：无论哪台设备都只走离线
  assert.deepEqual(API.routePlan({ mode: "offline" }, { native: true }), ["offline"]);
  // mock：桌面走 mock 服务；安卓壳内没有本地 mock → 直接离线
  assert.deepEqual(API.routePlan({ mode: "mock" }, { native: false }), ["mock"]);
  assert.deepEqual(API.routePlan({ mode: "mock" }, { native: true }), ["offline"]);
  // 在线 + 桌面：无 key 走服务端代理
  assert.deepEqual(API.routePlan({ mode: "openai" }, { native: false }), ["proxy"]);
  // 在线 + 安卓免 Key：隐私闸门默认关上 → 没有在线通道（落到 mock/offline）
  assert.deepEqual(API.routePlan({ mode: "openai", allowFreePersonal: false }, { native: true }), []);
  // 用户显式打开免费通道 → 才会走 native
  assert.deepEqual(API.routePlan({ mode: "openai", allowFreePersonal: true }, { native: true }), ["native"]);
  // 自己填了 Key：直连优先，与免费通道开关无关
  assert.deepEqual(API.routePlan({ mode: "openai", apiKey: "sk-x", allowFreePersonal: false }, { native: true }), ["direct"]);
  assert.deepEqual(API.routePlan({ mode: "openai", apiKey: "sk-x" }, { native: false }), ["direct"]);
});

test("主动问候开关 → 心跳真的停/发（同一 tick，只改配置）", async () => {
  const RealDate = global.Date;
  const base = new RealDate("2026-11-05T15:00:00").getTime();   // 下午，非静默时段
  class FakeDate extends RealDate {
    constructor(...args) { super(...(args.length ? args : [base])); }
    static now() { return base; }
  }
  global.Date = FakeDate;
  const store = new Map();
  global.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  global.Reminders = { due: () => [], markDone: () => {} };
  global.Stats = { snapshot: () => ({ haha: 0, night: 0, days: 0, msgs: 0, emoji: null, emojiN: 0 }) };
  const pushed = [];
  const cfg = { heartbeatOn: false, mode: "offline", apiKey: "" };
  global.API = { loadCfg: () => cfg, chat: async () => null };

  const Heartbeat = require("../js/heartbeat.js");   // 需在 window/API 准备好之后加载
  let stopTimer = null;
  try {
    stopTimer = Heartbeat.start(async (msg) => { pushed.push(msg); }, () => []);
    const rnd = Math.random;
    Math.random = () => 0;              // 必过 25% 概率闸门
    try {
      await Heartbeat.tick();
      assert.equal(pushed.length, 0, "关闭主动问候后 tick 不得推送闲聊");
      cfg.heartbeatOn = true;
      await Heartbeat.tick();
      assert.equal(pushed.length, 1, "打开后同一 tick 应该推送一条");
      assert.equal(pushed[0].kind, "ping");
    } finally {
      Math.random = rnd;
    }
  } finally {
    if (stopTimer) stopTimer();
    global.Date = RealDate;
  }
});
