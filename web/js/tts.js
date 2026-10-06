/* ============================================================
 * tts.js · 语音回复适配层
 * ------------------------------------------------------------
 * 1. server  → 服务端 edge-tts 生成 mp3（自然度高，默认）
 *      GET {ttsBase}/tts?text=…&voice=…  → audio/mpeg
 * 2. browser → Web Speech API（离线兜底，音质一般）
 * 播放时通过 onFrame 回调输出音量包络，供 Live2D 口型驱动。
 * 失败语义：server 路不通 → 自动降级 browser，绝不静默无声。
 * ============================================================ */

const TTS = (() => {
  let currentAudio = null;
  let fakeTimer = null;      // 假包络 interval（Android/browser 路），全局唯一
  let fakeStopTimer = null;  // Android 路时长估算的兜底停止定时器
  let androidSettle = null;  // 当前 Android TTS 的完成回调（供原生 __ttsEnded 触发）

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

  /** 清掉所有假包络定时器（避免连发消息时多路叠加、口型乱抖） */
  function clearFake() {
    if (fakeTimer) { clearInterval(fakeTimer); fakeTimer = null; }
    if (fakeStopTimer) { clearTimeout(fakeStopTimer); fakeStopTimer = null; }
  }

  /** 停掉当前所有正在播的语音（含假包络定时器） */
  function stop() {
    if (currentAudio) { try { currentAudio.pause(); } catch (e) {} currentAudio = null; }
    androidSettle = null;   // 丢弃上一段 Android TTS 的完成回调，防止串台
    clearFake();
    try { if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel(); } catch (e) {}
    if (window.AndroidTTS && window.AndroidTTS.stop) { try { window.AndroidTTS.stop(); } catch (e) {} }
  }

  /**
   * 用 AudioContext 分析音量，驱动口型
   * @param onFail 播放失败（加载/解码/自动播放被拦）时回调 —— 由调用方决定降级
   */
  function playWithLipSync(url, onFrame, onEnd, onFail) {
    const audio = new Audio(url);
    if (currentAudio) { try { currentAudio.pause(); } catch (e) {} }
    currentAudio = audio;
    let ctx = null, analyser = null, srcNode = null, raf = 0, settled = false;
    const settle = (failed) => {
      if (settled) return;
      settled = true;
      cancelAnimationFrame(raf);
      if (ctx) { try { ctx.close(); } catch (e) {} }
      if (currentAudio === audio) currentAudio = null;
      if (failed) { onFail && onFail(); } else { onEnd && onEnd(); }
    };

    audio.onended = () => settle(false);
    audio.onerror = () => settle(true);   // 真败因（404/解码失败）走降级，不静默

    audio.play().then(() => {
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
        // 非用户手势下 AudioContext 可能 suspended → 全部输出为 0、嘴不动
        if (ctx.state === "suspended") ctx.resume().catch(() => {});
        srcNode = ctx.createMediaElementSource(audio);
        analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        srcNode.connect(analyser); analyser.connect(ctx.destination);
        const buf = new Uint8Array(analyser.frequencyBinCount);
        (function loop() {
          analyser.getByteFrequencyData(buf);
          let sum = 0; for (let i = 2; i < 40; i++) sum += buf[i];
          const level = Math.min(1, sum / (38 * 160));   // 0~1 音量包络
          onFrame && onFrame(level);
          raf = requestAnimationFrame(loop);
        })();
      } catch (e) {
        // 分析失败就静默播，口型走兜底（-1 触发 lipFrame 的随机包络）
        onFrame && onFrame(-1);
      }
    }).catch(() => settle(true));
  }

  /** 浏览器 SpeechSynthesis（离线兜底） */
  function speakBrowser(text, onFrame, onEnd) {
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "zh-CN"; u.rate = 1.02; u.pitch = 1.15;
      const done = () => { clearFake(); onFrame && onFrame(0); onEnd && onEnd(); };
      u.onend = done;
      u.onerror = done;
      clearFake();
      fakeTimer = setInterval(() => onFrame && onFrame(0.4 + Math.random() * 0.3), 90);
      speechSynthesis.speak(u);
    } catch (e) { onEnd && onEnd(); }
  }

  /** Android JSBridge：系统 TTS（WebView 内 speechSynthesis 不可用时的真机方案） */
  function speakAndroid(text, onFrame, onEnd) {
    try {
      if (!(window.AndroidTTS.speak(text))) return false;
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
    } catch (e) { console.warn("AndroidTTS 桥失败:", e); return false; }
  }

  async function speak(text, onFrame, onEnd) {
    const cfg = API.loadCfg();
    const mode = cfg.tts || "server";
    stop();                                  // 先停上一段，防多路叠加
    if (mode === "off" || !text) { onEnd && onEnd(); return; }

    // Android 桥优先于 server（真机无本地 8903 时）
    if (window.AndroidTTS && window.AndroidTTS.available && window.AndroidTTS.available()) {
      if (speakAndroid(text, onFrame, onEnd)) return;
    }

    if (mode === "server") {
      const base = (cfg.ttsBase || "http://127.0.0.1:8903").replace(/\/$/, "");
      const url = `${base}/tts?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(cfg.voice || "zh-CN-XiaoyiNeural")}`;
      // server 路失败（服务挂/超时/解码失败）→ 显式降级 browser，避免静默无声
      playWithLipSync(url, onFrame, onEnd, () => {
        console.warn("server tts 失败，降级 browser");
        speakBrowser(text, onFrame, onEnd);
      });
      return;
    }

    speakBrowser(text, onFrame, onEnd);
  }

  return { speak, stop };
})();
