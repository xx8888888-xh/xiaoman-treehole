/* ============================================================
 * xm_host_bootstrap.js · Android 壳注入脚本（只存在于 APK 内）
 * ------------------------------------------------------------
 * 由 MainActivity.assetResponse 在 assets/www/index.html 的 </head> 前注入，
 * 不修改 web/ 源文件。职责：
 *  1) 计时器台账：pause() 冻结页面全部 setInterval/setTimeout，resume() 按剩余
 *     时长重新武装（不丢语义），用于"退后台真释放 + 回前台按需重建"；
 *  2) 后台硬闸：暂停期间 Reminders.due() 恒空、markDone() 空操作，
 *     杜绝"隐藏页收到提醒还标记完成"的路径；
 *  3) 提醒镜像：Reminders 增删改后把整表推给原生（AlarmManager 负责锁屏/杀进程投递）；
 *     启动时把原生"已投递"的 id 补记为完成，避免重复催；
 *  4) 语音策略：默认"点击才朗读"。自动朗读需显式开关
 *     （localStorage.xiaoman_tts_autoplay="1" 或原生 AndroidHost.isAutoRead()）；
 *  5) 心跳间隔可配置：包装 Heartbeat.start，把它的 60s 周期换成原生配置值。
 * 说明：任何一处失败都只降级，不抛未捕获异常、不影响聊天主链路。
 * ============================================================ */
(function () {
  "use strict";
  if (window.__xmHost) return;

  var H = { version: 1, paused: false };
  window.__xmHost = H;

  function host() {
    try { return window.AndroidHost || null; } catch (e) { return null; }
  }

  var rawSetInterval = window.setInterval;
  var rawClearInterval = window.clearInterval;
  var rawSetTimeout = window.setTimeout;
  var rawClearTimeout = window.clearTimeout;

  var seq = 0;
  var ledger = [];          // {kind, fn, args, delay, handle, real, armedAt, remaining}
  var hbDepth = 0;          // Heartbeat.start 调用期间为 1（用于替换 60s 周期）

  function hostInterval() {
    var d = 0;
    try { d = Number(host() && host().getHeartbeatIntervalMs()); } catch (e) { d = 0; }
    if (!isFinite(d) || d <= 0) d = 60000;
    return Math.min(1800000, Math.max(30000, d));
  }

  function arm(r) {
    if (H.paused) return;
    if (r.kind === "interval") {
      r.real = rawSetInterval.call(window, function () {
        if (H.paused) return;
        try { r.fn.apply(null, r.args); } catch (e) { console.error("[xmHost] interval", e); }
      }, r.delay);
    } else {
      r.armedAt = Date.now();
      var ms = (r.remaining === null || r.remaining === undefined) ? r.delay : r.remaining;
      r.real = rawSetTimeout.call(window, function () {
        var i = ledger.indexOf(r);
        if (i >= 0) ledger.splice(i, 1);
        if (H.paused) return;
        try { r.fn.apply(null, r.args); } catch (e) { console.error("[xmHost] timeout", e); }
      }, ms);
      r.remaining = null;
    }
  }

  window.setInterval = function (fn, delay) {
    var args = Array.prototype.slice.call(arguments, 2);
    var d = Number(delay);
    if (!isFinite(d) || d < 0) d = 0;
    if (hbDepth > 0 && d === 60000) d = hostInterval();   // 心跳周期可配置
    var r = { kind: "interval", fn: fn, args: args, delay: d, handle: ++seq, real: null };
    ledger.push(r);
    arm(r);
    return r.handle;
  };

  window.clearInterval = function (handle) {
    for (var i = 0; i < ledger.length; i++) {
      var r = ledger[i];
      if (r.handle === handle && r.kind === "interval") {
        if (r.real !== null) rawClearInterval.call(window, r.real);
        ledger.splice(i, 1);
        return;
      }
    }
  };

  window.setTimeout = function (fn, delay) {
    var args = Array.prototype.slice.call(arguments, 2);
    var d = Number(delay);
    if (!isFinite(d) || d < 0) d = 0;
    var r = { kind: "timeout", fn: fn, args: args, delay: d, handle: ++seq, real: null, armedAt: 0, remaining: d };
    ledger.push(r);
    arm(r);
    return r.handle;
  };

  window.clearTimeout = function (handle) {
    for (var i = 0; i < ledger.length; i++) {
      var r = ledger[i];
      if (r.handle === handle && r.kind === "timeout") {
        if (r.real !== null) rawClearTimeout.call(window, r.real);
        ledger.splice(i, 1);
        return;
      }
    }
  };

  // ---------- 暂停 / 恢复 ----------

  try {
    Object.defineProperty(document, "hidden", { configurable: true, get: function () { return H.paused; } });
    Object.defineProperty(document, "visibilityState", {
      configurable: true, get: function () { return H.paused ? "hidden" : "visible"; }
    });
  } catch (e) { /* 老 WebView 不改也无碍 */ }

  H.pause = function () {
    if (H.paused) return;
    H.paused = true;
    var now = Date.now();
    for (var i = 0; i < ledger.length; i++) {
      var r = ledger[i];
      if (r.kind === "interval") {
        if (r.real !== null) rawClearInterval.call(window, r.real);
        r.real = null;
      } else {
        if (r.real !== null) rawClearTimeout.call(window, r.real);
        var left = (r.armedAt ? r.armedAt + r.delay - now : r.delay);
        r.remaining = Math.max(0, left);
        r.real = null;
      }
    }
    try { if (window.TTS && window.TTS.stop) window.TTS.stop(); } catch (e) {}
    try { document.dispatchEvent(new Event("visibilitychange")); } catch (e) {}
    try { if (host() && host().onPageHidden) host().onPageHidden(); } catch (e) {}
  };

  H.resume = function () {
    if (!H.paused) return;
    H.paused = false;
    for (var i = 0; i < ledger.length; i++) {
      if (ledger[i].real === null) arm(ledger[i]);
    }
    try { document.dispatchEvent(new Event("visibilitychange")); } catch (e) {}
    try { if (host() && host().onPageVisible) host().onPageVisible(); } catch (e) {}
  };

  // ---------- 提醒镜像（原生 AlarmManager 是权威调度方） ----------

  var remindersPatched = false;

  function pushReminders() {
    var h = host();
    if (!h || !window.Reminders) return null;
    try {
      var res = h.syncReminders(JSON.stringify(window.Reminders.all()));
      return res ? JSON.parse(res) : null;
    } catch (e) { return null; }
  }

  function drainDelivered() {
    var h = host();
    if (!h || !window.Reminders) return;
    try {
      var raw = h.drainDelivered();
      var ids = raw ? JSON.parse(raw) : [];
      if (ids && ids.length) window.Reminders.markDone(ids);   // 原生已投递过 → 不再重复催
    } catch (e) {}
  }

  function patchReminders() {
    var R = window.Reminders;
    if (!R || remindersPatched) return !!R;
    remindersPatched = true;
    var origAdd = R.add, origDone = R.markDone, origCancel = R.cancel, origDue = R.due;
    var origUpdate = R.update, origMerge = R.merge, origClearDone = R.clearDone;
    if (typeof origAdd === "function") {
      R.add = function () { var out = origAdd.apply(R, arguments); pushReminders(); return out; };
    }
    // 备份导入 / 改时间 / 清已完成也属于"表变了"：漏推会导致提醒在列表里却不响
    if (typeof origUpdate === "function") {
      R.update = function () { var out = origUpdate.apply(R, arguments); pushReminders(); return out; };
    }
    if (typeof origMerge === "function") {
      R.merge = function () { var out = origMerge.apply(R, arguments); pushReminders(); return out; };
    }
    if (typeof origClearDone === "function") {
      R.clearDone = function () { var out = origClearDone.apply(R, arguments); pushReminders(); return out; };
    }
    if (typeof origCancel === "function") {
      R.cancel = function () { var out = origCancel.apply(R, arguments); pushReminders(); return out; };
    }
    if (typeof origDone === "function") {
      // 后台冻结期间禁止标记完成：隐藏页不该有"已处理"的副作用
      R.markDone = function (ids) {
        if (H.paused) return;
        var out = origDone.apply(R, arguments);
        pushReminders();
        return out;
      };
    }
    if (typeof origDue === "function") {
      // 后台冻结期间到期检查恒空（提醒由原生闹钟 + 通知负责）
      R.due = function () { return H.paused ? [] : origDue.apply(R, arguments); };
    }
    pushReminders();
    drainDelivered();
    return true;
  }

  var remindersWatch = window.setInterval(function () {
    if (patchReminders()) { window.clearInterval(remindersWatch); remindersWatch = 0; }
  }, 400);

  // ---------- 心跳周期可配置 ----------

  var heartbeatPatched = false;
  function patchHeartbeat() {
    var HB = window.Heartbeat;
    if (!HB || heartbeatPatched || typeof HB.start !== "function") return !!HB;
    heartbeatPatched = true;
    var origStart = HB.start;
    HB.start = function (push, hist) {
      hbDepth++;
      try { return origStart.call(HB, push, hist); } finally { hbDepth--; }
    };
  }
  var heartbeatWatch = window.setInterval(function () {
    if (patchHeartbeat()) { window.clearInterval(heartbeatWatch); heartbeatWatch = 0; }
  }, 400);

  // ---------- 语音：默认"点击才朗读" ----------

  var lastVoiceTap = 0;

  function looksLikeVoiceTarget(el) {
    if (!el || !el.closest) return false;
    try {
      return !!el.closest("#messages, [data-tts], [class*=voice], [id*=voice], [class*=speak], " +
        "[class*=bubble], [aria-label*=朗读], [title*=朗读], [aria-label*=语音], [title*=语音]");
    } catch (e) { return false; }
  }

  function markVoiceTap(ev) {
    var t = ev && ev.target;
    if (looksLikeVoiceTarget(t)) lastVoiceTap = Date.now();
  }
  document.addEventListener("pointerdown", markVoiceTap, true);
  document.addEventListener("touchstart", markVoiceTap, true);
  document.addEventListener("click", markVoiceTap, true);

  function autoReadOn() {
    try { if (localStorage.getItem("xiaoman_tts_autoplay") === "1") return true; } catch (e) {}
    try { var h = host(); if (h && h.isAutoRead && h.isAutoRead()) return true; } catch (e) {}
    return false;
  }

  /** 点击朗读模式下：既不出声，也不要弹"语音不可用"提示、不标记气泡已播 */
  function silentTipOff() {
    try { sessionStorage.setItem("xiaoman_voice_tip_off", "1"); } catch (e) {}
  }

  var ttsPatched = false;
  function patchTTS() {
    var T = window.TTS;
    if (!T || ttsPatched || typeof T.speak !== "function") return !!T;
    ttsPatched = true;
    var origSpeak = T.speak;
    T.speak = function (text, onFrame, onEnd) {
      var allowed = autoReadOn() || (Date.now() - lastVoiceTap < 5000);
      if (!H.paused && allowed) return origSpeak.apply(T, arguments);
      // 不出声时**不回调 onEnd**：调用方会把它当成「播放完成」并把气泡标成
      // 「🔊 语音已播」。状态与 UI 必须同源：没出声就不能有已播标记。
      if (!H.paused) silentTipOff();
      try { if (typeof onFrame === "function") onFrame(0); } catch (e) {}
      return Promise.resolve({ spoke: false, provider: H.paused ? "paused" : "tap-only" });
    };
  }
  var ttsWatch = window.setInterval(function () {
    if (patchTTS()) { window.clearInterval(ttsWatch); ttsWatch = 0; }
  }, 400);

  // 页面脚本已加载完（本脚本在 </head> 前、业务脚本之后仍可能晚到）：再兜底扫一次
  window.addEventListener("load", function () {
    try { patchReminders(); } catch (e) {}
    try { patchHeartbeat(); } catch (e) {}
    try { patchTTS(); } catch (e) {}
    drainDelivered();
    pushReminders();
  });
})();
