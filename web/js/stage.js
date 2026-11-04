/* ============================================================
 * stage.js · 角色舞台（Layered PNG 分层立绘 · 开源 original 模型）
 * ------------------------------------------------------------
 * 引擎：web/js/vendor/puppet.js（window.LayeredPuppet，无依赖 Canvas 2D）
 * 资产：web/assets/live2d/original（manifest.json/manifest.js + art/）
 * 接入契约：docs/live2d_integration.md（层名/锚点/口型 7 档/眨眼 216ms/
 *           视线 ±4/±3px/呼吸摆动 0.55·bodySway+0.45·gazeX, τ=0.42s）
 *
 * 对外能力（页面其余模块只依赖这些）：
 *  - mount/unmount                 挂载与销毁（挂载失败按三级降级，不抛错）
 *  - setEmotion(name)              表情（happy/sad/gentle/surprised/shy/neutral）
 *  - playMotion(group)             动作（Greeting/Nod/Shake/HappyJump/talk/idle）
 *  - lipFrame(level)               音频包络 → 连续口型（引擎内部 7 档交叉淡入）
 *  - focusHead/restoreView         悬浮挂件头部特写与还原
 *  - setMotionEnabled(on)          外观零动画：停 rAF、定格同一帧
 *
 * 降级阶梯（mode）：layers → static（art/full.png）→ silhouette
 * （art/silhouette.png）→ hidden（visibility:hidden，保留布局）
 * ============================================================ */

const Stage = (() => {
  let puppet = null;          // LayeredPuppet 实例
  let canvas = null;          // 承载画布（#stage 内唯一 canvas）
  let mode = "idle";          // idle | layers | static | silhouette | hidden
  let lipOpen = 0;            // 当前口型目标值
  let lipHold = 0;            // 剩余保持帧（无分析器时的兜底）
  // 极简模式/关动画：停 rAF，画面定格；默认值取外观（Appearance.motion=true 表示允许动效）
  let motionEnabled = typeof window !== "undefined" && window.Appearance
    ? Appearance.get().motion
    : true;
  const MOODS = { happy: "开心", sad: "有点难过", gentle: "温柔模式", surprised: "诶？", shy: "脸红了", neutral: "在听你说" };

  /* ---------- 资产与引擎（唯一模型源） ----------
   * manifest.js 提供 window.PUPPET_MANIFEST（免 fetch，file:// 直开也能用）；
   * puppet.js 只定义 LayeredPuppet，本身不碰资产。 */
  const MODEL_BASE = "assets/live2d/original/";
  const VENDOR = [
    "assets/live2d/original/manifest.js",
    "js/vendor/puppet.js",
  ];
  const ART_W = 1235;
  const ART_H = 2841;
  const GPU_BUDGET = 12 * 1024 * 1024;   // 首屏：解码纹理 + 画布后备缓冲 ≤12MB
  // manifest.tiers 的 decoded（RGBA 解码字节），选档是纯函数，故在此固化同源常量
  const TIERS = {
    s050: { scale: 0.5, decoded: 5001068 },
    s070: { scale: 0.7, decoded: 9791028 },
  };

  let vendorPromise = null;
  let host = null;            // 舞台容器：加载占位/失败重试都要用
  let wanderTimer = null;     // 视线漫游计时器：零动画时必须真正停掉
  let freezeTimer = null;     // 零动画的"渲染几帧后定格"计时器
  let rafId = null;           // 动画循环句柄（零动画必须为 null）
  let lastTs = 0;
  let resizeObs = null;
  let fallbackImg = null;     // 静态/剪影兜底图（容器尺寸变化时重绘，不再请求网络）
  let envBound = false;       // 环境监听（hidden/blur/pagehide）只绑定一次
  let pausedByEnv = false;    // 页面不可见/窗口失焦期间暂停，恢复时清

  /** 纹理档位：按 DPR + 画布可视尺寸 + 12MB 预算选 s050/s070（首屏只下载当前档）。
   *  与引擎内 pickTier 同一口径（预算含画布后备缓冲），前端先算出来便于探针/E2E。 */
  function pickTier(o) {
    const opt = o || {};
    const force = opt.force;
    if (force === "s050" || force === "s070") return force;
    // 兼容旧探针/旧调用：1024(=0.5×) / 2048(=0.7×)
    if (force === "1024") return "s050";
    if (force === "2048") return "s070";
    if (opt.native) return "s050";
    const mem = Number(opt.memory) || 8;
    if (mem < 4) return "s050";
    const dpr = Math.min(2, Number(opt.dpr) || 1);
    const cssH = Number(opt.height) || (Number(opt.width) ? Number(opt.width) * ART_H / ART_W : 0);
    const ph = Math.max(240, Math.min(1600, Math.round((cssH || 240) * dpr)));
    const pw = Math.round(ph * ART_W / ART_H);
    const fb = pw * ph * 4;                       // 画布后备缓冲
    if (TIERS.s070.decoded + fb <= GPU_BUDGET) return "s070";
    return "s050";
  }

  /** 模型清单地址（档位由引擎按 manifest.tiers 换 art/tiers/<key>/layers 前缀） */
  function modelUrl(tier) {
    // 档位只影响纹理前缀，清单本身与档位无关；保留参数便于探针打印与单测断言
    return MODEL_BASE + "manifest.json" + (tier ? "?tier=" + encodeURIComponent(tier) : "");
  }

  function currentTier(container) {
    const native = !!(window.AndroidTTS && window.AndroidTTS.available);
    return pickTier({
      width: container ? container.clientWidth : 0,
      height: container ? container.clientHeight : 0,
      dpr: window.devicePixelRatio || 1,
      memory: navigator && navigator.deviceMemory,
      native,
      force: window.__texTier
    });
  }

  let activeTier = null;   // 本次挂载实际选中的档位（[probe] 与 E2E 断言用）

  /** 首屏显存（实测口径）：选中档解码字节 + 画布后备缓冲 + 视线裁剪离屏画布 */
  function vramFor(tier, canvasW, canvasH, scale) {
    const t = TIERS[tier] || TIERS.s050;
    const eyeW = Math.round(106 * (scale || 1));
    const eyeH = Math.round(115 * (scale || 1));
    return {
      tier: tier,
      texturesBytes: t.decoded,
      canvasBytes: canvasW * canvasH * 4,
      eyeBytes: eyeW * eyeH * 4,
      totalBytes: t.decoded + canvasW * canvasH * 4 + eyeW * eyeH * 4,
    };
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("加载失败: " + src));
      document.head.appendChild(s);
    });
  }

  function ensureVendor() {
    if (!vendorPromise) {
      vendorPromise = (async () => {
        for (const src of VENDOR) await loadScript(src);
        if (!window.PUPPET_MANIFEST) throw new Error("manifest 未提供");
        if (typeof window.LayeredPuppet !== "function") throw new Error("LayeredPuppet 未加载");
      })();
    }
    return vendorPromise;
  }

  const FALLBACK_TEXT = "小满在打盹…（画面没能加载出来，聊天功能不受影响）";

  /** 清空占位/失败层：重试与成功加载时都先走这里，避免叠加两套提示 */
  function clearStageNotes(container) {
    container.classList.remove("is-fallback");
    container.querySelectorAll(".stage-note, .stage-fallback").forEach(el => el.remove());
  }

  function showNote(container) {
    clearStageNotes(container);
    const note = document.createElement("div");
    note.className = "stage-note";
    note.setAttribute("role", "status");
    note.setAttribute("aria-live", "polite");
    note.textContent = "小满在打盹…";
    container.appendChild(note);
  }

  function showFallback(container, detail) {
    clearStageNotes(container);
    const tip = document.createElement("div");
    tip.className = "stage-fallback";
    tip.setAttribute("role", "status");
    const msg = document.createElement("div");
    msg.className = "stage-fallback-text";
    msg.textContent = FALLBACK_TEXT;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "stage-retry";
    retry.textContent = "重试";
    retry.setAttribute("aria-label", "重新加载小满画面");
    retry.onclick = () => {
      // 脚本/模型任一环节失败都可能留下半初始化状态；整页重载最可靠。
      // 对话与外观都在本地存储里，重载不会丢历史。
      window.location.reload();
    };
    tip.appendChild(msg);
    tip.appendChild(retry);
    container.appendChild(tip);
    container.classList.add("is-fallback");
    console.warn("Stage 降级:", detail);
  }

  /** 降级落点：把 mode 写到探针，按级处理布局与提示（hidden 用 visibility 保占位） */
  function setMode(next, detail) {
    mode = next;
    if (typeof window !== "undefined") window.__stageMode = next;
    if (next === "hidden" && canvas) canvas.style.visibility = "hidden";
    if (next === "hidden" && host) showFallback(host, detail);
    else if (host) clearStageNotes(host);
    console.log("[probe] stage-mode=" + next);
  }

  /* ---------- 视线：参数目标（引擎按 τ=0.42s 做滞后跟随） ---------- */
  function gazeTo(gx, gy) {
    if (!puppet) return;
    puppet.setParam("gazeX", gx);
    puppet.setParam("gazeY", gy);
    puppet.setParam("headTurn", gx * 0.6);
    puppet.setParam("headTilt", gy * 0.35);
  }

  function startWander() {
    stopWander();
    if (!puppet || !motionEnabled || envBlocked()) return;
    wanderTimer = setTimeout(() => {
      wanderTimer = null;
      if (!puppet || !motionEnabled) return;
      gazeTo((Math.random() - 0.5) * 0.9, (Math.random() - 0.5) * 0.5);
      startWander();
    }, 3000 + Math.random() * 3000);
  }

  function stopWander() {
    if (wanderTimer) clearTimeout(wanderTimer);
    wanderTimer = null;
  }

  /* ---------- 环境暂停（耗电 P0）----------
   * 零动画、document.hidden、窗口失焦三种情况下都不允许 rAF 常驻：
   * 暂停时同时停自家循环与（宿主流可能残留的）PixiJS 共享 ticker，
   * 恢复时只在「允许动画且分层渲染」时按需重启，其余情况只补一帧。 */
  function envBlocked() {
    return pausedByEnv || (typeof document !== "undefined" && document.hidden === true);
  }

  function sharedTicker() {
    const P = typeof window !== "undefined" ? window.PIXI : null;
    return P && P.Ticker && P.Ticker.shared ? P.Ticker.shared : null;
  }

  function stopSharedTicker() {
    const t = sharedTicker();
    if (!t) return;
    try { t.autoStart = false; t.stop(); } catch (e) { /* 忽略 */ }
  }

  function startSharedTicker() {
    const t = sharedTicker();
    if (!t) return;
    try { t.autoStart = true; t.start(); } catch (e) { /* 忽略 */ }
  }

  function pauseForEnv(why) {
    if (pausedByEnv) return;
    pausedByEnv = true;
    stopLoop();
    stopWander();
    stopSharedTicker();
    if (freezeTimer) clearTimeout(freezeTimer);
    freezeTimer = null;
    if (puppet && mode === "layers") renderOnce();   // 定格在暂停瞬间的最后一帧
    console.log("[probe] stage-pause=" + why);
  }

  function resumeForEnv(why) {
    pausedByEnv = false;
    if (typeof document !== "undefined" && document.hidden) return;
    if (!puppet || mode !== "layers") { renderOnce(); return; }
    if (motionEnabled) {
      puppet.setAutoIdle(true);
      try { puppet.fit(); } catch (e) { /* 布局尺寸可能刚恢复，fit 失败不阻断 */ }
      startSharedTicker();
      startWander();
      startLoop();
    } else {
      freezeFrame();
    }
    console.log("[probe] stage-resume=" + why);
  }

  function onVisibility() {
    if (document.hidden) pauseForEnv("hidden");
    else resumeForEnv("visible");
  }

  function onBlur() { pauseForEnv("blur"); }
  function onFocus() { resumeForEnv("focus"); }
  function onPageHide(e) { if (!e || !e.persisted) destroy(); }

  function bindEnv() {
    if (envBound || typeof document === "undefined") return;
    envBound = true;
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);
    window.addEventListener("pagehide", onPageHide);
  }

  function unbindEnv() {
    if (!envBound || typeof document === "undefined") return;
    envBound = false;
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("blur", onBlur);
    window.removeEventListener("focus", onFocus);
    window.removeEventListener("pagehide", onPageHide);
  }

  /* ---------- 渲染循环（唯一 rAF；零动画/不可见时不得存在） ---------- */
  function stopLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    lastTs = 0;
  }

  function startLoop() {
    if (rafId || !puppet || mode !== "layers" || !motionEnabled || envBlocked()) return;
    lastTs = 0;
    const tick = (ts) => {
      rafId = null;
      if (!puppet || mode !== "layers" || !motionEnabled) return;
      const dt = lastTs ? Math.min(0.05, (ts - lastTs) / 1000) : 1 / 60;
      lastTs = ts;
      // 口型：包络驱动，短暂保持后回 0（不随机张嘴）；引擎内部 7 档交叉淡入
      const lip = lipHold > 0 ? lipOpen : 0;
      puppet.setParam("lipSync", lip);
      if (lipHold > 0) lipHold -= 1;
      puppet.update(dt);
      puppet.render();
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
  }

  function renderOnce() {
    if (!puppet) return;
    try { puppet.fit(); puppet.render(); } catch (e) { /* 单帧失败不阻断 */ }
  }

  /** 零动画：停循环、冻结到同一帧；只保留一次延时补帧（纹理解码后）随后清零 */
  function freezeFrame() {
    stopLoop();
    stopWander();
    stopSharedTicker();
    if (!puppet) return;
    puppet.setAutoIdle(false);
    renderOnce();
    if (freezeTimer) clearTimeout(freezeTimer);
    freezeTimer = setTimeout(() => {
      freezeTimer = null;
      if (motionEnabled) return;
      renderOnce();
    }, 300);
  }

  async function mount(container) {
    host = container;
    bindEnv();
    showNote(container);
    try {
      await ensureVendor();
    } catch (e) {
      await enterFallback(e.message);
      return;
    }
    // 画布必须在容器内有确定高度（#stage 的 CSS 已给）
    canvas = document.createElement("canvas");
    canvas.id = "puppet";
    canvas.className = "stage-canvas";
    canvas.setAttribute("aria-label", "角色");
    // 占位层保持在最上层：插到 note 之前，避免被画布盖住
    container.insertBefore(canvas, container.firstChild);

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      // 拿不到 2D 上下文：第 3 级，隐藏画布但保留布局
      setMode("hidden", "canvas.getContext('2d') 为 null");
      return;
    }

    activeTier = currentTier(container);
    window.__stageTier = activeTier;   // 探针：E2E 读取实际纹理档位
    console.log("[probe] tex-tier=" + activeTier);

    try {
      puppet = new LayeredPuppet(canvas, window.PUPPET_MANIFEST, {
        base: MODEL_BASE,
        tier: activeTier,
      });
      await puppet.load();       // 纹理失败时引擎自行进入 static/silhouette/hidden
    } catch (e) {
      await enterFallback((e && e.message) || "引擎初始化失败");
      return;
    }

    if (puppet.mode !== "layers") {
      // 引擎内部已按契约降级：这里只负责停帧、探针与提示
      setMode(puppet.mode, "纹理加载失败，已降级");
      renderOnce();
      try { puppet.stop(); } catch (e) { /* 未启动 */ }
      bindResize();
      return;
    }

    setMode("layers");
    puppet.setEmotion("neutral");
    puppet.setAutoIdle(motionEnabled);
    bindResize();
    bindPoke();
    settle();
  }

  /** 脚本/manifest 侧失败：直接按 full → silhouette → hidden 走降级 */
  async function enterFallback(detail) {
    stopLoop();
    stopWander();
    if (!canvas) {
      const c = document.createElement("canvas");
      c.id = "puppet";
      c.className = "stage-canvas";
      host.insertBefore(c, host.firstChild);
      canvas = c;
      activeTier = currentTier(host);
      window.__stageTier = activeTier;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) { setMode("hidden", detail); return; }
    sizeCanvas();
    const full = await drawImageToCanvas("art/full.png");
    if (full) { setMode("static", detail); bindResize(); return; }
    const sil = await drawImageToCanvas("art/silhouette.png");
    if (sil) { setMode("silhouette", detail); bindResize(); return; }
    setMode("hidden", detail);
  }

  /** 画布内部尺寸按可视高度与 DPR（上限 1600），与引擎 fit 同一口径 */
  function sizeCanvas() {
    const scale = Math.min(2, window.devicePixelRatio || 1);
    const cssH = (canvas && canvas.clientHeight) || (host && host.clientHeight) || 320;
    const h = Math.max(240, Math.min(1600, Math.round(cssH * scale)));
    const w = Math.max(120, Math.round(h * ART_W / ART_H));
    canvas.width = w;
    canvas.height = h;
    return { w: w, h: h, s: h / ART_H };
  }

  function drawImageToCanvas(relPath) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        fallbackImg = img;
        resolve(paintFallback());
      };
      img.onerror = () => resolve(false);
      img.src = MODEL_BASE + relPath;
    });
  }

  /** 把兜底图等比铺满当前画布（降级后不参与参数动画，仍可截图） */
  function paintFallback() {
    if (!canvas || !fallbackImg) return false;
    try {
      const ctx = canvas.getContext("2d");
      const { w, h } = sizeCanvas();
      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(fallbackImg, 0, 0, w, h);
      return true;
    } catch (e) { return false; }
  }

  function bindResize() {
    if (!host || resizeObs) return;
    const onResize = () => {
      if (!puppet) {
        if (mode === "static" || mode === "silhouette") paintFallback();
        return;
      }
      puppet.fit();
      if (mode === "layers") updateVramProbe();
      if (!motionEnabled || mode !== "layers") renderOnce();
    };
    if (window.ResizeObserver) {
      resizeObs = new ResizeObserver(onResize);
      resizeObs.observe(host);
    } else {
      window.addEventListener("resize", onResize);
      resizeObs = { disconnect: () => window.removeEventListener("resize", onResize) };
    }
  }

  function bindPoke() {
    if (!canvas) return;
    canvas.addEventListener("click", (e) => {
      // 挂件模式下点挂件 = 展开（widget.js 在 capture 阶段已拦截，不会到这里）
      if (host && host.classList.contains("stage-focus")) return;
      const r = host.getBoundingClientRect();
      const ratio = r.height ? (e.clientY - r.top) / r.height : 0.4;
      const head = ratio < 0.55;
      setEmotion(head ? "shy" : "surprised");
      playMotion(head ? "Shake" : "HappyJump");
      window.App && App.onPoke(head ? "head" : "body");
    });
  }

  /** 加载完成/纹理就绪：有动画起循环，零动画定格 */
  function settle() {
    if (!puppet) return;
    if (motionEnabled) {
      if (freezeTimer) clearTimeout(freezeTimer);
      freezeTimer = null;
      puppet.setAutoIdle(true);
      puppet.fit();
      updateVramProbe();
      startWander();
      startLoop();
    } else {
      freezeFrame();
      updateVramProbe();
    }
  }

  function updateVramProbe() {
    if (!puppet || !canvas) return;
    const v = vramFor(activeTier, canvas.width, canvas.height, puppet.s);
    v.canvasW = canvas.width;
    v.canvasH = canvas.height;
    v.scale = puppet.s;
    v.mode = mode;
    window.__stageVram = v;
    console.log("[probe] vram tier=" + v.tier + " total=" + (v.totalBytes / 1048576).toFixed(2)
      + "MB canvas=" + canvas.width + "x" + canvas.height);
  }

  /* ---------- 情绪/动作映射（页面用的是 Hiyori 时期的英文名） ---------- */
  const EMOTION_MAP = {
    happy: "smile",
    sad: "neutral",
    gentle: "smile",
    surprised: "smile",
    shy: "shy",
    neutral: "neutral",
    smile: "smile", laugh: "laugh", talk: "talk",
  };
  const MOTION_MAP = {
    greeting: "nod", nod: "nod", shake: "shake", happyjump: "nod",
    talk: "talk", idle: "idle",
  };

  function emotionFor(name) {
    return EMOTION_MAP[String(name || "").toLowerCase()] || "neutral";
  }

  function motionFor(group) {
    return MOTION_MAP[String(group || "").toLowerCase()] || null;
  }

  function setEmotion(name) {
    if (!puppet) return;
    const mood = emotionFor(name);
    puppet.setEmotion(mood);
    // sad 没有独立表情层：用轻微低头表达（引擎的 headTilt 参数）
    puppet.setParam("headTilt", mood === "neutral" && name === "sad" ? -0.6 : 0);
    // 零动画模式下 rAF 已停：状态切换后手动渲染一帧，否则画面停在旧表情
    if (!motionEnabled) renderOnce();
    const moodEl = document.getElementById("moodText");
    if (moodEl) {
      moodEl.textContent = MOODS[name] || "在听你说";
      const chip = moodEl.parentElement;
      chip.classList.add("pop");
      setTimeout(() => chip.classList.remove("pop"), 350);
    }
  }

  function playMotion(group) {
    if (!puppet || !motionEnabled) return;   // 零动画：动作不启动，帧不变
    const id = motionFor(group);
    if (!id) return;
    puppet.playMotion(id);
    startLoop();
  }

  /** 卸载舞台：停循环/漫游/计时器、断开尺寸监听、销毁实例、移除画布 */
  function unmount() {
    stopWander();
    stopLoop();
    stopSharedTicker();
    if (freezeTimer) clearTimeout(freezeTimer);
    freezeTimer = null;
    if (resizeObs) {
      try { resizeObs.disconnect(); } catch (e) { /* 忽略 */ }
      resizeObs = null;
    }
    if (puppet) {
      try { puppet.stop(); } catch (e) { /* 忽略 */ }   // 引擎自带的 rAF（若曾启动）
      puppet = null;
    }
    if (canvas && canvas.parentElement) canvas.parentElement.removeChild(canvas);
    canvas = null;
    pausedByEnv = false;
    mode = "idle";
    window.__stageMode = "idle";
  }

  /** 真正卸载路径（pagehide / 页面切换）：卸载舞台并解绑环境监听，之后不再有任何 rAF */
  function destroy() {
    unmount();
    unbindEnv();
    host = null;
    fallbackImg = null;
    activeTier = null;
  }

  /** 挂件头部特写：CSS 放大画布并上移，让脸部落在圆形框内（不改引擎坐标） */
  function focusHead() {
    if (!host || !puppet) return;
    host.classList.add("stage-focus");
    try { puppet.fit(); } catch (e) { /* 忽略 */ }
    renderOnce();
  }

  /** 展开还原主界面视图 */
  function restoreView() {
    if (!host || !puppet) return;
    host.classList.remove("stage-focus");
    try { puppet.fit(); } catch (e) { /* 忽略 */ }
    renderOnce();
  }

  /** 动画开关：关 → 停 rAF/漫游并定格当前帧；开 → 恢复呼吸/眨眼/视线漫游 */
  function setMotionEnabled(on) {
    motionEnabled = !!on;
    if (!puppet) return;
    puppet.setAutoIdle(motionEnabled);
    if (motionEnabled) {
      if (freezeTimer) clearTimeout(freezeTimer);
      freezeTimer = null;
      startWander();
      startLoop();
    } else {
      freezeFrame();
    }
  }

  /** 纯函数：TTS 包络 level → 口型目标（<0=无音频 → 静止；零动画连呼吸都停） */
  function lipFor(level, motion) {
    if (typeof level !== "number" || !isFinite(level) || level <= 0) return 0;
    if (!motion) return 0;
    return Math.min(1, 0.04 + Math.min(1, level) * 0.55);
  }

  /** TTS 播放期间的口型喂入（level 来自音频包络关键帧；<0 = 无音频，静止） */
  function lipFrame(level) {
    const target = lipFor(level, motionEnabled);
    lipOpen = target;
    lipHold = target > 0 ? 6 : 0;
    if (!puppet) return;
    // 零动画：直接落到目标值并渲染一帧（不启动循环）
    if (!motionEnabled) {
      puppet.setParam("lipSync", lipHold > 0 ? lipOpen : 0);
      if (lipHold > 0) lipHold = 0;
      renderOnce();
    }
  }

  return {
    mount, unmount, destroy, setEmotion, playMotion, lipFrame, focusHead, restoreView, setMotionEnabled,
    /** 单测/探针：纹理档位、模型源、本次挂载档位与实际模式 */
    _pickTier: pickTier,
    _modelUrl: modelUrl,
    _tier: () => activeTier,
    _mode: () => mode,
    _rafActive: () => rafId !== null,
    _envPaused: () => pausedByEnv,
    _emotionFor: emotionFor,
    _motionFor: motionFor,
    _vramFor: vramFor,
    /** 单测入口：口型目标纯函数与当前内部状态 */
    _lipFor: lipFor,
    _lipState: () => ({ lipOpen, lipHold })
  };
})();

if (typeof window !== "undefined") window.Stage = Stage;
if (typeof module === "object" && module.exports) module.exports = Stage;
