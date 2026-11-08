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

  var ttsOrig = null;
  var ttsPatched = false;
  function patchTTS() {
    var T = window.TTS;
    if (!T || ttsPatched || typeof T.speak !== "function") return !!T;
    ttsPatched = true;
    ttsOrig = T.speak;
    T.speak = function (text, onFrame, onEnd) {
      var allowed = autoReadOn() || (Date.now() - lastVoiceTap < 5000);
      if (!H.paused && allowed) return ttsOrig.apply(T, arguments);
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

  // 点已载入的回复气泡 → 立即重读这一条。
  // 原来"5 秒内点过消息区才发声"只是给自动朗读开了一个时间窗，点击本身不朗读，
  // 回复到达时窗口早已过期 → App 内实际上永远不出声（声明"点击才朗读"却没有入口）。
  // 这里补上真正的入口；重播不走上面的 tap-only 门（用户手势就是明确意图）。
  var replayCount = 0;
  function bubbleText(b) {
    var t = "";
    for (var i = 0; i < b.childNodes.length; i++) {
      var n = b.childNodes[i];
      if (n.nodeType === 3) t += n.nodeValue;
      else if (n.nodeType === 1
               && !(n.className && String(n.className).indexOf("meta") >= 0)) {
        t += n.textContent || "";
      }
    }
    return String(t).replace(/\s+/g, " ").trim();
  }
  function replayBubble(ev) {
    var el = ev && ev.target;
    if (!el || !el.closest) return;
    if (!ttsOrig) patchTTS();   // 页面可能晚于本脚本加载 TTS：点的时候再补一次包装
    if (!ttsOrig) return;
    // 消息操作按钮/菜单/链接不触发重播
    if (el.closest(".msg-more, .msg-menu, button, a, input, textarea")) return;
    var bubble = el.closest(".bubble.them");
    if (!bubble || !bubble.closest(".msg-row.them")) return;
    var text = bubbleText(bubble);
    if (!text) return;
    lastVoiceTap = Date.now();
    replayCount++;
    try {
      window.__ttsReplay = { at: lastVoiceTap, count: replayCount, text: text };
    } catch (e) {}
    try { ttsOrig.call(window.TTS, text, function () {}, function () {}); } catch (e) {}
  }
  document.addEventListener("click", replayBubble, false);

  // 页面脚本已加载完（本脚本在 </head> 前、业务脚本之后仍可能晚到）：再兜底扫一次
  window.addEventListener("load", function () {
    try { patchReminders(); } catch (e) {}
    try { patchHeartbeat(); } catch (e) {}
    try { patchTTS(); } catch (e) {}
    drainDelivered();
    pushReminders();
  });
})();
