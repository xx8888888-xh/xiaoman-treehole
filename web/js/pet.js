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
  let view = null;         // 当前后备裁切/映射（PetCore.backingRect 的结果）
  let logical = null;      // 逻辑画布（CSS 盒）缓存，与后备裁切无关
  let resizeTimer = 0;
  let resizeObs = null;

  /* ---------- 静止分档（耗电 P0） ----------
   * 实测：后台桌宠可见时主进程 5 分钟吃掉 194.2s CPU（单核 64.7%），
   * 根因是 60fps 全画布重绘一直在跑，而桌面宠物绝大多数时间是"没人看"的。
   * 分三档，全部以「最后一次交互」为基准：
   *   0–6s   全帧（呼吸/眨眼/视线漫游）
   *   6–10s  15fps（保呼吸与眨眼，砍掉 3/4 的帧）
   *   >10s   完全停 rAF 与 draw，只在触摸/可见性变化/动作切换时重绘；
   *          另留一个 20s 的兜底补帧（见 BREATH_WAKE_MS），只防 overlay 表面被系统收回
   *          后整块透明，不追求动画连续性。
   * 任何触摸、表情动作、原生补帧都会把计时器清零并重新开循环。 */
  const IDLE_THROTTLE_MS = 6000;
  const IDLE_STOP_MS = 10000;
  const IDLE_FPS = 15;
  const FRAME_CAP_MS = 1000 / IDLE_FPS;
  /* 停机后的兜底补帧间隔：画面此刻已无任何变化（呼吸/眨眼都停），
   * 每帧只为验证"表面还在"。10s 一轮实测收益有限且仍属无变化重绘，
   * 拉到 20s 后每分钟 3 帧即可挡住 overlay 掉帧，像素成本再减半。 */
  const BREATH_WAKE_MS = 20000;
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

  /** 停机后的兜底节拍：只补一帧（update+render 一次），绝不重开循环。
   *  这一帧的目的只是确认 overlay 表面没有整块透明，不是动画：间隔 20s。 */
  function breathTick() {
    breathTimer = 0;
    if (!mounted || !motion || !puppet || envBlocked() || !idleStopped) return;
    try {
      puppet.update(0.4);      // 只推进一点呼吸相位，避免补帧时姿态跳变过大
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

  /** 页内气泡最长 44 字（原生长气泡是独立 View，不受浮窗宽高限制） */
  function showLocalTip(text) {
    const s = String(text == null ? "" : text);
    tipEl.textContent = s.length > 44 ? s.slice(0, 44) + "…" : s;
    tipEl.classList.remove("hidden");
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => tipEl.classList.add("hidden"), 5200);
  }

  function showMenu(show) {
    // 气泡与菜单同一块底部空间：开菜单就收起气泡，避免两层一起盖住脸/下巴
    if (show) {
      clearTimeout(tipTimer);
      tipEl.classList.add("hidden");
    }
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
    happy: "smile", sad: "neutral", gentle: "smile", surprised: "surprised",
    shy: "shy", neutral: "neutral", smile: "smile", laugh: "laugh", talk: "talk",
    tired: "tired", flat: "flat", quiet: "neutral", silent: "neutral",
  };
  const MOTION_MAP = {
    greeting: "nod", nod: "nod", shake: "shake", happyjump: "nod", talk: "talk",
  };

  /* 只画窗口真正看得见的那一段：画布 CSS 高 300% 窗口、宽屏时还会左右溢出，
   * 剩下约 2/3 高度和两侧都被父级 overflow:hidden 裁掉。
   * R5 已用 clip 把"被丢弃的像素"挡在光栅化之外，但后备缓冲仍是整块 3 倍画布；
   * 这里进一步把后备缩到可见区（PetCore.backingRect），内存按面积下降。
   * 绘制坐标语义不变：ctx.setTransform 只做平移，把原画布坐标映射到新后备。
   * 余量只留给抗锯齿与 floor/ceil 取整误差：2px（设备像素），且后备与 clip 取并集。 */
  const CLIP_MARGIN_PX = 2;                   // 后备像素（设备像素）
  const CANVAS_TOP_CSS = 8;                   // 与 css/pet.css 的 `top: 8px` 一致

  /** 逻辑画布（CSS 盒）：引擎按"300% 窗口高 + 立绘宽高比"铺满，与后备裁切无关 */
  function logicalBox() {
    if (!puppet || !puppet.m || !puppet.m.canvas) return { cssW: 0, cssH: 0, W: 0, H: 0, artW: 0, artH: 0 };
    const artW = puppet.m.canvas.width;
    const artH = puppet.m.canvas.height;
    const cssH = (stageEl.clientHeight * 3) || 720;   // 与 vendor fit 的兜底一致
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const H = Math.max(240, Math.min(1600, Math.round(cssH * dpr)));
    const W = Math.max(120, Math.round(H * artW / artH));
    return { cssW: cssH * W / H, cssH: cssH, W: W, H: H, artW: artW, artH: artH };
  }

  /**
   * 把 canvas 后备缩到可见区；只在尺寸变化时重建缓冲（重建会清空像素，
   * 因此调用方必须在同一任务里立刻重绘，合成器不会看到空帧）。
   */
  function syncCanvas() {
    if (!puppet || !canvas) return null;
    const lb = logicalBox();
    if (!lb.artH || !lb.cssH) return null;
    logical = lb;
    // 引擎坐标系保持"整块 3 倍画布"语义，只有后备缓冲变小
    puppet.W = lb.W;
    puppet.H = lb.H;
    puppet.s = lb.H / lb.artH;                 // 与 vendor fit 完全一致
    const stageW = stageEl.clientWidth;
    const stageH = stageEl.clientHeight;
    const k = lb.H / lb.cssH;                  // 设备像素 / CSS 像素
    const b = core.backingRect({
      fullW: lb.W, fullH: lb.H,
      canvasCssW: lb.cssW, canvasCssH: lb.cssH,
      windowCssW: stageW, windowCssH: stageH,
      canvasTop: CANVAS_TOP_CSS, dpr: k, margin: CLIP_MARGIN_PX,
    });
    if (canvas.width !== b.w || canvas.height !== b.h) {
      canvas.width = b.w;                      // 赋值即重置 context 状态
      canvas.height = b.h;
    }
    // 画布 CSS 盒 = 后备在舞台里的投影：1:1 映射，绝不把裁切图拉伸回原尺寸
    const left = (stageW - lb.cssW) / 2 + b.css.left;
    const top = CANVAS_TOP_CSS + b.css.top;
    const key = left + "|" + top + "|" + b.css.width + "|" + b.css.height;
    if (!view || view.cssKey !== key) {
      canvas.style.setProperty("left", left + "px", "important");
      canvas.style.setProperty("top", top + "px", "important");
      canvas.style.setProperty("width", b.css.width + "px", "important");
      canvas.style.setProperty("height", b.css.height + "px", "important");
      canvas.style.transform = "none";
    }
    view = {
      kx: b.kx, ky: b.ky, tx: b.tx, ty: b.ty, cssKey: key,
      bw: b.w, bh: b.h, dpr: Math.min(2, window.devicePixelRatio || 1),
      logical: lb, stageW: stageW, stageH: stageH,
    };
    return b;
  }

  /** 取景矩形按"当前后备那一版"的几何计算，resize 未完成前不混用新旧尺寸 */
  function visibleRect() {
    const lb = (view && view.logical) || logical || (puppet ? logicalBox() : null);
    const sw = view ? view.stageW : stageEl.clientWidth;
    const sh = view ? view.stageH : stageEl.clientHeight;
    const full = { x: 0, y: 0, w: puppet ? puppet.W : 0, h: puppet ? puppet.H : 0 };
    if (!puppet || !canvas || !lb) return full;
    return core.clipRect({
      modelW: lb.W,
      modelH: lb.H,
      canvasW: lb.cssW,
      canvasH: lb.cssH,
      stageW: sw,
      stageH: sh,
      canvasTop: CANVAS_TOP_CSS,
      margin: CLIP_MARGIN_PX,
    });
  }

  function renderFrame() {
    if (!puppet) return;
    const ctx = puppet.ctx;
    if (!view && !syncCanvas()) return;
    const v = view || { kx: 1, ky: 1, tx: 0, ty: 0 };
    // 每次绘制前重建映射：canvas.width/height 赋值会重置 transform/clip
    ctx.setTransform(v.kx, 0, 0, v.ky, v.tx, v.ty);
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
      syncCanvas();
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
    const key = String(name || "").toLowerCase();
    const mood = EMOTION_MAP[key] || "neutral";
    try { puppet.setEmotion(mood); } catch (e) { /* 忽略 */ }
    setParam("headTilt", key === "sad" ? -0.6 : mood === "tired" ? -0.35 : mood === "flat" ? -0.18 : 0);
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

  /** 自适应：逻辑画布 CSS 高 = 300% 窗口（头部特写），后备再裁到可见区 */
  function fit() {
    if (!puppet) return;
    try {
      syncCanvas();
      renderFrame();             // 缓冲刚重建，立刻补帧避免空白
      log("pet-fit w=" + canvas.width + " h=" + canvas.height);
    } catch (e) {
      log("pet-fit-error " + (e && e.message));
    }
  }

  /** 窗口尺寸/DPI 变化：debounce 后重建后备，并在同一任务里重绘一帧 */
  function rebuild(why) {
    if (!mounted || !puppet || !canvas) return;
    try {
      const b = syncCanvas();
      renderFrame();
      if (b) {
        log("pet-backing x=" + b.x + " y=" + b.y + " w=" + b.w + " h=" + b.h +
          " full=" + b.fullW + "x" + b.fullH);
      }
    } catch (e) {
      log("pet-rebuild-error " + (e && e.message));
    }
    log("pet-rebuild:" + (why || "resize"));
  }

  function scheduleRebuild(why) {
    if (!mounted || !puppet) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      resizeTimer = 0;
      rebuild(why);
    }, 120);
  }

  /** 复用 ResizeObserver（若可用）监听 stage 尺寸；退化到 window.resize */
  function watchResize() {
    if (window.ResizeObserver && !resizeObs) {
      resizeObs = new ResizeObserver(() => scheduleRebuild("stage"));
      resizeObs.observe(stageEl);
    }
  }

  function onWindowResize() { scheduleRebuild("window"); }

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
      if (!mounted || !puppet) return;
      // DPI 变化（跨屏/分辨率切换）不一定会触发 resize：看门狗兜底检测
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (view && Math.abs(view.dpr - dpr) >= 0.01) {
        scheduleRebuild("dpr");
        return;
      }
      if (!motion || envBlocked()) return;
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
    clearTimeout(resizeTimer); resizeTimer = 0;
    if (resizeObs) { try { resizeObs.disconnect(); } catch (e) { /* 忽略 */ } }
    resizeObs = null;
    view = null;
    logical = null;
    window.removeEventListener("appearance:change", syncAppearance);
    window.removeEventListener("resize", onWindowResize);
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
  window.addEventListener("resize", onWindowResize);

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
    /** 预览/截图用：菜单本身就由长按触发，这里只暴露同一状态切换 */
    menu(show) { showMenu(!!show); },
    /** 预览/截图用：注入一条长文本，验证小窗 4 行封顶与省略 */
    tip(text) { showLocalTip(text); },
    state() {
      const idleMs = Date.now() - lastActionAt;
      const mode = idleStopped ? "stopped" : (idleMs >= IDLE_THROTTLE_MS ? "preview" : "full");
      return { mounted, motion, largeFont, grayIcons, ready: !!(puppet && puppet.mode !== "hidden"),
        idle: idleStopped, idleMs, mode, loop: !!(rafId || loopTimer) };
    },
  };

  /** 原生（FloatWindowService）在贴边/恢复可见后调用：强制补帧 */
  window.__petRepaint = function (why) { repaint(why || "native"); };

  watchResize();
  startWatchdog();

  syncAppearance();
  mountVendor().then(mount).catch((err) => {
    log("pet-vendor-failed");
    try { console.error("悬浮宠物依赖加载失败:", err && err.message); } catch (e) { /* 忽略 */ }
  });
  startTimers();
})();
