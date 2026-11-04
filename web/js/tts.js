/* ============================================================
 * tts.js · 语音回复适配层（统一 provider 接口，可插拔）
 * ------------------------------------------------------------
 * 统一接口（每个 provider 三个方法，语义一致）：
 *   available(cfg)      bool    当前环境下是否可用（缺失即 false）
 *   speak(text, opts)   bool    接管播放返回 true；不可用/失败返回 false
 *                     opts = { cfg, onFrame(level), onEnd, gen }
 *   stop()              void    立刻停止本 provider 的播放（幂等）
 *
 * provider：
 * 1. native  → 安卓系统 TTS（AndroidTTS JS Bridge；优先，离线、零依赖、零许可风险）
 * 2. server  → 可选自建语音服务（**默认关闭**：只有注入 XIAOMAN_TTS_SERVER
 *              或用户显式配置 ttsServer 才可用；没配地址 available()=false）
 *              POST {base}/tts  JSON {text, voice, rate} → audio/*
 *              原文只放 body，不进查询串（避免整句对话被访问日志落盘，审计 P1-5）
 * 3. browser → Web Speech API speechSynthesis（离线兜底）
 * 4. none    → 关闭语音（设置页 tts=off，随配置持久化）
 * 顺序：native → server（如启用）→ browser → none。
 * 失败语义：任一路失败都只降级，绝不阻塞文字、不抛未捕获异常（弱网先出文字）。
 * 口型：音频解码后预计算 RMS 包络关键帧（不用实时 analyser——MuMu 上
 *      analyser RMS 恒 0）；无音频时只喂极轻微呼吸（极简模式由 stage 归零）。
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
  const ENVELOPE_FRAME_MS = 50;       // 口型包络关键帧间隔（20Hz，足够 15/30fps 舞台）

  /** 纯函数：PCM 通道 → 归一化 RMS 包络（可单测，不依赖 WebAudio） */
  function envelopeLevels(ch, frameMs) {
    const total = Math.max(1, Math.floor(ch.length / Math.max(1, frameMs)));
    const per = Math.max(1, Math.floor(ch.length / total));
    const levels = new Float32Array(total);
    let peak = 0;
    for (let i = 0; i < total; i++) {
      const start = i * per;
      const end = Math.min(ch.length, start + per);
      let sum = 0;
      for (let j = start; j < end; j++) sum += ch[j] * ch[j];
      const rms = Math.sqrt(sum / Math.max(1, end - start));
      levels[i] = rms;
      if (rms > peak) peak = rms;
    }
    if (peak > 0) for (let i = 0; i < total; i++) levels[i] = Math.min(1, levels[i] / peak);
    return levels;
  }

  /** 纯函数：播放进度(秒) → 包络关键帧下标（越界钳制，末尾不抖到 0） */
  function envelopeIndex(timeSec, frameMs, len) {
    if (!len) return 0;
    const i = Math.floor(((Number(timeSec) || 0) * 1000) / Math.max(1, frameMs));
    return Math.max(0, Math.min(len - 1, i));
  }

  /** 无音频时的极轻微呼吸（静止为主；极简模式由 stage 归零） */
  function breathLevel(nowMs) {
    const t = (typeof nowMs === "number" ? nowMs : Date.now()) / 1000;
    return 0.05 + 0.04 * Math.sin(t * 2.4);
  }

  /** 地址规范化：仅接受 http(s)，去掉尾斜杠；非法/空返回 "" */
  function normalizeBase(value) {
    const raw = String(value == null ? "" : value).trim();
    if (!/^https?:\/\//i.test(raw)) return "";
    return raw.replace(/\/+$/, "");
  }

  /** 音色白名单校验：非法值回退默认音色 */
  function safeVoice(cfg) {
    const v = String((cfg && cfg.voice) || "").trim();
    return VOICES.indexOf(v) >= 0 ? v : DEFAULT_VOICE;
  }

  /**
   * 服务端地址：默认关闭。只有显式注入/显式配置才启用（新装用户不会请求任何
   * 远程语音服务；provider 缺失时 available()=false）。
   */
  function serverBase(win, cfg) {
    const injected = normalizeBase(win && win.XIAOMAN_TTS_SERVER);
    const fromCfg = normalizeBase(cfg && cfg.ttsServer);
    return injected || fromCfg || "";
  }

  /** 按环境创建一套引擎（浏览器用真实 window；单测注入 fake window） */
  function createEngine(win) {
    const W = win || {};
    const now = () => (W.performance && W.performance.now ? W.performance.now() : Date.now());

    let currentAudio = null;
    let fakeTimer = null;      // 假包络 interval（Android/browser 路），全局唯一
    let fakeStopTimer = null;  // Android/browser 路时长估算的兜底停止定时器
    let androidSettle = null;  // 当前 Android TTS 的完成回调（供原生 __ttsEnded 触发）
    let speakGen = 0;          // 播放代际：异步取音频返回时，过期的请求直接丢弃
    let muted = W.XIAOMAN_TTS_MUTED === "1" || W.XIAOMAN_TTS_MUTED === true;

    /**
     * 读取前端配置（API.loadCfg 由 api.js 提供；缺失时用空配置走默认链路）。
     * 注意：api.js 里 API 是顶层 const，在浏览器里**只存在于全局词法作用域、不挂在 window 上**；
     * 之前只查 W.API 会让真机页面永远读到空配置，provider 恒为 none/与设置页不一致。
     * 所以先看单测注入的 win.API，再回退到全局 API 绑定。
     */
    function loadCfg() {
      try {
        if (W.API && W.API.loadCfg) return W.API.loadCfg() || {};
        if (typeof API !== "undefined" && API && typeof API.loadCfg === "function") {
          return API.loadCfg() || {};
        }
      } catch (e) {
        /* 配置读取失败不阻断语音降级 */
      }
      return {};
    }

    function clearFake() {
      if (fakeTimer) { clearInterval(fakeTimer); fakeTimer = null; }
      if (fakeStopTimer) { clearTimeout(fakeStopTimer); fakeStopTimer = null; }
    }

    /** 只结算一次：先归零口型再回调 onEnd，避免多路叠加/重复 onEnd */
    function settleOnce(onFrame, onEnd) {
      let done = false;
      return () => {
        if (done) return;
        done = true;
        clearFake();
        try { onFrame && onFrame(0); } catch (e) {}
        try { onEnd && onEnd(); } catch (e) {}
      };
    }

    /** 没有音频数据时（系统 TTS 在原生层播放）只喂极轻微呼吸 */
    function startBreath(onFrame) {
      clearFake();
      fakeTimer = setInterval(() => onFrame && onFrame(breathLevel(now())), 90);
    }

    // 原生系统 TTS 完成回调穿透：MainActivity UtteranceProgressListener.onDone/onError
    // → evaluateJavascript("window.__ttsEnded && window.__ttsEnded()")。
    if (W && typeof W === "object") {
      W.__ttsEnded = () => {
        const fn = androidSettle;
        androidSettle = null;
        if (fn) fn();
      };
    }

    /** 解码音频并预先算好 RMS 包络关键帧（20Hz），与播放进度对齐，与设备无关 */
    async function computeEnvelope(url) {
      const AC = W.AudioContext || W.webkitAudioContext;
      if (!AC || typeof W.fetch !== "function") return null;
      const res = await W.fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error("audio " + res.status);
      const data = await res.arrayBuffer();
      const ctx = new AC();
      try {
        const buf = await ctx.decodeAudioData(data);
        const perFrame = Math.max(1, Math.round((buf.sampleRate * ENVELOPE_FRAME_MS) / 1000));
        return { levels: envelopeLevels(buf.getChannelData(0), perFrame), frameMs: ENVELOPE_FRAME_MS };
      } finally {
        try { ctx.close(); } catch (e) {}
      }
    }

    /**
     * 播放音频并用「包络关键帧 + 播放进度」驱动口型。
     * 音频元素直接播放（不接 WebAudio 图），避免 createMediaElementSource 在
     * 跨域/被挂起时把声音静音。包络解码是旁路，失败只影响口型、不影响声音。
     */
    function playWithEnvelope(url, onFrame, onEnd, onFail) {
      const audio = new W.Audio();
      audio.preload = "auto";
      audio.src = url;
      if (currentAudio) { try { currentAudio.pause(); } catch (e) {} }
      currentAudio = audio;
      let raf = 0, settled = false, env = null, last = 0;
      let startTimer = 0;
      const settle = (failed) => {
        if (settled) return;
        settled = true;
        clearTimeout(startTimer);
        if (W.cancelAnimationFrame) W.cancelAnimationFrame(raf);
        if (currentAudio === audio) currentAudio = null;
        if (failed) { onFail && onFail(); } else { onEnd && onEnd(); }
      };

      audio.onended = () => settle(false);
      audio.onerror = () => settle(true);   // 真败因（404/解码失败）走降级

      // 弱网兜底：请求发出但迟迟没有真正开播 → 判定失败并降级（文字已经在屏幕上了）
      startTimer = setTimeout(() => {
        if (settled) return;
        if (!audio.currentTime && audio.readyState < 3) settle(true);
      }, AUDIO_START_TIMEOUT);

      const tick = () => {
        if (settled) return;
        let level;
        if (env && env.levels.length) {
          const idx = envelopeIndex(audio.currentTime, env.frameMs, env.levels.length);
          const target = env.levels[idx] || 0;
          last = last * 0.62 + target * 0.38;   // 起落平滑，避免嘴抖
          level = last;
        } else {
          level = breathLevel(now());   // 解码前 / 解码失败：轻微呼吸（不随机张嘴）
        }
        onFrame && onFrame(level);
        raf = W.requestAnimationFrame ? W.requestAnimationFrame(tick) : setTimeout(tick, 16);
      };

      audio.play().then(() => {
        audio.onplaying = () => clearTimeout(startTimer);
        computeEnvelope(url).then(e => { env = e; }).catch(() => { env = null; });
        tick();
      }).catch(() => settle(true));
    }

    /** 纯播放（不接 WebAudio 图）：口型包络失败时仍保证"有声音"，只是口型只轻微呼吸 */
    function playPlain(url, onFrame, onEnd, onFail) {
      const audio = new W.Audio(url);
      if (currentAudio) { try { currentAudio.pause(); } catch (e) {} }
      currentAudio = audio;
      let settled = false;
      let startTimer = 0;
      const settle = (failed) => {
        if (settled) return;
        settled = true;
        clearTimeout(startTimer);
        clearFake();
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
      startBreath(onFrame);
      audio.play().catch(() => settle(true));
    }

    /** 服务端语音请求：POST JSON body → blob URL（原文不进 URL） */
    async function fetchServerAudio(base, text, voice, rate) {
      const ctrl = typeof W.AbortController !== "undefined" ? new W.AbortController() : null;
      const abortTimer = ctrl ? setTimeout(() => ctrl.abort(), AUDIO_START_TIMEOUT) : 0;
      try {
        const res = await W.fetch(`${base}/tts`, {
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
        return W.URL.createObjectURL(blob);
      } finally {
        if (abortTimer) clearTimeout(abortTimer);
      }
    }

    // ---------- provider 实现（统一 available / speak / stop） ----------

    /** 安卓 JSBridge 系统 TTS（WebView 内 speechSynthesis 不可用时的真机方案） */
    const nativeProvider = {
      id: "native",
      available() {
        const b = W.AndroidTTS;
        if (!b) return false;
        try {
          return typeof b.available === "function" && !!b.available()
            && (typeof b.speakRate === "function" || typeof b.speak === "function");
        } catch (e) {
          return false;
        }
      },
      speak(text, opts) {
        const bridge = W.AndroidTTS;
        if (!bridge) return false;
        const { cfg, onFrame, onEnd } = opts || {};
        try {
          const r = RATE[cfg && cfg.ttsRate] || RATE.normal;
          // speakRate 为 0.5 起的新桥方法；老包没有时退回固定语速
          const ok = bridge.speakRate ? bridge.speakRate(text, r) : bridge.speak(text);
          if (!ok) return false;
          startBreath(onFrame);
          const done = settleOnce(onFrame, onEnd);
          androidSettle = done;
          // 原生完成回调（__ttsEnded）优先；未穿透时用估算兜底（中文约 180~200ms/字）
          fakeStopTimer = setTimeout(done, Math.max(1800, text.length * 190));
          return true;
        } catch (e) {
          console.warn("AndroidTTS 桥失败:", e && e.message);
          clearFake();
          return false;
        }
      },
      stop() {
        androidSettle = null;   // 丢弃上一段 Android TTS 的完成回调，防止串台
        if (W.AndroidTTS && W.AndroidTTS.stop) { try { W.AndroidTTS.stop(); } catch (e) {} }
      }
    };

    /** 浏览器 SpeechSynthesis（离线兜底） */
    const browserProvider = {
      id: "browser",
      available() {
        return typeof W.speechSynthesis !== "undefined"
          && typeof W.SpeechSynthesisUtterance === "function";
      },
      speak(text, opts) {
        if (!this.available()) return false;
        const { cfg, onFrame, onEnd } = opts || {};
        try {
          const u = new W.SpeechSynthesisUtterance(text);
          u.lang = "zh-CN";
          u.rate = RATE[cfg && cfg.ttsRate] || RATE.normal;
          u.pitch = 1.15;
          const finish = settleOnce(onFrame, onEnd);
          u.onend = finish;
          u.onerror = finish;
          startBreath(onFrame);
          // 某些 WebView / 未装中文语音包时不会回调 onend：按时长估算兜底
          fakeStopTimer = setTimeout(finish, Math.max(1800, text.length * 190));
          W.speechSynthesis.speak(u);
          return true;
        } catch (e) {
          console.warn("browser tts 失败:", e && e.message);
          clearFake();
          return false;
        }
      },
      stop() {
        try { if (typeof W.speechSynthesis !== "undefined") W.speechSynthesis.cancel(); } catch (e) {}
      }
    };

    /** 自建语音服务（默认关闭；请求失败只降级，不打扰用户） */
    const serverProvider = {
      id: "server",
      available(cfg) {
        const c = cfg || loadCfg();
        const injected = normalizeBase(W.XIAOMAN_TTS_SERVER);
        if (injected) return true;
        return c.tts === "server" && !!normalizeBase(c.ttsServer);
      },
      async speak(text, opts) {
        const { cfg, onFrame, onEnd, gen } = opts || {};
        const base = serverBase(W, cfg);
        if (!base) return false;
        let blobUrl = null;
        try {
          blobUrl = await fetchServerAudio(base, text, safeVoice(cfg), (cfg && cfg.ttsRate) || "normal");
        } catch (e) {
          console.info("server tts 不可用，降级本机语音:", e && e.message ? e.message : e);
          return false;
        }
        if (gen !== speakGen) {                // 请求期间用户又发了新消息 → 丢弃旧音频
          try { W.URL.revokeObjectURL(blobUrl); } catch (e) {}
          return true;
        }
        const cleanup = () => { try { W.URL.revokeObjectURL(blobUrl); } catch (e) {} };
        // server 路失败（解码失败/跨域被拦）→ 先纯播放，再降级 browser
        playWithEnvelope(blobUrl, onFrame, () => { cleanup(); onEnd && onEnd(); }, () => {
          console.info("server tts 口型链路失败，尝试纯播放");
          playPlain(blobUrl, onFrame, () => { cleanup(); onEnd && onEnd(); }, () => {
            console.info("server tts 纯播放失败，降级本机语音");
            cleanup();
            if (!browserProvider.speak(text, opts)) {
              onFrame && onFrame(0);
              onEnd && onEnd();
            }
          });
        });
        return true;
      },
      stop() {
        if (currentAudio) { try { currentAudio.pause(); } catch (e) {} currentAudio = null; }
      }
    };

    const providers = { native: nativeProvider, server: serverProvider, browser: browserProvider };

    /** 按配置生成 provider 尝试顺序；tts=off 时直接静音，不走任何后端 */
    function providerChain(cfg) {
      const c = cfg || {};
      if (c.tts === "off") return ["none"];
      const chain = ["native", "server", "browser"].filter((id) => {
        try { return !!providers[id].available(c); } catch (e) { return false; }
      });
      return chain.length ? chain : ["none"];
    }

    /** 停掉当前所有正在播的语音（含假包络定时器），并让未返回的异步请求作废 */
    function stop() {
      speakGen++;
      androidSettle = null;
      clearFake();
      for (const id of Object.keys(providers)) {
        try { providers[id].stop(); } catch (e) {}
      }
      if (currentAudio) { try { currentAudio.pause(); } catch (e) {} currentAudio = null; }
    }

    /**
     * 朗读入口：先 stop 上一段，再按 provider 链路尝试；永远只回调一次 onEnd。
     * 返回 { spoke, provider }：spoke=false 表示本次没有出声（可由 UI 给安静提示）。
     * 文字渲染由调用方先完成，本函数不阻塞、不抛异常。
     */
    async function speak(text, onFrame, onEnd) {
      const cfg = loadCfg();
      const frame = typeof onFrame === "function" ? onFrame : () => {};
      const finish = typeof onEnd === "function" ? onEnd : () => {};
      let ended = false;
      const done = () => {
        if (ended) return;
        ended = true;
        try { finish(); } catch (e) {}
      };

      stop();                                  // 先停上一段，防多路叠加
      const gen = speakGen;                    // stop() 之后取代际，异步返回时校验
      if (muted) { frame(0); done(); return { spoke: false, provider: "muted" }; }
      if (typeof text !== "string" || !text.trim()) {
        frame(0);
        done();
        return { spoke: false, provider: "none" };
      }
      // 用户显式关闭语音（设置页 tts=off）：只静默收尾，且**不回调 onEnd**。
      // 原因：onEnd 在 app.js 里承担「气泡标记 🔊 语音已播」，off 时回调会把一句
      // 根本没出声的话标成"已播"。状态与文案必须是同一事实：off = 无声 = 无标记。
      if (cfg.tts === "off") {
        frame(0);
        return { spoke: false, provider: "off" };
      }

      try {
        for (const id of providerChain(cfg)) {
          if (gen !== speakGen) return { spoke: false, provider: "stale" };
          const p = providers[id];
          if (!p) continue;   // "none"：没有可发声后端
          const handled = await Promise.resolve(
            p.speak(text, { cfg, onFrame: frame, onEnd: done, gen })
          );
          if (handled) return { spoke: true, provider: id };
        }
      } catch (e) {
        console.warn("TTS 链路异常，静默降级:", e && e.message);
      }
      frame(0);
      done();
      return { spoke: false, provider: "none" };
    }

    return {
      speak,
      stop,
      /** 当前会使用的 provider（便于设置页/自检显示）；off 如实返回 "off" 而不是 "none" */
      provider: (cfg) => {
        const c = cfg || loadCfg();
        if (c.tts === "off") return "off";
        return providerChain(c)[0] || "none";
      },
      /** 当前配置下的完整降级链路 */
      chain: (cfg) => providerChain(cfg || loadCfg()),
      /** 是否有任何可用的发声 provider（muted 时恒 false） */
      available: (cfg) => !muted && providerChain(cfg || loadCfg())[0] !== "none",
      setMuted: (v) => {
        muted = !!v;
        if (muted) stop();
        return muted;
      },
      isMuted: () => muted,
      /** provider 对象（单测用：直接校验 available/speak/stop 接口与参数） */
      providerObjects: providers,
      providers: Object.keys(providers),
      voices: VOICES,
      defaultVoice: DEFAULT_VOICE
    };
  }

  const engine = createEngine(typeof window !== "undefined" ? window : undefined);

  const api = Object.assign({}, engine, {
    /** 单测入口：包络关键帧纯函数、帧长、呼吸、帧下标 */
    _envelopeLevels: envelopeLevels,
    _envelopeIndex: envelopeIndex,
    _frameMs: ENVELOPE_FRAME_MS,
    _breathLevel: breathLevel,
    /** 单测入口：注入 fake window 造独立引擎（不触碰真实浏览器/音频） */
    _create: createEngine
  });
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.TTS = api;
  return api;
})();
