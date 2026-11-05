/* ============================================================
 * pet.js · Android 桌面悬浮宠物（分层立绘 + 点击反馈 + 提醒气泡）
 * ------------------------------------------------------------
 * 运行环境：FloatWindowService 里的透明 WebView（窗口仅 140x180dp）。
 * 分工：
 *  - 窗口拖动/贴边：原生 OnTouchListener 负责（本页只观察指针，不再移动窗口）
 *  - 单击互动、双击聊天、长按菜单：本页负责
 *  - 文字气泡：原生 TextView（AndroidPet.bubble），无桥时退化为页内气泡
 *  - 提醒：每 30s 读 Reminders.due()，经 PetCore 节流后播报；主 App 在前台时让位
 *  - 静音：本页不加载 tts.js、不创建 Audio，静音约束始终满足
 *  - 渲染：与主舞台同一套 LayeredPuppet（Canvas 2D）。过去 WebGL 画布在
 *    overlay 里不会被合成，只能每帧 drawImage 到 2D 镜像层；现在引擎本身就是
 *    2D，镜像层/上下文丢失重载整套补丁一并移除。
 * ============================================================ */
(function () {
  "use strict";

  const core = window.PetCore;
  const stageEl = document.getElementById("petStage");
  const tipEl = document.getElementById("petTip");
  const menuEl = document.getElementById("petMenu");
  const BRIDGE = window.AndroidPet || null;

  const MODEL_BASE = "assets/live2d/original/";
  const VENDOR = [
    "assets/live2d/original/manifest.js",
    "js/vendor/puppet.js",
  ];

  let puppet = null;
  let canvas = null;
  let mounted = false;
  let motion = true;
  let largeFont = false;
  let grayIcons = true;
  let styleKey = "";
  let lastTapAt = 0;
  let pressTimer = 0;
  let downPoint = null;
  let moved = false;
  let longFired = false;
  let tipTimer = 0;
  let introShown = false;
  let reminderTimer = 0;
  let styleTimer = 0;
  let lastPaintAt = 0;
  let repaintTimer = 0;
  let rafId = null;
  let lastTs = 0;
  let freezeTimer = 0;
  let watchdogTimer = 0;
  let envPaused = false;   // 页面不可见/窗口失焦期间暂停（收起桌宠时也走这里）

  /* ---------- 静止分档（耗电 P0） ----------
   * 实测：后台桌宠可见时主进程 5 分钟吃掉 194.2s CPU（单核 64.7%），
   * 根因是 60fps 全画布重绘一直在跑，而桌面宠物绝大多数时间是"没人看"的。
   * 分三档，全部以「最后一次交互」为基准：
   *   0–6s   全帧（呼吸/眨眼/视线漫游）
   *   6–10s  15fps（保呼吸与眨眼，砍掉 3/4 的帧）
   *   >10s   完全停 rAF 与 draw，只在触摸/可见性变化/10s 呼吸节拍补一帧
   * 任何触摸、表情动作、原生补帧都会把计时器清零并重新开循环。 */
  const IDLE_THROTTLE_MS = 6000;
  const IDLE_STOP_MS = 10000;
  const IDLE_FPS = 15;
  const FRAME_CAP_MS = 1000 / IDLE_FPS;
  const BREATH_WAKE_MS = 10000;
  let lastActionAt = Date.now();
  let idleStopped = false;
  let breathTimer = 0;
  let appliedMotion = null;
  let loopTimer = 0;      // 降帧期用 setTimeout 排下一帧，彻底不占 rAF

  /** 记录一次"有人在看"：重置静止计时；若已停机则重新开循环 */
  function noteActivity() {
    lastActionAt = Date.now();
    if (!idleStopped) return;
    idleStopped = false;
    clearTimeout(breathTimer);
    breathTimer = 0;
    startLoop();
  }

  /** 连续静止到点：停 rAF、定格最后一帧，改为低频呼吸节拍 */
  function enterIdleStop() {
    stopLoop();
    idleStopped = true;
    lastPaintAt = Date.now();
    log("pet-idle-stop");
    clearTimeout(breathTimer);
    breathTimer = setTimeout(breathTick, BREATH_WAKE_MS);
  }

  /** 停机后的呼吸节拍：只补一帧（update+render 一次），绝不重开循环 */
  function breathTick() {
    breathTimer = 0;
    if (!mounted || !motion || !puppet || envBlocked() || !idleStopped) return;
    try {
      puppet.update(0.4);      // 约 10s 的呼吸相位推进
      renderFrame();
    } catch (e) {
      log("pet-breath-error " + (e && e.message));
    }
    if (idleStopped && Date.now() - lastActionAt >= IDLE_STOP_MS) {
      breathTimer = setTimeout(breathTick, BREATH_WAKE_MS);
    }
  }

  function log(msg) {
    try { console.log("[probe] " + msg); } catch (e) { /* 忽略 */ }
  }

  function readAppearance() {
    try {
      const raw = window.Appearance ? Appearance.get() : null;
      return core.appearance(raw);
    } catch (e) {
      return core.appearance(null);
    }
  }

  function applyStyle(p) {
    motion = !!p.motion;
    largeFont = !!p.largeFont;
    grayIcons = !!p.grayIcons;
    const key = [motion, largeFont, grayIcons].join("|");
    if (key !== styleKey) {
      styleKey = key;
      try { if (BRIDGE && BRIDGE.setStyle) BRIDGE.setStyle(motion, largeFont, grayIcons); } catch (e) { /* 原生已回收 */ }
    }
    setMotionEnabled(motion);
  }

  function syncAppearance() {
    applyStyle(readAppearance());
  }

  /** 原生气泡优先；没有桥（浏览器预览/原生异常）时用页内气泡，功能不丢 */
  function bubble(text) {
    if (BRIDGE && BRIDGE.bubble) {
      try { BRIDGE.bubble(String(text).slice(0, 120)); return; } catch (e) { /* 降级 */ }
    }
    showLocalTip(text);
  }

  function showLocalTip(text) {
    tipEl.textContent = text;
    tipEl.classList.remove("hidden");
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => tipEl.classList.add("hidden"), 5200);
  }

  function showMenu(show) {
    menuEl.classList.toggle("hidden", !show);
  }

  function openPanel() {
    showMenu(false);
    log("pet-panel");
    if (BRIDGE && BRIDGE.openPanel) {
      try { BRIDGE.openPanel(); return; } catch (e) { /* 降级 */ }
    }
    showLocalTip("聊天面板在 App 内打开");
  }

  function closePet() {
    showMenu(false);
    log("pet-close");
    if (BRIDGE && BRIDGE.close) {
      try { BRIDGE.close(); return; } catch (e) { /* 降级 */ }
    }
    showLocalTip("关闭悬浮窗（预览模式）");
  }

  function setParam(name, value) {
    if (!puppet) return;
    try { puppet.setParam(name, value); } catch (e) { /* 模型无此参数 */ }
  }

  const EMOTION_MAP = {
    happy: "smile", sad: "neutral", gentle: "smile", surprised: "smile",
    shy: "shy", neutral: "neutral", smile: "smile", laugh: "laugh", talk: "talk",
  };
  const MOTION_MAP = {
    greeting: "nod", nod: "nod", shake: "shake", happyjump: "nod", talk: "talk",
  };

  /* 只画窗口真正看得见的那一段：画布 CSS 高 300% 窗口、宽屏时还会左右溢出，
   * 剩下约 2/3 高度和两侧都被 #petStage 的 overflow:hidden 裁掉。
   * Canvas2D 在软件光栅下会为整块后备缓冲逐帧上色，这里加一个取景 clip，
   * Skia 只处理可见矩形（约为整块面积的 20%），帧量不变而像素量按面积下降。 */
  const CLIP_MARGIN_PX = 6;

  function visibleRect() {
    const full = { x: 0, y: 0, w: puppet ? puppet.W : 0, h: puppet ? puppet.H : 0 };
    if (!puppet || !canvas) return full;
    const cw = canvas.clientWidth;
    const chh = canvas.clientHeight;
    const sw = stageEl.clientWidth;
    const sh = stageEl.clientHeight;
    if (!cw || !chh || !sw || !sh) return full;
    const k = puppet.H / chh;                 // 后备像素 / CSS 像素
    const vw = Math.min(cw, sw);
    const vh = Math.min(chh, sh - 8);         // CSS: canvas top = 8px
    const x = Math.max(0, Math.round(((cw - vw) / 2) * k) - CLIP_MARGIN_PX);
    const y = 0;                              // 顶边从 0 起，margin 已把 8px 偏移算进去
    const w = Math.min(puppet.W - x, Math.round(vw * k) + CLIP_MARGIN_PX * 2);
    const h = Math.min(puppet.H - y, Math.round(vh * k) + CLIP_MARGIN_PX * 2);
    return { x, y, w, h };
  }

  function renderFrame() {
    if (!puppet) return;
    const ctx = puppet.ctx;
    const r = visibleRect();
    ctx.save();
    ctx.beginPath();
    ctx.rect(r.x, r.y, r.w, r.h);
    ctx.clip();
    try {
      puppet.render();
    } finally {
      ctx.restore();
    }
    lastPaintAt = Date.now();
  }

  function renderOnce() {
    if (!puppet) return;
    try {
      puppet.fit();
      renderFrame();
    } catch (e) {
      log("pet-render-error " + (e && e.message));
    }
  }

  function stopLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    if (loopTimer) clearTimeout(loopTimer);
    loopTimer = 0;
    lastTs = 0;
  }

  /* ---------- 环境暂停（耗电 P0） ----------
   * 桌宠窗口被收起（GONE → document.hidden）、窗口失焦、页面切换时停掉 rAF；
   * 恢复可见/聚焦后按需重启，零动画（motion=off）下永不启动。 */
  function envBlocked() {
    return envPaused || (typeof document !== "undefined" && document.hidden === true);
  }

  function pauseForEnv(why) {
    if (envPaused) return;
    envPaused = true;
    stopLoop();
    idleStopped = false;
    clearTimeout(breathTimer);
    breathTimer = 0;
    clearTimeout(repaintTimer);
    repaintTimer = 0;
    log("pet-pause:" + why);
  }

  function resumeForEnv(why) {
    envPaused = false;
    if (document.hidden) return;
    if (!mounted || !puppet) return;
    lastActionAt = Date.now();   // 重新可见 = 新的交互窗口，先跑满帧
    if (motion) startLoop();
    else renderOnce();
    log("pet-resume:" + why);
  }

  /** 排下一帧：满帧走 rAF；降帧期（≥6s 静止）改走 setTimeout，不再占 rAF 配额 */
  function scheduleFrame(delayMs) {
    if (delayMs > 0) {
      loopTimer = setTimeout(() => {
        loopTimer = 0;
        frame(performance.now());
      }, delayMs);
    } else {
      rafId = requestAnimationFrame(frame);
    }
  }

  function frame(ts) {
    rafId = null;
    if (!puppet || !motion || envBlocked()) return;
    const idleFor = Date.now() - lastActionAt;
    if (idleFor >= IDLE_STOP_MS) {
      enterIdleStop();             // 连续静止：彻底停渲，等触摸/节拍唤醒
      return;
    }
    const throttled = idleFor >= IDLE_THROTTLE_MS;
    // 只按"真正画出来的帧"推进时间；降帧期单帧跨度更大，上限放宽到 90ms
    const dt = lastTs ? Math.min(throttled ? 0.09 : 0.05, (ts - lastTs) / 1000) : 1 / 60;
    lastTs = ts;
    try {
      puppet.update(dt);           // 呼吸/眨眼/视线漫游由引擎的自动待机统一驱动
      renderFrame();
    } catch (e) {
      log("pet-frame-error " + (e && e.message));
    }
    scheduleFrame(throttled ? FRAME_CAP_MS : 0);
  }

  function startLoop() {
    if (rafId || loopTimer || !puppet || !motion || envBlocked()) return;
    idleStopped = false;
    clearTimeout(breathTimer);
    breathTimer = 0;
    lastTs = 0;
    scheduleFrame(0);
  }

  /** 零动画：停 rAF 并定格同一帧；纹理解码后补一帧，随后不再重绘 */
  function freezeFrame() {
    stopLoop();
    idleStopped = false;
    clearTimeout(breathTimer);
    breathTimer = 0;
    if (!puppet) return;
    puppet.setAutoIdle(false);
    renderOnce();
    clearTimeout(freezeTimer);
    freezeTimer = setTimeout(() => {
      freezeTimer = 0;
      if (!motion) renderOnce();
    }, 300);
  }

  function setEmotion(name) {
    if (!puppet) return;
    try { puppet.setEmotion(EMOTION_MAP[name] || "neutral"); } catch (e) { /* 忽略 */ }
    setParam("headTilt", name === "sad" ? -0.6 : 0);
    if (!motion) renderOnce();
  }

  function playMotion(group) {
    if (!motion || !puppet) return;
    const id = MOTION_MAP[String(group || "").toLowerCase()];
    if (!id) return;
    noteActivity();
    try { puppet.playMotion(id); } catch (e) { /* 忽略 */ }
    startLoop();
  }

  function interact(yRatio) {
    noteActivity();
    const r = core.interactionFor(yRatio, Math.random());
    setEmotion(r.emotion);
    playMotion(r.motion);
    bubble(r.line);
    log("pet-tap:" + r.area);
  }

  function setMotionEnabled(on) {
    motion = !!on;
    if (!puppet) return;
    // syncAppearance 每 15s 会重放同一份外观：值没变就不能把静止停机的循环重新拉起
    if (appliedMotion === motion) return;
    appliedMotion = motion;
    puppet.setAutoIdle(motion);
    if (motion) {
      clearTimeout(freezeTimer);
      freezeTimer = 0;
      noteActivity();
      startLoop();
    } else {
      freezeFrame();
    }
  }

  /** 自适应：画布 CSS 高度 = 300% 窗口（头部特写），引擎按 clientHeight 等比铺满 */
  function fit() {
    if (!puppet) return;
    try {
      puppet.fit();
      log("pet-fit w=" + canvas.width + " h=" + canvas.height);
    } catch (e) {
      log("pet-fit-error " + (e && e.message));
    }
  }

  /**
   * 强制重绘：overlay 窗口被移动/从 GONE 切回可见后，部分设备（软件 GL 模拟器实测）
   * 的 Chromium 会停止给这个窗口出帧，画面变成整块透明。原生侧在贴边/恢复可见时
   * 通过 evaluateJavascript 调用本函数，把尺寸重置 + 立刻补一帧。
   */
  function repaint(why) {
    if (!mounted || envBlocked()) return;   // 不可见时不排 rAF；恢复可见由 visibilitychange 补帧
    clearTimeout(repaintTimer);
    lastActionAt = Date.now();              // 原生补帧 = 窗口刚被动过，重新给 10s 满帧窗口
    log("pet-repaint:" + (why || "manual"));
    repaintTimer = setTimeout(() => {
      renderOnce();
      requestAnimationFrame(() => renderOnce());
    }, 120);
  }

  /** 看门狗：rAF 被系统暂停时（窗口不可见/被移动）重新拉起并补一帧。
   *  静止停机是刻意行为：看门狗不得把已经停掉的循环重新拉起。 */
  function startWatchdog() {
    clearInterval(watchdogTimer);
    watchdogTimer = setInterval(() => {
      if (!mounted || !puppet || !motion || envBlocked()) return;
      if (idleStopped || Date.now() - lastActionAt >= IDLE_STOP_MS) return;
      if (Date.now() - lastPaintAt > 2500) {
        log("pet-watchdog");
        startLoop();
        renderOnce();
      }
    }, 1500);
  }

  function mountVendor() {
    return VENDOR.reduce((p, src) => p.then(() => new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.async = false;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error(src));
      document.body.appendChild(s);
    })), Promise.resolve());
  }

  function mount() {
    if (mounted) return;
    mounted = true;
    canvas = document.createElement("canvas");
    canvas.id = "petCanvas";
    canvas.className = "pet-canvas";
    canvas.setAttribute("aria-label", "小满");
    stageEl.appendChild(canvas);

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      log("pet-canvas-failed");
      return;
    }

    // 桌宠画布 CSS 高 300% 窗口（头像式取景），DPR 上限 2 → s070 仍在 12MB 预算内
    const mem = navigator && navigator.deviceMemory;
    const tier = mem && mem < 4 ? "s050" : "s070";
    try {
      puppet = new LayeredPuppet(canvas, window.PUPPET_MANIFEST, { base: MODEL_BASE, tier: tier });
    } catch (e) {
      log("pet-model-failed " + (e && e.message));
      return;
    }

    puppet.load().then(() => {
      if (!puppet) return;
      fit();
      puppet.setEmotion("neutral");
      puppet.setAutoIdle(motion);
      appliedMotion = motion;        // 挂载即记录已应用值，15s 外观同步不会误重启静帧
      if (motion) startLoop();
      else freezeFrame();
      log("pet-mode=" + puppet.mode);
      if (puppet.mode === "hidden") {
        log("pet-model-failed");
        return;
      }
      log("pet-ready");
      try { if (BRIDGE && BRIDGE.ping) BRIDGE.ping(); } catch (e) { /* 忽略 */ }
      if (!introShown) {
        introShown = true;
        setTimeout(() => bubble("小满来啦，戳戳我；双击聊天，长按更多"), 1500);
      }
    }).catch((err) => {
      log("pet-model-failed");
      try { console.error("分层立绘加载失败:", err && err.message); } catch (e) { /* 忽略 */ }
    });
  }

  // ---------- 手势：单击互动 / 双击聊天 / 长按菜单 ----------
  function clearPress() {
    if (pressTimer) {
      clearTimeout(pressTimer);
      pressTimer = 0;
    }
  }

  function onDown(e) {
    if (e.target && e.target.closest && e.target.closest("button")) return;
    noteActivity();               // 触摸即恢复满帧，静止停机立即解除
    downPoint = { x: e.screenX, y: e.screenY, t: Date.now() };
    moved = false;
    longFired = false;
    showMenu(false);
    clearPress();
    pressTimer = setTimeout(() => {
      longFired = true;
      log("pet-long");
      showMenu(true);
    }, core.LONG_MS);
  }

  function onMove(e) {
    if (!downPoint || moved) return;
    if (Math.abs(e.screenX - downPoint.x) > core.TAP_SLOP_PX ||
        Math.abs(e.screenY - downPoint.y) > core.TAP_SLOP_PX) {
      moved = true;
      clearPress();
      log("pet-drag");
    }
  }

  function onUp(e) {
    if (!downPoint) return;
    clearPress();
    const dx = e.screenX - downPoint.x;
    const dy = e.screenY - downPoint.y;
    const ms = Date.now() - downPoint.t;
    const kind = core.gesture(dx, dy, ms, { slopPx: core.TAP_SLOP_PX, longMs: core.LONG_MS });
    const wasLong = longFired;
    downPoint = null;
    if (wasLong || kind !== "tap") return;
    if (core.isDoubleTap(lastTapAt, Date.now())) {
      lastTapAt = 0;
      log("pet-double");
      openPanel();
      return;
    }
    lastTapAt = Date.now();
    const rect = stageEl.getBoundingClientRect();
    interact(rect.height ? (e.clientY - rect.top) / rect.height : 0.4);
  }

  function onCancel() {
    clearPress();
    downPoint = null;
  }

  /** 真正卸载（pagehide / 桌宠窗被原生销毁）：停循环与全部定时器、销毁实例、移除画布 */
  function destroy() {
    mounted = false;
    stopLoop();
    idleStopped = false;
    appliedMotion = null;
    clearTimeout(breathTimer); breathTimer = 0;
    clearTimeout(repaintTimer); repaintTimer = 0;
    clearTimeout(freezeTimer); freezeTimer = 0;
    clearTimeout(pressTimer); pressTimer = 0;
    clearTimeout(tipTimer); tipTimer = 0;
    clearInterval(reminderTimer); reminderTimer = 0;
    clearInterval(styleTimer); styleTimer = 0;
    clearInterval(watchdogTimer); watchdogTimer = 0;
    window.removeEventListener("appearance:change", syncAppearance);
    try { showMenu(false); } catch (e) { /* 忽略 */ }
    if (puppet) {
      try { puppet.stop(); } catch (e) { /* 忽略 */ }
      puppet = null;
    }
    if (canvas && canvas.parentElement) canvas.parentElement.removeChild(canvas);
    canvas = null;
    envPaused = false;
    log("pet-destroy");
  }

  // 渲染异常只记录不抛出：桌宠挂掉也要保留悬浮层可用
  window.addEventListener("error", (e) => log("pet-js-error " + (e && e.message)));
  // 窗口从不可见恢复（面板收起/系统回收后回来）时按需重启并补帧，否则会停在空白帧
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pauseForEnv("hidden");
    else { resumeForEnv("visible"); repaint("visibility"); }
  });
  window.addEventListener("blur", () => pauseForEnv("blur"));
  window.addEventListener("focus", () => { resumeForEnv("focus"); repaint("focus"); });
  window.addEventListener("pagehide", (e) => { if (!e || !e.persisted) destroy(); });
  window.addEventListener("pageshow", () => repaint("pageshow"));
  document.addEventListener("pointerdown", onDown, true);
  document.addEventListener("pointermove", onMove, true);
  document.addEventListener("pointerup", onUp, true);
  document.addEventListener("pointercancel", onCancel, true);
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  window.addEventListener("resize", fit);

  document.getElementById("petChat").addEventListener("click", openPanel);
  document.getElementById("petClose").addEventListener("click", closePet);

  // ---------- 提醒：节流后气泡提示（主 App 在前台时让位，避免双弹） ----------
  function readNoticeState() {
    try {
      const raw = localStorage.getItem(core.KEYS.notice);
      const o = raw ? JSON.parse(raw) : null;
      return o && typeof o === "object" ? o : {};
    } catch (e) { return {}; }
  }

  function saveNoticeState(state) {
    try { localStorage.setItem(core.KEYS.notice, JSON.stringify(state)); } catch (e) { /* 忽略 */ }
  }

  function presenceTs() {
    try { return Number(localStorage.getItem(core.KEYS.presence) || 0) || 0; } catch (e) { return 0; }
  }

  function tickReminders() {
    if (!window.Reminders || typeof Reminders.due !== "function") return;
    const now = Date.now();
    if (core.suppressByPresence(presenceTs(), now, 90000)) return;
    let due = [];
    try { due = Reminders.due() || []; } catch (e) { return; }
    const state = readNoticeState();
    const d = core.reminderDecision(due, now, state);
    if (!d) return;
    try {
      if (typeof Reminders.markDone === "function") Reminders.markDone([d.id]);
    } catch (e) { /* 标记失败也不阻塞气泡 */ }
    saveNoticeState(core.recordNotice(state, d, now));
    bubble(core.bubbleText(d.text, 44));
    setEmotion("gentle");
    playMotion("Nod");
    log("pet-reminder:" + d.id);
  }

  function startTimers() {
    clearInterval(reminderTimer);
    reminderTimer = setInterval(tickReminders, 30000);
    setTimeout(tickReminders, 2500);
    clearInterval(styleTimer);
    styleTimer = setInterval(syncAppearance, 15000);
    window.addEventListener("appearance:change", syncAppearance);
  }

  /** 自动化/浏览器预览入口（不参与生产逻辑） */
  window.__pet = {
    tap(ratio) { interact(typeof ratio === "number" ? ratio : 0.4); },
    openPanel,
    close: closePet,
    destroy,
    tickReminders,
    repaint,
    state() {
      const idleMs = Date.now() - lastActionAt;
      const mode = idleStopped ? "stopped" : (idleMs >= IDLE_THROTTLE_MS ? "preview" : "full");
      return { mounted, motion, largeFont, grayIcons, ready: !!(puppet && puppet.mode !== "hidden"),
        idle: idleStopped, idleMs, mode, loop: !!(rafId || loopTimer) };
    },
  };

  /** 原生（FloatWindowService）在贴边/恢复可见后调用：强制补帧 */
  window.__petRepaint = function (why) { repaint(why || "native"); };

  startWatchdog();

  syncAppearance();
  mountVendor().then(mount).catch((err) => {
    log("pet-vendor-failed");
    try { console.error("悬浮宠物依赖加载失败:", err && err.message); } catch (e) { /* 忽略 */ }
  });
  startTimers();
})();
