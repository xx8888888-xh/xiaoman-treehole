/* ============================================================
 * tts.js · 语音回复适配层
 * ------------------------------------------------------------
 * 1. server  → 服务端 edge-tts 生成 mp3（自然度高，默认）
 *      GET {ttsBase}/tts?text=…&voice=…  → audio/mpeg
 * 2. browser → Web Speech API（离线兜底，音质一般）
 * 播放时通过 onFrame 回调输出音量包络，供 Live2D 口型驱动。
 * ============================================================ */

const TTS = (() => {
  let currentAudio = null;

  /** 用 AudioContext 分析音量，驱动口型 */
  function playWithLipSync(url, onFrame, onEnd) {
    const audio = new Audio(url);
    currentAudio && currentAudio.pause();
    currentAudio = audio;
    let ctx = null, analyser = null, srcNode = null, raf = 0;

    audio.onended = () => { cancelAnimationFrame(raf); ctx && ctx.close(); onEnd && onEnd(); };
    audio.onerror = () => { cancelAnimationFrame(raf); ctx && ctx.close(); onEnd && onEnd(); };

    audio.play().then(() => {
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
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
      } catch (e) { /* 分析失败就静默播，口型走兜底 */ onFrame && onFrame(-1); }
    }).catch(() => onEnd && onEnd());
  }

  async function speak(text, onFrame, onEnd) {
    const cfg = API.loadCfg();
    const mode = cfg.tts || "server";
    if (mode === "off" || !text) { onEnd && onEnd(); return; }

    if (mode === "server") {
      const base = (cfg.apiBase || "http://127.0.0.1:8902").replace(/\/$/, "");
      const url = `${base}/tts?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(cfg.voice || "zh-CN-XiaoyiNeural")}`;
      try {
        playWithLipSync(url, onFrame, onEnd);
        return;
      } catch (e) { console.warn("server tts 失败，降级 browser:", e); }
    }

    // Android JSBridge：系统 TTS（WebView 内 speechSynthesis 不可用时的真机方案）
    if (window.AndroidTTS && window.AndroidTTS.available && window.AndroidTTS.available()) {
      try {
        const ok = window.AndroidTTS.speak(text);
        if (ok) {
          const fake = setInterval(() => onFrame && onFrame(0.35 + Math.random() * 0.35), 90);
          const stopFake = () => { clearInterval(fake); onFrame && onFrame(0); onEnd && onEnd(); };
          // 按语速粗估时长，系统TTS无完成回调穿透JS时用估算兜底
          setTimeout(stopFake, Math.max(2500, text.length * 260));
          return;
        }
      } catch (e) { console.warn("AndroidTTS 桥失败:", e); }
    }

    // browser 兜底
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "zh-CN"; u.rate = 1.02; u.pitch = 1.15;
      const fake = setInterval(() => onFrame && onFrame(0.4 + Math.random() * 0.3), 90);
      u.onend = () => { clearInterval(fake); onFrame && onFrame(0); onEnd && onEnd(); };
      u.onerror = () => { clearInterval(fake); onEnd && onEnd(); };
      speechSynthesis.speak(u);
    } catch (e) { onEnd && onEnd(); }
  }

  function stop() {
    currentAudio && currentAudio.pause();
    speechSynthesis && speechSynthesis.cancel();
    if (window.AndroidTTS && window.AndroidTTS.stop) try { window.AndroidTTS.stop(); } catch (e) {}
  }

  return { speak, stop };
})();
