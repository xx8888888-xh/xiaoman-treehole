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
  // 舞台改为"聊天落屏后再挂载"（见 app.js requestStageMount）：挂载前收到的
  // 表情/动作先记下，挂载完成后补放一次，开场白的 Greeting 动作不会丢。
  let pendingEmotion = null;
  let pendingMotion = null;
  let stillSig = "";          // 零动画：上一次真正画到画布上的冻结帧签名（见 stillFrameSig）
  // 极简模式/关动画：停 rAF，画面定格；默认值取外观（Appearance.motion=true 表示允许动效）
  let motionEnabled = typeof window !== "undefined" && window.Appearance
    ? Appearance.get().motion
    : true;
  const MOODS = {
    happy: "开心", sad: "有点难过", gentle: "温柔模式", surprised: "诶？",
    shy: "脸红了", neutral: "在听你说", tired: "也有点累", flat: "嗯，在听", quiet: "安静陪着",
  };

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
  // 纹理预解码：每批 3 张，批间让出一个空闲帧（idle timeout 90ms 兜底）。
  // 目的：把 74ms 的「首帧 drawImage 同步解码」拆成多个小片，最长块 ≤50ms。
  const DECODE_BATCH = 3;
  const DECODE_IDLE_TIMEOUT = 90;
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
  let frameTimer = null;      // 低频档的唤醒计时器：到点只请求一帧，不补帧
  let lastTs = 0;             // 上一次真正渲染的时间戳（dt 与门控共用）
  let resizeObs = null;
  let fallbackImg = null;     // 静态/剪影兜底图（容器尺寸变化时重绘，不再请求网络）
  let envBound = false;       // 环境监听（hidden/blur/pagehide）只绑定一次
  let pausedByEnv = false;    // 页面不可见/窗口失焦期间暂停，恢复时清

  /* ---------- 空闲分级降档（第四轮耗电实测：默认预设静置 60s ≈54.75% CPU） ----------
   * 只认真实信号，不做"猜你闲置"的假计时器：
   *   活跃 = TTS/音频帧喂入口型、口型保持、动作/眨眼动画在播、3s 内用户输入。
   *   降档 = 空闲 3s → 15fps；10s → 5fps；30s → 1fps（见 gate）。
   * 门控只看时间戳：不足帧长就不渲染、不累计欠账；恢复时清掉等待并立刻补一帧，
   * 避免切回满帧瞬间"补一大批帧"造成卡顿。
   * 30s 不彻底停 rAF：默认预设仍有呼吸，全停会让画面"假死"；1fps 采样呼吸
   * 每秒只画一次（1 次 canvas 填充），耗电几乎为零，又能看出轻微起伏。
   * 眨眼（215ms）在 1fps 下无法被采样，相位机会被拖成数秒的"半闭眼"，
   * 因此该档保持睁眼、只让呼吸继续动；回到其它档位后眨眼自动恢复。
   * ---------- 状态机（纯时间函数，便于单测注入时间源） ---------- */
  const IDLE_15_MS = 3000;
  const IDLE_5_MS = 10000;
  const IDLE_1FPS_MS = 30000;
  const RATE_FULL = 0;        // 0 = 不节流（跟随 rAF）
  const RATE_STOPPED = -1;    // 不可见：不渲染也不排帧

  function createIdleGovernor() {
    let lastActivity = nowMs();
    let lastRender = -Infinity;
    let hidden = false;

    /** 记一次活跃信号：计时归零，并允许下一帧立即渲染（不补历史帧） */
    function markActivity(t) {
      lastActivity = typeof t === "number" ? t : nowMs();
      lastRender = -Infinity;
    }

    /** 纯函数：t 时刻的空闲档位（不看任何真实信号之外的输入） */
    function rateAt(t) {
      if (hidden) return RATE_STOPPED;
      const idle = t - lastActivity;
      if (idle >= IDLE_1FPS_MS) return 1;
      if (idle >= IDLE_5_MS) return 5;
      if (idle >= IDLE_15_MS) return 15;
      return RATE_FULL;
    }

    /** 本帧该不该画：render=false 时 waitMs 给出下一次最早可画的时间 */
    function gate(t) {
      const rate = rateAt(t);
      if (rate === RATE_STOPPED) return { rate, render: false, waitMs: -1 };
      if (rate === RATE_FULL) { lastRender = t; return { rate, render: true, waitMs: 0 }; }
      const interval = 1000 / rate;
      const elapsed = t - lastRender;
      if (elapsed + 0.5 < interval) {
        return { rate, render: false, waitMs: Math.max(1, Math.ceil(interval - elapsed)) };
      }
      lastRender = t;
      return { rate, render: true, waitMs: 0 };
    }

    function hide() { hidden = true; }
    function show(t) {
      hidden = false;
      lastActivity = typeof t === "number" ? t : nowMs();
      lastRender = -Infinity;
    }

    return { markActivity, rateAt, gate, hide, show, state: () => ({ lastActivity, lastRender, hidden }) };
  }

  const idleGov = createIdleGovernor();

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
    return `${MODEL_BASE}manifest.json${tier ? `?tier=${encodeURIComponent(tier)}` : ""}`;
  }

  function currentTier(container) {
    const native = !!(window.AndroidTTS?.available);
    return pickTier({
      width: container ? container.clientWidth : 0,
      height: container ? container.clientHeight : 0,
      dpr: window.devicePixelRatio || 1,
      memory: navigator?.deviceMemory,
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
      s.onerror = () => reject(new Error(`加载失败: ${src}`));
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

  /* ---------- 纹理预解码（加载期长任务收口）----------
   * puppet.load() 只 await 了 Image.onload（字节到达），真正的像素解码发生在
   * 第一次 drawImage 时同步执行——12 层一次画完就是那个 74ms 长任务。
   * 这里在首帧之前用 requestIdleCallback 分批调用 img.decode()：解码在空闲帧里
   * 完成（异步、可被浏览器调度到 worker），首帧只剩 drawImage 的合成，不再触发
   * 同步解码。批大小 3 保证单批工作量有界；不支持 decode() 的老 WebView 直接跳过，
   * 保持原行为。 */
  function nowMs() {
    return typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
  }

  function idleSlice() {
    return new Promise((resolve) => {
      if (typeof requestIdleCallback === "function") {
        requestIdleCallback(() => resolve(), { timeout: DECODE_IDLE_TIMEOUT });
      } else {
        setTimeout(resolve, 0);
      }
    });
  }

  async function prewarmTextures(p) {
    const images = Object.keys((p?.images) || {}).map(k => p.images[k]).filter(Boolean);
    if (!images.length || typeof images[0].decode !== "function") return null;
    const t0 = nowMs();
    const stats = { images: images.length, batches: 0, decoded: 0, failed: 0, decodeMs: 0, wallMs: 0 };
    for (let i = 0; i < images.length; i += DECODE_BATCH) {
      await idleSlice();
      const batch = images.slice(i, i + DECODE_BATCH);
      const d0 = nowMs();
      const ok = await Promise.all(batch.map(img => img.decode().then(
        () => true, () => { stats.failed += 1; return false; })));
      stats.decodeMs += nowMs() - d0;
      stats.decoded += ok.filter(Boolean).length;
      stats.batches += 1;
    }
    stats.decodeMs = Math.round(stats.decodeMs);
    stats.wallMs = Math.round(nowMs() - t0);
    stats.bytes = images.reduce((n, im) => n + (im.naturalWidth || 0) * (im.naturalHeight || 0) * 4, 0);
    if (typeof window !== "undefined") window.__stageDecode = stats;
    console.log("[probe] tex-decode images=" + stats.images + " batches=" + stats.batches
      + " failed=" + stats.failed + " wall=" + stats.wallMs + "ms");
    return stats;
  }

  /** 清空占位/失败层：重试与成功加载时都先走这里，避免叠加两套提示 */
  function clearStageNotes(container) {
    container.classList.remove("is-fallback");
    container.querySelectorAll(".stage-note, .stage-fallback").forEach((el) => { el.remove(); });
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
    console.log(`[probe] stage-mode=${next}`);
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
    return P?.Ticker?.shared ? P.Ticker.shared : null;
  }

  function stopSharedTicker() {
    const t = sharedTicker();
    if (!t) return;
    try { t.autoStart = false; t.stop(); } catch (_e) { /* 忽略 */ }
  }

  function startSharedTicker() {
    const t = sharedTicker();
    if (!t) return;
    try { t.autoStart = true; t.start(); } catch (_e) { /* 忽略 */ }
  }

  function pauseForEnv(why) {
    if (pausedByEnv) return;
    pausedByEnv = true;
    idleGov.hide();
    stopLoop();
    stopWander();
    stopSharedTicker();
    if (freezeTimer) clearTimeout(freezeTimer);
    freezeTimer = null;
    if (puppet && mode === "layers") renderOnce("env-pause");   // 定格在暂停瞬间的最后一帧
    console.log(`[probe] stage-pause=${why}`);
  }

  function resumeForEnv(why) {
    pausedByEnv = false;
    idleGov.show(nowMs());
    if (typeof document !== "undefined" && document.hidden) { idleGov.hide(); return; }
    if (!puppet || mode !== "layers") { renderOnce("env-resume"); return; }
    if (motionEnabled) {
      puppet.setAutoIdle(true);
      try { puppet.fit(); } catch (_e) { /* 布局尺寸可能刚恢复，fit 失败不阻断 */ }
      startSharedTicker();
      startWander();
      startLoop();
    } else {
      freezeFrame();
    }
    console.log(`[probe] stage-resume=${why}`);
  }

  function onVisibility() {
    if (document.hidden) pauseForEnv("hidden");
    else resumeForEnv("visible");
  }

  function onBlur() { pauseForEnv("blur"); }
  function onFocus() { resumeForEnv("focus"); }
  function onPageHide(e) { if (!e?.persisted) destroy(); }

  // 用户真实输入：只用来"唤醒满帧"，不参与任何视觉判断；passive 不阻塞滚动
  const ACTIVITY_EVENTS = ["pointerdown", "touchstart", "keydown", "wheel", "scroll"];
  const ACTIVITY_OPTS = { passive: true, capture: true };

  function onUserActivity() { markActive(); }

  function bindEnv() {
    if (envBound || typeof document === "undefined") return;
    envBound = true;
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);
    window.addEventListener("pagehide", onPageHide);
    for (const ev of ACTIVITY_EVENTS) document.addEventListener(ev, onUserActivity, ACTIVITY_OPTS);
  }

  function unbindEnv() {
    if (!envBound || typeof document === "undefined") return;
    envBound = false;
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("blur", onBlur);
    window.removeEventListener("focus", onFocus);
    window.removeEventListener("pagehide", onPageHide);
    for (const ev of ACTIVITY_EVENTS) document.removeEventListener(ev, onUserActivity, ACTIVITY_OPTS);
  }

  /* ---------- 渲染循环（唯一 rAF；零动画/不可见时不得存在） ----------
   * 满帧档跟随 rAF；降档后不再空转 rAF：算好下一次可画的时间，只等一个 timer，
   * 到点再请求单帧。这样 15/5/1fps 的 rAF 调度次数与真实帧数一致（便于实测）。 */
  function stopLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    if (frameTimer) clearTimeout(frameTimer);
    frameTimer = null;
    lastTs = 0;
  }

  function canAnimate() {
    return !!puppet && mode === "layers" && motionEnabled && !envBlocked();
  }

  function requestNext(waitMs) {
    if (rafId || frameTimer || !canAnimate()) return;
    if (waitMs > 0) {
      frameTimer = setTimeout(() => {
        frameTimer = null;
        if (!canAnimate()) return;
        rafId = requestAnimationFrame(tick);
      }, waitMs);
    } else {
      rafId = requestAnimationFrame(tick);
    }
  }

  /** 真实活跃信号：重置空闲计时；若正处在低频等待，立即取消等待补一帧 */
  function markActive(ts) {
    idleGov.markActivity(typeof ts === "number" ? ts : nowMs());
    if (frameTimer) {
      clearTimeout(frameTimer);
      frameTimer = null;
      requestNext(0);
    }
  }

  function tick(ts) {
    rafId = null;
    if (!canAnimate()) return;
    const t = typeof ts === "number" ? ts : nowMs();
    // 动作/口型在播 → 真实活跃，重置空闲计时（呼吸不算，否则永不休眠）。
    // 眨眼只把本段动画临时提到满帧，不重置空闲计时：自动眨眼每 3~6s 一次，
    // 若也算活跃会把计时永远清零，10s/30s 档根本到不了。
    if (puppet.motion || lipHold > 0) idleGov.markActivity(t);
    const blinking = !!(puppet.blink && puppet.blink.phase !== "open");
    const g = blinking ? { rate: RATE_FULL, render: true, waitMs: 0 } : idleGov.gate(t);
    if (!g.render) { if (g.waitMs > 0) requestNext(g.waitMs); return; }
    // 1fps 采样不到 215ms 的眨眼：保持睁眼，避免相位机拖成数秒"半闭眼"
    if (g.rate === 1 && puppet.blink && puppet.blink.phase === "open") puppet.blink.t = 0;
    // 时间戳累加出 dt：低频时按真实墙钟推进呼吸，不按帧数补动画
    const dt = lastTs ? Math.min(1, Math.max(0, (t - lastTs) / 1000)) : 1 / 60;
    lastTs = t;
    // 口型：包络驱动，短暂保持后回 0（不随机张嘴）；引擎内部 7 档交叉淡入
    const lip = lipHold > 0 ? lipOpen : 0;
    puppet.setParam("lipSync", lip);
    // 保持量按 60fps 帧当量递减：低帧率时一帧抵多帧，墙钟时长恒为 ~100ms
    if (lipHold > 0) lipHold = Math.max(0, lipHold - dt * 60);
    puppet.update(dt);
    puppet.render();
    requestNext(g.rate === RATE_FULL ? 0 : Math.max(1, Math.ceil(1000 / g.rate)));
  }

  function startLoop() {
    if (rafId || frameTimer || !canAnimate()) return;
    idleGov.show(nowMs());
    lastTs = 0;
    requestNext(0);
  }

  function renderOnce(src) {
    if (!puppet) return;
    try { puppet.fit(); puppet.render(); } catch (_e) { /* 单帧失败不阻断 */ return; }
    stillSig = stillFrameSig(puppet);
    // 打点：Android 侧 MainActivity.onConsoleMessage 把 [probe] 行转到 logcat
    // tag=XiaoManWeb，AVD 探针据 `[probe] stage-repaint src=...` 判定这次重绘的来源。
    // 只在真的画了一帧时打一行，不随帧循环输出。
    const tag = src || "manual";
    if (typeof window !== "undefined") {
      window.__stageRepaint = { src: tag, at: Math.round(nowMs()) };
      console.log("[probe] stage-repaint src=" + tag);
    }
  }

  /* ---------- 零动画的"空刷新"抑制 ----------
   * motion=false 时 rAF 已停，画面只由 renderOnce 更新。冻结帧上真正会变的只有
   * 表情/眼睛/嘴型/眨眼相位（呼吸、视线、lipSync 参数都停在上一帧）。
   * 签名相同 = 重绘结果与屏上像素完全一致 → 不画。
   * 典型场景：空闲问候与开场白用同一个 emotion=gentle，60s 后再 setEmotion 一次
   * 属于"状态没变"；TTS 收尾的 lipFrame(0) 在零动画下也不会改动 params，
   * 之前这两处各补一帧（AVD 实测 2×151,654px、14 次 drawImage）。 */
  function stillFrameSig(p) {
    if (!p) return "";
    return [
      p.emotion,
      p.eye && p.eye.l,
      p.eye && p.eye.r,
      p.mouthFrame,
      p.blink && p.blink.phase
    ].join("|");
  }

  /** 零动画专用：签名没变就不重绘，变了只画一次 */
  function paintIfStillChanged(src) {
    if (!puppet) return false;
    const next = stillFrameSig(puppet);
    if (next === stillSig) {
      if (typeof window !== "undefined") console.log("[probe] stage-repaint skip src=" + src);
      return false;
    }
    renderOnce(src);
    return true;
  }

  /** 零动画：停循环、冻结到同一帧；只保留一次延时补帧（纹理解码后）随后清零 */
  function freezeFrame() {
    stopLoop();
    stopWander();
    stopSharedTicker();
    if (!puppet) return;
    puppet.setAutoIdle(false);
    renderOnce("freeze");
    if (freezeTimer) clearTimeout(freezeTimer);
    freezeTimer = setTimeout(() => {
      freezeTimer = null;
      if (motionEnabled) return;
      renderOnce("freeze+300ms");
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
    console.log(`[probe] tex-tier=${activeTier}`);

    try {
      puppet = new LayeredPuppet(canvas, window.PUPPET_MANIFEST, {
        base: MODEL_BASE,
        tier: activeTier,
      });
      await puppet.load();       // 纹理失败时引擎自行进入 static/silhouette/hidden
      // 首帧前把像素解码分批放到空闲帧（见 prewarmTextures 注释）
      if (puppet.mode === "layers") await prewarmTextures(puppet);
    } catch (e) {
      await enterFallback((e?.message) || "引擎初始化失败");
      return;
    }

    if (puppet.mode !== "layers") {
      // 引擎内部已按契约降级：这里只负责停帧、探针与提示
      setMode(puppet.mode, "纹理加载失败，已降级");
      renderOnce("fallback");
      try { puppet.stop(); } catch (_e) { /* 未启动 */ }
      bindResize();
      return;
    }

    setMode("layers");
    puppet.setEmotion("neutral");
    puppet.setAutoIdle(motionEnabled);
    // 挂载前收到的表情/动作补放一次：开场白先落屏、舞台后挂载时 Greeting 动作不丢
    const emo = pendingEmotion, mot = pendingMotion;
    pendingEmotion = null;
    pendingMotion = null;
    if (emo) setEmotion(emo);
    if (mot && motionEnabled) playMotion(mot);
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
    const cssH = (canvas?.clientHeight) || (host?.clientHeight) || 320;
    const h = Math.max(240, Math.min(1600, Math.round(cssH * scale)));
    const w = Math.max(120, Math.round(h * ART_W / ART_H));
    canvas.width = w;
    canvas.height = h;
    return { w: w, h: h, s: h / ART_H };
  }

  function drawImageToCanvas(relPath) {
    return new Promise((resolve) => {
      const img = new Image();
      // 兜底大图（full.png）同样先异步解码再画，避免 onload 里同步解码卡一帧
      img.onload = async () => {
        try { if (typeof img.decode === "function") await img.decode(); } catch (_e) { /* 解码失败仍可绘制 */ }
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
    } catch (_e) { return false; }
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
      if (!motionEnabled || mode !== "layers") renderOnce("resize");
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
      if (host?.classList.contains("stage-focus")) return;
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
    // 惊讶原来借微笑，嘴型与情绪相反；manifest 已补 open+open_l 的独立表情
    surprised: "surprised",
    shy: "shy",
    neutral: "neutral",
    // 疲惫/敷衍原来都落 gentle → smile；已补两张最小表情（复用现有眼/嘴图层）
    tired: "tired",
    flat: "flat",
    // 沉默不需要新图：用平静脸（不再借微笑），语义上"安静陪着"就是平静
    quiet: "neutral",
    silent: "neutral",
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
    markActive();                       // 表情切换是一次真实的状态变化
    if (!puppet) { pendingEmotion = String(name || "neutral").toLowerCase(); return; }
    const key = String(name || "").toLowerCase();
    const mood = emotionFor(key);
    puppet.setEmotion(mood);
    // sad 没有独立表情层：用轻微低头表达（引擎的 headTilt 参数）；
    // 疲惫再压一点肩颈，敷衍只比平静低一点，避免三张脸在视觉上又挤成一档
    const tilt = key === "sad" ? -0.6 : mood === "tired" ? -0.35 : mood === "flat" ? -0.18 : 0;
    puppet.setParam("headTilt", tilt);
    // 零动画模式下 rAF 已停：状态切换后手动渲染一帧，否则画面停在旧表情。
    // 但"表情本来就没变"（例如 60s 空闲问候与开场白都是 gentle）时签名相同，
    // 重绘结果与屏上像素一致，属于空刷新：这时代码只记一行 skip 日志，不画。
    if (!motionEnabled) paintIfStillChanged("emotion");
    if (typeof document !== "undefined") {
      const moodEl = document.getElementById("moodText");
      if (moodEl) {
        moodEl.textContent = MOODS[key] || "在听你说";
        const chip = moodEl.parentElement;
        chip.classList.add("pop");
        setTimeout(() => chip.classList.remove("pop"), 350);
      }
    }
  }

  function playMotion(group) {
    if (!puppet) { pendingMotion = group; return; }   // 挂载前收到：记下，mount 后补放
    if (!motionEnabled) return;   // 零动画：动作不启动，帧不变
    const id = motionFor(group);
    if (!id) return;
    puppet.playMotion(id);
    markActive();                           // 动作开始播放 → 满帧
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
      try { resizeObs.disconnect(); } catch (_e) { /* 忽略 */ }
      resizeObs = null;
    }
    if (puppet) {
      try { puppet.stop(); } catch (_e) { /* 忽略 */ }   // 引擎自带的 rAF（若曾启动）
      puppet = null;
    }
    if (canvas?.parentElement) canvas.parentElement.removeChild(canvas);
    canvas = null;
    pendingEmotion = null;
    pendingMotion = null;
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
    try { puppet.fit(); } catch (_e) { /* 忽略 */ }
    renderOnce("focus-head");
  }

  /** 展开还原主界面视图 */
  function restoreView() {
    if (!host || !puppet) return;
    host.classList.remove("stage-focus");
    try { puppet.fit(); } catch (_e) { /* 忽略 */ }
    renderOnce("restore-view");
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
    if (typeof level !== "number" || !Number.isFinite(level) || level <= 0) return 0;
    if (!motion) return 0;
    return Math.min(1, 0.04 + Math.min(1, level) * 0.55);
  }

  /** TTS 播放期间的口型喂入（level 来自音频包络关键帧；<0 = 无音频，静止） */
  function lipFrame(level) {
    // TTS/音频每喂一帧（含静音段 level=0）都是"正在播放"的真实信号
    markActive();
    const target = lipFor(level, motionEnabled);
    lipOpen = target;
    lipHold = target > 0 ? 6 : 0;
    if (!puppet) return;
    // 零动画：直接落到目标值并渲染一帧（不启动循环）。
    // 但零动画下没有 update(dt)，setParam 只改目标、不会进入 params，
    // 这一帧的像素与上一帧完全相同（与 TTS 是否有声音无关）——签名不变即跳过。
    if (!motionEnabled) {
      puppet.setParam("lipSync", lipHold > 0 ? lipOpen : 0);
      if (lipHold > 0) lipHold = 0;
      paintIfStillChanged("lip");
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
    /** 空闲降档：阈值/档位常量 + 可注入时间源的状态机（单测用） */
    _idleThresholds: { to15: IDLE_15_MS, to5: IDLE_5_MS, to1: IDLE_1FPS_MS },
    _idleRates: { full: RATE_FULL, stopped: RATE_STOPPED, fps15: 15, fps5: 5, fps1: 1 },
    _createIdleGovernor: createIdleGovernor,
    _idleRate: () => idleGov.rateAt(nowMs()),
    _idleState: () => idleGov.state(),
    /** 截图取证：停视线漫游、把视线/平移/摆动目标归零（表情对比必须同角度） */
    _poseForShot: () => {
      if (!puppet) return false;
      stopWander();
      puppet.setParam("gazeX", 0);
      puppet.setParam("gazeY", 0);
      puppet.setParam("headTurn", 0);
      puppet.setParam("bodySway", 0);
      return true;
    },
    /** 探针：当前表情的图层与关键参数（表情差异取证用，不参与渲染） */
    _faceState: () => {
      if (!puppet) return null;
      const mx = typeof puppet.mouthMix === "function"
        ? puppet.mouthMix() : { a: puppet.mouthFrame, b: puppet.mouthFrame, p: 0 };
      return {
        emotion: puppet.emotion || null,
        eyeL: puppet.eye ? puppet.eye.l : null,
        eyeR: puppet.eye ? puppet.eye.r : null,
        mouth: puppet.mouthFrame || null,
        mouthMix: { a: mx.a, b: mx.b, p: Math.round((mx.p || 0) * 1000) / 1000 },
        headTilt: puppet.getParam("headTilt"),
        lipSync: puppet.getParam("lipSync"),
        gazeX: puppet.getParam("gazeX"),
        gazeY: puppet.getParam("gazeY")
      };
    },
    /** 单测入口：口型目标纯函数与当前内部状态 */
    _lipFor: lipFor,
    _lipState: () => ({ lipOpen, lipHold }),
    /** 单测入口：零动画冻结帧签名 + 注入假 puppet（返回还原函数） */
    _stillFrameSig: stillFrameSig,
    _stillForTest(nextPuppet, motionOn) {
      const prev = { puppet, motionEnabled, stillSig };
      puppet = nextPuppet;
      motionEnabled = !!motionOn;
      stillSig = stillFrameSig(nextPuppet);   // 视作"该状态已经画在屏上"
      return () => { puppet = prev.puppet; motionEnabled = prev.motionEnabled; stillSig = prev.stillSig; };
    }
  };
})();

if (typeof window !== "undefined") window.Stage = Stage;
if (typeof module === "object" && module.exports) module.exports = Stage;
