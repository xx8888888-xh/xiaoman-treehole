/* ============================================================
 * tts.js · 语音回复适配层（可插拔 provider）
 * ------------------------------------------------------------
 * provider：
 * 1. native  → 安卓系统 TTS（AndroidTTS JS Bridge；可用时优先，离线、零依赖）
 * 2. server  → 可选自建语音服务（仅当用户把「语音回复」选了服务端、
 *              或显式配置 XIAOMAN_TTS_SERVER / ttsServer 时启用）
 *              POST {ttsBase}/tts  JSON {text, voice, rate}  → audio/*
 *              原文只放 body，不进查询串（避免整句对话被访问日志落盘，审计 P1-5）
 * 3. browser → Web Speech API speechSynthesis（离线兜底）
 * 4. none    → 关闭语音（设置页 tts=off，随配置持久化）
 * 顺序：native → server（如启用）→ browser → none。
 * 失败语义：任一路失败都只降级，绝不阻塞文字、不抛未捕获异常（弱网先出文字）。
 * 播放时通过 onFrame 回调输出音量包络，供 Live2D 口型驱动。
 * ============================================================ */

const TTS = (() => {
  // 语速档位（与设置页同值）：服务端 / 浏览器 / 安卓桥三路共用同一映射
  const RATE = { slow: 0.86, normal: 1.02, fast: 1.18 };
  // 音色白名单：与服务端 ALLOWED_VOICES 对齐（至少 2 个）；非法值一律回默认音色
  const VOICES = Object.freeze([
    "zh-CN-XiaoyiNeural",     // 默认：女性，自然
    "zh-CN-XiaoxiaoNeural",   // 女性，温柔
    "zh-CN-XiaoyuanNeural",   // 女性，甜妹
    "zh-CN-YunjianNeural"     // 男性，沉稳
  ]);
  const DEFAULT_VOICE = "zh-CN-XiaoyiNeural";
  const AUDIO_START_TIMEOUT = 7000;   // 弱网：音频 7s 还没真正开播就降级，先保住文字

  let currentAudio = null;
  let fakeTimer = null;      // 假包络 interval（Android/browser 路），全局唯一
  let fakeStopTimer = null;  // Android/browser 路时长估算的兜底停止定时器
  let androidSettle = null;  // 当前 Android TTS 的完成回调（供原生 __ttsEnded 触发）
  let speakGen = 0;          // 播放代际：异步取音频返回时，过期的请求直接丢弃

  // 原生系统 TTS 完成回调穿透：MainActivity UtteranceProgressListener.onDone/onError
  // → evaluateJavascript("window.__ttsEnded && window.__ttsEnded()")。
  // 前端订阅后，口型结束不再依赖「190ms/字」估算（审计 android D9）。
  if (typeof window !== "undefined") {
    window.__ttsEnded = () => {
      const fn = androidSettle;
      androidSettle = null;
      if (fn) fn();
    };
  }

  /** 读取前端配置（API.loadCfg 由 api.js 提供；缺失时用空配置走默认链路） */
  function loadCfg() {
    try {
      if (typeof API !== "undefined" && API.loadCfg) return API.loadCfg() || {};
    } catch (e) {
      /* 配置读取失败不阻断语音降级 */
    }
    return {};
  }

  /** 清掉所有假包络定时器（避免连发消息时多路叠加、口型乱抖） */
  function clearFake() {
    if (fakeTimer) { clearInterval(fakeTimer); fakeTimer = null; }
    if (fakeStopTimer) { clearTimeout(fakeStopTimer); fakeStopTimer = null; }
  }

  /** 停掉当前所有正在播的语音（含假包络定时器） */
  function stop() {
    speakGen++;              // 让尚未返回的服务端音频请求作废
    if (currentAudio) { try { currentAudio.pause(); } catch (e) {} currentAudio = null; }
    androidSettle = null;   // 丢弃上一段 Android TTS 的完成回调，防止串台
    clearFake();
    try { if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel(); } catch (e) {}
    if (window.AndroidTTS && window.AndroidTTS.stop) { try { window.AndroidTTS.stop(); } catch (e) {} }
  }

  /** 安卓系统 TTS 是否可用（老包没有 available 方法时视为不可用） */
  function hasAndroid() {
    const b = typeof window !== "undefined" ? window.AndroidTTS : null;
    try {
      return !!(b && typeof b.available === "function" && b.available());
    } catch (e) {
      return false;
    }
  }

  /** 浏览器 Web Speech API 是否可用 */
  function hasBrowser() {
    return typeof window !== "undefined"
      && typeof window.speechSynthesis !== "undefined"
      && typeof window.SpeechSynthesisUtterance === "function";
  }

  /** 地址规范化：仅接受 http(s)，去掉尾斜杠；非法/空返回 "" */
  function normalizeBase(value) {
    const raw = String(value == null ? "" : value).trim();
    if (!/^https?:\/\//i.test(raw)) return "";
    return raw.replace(/\/+$/, "");
  }

  /**
   * 服务端地址：只有用户显式选择 server，或注入了 XIAOMAN_TTS_SERVER 才启用。
   * 默认部署 provider=none，前端仍会快速尝试一次并静默降级，不影响文字。
   */
  function serverBase(cfg) {
    const injected = typeof window !== "undefined" ? window.XIAOMAN_TTS_SERVER : "";
    const fromCfg = cfg && (cfg.ttsServer || cfg.ttsBase);
    return normalizeBase(injected) || normalizeBase(fromCfg) || "";
  }

  /** 是否尝试服务端合成（tts=off 时整体静音；tts=browser 时跳过服务端） */
  function serverEnabled(cfg) {
    if (!cfg) return false;
    const injected = typeof window !== "undefined" && normalizeBase(window.XIAOMAN_TTS_SERVER);
    if (injected) return true;
    return cfg.tts === "server" && !!serverBase(cfg);
  }

  /** 音色白名单校验：非法值回退默认音色 */
  function safeVoice(cfg) {
    const v = String((cfg && cfg.voice) || "").trim();
    return VOICES.indexOf(v) >= 0 ? v : DEFAULT_VOICE;
  }

  /**
   * 用 AudioContext 分析音量，驱动口型
   * @param onFail 播放失败（加载/解码/自动播放被拦）时回调 —— 由调用方决定降级
   */
  function playWithLipSync(url, onFrame, onEnd, onFail) {
    // 关键：跨域音频若不带 CORS（crossOrigin），createMediaElementSource 会把输出
    // 静音（analyser 全 0 采样）——表现为"有字幕没声音"。服务端 /tts 已回
    // Access-Control-Allow-Origin: *，这里必须同步声明 anonymous。
    const audio = new Audio();
    audio.crossOrigin = "anonymous";
    audio.src = url;
    if (currentAudio) { try { currentAudio.pause(); } catch (e) {} }
    currentAudio = audio;
    let ctx = null, analyser = null, srcNode = null, raf = 0, settled = false;
    let startTimer = 0;
    const settle = (failed) => {
      if (settled) return;
      settled = true;
      clearTimeout(startTimer);
      cancelAnimationFrame(raf);
      if (ctx) { try { ctx.close(); } catch (e) {} }
      if (currentAudio === audio) currentAudio = null;
      if (failed) { onFail && onFail(); } else { onEnd && onEnd(); }
    };

    audio.onended = () => settle(false);
    audio.onerror = () => settle(true);   // 真败因（404/解码失败/服务端无 CORS）走降级

    // 弱网兜底：请求发出但迟迟没有真正开播 → 判定失败并降级（文字已经在屏幕上了）
    startTimer = setTimeout(() => {
      if (settled) return;
      if (!audio.currentTime && audio.readyState < 3) settle(true);
    }, AUDIO_START_TIMEOUT);

    audio.play().then(() => {
      audio.onplaying = () => clearTimeout(startTimer);
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
        // 非用户手势下 AudioContext 可能 suspended → 全部输出为 0、嘴不动
        if (ctx.state === "suspended") ctx.resume().catch(() => {});
        srcNode = ctx.createMediaElementSource(audio);
        analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        srcNode.connect(analyser); analyser.connect(ctx.destination);
        const buf = new Uint8Array(analyser.frequencyBinCount);
        let zeroFrames = 0;
        (function loop() {
          analyser.getByteFrequencyData(buf);
          let sum = 0; for (let i = 2; i < 40; i++) sum += buf[i];
          const level = Math.min(1, sum / (38 * 160));   // 0~1 音量包络
          // AudioContext 被自动播放策略挂起时 analyser 会永远 0 采样：此时不能干等，
          // 转交给舞台的随机口型兜底（-1），但**不拆音频图**（拆了会真静音）
          zeroFrames = level === 0 ? zeroFrames + 1 : 0;
          if (zeroFrames > 45) {
            onFrame && onFrame(-1);
            raf = 0;
            return;
          }
          onFrame && onFrame(level);
          raf = requestAnimationFrame(loop);
        })();
      } catch (e) {
        // 分析失败就静默播，口型走兜底（-1 触发 lipFrame 的随机包络）
        onFrame && onFrame(-1);
      }
    }).catch(() => settle(true));
  }

  /** 纯播放（不接 WebAudio）：CORS 拿不到时仍保证"有声音"，只是没有口型同步 */
  function playPlain(url, onFrame, onEnd, onFail) {
    const audio = new Audio(url);
    if (currentAudio) { try { currentAudio.pause(); } catch (e) {} }
    currentAudio = audio;
    let settled = false;
    let startTimer = 0;
    const settle = (failed) => {
      if (settled) return;
      settled = true;
      clearTimeout(startTimer);
      if (currentAudio === audio) currentAudio = null;
      onFrame && onFrame(0);
      if (failed) { onFail && onFail(); } else { onEnd && onEnd(); }
    };
    audio.onended = () => settle(false);
    audio.onerror = () => settle(true);
    audio.onplaying = () => clearTimeout(startTimer);
    startTimer = setTimeout(() => {
      if (!audio.currentTime && audio.readyState < 3) settle(true);
    }, AUDIO_START_TIMEOUT);
    onFrame && onFrame(-1);   // 没有真实包络 → 走舞台的随机口型兜底
    audio.play().catch(() => settle(true));
  }

  /** provider: 浏览器 SpeechSynthesis（离线兜底） */
  function speakBrowser(cfg, text, onFrame, onEnd) {
    if (!hasBrowser()) return false;
    clearFake();
    try {
      const u = new window.SpeechSynthesisUtterance(text);
      u.lang = "zh-CN";
      u.rate = RATE[cfg && cfg.ttsRate] || RATE.normal;
      u.pitch = 1.15;
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearFake();
        onFrame && onFrame(0);
        onEnd && onEnd();
      };
      u.onend = finish;
      u.onerror = finish;
      fakeTimer = setInterval(() => onFrame && onFrame(0.4 + Math.random() * 0.3), 90);
      // 某些 WebView / 未装中文语音包时不会回调 onend：按时长估算兜底，避免口型一直动
      fakeStopTimer = setTimeout(finish, Math.max(1800, text.length * 190));
      window.speechSynthesis.speak(u);
      return true;
    } catch (e) {
      console.warn("browser tts 失败:", e);
      clearFake();
      return false;
    }
  }

  /** provider: 安卓 JSBridge 系统 TTS（WebView 内 speechSynthesis 不可用时的真机方案） */
  function speakNative(cfg, text, onFrame, onEnd) {
    const bridge = typeof window !== "undefined" ? window.AndroidTTS : null;
    if (!bridge || (!bridge.speakRate && !bridge.speak)) return false;
    try {
      const r = RATE[cfg && cfg.ttsRate] || RATE.normal;
      // speakRate 为 0.5 起的新桥方法；老包没有时退回固定语速
      const ok = bridge.speakRate ? bridge.speakRate(text, r) : bridge.speak(text);
      if (!ok) return false;
      clearFake();
      fakeTimer = setInterval(() => onFrame && onFrame(0.35 + Math.random() * 0.35), 90);
      let settled = false;
      const done = () => {
        if (settled) return;            // 原生回调与兜底定时器只结算一次，避免 onEnd 重复
        settled = true;
        androidSettle = null;
        clearFake();
        onFrame && onFrame(0);
        onEnd && onEnd();
      };
      // 原生完成回调（__ttsEnded）优先；未穿透时用估算兜底（中文语速约 180~200ms/字）
      androidSettle = done;
      fakeStopTimer = setTimeout(done, Math.max(1800, text.length * 190));
      return true;
    } catch (e) {
      console.warn("AndroidTTS 桥失败:", e);
      return false;
    }
  }

  /**
   * provider: 服务端语音。POST JSON body → blob URL（audio/*）
   * 原文不进 URL：GET 查询串会被服务端访问日志整行记录（审计 P1-5）。
   * 沿用 7s 弱网兜底：请求本身也受 AUDIO_START_TIMEOUT 约束，超时即降级。
   * 返回 true 表示已接管播放；false 表示请求失败，由调用方继续降级。
   */
  async function speakServer(cfg, text, onFrame, onEnd, gen) {
    const base = serverBase(cfg);
    if (!base) return false;
    const voice = safeVoice(cfg);
    let blobUrl = null;
    try {
      blobUrl = await fetchServerAudio(base, text, voice, cfg.ttsRate || "normal");
    } catch (e) {
      // provider=none（503）/ 未部署 / 超时：都不打扰用户，直接交给本机语音
      console.info("server tts 不可用，降级本机语音:", e && e.message ? e.message : e);
      return false;
    }
    if (gen !== speakGen) {                // 请求期间用户又发了新消息 → 丢弃旧音频
      try { URL.revokeObjectURL(blobUrl); } catch (e) {}
      return true;
    }
    const cleanup = () => { try { URL.revokeObjectURL(blobUrl); } catch (e) {} };
    // server 路失败（解码失败/跨域被拦）→ 先尝试无口型的纯播放，再降级 browser
    playWithLipSync(blobUrl, onFrame, () => { cleanup(); onEnd && onEnd(); }, () => {
      console.info("server tts 口型链路失败，尝试纯播放");
      playPlain(blobUrl, onFrame, () => { cleanup(); onEnd && onEnd(); }, () => {
        console.info("server tts 纯播放失败，降级本机语音");
        cleanup();
        if (!speakBrowser(cfg, text, onFrame, onEnd)) { onFrame && onFrame(0); onEnd && onEnd(); }
      });
    });
    return true;
  }

  /** provider: 关闭语音 */
  function providerNone() {
    return false;
  }

  /**
   * provider 注册表：新增后端只需在这里登记，speak 会自动按链路降级。
   * 约定 speak 返回 true = 已接管播放；false = 不可用/失败，继续下一路。
   */
  const PROVIDERS = Object.freeze({
    native: { id: "native", available: hasAndroid, speak: speakNative },
    server: { id: "server", available: serverEnabled, speak: speakServer },
    browser: { id: "browser", available: hasBrowser, speak: speakBrowser },
    none: { id: "none", available: () => true, speak: providerNone }
  });

  /** 按配置生成 provider 尝试顺序；tts=off 时直接静音，不走任何后端 */
  function providerChain(cfg) {
    const c = cfg || {};
    if (c.tts === "off") return ["none"];
    const chain = ["native", "server", "browser"].filter((id) => {
      try { return !!PROVIDERS[id].available(c); } catch (e) { return false; }
    });
    return chain.length ? chain : ["none"];
  }

  /**
   * 服务端语音请求：POST JSON body → blob URL
   * 原文不进 URL：GET 查询串会被服务端访问日志整行记录（审计 P1-5）。
   */
  async function fetchServerAudio(base, text, voice, rate) {
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const abortTimer = ctrl ? setTimeout(() => ctrl.abort(), AUDIO_START_TIMEOUT) : 0;
    try {
      const res = await fetch(`${base}/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice, rate }),
        signal: ctrl ? ctrl.signal : undefined
      });
      if (!res.ok) {
        let detail = "";
        try {
          const j = await res.json();
          detail = j && j.error ? ` ${j.error}` : "";
        } catch (e) { /* 非 JSON 错误体不深究 */ }
        throw new Error(`tts http ${res.status}${detail}`);
      }
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    } finally {
      if (abortTimer) clearTimeout(abortTimer);
    }
  }

  /**
   * 朗读入口：先 stop 上一段，再按 provider 链路尝试；永远只回调一次 onEnd。
   * 注意：文字渲染由调用方先完成，本函数不阻塞、不抛异常。
   */
  async function speak(text, onFrame, onEnd) {
    const cfg = loadCfg();
    const frame = typeof onFrame === "function" ? onFrame : () => {};
    const finish = typeof onEnd === "function" ? onEnd : () => {};
    let ended = false;
    const done = () => {
      if (ended) return;
      ended = true;
      finish();
    };

    stop();                                  // 先停上一段，防多路叠加
    const gen = speakGen;                    // stop() 之后取代际，异步返回时校验
    if (typeof text !== "string" || !text.trim()) { frame(0); done(); return; }

    try {
      for (const id of providerChain(cfg)) {
        if (gen !== speakGen) return;        // 已发新消息/已 stop，不再插播旧语音
        const handled = await Promise.resolve(PROVIDERS[id].speak(cfg, text, frame, done, gen));
        if (handled) return;
      }
    } catch (e) {
      console.warn("TTS 链路异常，静默降级:", e);
    }
    frame(0);
    done();
  }

  return {
    speak,
    stop,
    /** 当前会使用的 provider（便于设置页/自检显示） */
    provider: (cfg) => providerChain(cfg || loadCfg())[0] || "none",
    /** 当前配置下的完整降级链路 */
    chain: (cfg) => providerChain(cfg || loadCfg()),
    providers: Object.keys(PROVIDERS),
    voices: VOICES,
    defaultVoice: DEFAULT_VOICE
  };
})();
