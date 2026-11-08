/* ============================================================
 * stream_typer.js · 流式逐字的“展示节奏”控制器（与网络到达解耦）
 * ------------------------------------------------------------
 * 只回答“这一刻该显示到第几个字”，不碰 DOM：
 *  - 短回复（≤ SHORT_CHARS 字）：整段一次落下，没有 3 字一顿的长尾；
 *  - 长回复：按当前字数自适应出字速度（240 字 ≈0.75s），
 *    网络一次灌入一大段时也不会瞬间铺满（避免“啪”地跳出整段）；
 *  - flow 结束时若屏幕上还欠几十字，用 finishSoon(ms) 在有限窗口里补齐，
 *    不 flush 瞬移（服务端最后 2 字缓冲的收尾跳变就是这么来的）；
 *  - flush() 仅供降级/危机改写时立刻定稿（TTS、history 都不等动画）。
 * 可注入 now/raf/cancel，Node 里可用假时钟精确断言节奏。
 * ============================================================ */
(() => {
  const SHORT_CHARS = 20;    // ≤20 字：短句，一次落下，不拆碎步（更长的句子按自适应节奏走）
  const MIN_CPS = 170;       // 长回复的基础速度（字/秒）：略高于常见流式到达速度，避免收尾欠账
  const CPS_PER_CHAR = 0.6;  // 字数越多越快：125 字 ≈245cps，300 字 ≈350cps
  const MAX_CPS = 400;       // 上限：避免超长文一帧灌入一大坨
  const MAX_DT = 120;        // 单帧最多按 120ms 计时，切后台回来不跳字

  /** 当前目标长度对应的出字速度（字/秒）；短文本返回 Infinity = 一次落下 */
  function speedFor(len) {
    const n = Math.max(0, Number(len) || 0);
    if (n <= SHORT_CHARS) return Infinity;
    return Math.min(MAX_CPS, MIN_CPS + n * CPS_PER_CHAR);
  }

  function create(opts) {
    const o = opts || {};
    const clock = o.now || (() => (typeof performance !== "undefined" && performance.now
      ? performance.now() : Date.now()));
    const schedule = o.raf || (typeof requestAnimationFrame === "function"
      ? (fn) => requestAnimationFrame(fn)
      : (fn) => setTimeout(fn, 16));
    const unschedule = o.cancel || (typeof cancelAnimationFrame === "function"
      ? (id) => cancelAnimationFrame(id)
      : (id) => clearTimeout(id));
    const onChange = typeof o.onChange === "function" ? o.onChange : () => {};

    let target = "";
    // initial：接手上屏时已经显示的字数（短句前缀跨过阈值时从当前位置续播，不回收文本）
    let shown = Math.max(0, Number(o.initial) || 0);
    let painted = Math.floor(shown);   // 上一次真正写出去的整数位置（只在变化时才回调）
    let lastTs = 0;
    let frame = null;
    let frames = 0;
    let startedAt = 0;
    let boost = 0;      // 收尾补速（字/秒）：只抬高本次剩余欠账的速度，不改常态节奏

    function emit() {
      const n = Math.floor(shown);
      if (n === painted) return;
      painted = n;
      onChange(target.slice(0, n), target);
    }

    function stopFrame() {
      if (frame != null) unschedule(frame);
      frame = null;
    }

    function tick() {
      frame = null;
      frames += 1;
      const t = clock();
      const dt = lastTs ? Math.max(0, Math.min(MAX_DT, t - lastTs)) : 16;
      lastTs = t;
      if (target.length <= SHORT_CHARS) {
        shown = target.length;
      } else {
        const cps = Math.max(speedFor(target.length), boost);
        shown = Math.min(target.length, shown + cps * dt / 1000);
      }
      emit();
      if (shown < target.length) scheduleNext();
      else boost = 0;
    }

    function scheduleNext() {
      if (frame != null) return;
      frame = schedule(tick);
    }

    /** 设定/增长目标文本；短文本同步落屏，长文本进入逐字节奏 */
    function push(full) {
      const next = String(full == null ? "" : full);
      if (next === target && Math.floor(shown) === target.length) return;
      if (!startedAt) startedAt = clock();
      target = next;
      if (target.length <= SHORT_CHARS) {
        shown = target.length;
        emit();
        return;
      }
      // 长文本：先按“当前已知长度”的速度起步；新增长只改速度，不重置进度
      if (!lastTs) lastTs = clock();
      scheduleNext();
    }

    /** 收尾补速：把当前剩余欠账在 ms 内播完（而不是 flush 瞬移）。
     *  只在流式正常结束时用；服务端最后 2 字缓冲、网络先到而屏幕落后都属于这种欠账。 */
    function finishSoon(ms) {
      const remain = target.length - shown;
      if (remain <= 0) { flush(); return painted; }
      const span = Math.max(60, Number(ms) || 160);
      // 欠账特别大时宁可多播两帧，也不允许一帧灌进半段（上限 2×MAX_CPS）
      boost = Math.max(boost, Math.min(remain / (span / 1000), MAX_CPS * 2));
      if (!lastTs) lastTs = clock();
      scheduleNext();
      return painted;
    }

    /** 立刻显示全部并停止（降级/危机改写/危机话术都不该等节奏） */
    function flush() {
      stopFrame();
      shown = target.length;
      boost = 0;
      emit();
      lastTs = 0;
      startedAt = 0;
      return painted;
    }

    return {
      push, flush, finishSoon,
      shown: () => Math.floor(shown),
      target: () => target,
      stats: () => ({
        target: target.length,
        shown: Math.floor(shown),
        painted,
        frames,
        elapsed: startedAt ? Math.round(clock() - startedAt) : 0,
        short: target.length <= SHORT_CHARS
      })
    };
  }

  const api = { create, speedFor, SHORT_CHARS, MIN_CPS, CPS_PER_CHAR, MAX_CPS };
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.StreamTyper = api;
  return api;
})();
