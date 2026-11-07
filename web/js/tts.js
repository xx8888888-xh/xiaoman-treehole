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
 * 顺序：跟随设置页选择——server=服务端优先，browser=本机优先（逐级降级）；
 *        tts=off 直接静音，不触碰任何后端。
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

  /* ---------- 无音频 provider 的文本节奏口型 ----------
   * 系统 TTS / Web Speech 在原生层发声，Web 侧拿不到 PCM。旧实现只喂
   * 0.05~0.09 的呼吸值 → 整句嘴几乎不动，谈不上音节对齐。
   * 这里按中文阅读节奏估算：单字 190ms（与原兜底定时器同一口径），
   * 标点后插入停顿。误差来源只有"系统实际语速 ≠ 估算"，
   * 不涉及音频链路，因此可离线复现、可量化（见 live2d_lipsync.test.js）。 */
  const CHAR_MS = 190;
  const PAUSE_MS = {
    "，": 180, ",": 180, "、": 130, "；": 240, ";": 240, "：": 200, ":": 200,
    "。": 360, "！": 320, "!": 320, "？": 360, "?": 360, "…": 420,
    "~": 200, "～": 200,
  };
  const SILENT_CHAR_RE = /[「」『』《》"'“”‘’·—\-_]/;

  /** 纯函数：文本 → 音节时间线（rate 与 RATE 同口径，1.0≈正常语速） */
  function speechPlan(text, rate) {
    const r = Number(rate) > 0 ? Number(rate) : RATE.normal;
    const per = CHAR_MS / r;
    const syllables = [];
    let t = 0;
    for (const ch of Array.from(String(text == null ? "" : text))) {
      const pause = PAUSE_MS[ch];
      if (pause !== undefined) { t += pause / r; continue; }
      if (/\s/.test(ch) || SILENT_CHAR_RE.test(ch)) continue;
      syllables.push({ start: Math.round(t), dur: Math.round(per * 0.82) });
      t += per;
    }
    return { syllables, total: Math.round(t) + 240 };
  }

  /** 纯函数：时间线 → 0..1 口型包络（起 25% 渐入、末 30% 渐出，字间自然闭合） */
  function speechLevel(plan, elapsedMs) {
    const t = Number(elapsedMs) || 0;
    let level = 0;
    for (const s of plan.syllables) {
      if (t < s.start) break;
      const d = t - s.start;
      if (d > s.dur) continue;
      const p = d / s.dur;
      const shape = p < 0.25 ? p / 0.25 : (p < 0.7 ? 1 : Math.max(0, (1 - p) / 0.3));
      if (shape > level) level = shape;
    }
    return level * 0.9;
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
    let lastSpeakAt = 0;       // speak() 入口时刻（≈用户点击播放），供延迟探针取基准
    let lastTiming = null;     // 上一次服务端播放的分段耗时（CI/探针只读取，不参与播放逻辑）
    let lastFailure = null;    // 最近一次 speak 的失败诊断：{textLen,textHash,error,attempts[]}
    let speakStartAt = 0;      // 本次 speak 入口时刻，用于失败 elapsedMs

    /** 文本指纹（FNV-1a）：只用于识别"同一句反复失败"，不可逆推正文（隐私红线 P1-5） */
    function textHash(text) {
      let h = 0x811c9dc5;
      const s = String(text || "");
      for (let i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i);
        h = (h * 0x01000193) >>> 0;
      }
      return ("00000000" + h.toString(16)).slice(-8);
    }

    /** 记录一次失败尝试；lastFailure.error 保留**首个**败因（最接近根因的一次） */
    function noteFailure(provider, error, extra) {
      if (!lastFailure) lastFailure = { textLen: 0, textHash: "", attempts: [] };
      const a = Object.assign({
        provider: provider || "none",
        error: error || "unknown",
        elapsedMs: Math.max(0, Math.round(now() - speakStartAt)),
      }, extra || {});
      lastFailure.attempts.push(a);
      if (lastFailure.attempts.length > 4) lastFailure.attempts.shift();
      if (!lastFailure.error) {
        lastFailure.error = a.error;
        lastFailure.provider = a.provider;
        lastFailure.elapsedMs = a.elapsedMs;
        if (a.httpStatus) lastFailure.httpStatus = a.httpStatus;
        if (a.timedOut) lastFailure.timedOut = true;
      }
    }

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

    /** 只结算一次：先归零口型再回调 onEnd，避免多路叠加/重复 onEnd。
     *  onEnd(true) = 真的出声并播完；调用方据此才允许打「语音已播」标记。 */
    function settleOnce(onFrame, onEnd) {
      let done = false;
      return () => {
        if (done) return;
        done = true;
        clearFake();
        try { onFrame && onFrame(0); } catch (e) {}
        try { onEnd && onEnd(true); } catch (e) {}
      };
    }

    /** 没有音频数据时（系统 TTS 在原生层播放）只喂极轻微呼吸 */
    function startBreath(onFrame) {
      clearFake();
      fakeTimer = setInterval(() => onFrame && onFrame(breathLevel(now())), 90);
    }

    /** 无 PCM 时的文本节奏口型；返回估算总时长（ms），供兜底停止定时器复用 */
    function startText(onFrame, text, rate) {
      clearFake();
      const plan = speechPlan(text, rate);
      const t0 = now();
      fakeTimer = setInterval(
        () => onFrame && onFrame(speechLevel(plan, now() - t0)),
        ENVELOPE_FRAME_MS
      );
      return plan.total;
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
      // 延迟探针：从 speak() 入口（≈用户点击）到首个"真语音"包络帧。
      // 阈值 0.12 高于呼吸兜底峰值 0.09，避免把"没解码时的呼吸"误算成首音节。
      const timing = {
        speakAt: lastSpeakAt || now(), fetchEndAt: now(), decodeStartAt: 0, playRequestAt: 0,
        playAt: 0, envReadyAt: 0, firstNonZeroAt: 0,
        envError: "", envFrames: 0, frames: 0,
      };
      lastTiming = timing;
      const settle = (failed, error, extra) => {
        if (settled) return;
        settled = true;
        clearTimeout(startTimer);
        if (W.cancelAnimationFrame) W.cancelAnimationFrame(raf);
        if (currentAudio === audio) currentAudio = null;
        if (failed) { noteFailure("server", error || "play_failed", extra); onFail && onFail(); }
        else { onEnd && onEnd(true); }
      };

      audio.onended = () => settle(false);
      audio.onerror = () => settle(true, "audio_error");   // 真败因（404/解码失败）走降级

      // 弱网兜底：请求发出但迟迟没有真正开播 → 判定失败并降级（文字已经在屏幕上了）
      startTimer = setTimeout(() => {
        if (settled) return;
        if (!audio.currentTime && audio.readyState < 3) settle(true, "start_timeout", { timedOut: true });
      }, AUDIO_START_TIMEOUT);

      // 解码与开播并行：旧实现等 audio.play() resolve 后才 fetch+decode，
      // 白等一个"真正开播"的往返（Chromium/WebView 30-80ms）。现在同一轮启动，
      // 包络先就绪就先按播放进度出帧，开播延迟不受影响。
      timing.decodeStartAt = now();
      computeEnvelope(url).then(e => {
        env = e;
        timing.envReadyAt = now();
        timing.envFrames = (e && e.levels) ? e.levels.length : 0;
      }).catch((err) => {
        env = null;
        timing.envReadyAt = -1;
        timing.envError = String((err && err.message) || err || "decode failed");
      });

      const tick = () => {
        if (settled) return;
        timing.frames++;
        let level;
        if (env && env.levels.length) {
          const idx = envelopeIndex(audio.currentTime, env.frameMs, env.levels.length);
          const target = env.levels[idx] || 0;
          last = last * 0.62 + target * 0.38;   // 起落平滑，避免嘴抖
          level = last;
          if (!timing.firstNonZeroAt && level >= 0.12) timing.firstNonZeroAt = now();
        } else {
          level = breathLevel(now());   // 解码前 / 解码失败：轻微呼吸（不随机张嘴）
        }
        onFrame && onFrame(level);
        raf = W.requestAnimationFrame ? W.requestAnimationFrame(tick) : setTimeout(tick, 16);
      };

      timing.playRequestAt = now();
      audio.play().then(() => {
        timing.playAt = now();
        audio.onplaying = () => clearTimeout(startTimer);
        tick();
      }).catch(() => settle(true, "play_rejected"));
    }

    /** 纯播放（不接 WebAudio 图）：口型包络失败时仍保证"有声音"，只是口型只轻微呼吸 */
    function playPlain(url, onFrame, onEnd, onFail) {
      const audio = new W.Audio(url);
      if (currentAudio) { try { currentAudio.pause(); } catch (e) {} }
      currentAudio = audio;
      let settled = false;
      let startTimer = 0;
      const settle = (failed, error, extra) => {
        if (settled) return;
        settled = true;
        clearTimeout(startTimer);
        clearFake();
        if (currentAudio === audio) currentAudio = null;
        onFrame && onFrame(0);
        if (failed) { noteFailure("server", error || "play_plain_failed", extra); onFail && onFail(); }
        else { onEnd && onEnd(true); }
      };
      audio.onended = () => settle(false);
      audio.onerror = () => settle(true, "audio_error");
      audio.onplaying = () => clearTimeout(startTimer);
      startTimer = setTimeout(() => {
        if (!audio.currentTime && audio.readyState < 3) settle(true, "start_timeout", { timedOut: true });
      }, AUDIO_START_TIMEOUT);
      startBreath(onFrame);
      audio.play().catch(() => settle(true, "play_rejected"));
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
          const err = new Error(`tts http ${res.status}${detail}`);
          err.status = res.status;
          throw err;
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
          const planMs = startText(onFrame, text, r);
          const done = settleOnce(onFrame, onEnd);
          androidSettle = done;
          // 原生完成回调（__ttsEnded）优先；未穿透时用同一文本时间线兜底
          fakeStopTimer = setTimeout(done, Math.max(1800, planMs + 400));
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
          u.onerror = () => { noteFailure("browser", "browser_error"); finish(); };
          const planMs = startText(onFrame, text, u.rate);
          // 某些 WebView / 未装中文语音包时不会回调 onend：按同一条时间线兜底
          fakeStopTimer = setTimeout(finish, Math.max(1800, planMs + 400));
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
          const st = e && e.status;
          const aborted = !!(e && e.name === "AbortError");
          noteFailure("server", st ? `http_${st}` : (aborted ? "start_timeout" : "network_error"),
            { httpStatus: st, timedOut: aborted });
          console.info("server tts 不可用，降级本机语音:", e && e.message ? e.message : e);
          return false;
        }
        if (gen !== speakGen) {                // 请求期间用户又发了新消息 → 丢弃旧音频
          try { W.URL.revokeObjectURL(blobUrl); } catch (e) {}
          return true;
        }
        const cleanup = () => { try { W.URL.revokeObjectURL(blobUrl); } catch (e) {} };
        // server 路失败（解码失败/跨域被拦）→ 先纯播放，再降级 browser
        playWithEnvelope(blobUrl, onFrame, () => { cleanup(); onEnd && onEnd(true); }, () => {
          console.info("server tts 口型链路失败，尝试纯播放");
          playPlain(blobUrl, onFrame, () => { cleanup(); onEnd && onEnd(true); }, () => {
            console.info("server tts 纯播放失败，降级本机语音");
            cleanup();
            if (!browserProvider.speak(text, opts)) {
              // 一路都没发出来：onEnd(false) 明确"没播成"，调用方不得打「语音已播」
              onFrame && onFrame(0);
              onEnd && onEnd(false);
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

    /** 按配置生成 provider 尝试顺序；tts=off 时直接静音，不走任何后端。
     *  这里必须尊重设置页的选择（审计发现旧版无论选什么都先 native，
     *  "服务端语音"形同虚设）：server → 服务端优先；browser → 本机优先；
     *  缺失/未配置时自然回落到下一条。 */
    function providerChain(cfg) {
      const c = cfg || {};
      if (c.tts === "off") return ["none"];
      const prefer = c.tts === "browser" ? ["browser", "native"] : ["server", "native", "browser"];
      const chain = prefer.filter((id) => {
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
     * onEnd(ok)：ok=true 表示真的出声并播完（UI 才可标「语音已播」）；
     * ok=false 表示链路跑完但没出声音（静默降级，UI 只能给安静提示）。
     * 返回 { spoke, provider }：spoke=false 表示本次没有出声（可由 UI 给安静提示）。
     * 文字渲染由调用方先完成，本函数不阻塞、不抛异常。
     */
    async function speak(text, onFrame, onEnd) {
      lastSpeakAt = now();   // 延迟探针基准：入口即"点击播放"
      speakStartAt = lastSpeakAt;
      const rawText = typeof text === "string" ? text : "";
      lastFailure = {
        textLen: Array.from(rawText).length,
        textHash: textHash(rawText),
        attempts: [],
      };
      const cfg = loadCfg();
      const frame = typeof onFrame === "function" ? onFrame : () => {};
      const finish = typeof onEnd === "function" ? onEnd : () => {};
      let ended = false;
      const done = (ok) => {
        if (ended) return;
        ended = true;
        try { finish(ok === false ? false : true); } catch (e) {}
      };

      stop();                                  // 先停上一段，防多路叠加
      const gen = speakGen;                    // stop() 之后取代际，异步返回时校验
      if (muted) { noteFailure(null, "muted"); frame(0); done(false); return { spoke: false, provider: "muted" }; }
      if (!rawText.trim()) {
        noteFailure(null, "empty_text");
        frame(0);
        done(false);
        return { spoke: false, provider: "none" };
      }
      // 用户显式关闭语音（设置页 tts=off）：只静默收尾，且**不回调 onEnd**。
      // 原因：onEnd 在 app.js 里承担「气泡标记 🔊 语音已播」，off 时回调会把一句
      // 根本没出声的话标成"已播"。状态与文案必须是同一事实：off = 无声 = 无标记。
      if (cfg.tts === "off") {
        noteFailure(null, "off");
        frame(0);
        return { spoke: false, provider: "off" };
      }

      try {
        const chain = providerChain(cfg);
        if (chain[0] === "none") noteFailure(null, "no_provider");
        for (const id of chain) {
          if (gen !== speakGen) return { spoke: false, provider: "stale" };
          const p = providers[id];
          if (!p) continue;   // "none"：没有可发声后端
          const handled = await Promise.resolve(
            p.speak(text, { cfg, onFrame: frame, onEnd: done, gen })
          );
          if (handled) return { spoke: true, provider: id };
          if (id !== "server") {
            noteFailure(id, id === "native" ? "bridge_rejected" : "browser_unavailable");
          }
        }
      } catch (e) {
        noteFailure(null, "speak_error");
        console.warn("TTS 链路异常，静默降级:", e && e.message);
      }
      if (!lastFailure.error) noteFailure(null, "no_provider");
      frame(0);
      done(false);
      return { spoke: false, provider: "none" };
    }

    /**
     * 安全朗读：调用方（app.js finalizeReply）是"文字先落屏 → 再补语音"，
     * 因此这里的同步异常、异步失败、配置读取崩溃都必须被吞掉，
     * 返回永不 reject 的 Promise，绝不把语音问题升级成发送链路失败。
     */
    function speakSafe(text, onFrame, onEnd, onSilent) {
      const silent = res => {
        if (typeof onSilent === "function") { try { onSilent(res); } catch (e) {} }
      };
      let p;
      try {
        p = speak(text, onFrame, onEnd);
      } catch (e) {
        noteFailure(null, "speak_error");
        console.warn("TTS 启动异常（文字照常）:", e && e.message);
        try { if (typeof onFrame === "function") onFrame(0); } catch (_e) {}
        silent({ spoke: false, provider: "error" });
        return Promise.resolve({ spoke: false, provider: "error" });
      }
      return Promise.resolve(p).then(res => {
        if (res && res.spoke === false) silent(res);
        return res;
      }, e => {
        noteFailure(null, "speak_error");
        console.warn("TTS 异常（文字照常）:", e && e.message);
        try { if (typeof onFrame === "function") onFrame(0); } catch (_e) {}
        silent({ spoke: false, provider: "error" });
        return { spoke: false, provider: "error" };
      });
    }

    return {
      speak,
      speakSafe,
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
      /** 探针：上一次服务端播放的分段耗时（毫秒，性能时间轴；不参与播放逻辑） */
      _lastTiming: () => lastTiming,
      /** 探针：最近一次 speak 的失败诊断（真正失败才有值；供失败日志与单测） */
      _lastFailure: () => (lastFailure && lastFailure.error ? lastFailure : null),
      /** 探针：开播超时阈值（音色线若调整 7s→10s，单测按此值 tick，不硬编码） */
      _audioStartTimeout: () => AUDIO_START_TIMEOUT,
      /** 探针：当前 <audio> 的真实状态（诊断"包络没出帧"到底是没解码还是没开播） */
      _probeAudio: () => (currentAudio ? {
        t: +(currentAudio.currentTime || 0).toFixed(3),
        dur: +((currentAudio.duration || 0).toFixed(3)),
        readyState: currentAudio.readyState,
        networkState: currentAudio.networkState,
        paused: !!currentAudio.paused,
        err: currentAudio.error ? currentAudio.error.code : 0
      } : null),
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
    /** 单测入口：无音频 provider 的文本时间线与口型包络 */
    _speechPlan: speechPlan,
    _speechLevel: speechLevel,
    _charMs: CHAR_MS,
    /** 单测入口：注入 fake window 造独立引擎（不触碰真实浏览器/音频） */
    _create: createEngine
  });
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.TTS = api;
  return api;
})();
