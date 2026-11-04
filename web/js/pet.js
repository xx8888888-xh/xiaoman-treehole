/* ============================================================
 * pet.js · Android 桌面悬浮宠物（Live2D + 点击反馈 + 提醒气泡）
 * ------------------------------------------------------------
 * 运行环境：FloatWindowService 里的透明 WebView（窗口仅 140x180dp）。
 * 分工：
 *  - 窗口拖动/贴边：原生 OnTouchListener 负责（本页只观察指针，不再移动窗口）
 *  - 单击互动、双击聊天、长按菜单：本页负责
 *  - 文字气泡：原生 TextView（AndroidPet.bubble），无桥时退化为页内气泡
 *  - 提醒：每 30s 读 Reminders.due()，经 PetCore 节流后播报；主 App 在前台时让位
 *  - 静音：本页不加载 tts.js、不创建 Audio，静音约束始终满足
 * ============================================================ */
(function () {
  "use strict";

  const core = window.PetCore;
  const stageEl = document.getElementById("petStage");
  const tipEl = document.getElementById("petTip");
  const menuEl = document.getElementById("petMenu");
  const BRIDGE = window.AndroidPet || null;

  const MODEL_URL = "assets/models/hiyori/Hiyori.model3.json";
  const VENDOR = [
    "js/vendor/pixi.min.js",
    "js/vendor/live2dcubismcore.min.js",
    "js/vendor/pixi-live2d-cubism4.min.js",
  ];

  let app = null;
  let model = null;
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
  let phase = 0;
  let nextBlink = 2.5;
  let blinkAt = -1;
  let nextGaze = 2.2;
  let gazeX = 0;
  let gazeY = 0;
  let gazeTX = 0;
  let gazeTY = 0;
  let introShown = false;
  let reminderTimer = 0;
  let styleTimer = 0;
  let lastPaintAt = 0;
  let repaintTimer = 0;

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
    if (!model) return;
    try { model.internalModel.coreModel.setParameterValueById(name, value); } catch (e) { /* 模型无此参数 */ }
  }

  /**
   * 原生 overlay 里 WebGL 画布不会被合成（Android WebView 把 WebGL 渲染到独立 surface，
   * 悬浮窗拿不到这块 surface，实测整块透明、只有 DOM 能显示）。这里把 WebGL 帧拷进一个
   * 2D canvas（普通 DOM 层，和迷你窗 HTML 同样能合成），桌宠因此能真正显示在桌面上。
   * 桌面浏览器预览不需要这层。
   */
  const MIRROR = !!BRIDGE;
  let mirror = null;
  let mirrorCtx = null;

  function syncMirror() {
    if (!mirror || !app) return;
    const w = app.renderer.width;
    const h = app.renderer.height;
    if (mirror.width !== w || mirror.height !== h) {
      mirror.width = w;
      mirror.height = h;
    }
  }

  function mirrorFrame() {
    if (!mirrorCtx || !app) return;
    try {
      mirrorCtx.clearRect(0, 0, mirror.width, mirror.height);
      mirrorCtx.drawImage(app.view, 0, 0, mirror.width, mirror.height);
    } catch (e) {
      log("pet-mirror-error " + (e && e.message));
    }
  }

  function setupMirror() {
    if (!MIRROR || mirror) return;
    mirror = document.createElement("canvas");
    mirror.id = "petMirror";
    mirror.style.position = "absolute";
    mirror.style.left = "0";
    mirror.style.top = "0";
    mirror.style.width = "100%";
    mirror.style.height = "100%";
    mirror.style.pointerEvents = "none";
    stageEl.appendChild(mirror);
    // WebGL 画布退居"离屏源"：仍在渲染、可被 drawImage 读取，只是不直接参与合成
    app.view.style.opacity = "0";
    mirrorCtx = mirror.getContext("2d");
    syncMirror();
    log("pet-mirror size=" + mirror.width + "x" + mirror.height);
  }

  function safeRender(tag) {
    if (!app) return;
    try {
      app.render();
      lastPaintAt = Date.now();
      mirrorFrame();
    } catch (e) {
      // 渲染异常只记录：单帧失败不该把整个桌宠弄挂
      log("pet-render-error:" + tag + " " + (e && e.message));
    }
  }

  function renderOnce() {
    if (!motion) safeRender("once");
  }

  function setEmotion(name) {
    if (!model) return;
    try {
      const names = model.expressionManager && model.expressionManager.expressionNames;
      const idx = (names || []).indexOf(name);
      if (idx >= 0) model.expression(idx);
      else {
        const fallback = { happy: 0, sad: 1, gentle: 2, surprised: 3, shy: 4, neutral: 5 }[name];
        if (fallback != null) model.expression(fallback);
      }
    } catch (e) { /* 模型无此表情 */ }
    renderOnce();
  }

  function playMotion(group) {
    if (!motion || !model) return;
    try { model.motion(group, undefined, 2); } catch (e) { /* 模型无此动作 */ }
  }

  function interact(yRatio) {
    const r = core.interactionFor(yRatio, Math.random());
    setEmotion(r.emotion);
    playMotion(r.motion);
    bubble(r.line);
    log("pet-tap:" + r.area);
  }

  function setMotionEnabled(on) {
    if (!app || !app.ticker) return;
    if (on) {
      app.ticker.maxFPS = 30;
      app.ticker.start();
    } else {
      // 零动画预设仍保留 2FPS 重绘：Live2D 贴图异步上传，ticker 全停会永远停在空白首帧
      app.ticker.maxFPS = 2;
      app.ticker.start();
      safeRender("motion-off");
    }
  }

  /**
   * 自适应缩放：model.width/height 是“当前 scale 下的像素尺寸”，
   * 先把 scale 归一到 1 再按窗口做等比裁剪，避免用模型内部单位（约 2）当像素算出巨大 scale。
   */
  function fit() {
    if (!model || !app) return;
    const cw = stageEl.clientWidth || window.innerWidth;
    const ch = stageEl.clientHeight || window.innerHeight;
    if (!cw || !ch) {
      log("pet-fit-skip w=" + cw + " h=" + ch);
      return;
    }
    try {
      // 渲染目标必须跟窗口一致：PIXI 默认 800x600，若靠 CSS 拉伸会让坐标/缩放全错位
      const r = app.renderer;
      const curW = Math.round(r.width / r.resolution);
      const curH = Math.round(r.height / r.resolution);
      if (curW !== cw || curH !== ch) r.resize(cw, ch);
      const w = r.width / r.resolution;
      const h = r.height / r.resolution;
      syncMirror();
      model.anchor.set(0.5, 0.5);
      model.scale.set(1);
      const bw = model.width || model.internalModel.originalWidth || 1;
      const bh = model.height || model.internalModel.originalHeight || 1;
      const s = Math.min(w / bw, h / bh) * 0.95;
      model.scale.set(s);
      model.x = w / 2;
      model.y = h * 0.55;
      log("pet-fit w=" + w + " h=" + h + " bw=" + bw.toFixed(2) + " bh=" + bh.toFixed(2)
        + " s=" + s.toFixed(3) + " mx=" + model.x + " my=" + model.y);
    } catch (e) {
      log("pet-fit-error " + (e && e.message));
    }
  }

  // 零动画时 ticker 停摆，纹理是异步加载的：首帧可能还没有贴图，需要补渲染几帧才不会“空白”
  function renderStaticFrames() {
    if (!app) return;
    let n = 0;
    const once = () => {
      if (motion) return;
      safeRender("static" + n);
      n += 1;
      if (n < 10) setTimeout(once, 900);
    };
    once();
  }

  function animate() {
    if (!app) return;
    app.ticker.add(() => {
      lastPaintAt = Date.now();
      if (!model || !motion) return;
      const dt = Math.min(0.05, (app.ticker.deltaMS || 16) / 1000);
      phase += dt;

      // 待机呼吸（ParamBreath 0~1，周期约 4.8s）
      const breath = 0.5 + 0.5 * Math.sin(phase * 1.3);
      setParam("ParamBreath", breath);

      // 眨眼：每 2.6~5.4s 一次，单次 180ms
      nextBlink -= dt;
      if (nextBlink <= 0 && blinkAt < 0) {
        blinkAt = 0;
        nextBlink = 2.6 + Math.random() * 2.8;
      }
      if (blinkAt >= 0) {
        blinkAt += dt;
        const p = blinkAt / 0.18;
        const open = p < 0.5 ? 1 - p * 2 : (p - 0.5) * 2;
        setParam("ParamEyeLOpen", Math.max(0, Math.min(1, open)));
        setParam("ParamEyeROpen", Math.max(0, Math.min(1, open)));
        if (p >= 1) {
          blinkAt = -1;
          setParam("ParamEyeLOpen", 1);
          setParam("ParamEyeROpen", 1);
        }
      }

      // 视线漫游：每 2~4s 换一个轻微目标，平滑跟随
      nextGaze -= dt;
      if (nextGaze <= 0) {
        nextGaze = 2.2 + Math.random() * 2.2;
        gazeTX = (Math.random() - 0.5) * 0.8;
        gazeTY = (Math.random() - 0.5) * 0.45;
      }
      gazeX += (gazeTX - gazeX) * 0.08;
      gazeY += (gazeTY - gazeY) * 0.08;
      setParam("ParamEyeBallX", gazeX);
      setParam("ParamEyeBallY", gazeY);
      setParam("ParamAngleX", gazeX * 10);
      setParam("ParamAngleY", gazeY * 8);
    });
  }

  /**
   * 强制重绘：overlay 窗口被移动/从 GONE 切回可见后，部分设备（软件 GL 模拟器实测）
   * 的 Chromium 会停止给这个窗口出帧，画面变成整块透明。原生侧在贴边/恢复可见时
   * 通过 evaluateJavascript 调用本函数，把尺寸重置 + 立刻补一帧。
   */
  function repaint(why) {
    if (!mounted) return;
    clearTimeout(repaintTimer);
    log("pet-repaint:" + (why || "manual"));
    repaintTimer = setTimeout(() => {
      fit();
      safeRender("repaint:" + (why || "manual"));
      requestAnimationFrame(() => safeRender("repaint-raf:" + (why || "manual")));
    }, 120);
  }

  /** 看门狗：rAF 被系统暂停时（窗口不可见/被移动）靠定时器兜底重绘 */
  function startWatchdog() {
    setInterval(() => {
      if (!model) return;
      if (Date.now() - lastPaintAt > 2500) {
        log("pet-watchdog");
        safeRender("watchdog");
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
    const vw = stageEl.clientWidth || window.innerWidth;
    const vh = stageEl.clientHeight || window.innerHeight;
    const stage = new PIXI.Application({
      width: vw,
      height: vh,
      backgroundAlpha: 0,
      antialias: true,
      resolution: Math.min(2, Math.max(1, window.devicePixelRatio || 1)),
      autoDensity: true,
      // 2D 镜像层要从 WebGL 画布 drawImage 取帧，必须保留绘制缓冲
      preserveDrawingBuffer: true,
    });
    app = stage;
    app.ticker.maxFPS = 30;
    stageEl.appendChild(app.view);
    app.view.style.background = "transparent";
    setupMirror();
    if (MIRROR) app.ticker.add(() => mirrorFrame());
    // WebGL 上下文丢失后 PIXI 6 不会自愈，整块画面会永久空白：记探针并限频重载一次
    app.view.addEventListener("webglcontextlost", (ev) => {
      try { ev.preventDefault(); } catch (e) { /* 忽略 */ }
      log("pet-gl-lost");
      let last = 0;
      try { last = Number(localStorage.getItem("xiaoman_pet_gl_reload_v1") || 0) || 0; } catch (e) { /* 忽略 */ }
      if (Date.now() - last > 5000) {
        try { localStorage.setItem("xiaoman_pet_gl_reload_v1", String(Date.now())); } catch (e) { /* 忽略 */ }
        setTimeout(() => { try { location.reload(); } catch (e) { /* 忽略 */ } }, 1200);
      }
    }, false);
    // 渲染后端探针：PIXI 拿不到 WebGL 会静默退回 CanvasRenderer，而 Live2D 只能在 WebGL 上出图
    try {
      const gl = stage.renderer.gl;
      log("pet-gl type=" + stage.renderer.type + (gl ? " version=" + gl.getParameter(gl.VERSION) : " none"));
    } catch (e) {
      log("pet-gl probe-failed " + (e && e.message));
    }

    PIXI.live2d.Live2DModel.from(MODEL_URL, { autoInteract: false })
      .catch(() => PIXI.live2d.Live2DModel.from(MODEL_URL))
      .then((m) => {
        model = m;
        app.stage.addChild(m);
        fit();
        animate();
        setMotionEnabled(motion);
        renderStaticFrames();
        log("pet-ready");
        try { if (BRIDGE && BRIDGE.ping) BRIDGE.ping(); } catch (e) { /* 忽略 */ }
        if (!introShown) {
          introShown = true;
          setTimeout(() => bubble("小满来啦，戳戳我；双击聊天，长按更多"), 1500);
        }
      })
      .catch((err) => {
        log("pet-model-failed");
        try { console.error("Live2D 加载失败:", err && err.message); } catch (e) { /* 忽略 */ }
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

  // 渲染异常只记录不抛出：桌宠挂掉也要保留悬浮层可用
  window.addEventListener("error", (e) => log("pet-js-error " + (e && e.message)));
  // 窗口从不可见恢复（面板收起/系统回收后回来）时补帧，否则会停在空白帧
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) repaint("visibility");
  });
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
    tickReminders,
    repaint,
    state() {
      return { mounted, motion, largeFont, grayIcons, ready: !!model };
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
