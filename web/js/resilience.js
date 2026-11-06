/* resilience.js · R7 韧性三件套（纯逻辑，可单测）
 * 1) 发送失败：可爱风文案 + 常驻提示条（一键重发入口），不弹系统 alert
 * 2) 输入法合成态：compositionstart→compositionend 之间的回车不发送
 * 3) 超长粘贴：按 500 字截断，告诉用户丢了多少，输入栏高度仍由 CSS 封顶
 */
((root) => {
  const MAX_INPUT = 500;
  // compositionend 与确认回车在同一击键里先后到达时（部分 WebView），留一个极短窗口
  const COMPOSE_END_GRACE_MS = 30;

  function toChars(v) {
    // 按码点切，避免把 emoji 的代理对切成半个
    return Array.from(String(v == null ? "" : v));
  }

  /** 截断策略：保留前 max 个字，返回丢了多少（0 表示没截）。 */
  function clampInput(raw, max) {
    const cap = max == null ? MAX_INPUT : max;
    const chars = toChars(raw);
    const kept = chars.length > cap ? chars.slice(0, cap) : chars;
    const dropped = Math.max(0, chars.length - cap);
    return { text: kept.join(""), length: kept.length, dropped, truncated: dropped > 0, max: cap };
  }

  function truncationLine(max) {
    return `一下说这么多，我先记住前 ${max == null ? MAX_INPUT : max} 字，剩下的你缓口气再讲`;
  }

  // 失败文案：不出现 HTTP/超时/错误码等术语，也不弹 alert
  const FAIL_COPY = {
    timeout: {
      toast: "小满刚刚走神太久，这句没送到",
      notice: "刚才那句没送出去（等太久啦）。点「再发一次」，我接着听",
    },
    network: {
      toast: "信号好像躲起来了，这句没送到",
      notice: "这句没送出去（网断了一下）。点「再发一次」，我马上接住",
    },
    http: {
      toast: "小满这边卡了一下，这句没送到",
      notice: "这句没送出去（服务器打了个嗝）。点「再发一次」，我继续听",
    },
    limit: {
      toast: "人有点多，小满忙不过来",
      notice: "小满暂时忙不过来。点「再发一次」，这句我记着呢",
    },
    other: {
      toast: "这句没送到小满那儿",
      notice: "这句没送出去。点「再发一次」，我不装没听见",
    },
  };

  function classifySendError(err) {
    const raw = (err?.kind) || "other";
    const kind = raw === "timeout" || raw === "network" || raw === "http" || raw === "limit" ? raw : "other";
    return Object.assign({ kind, retry: true }, FAIL_COPY[kind]);
  }

  /** 失败时同时走 toast + 常驻 notice（notice 带重发按钮），保证用户可感知且能一键重发。 */
  function createFailureUI(deps) {
    const d = deps || {};
    const toastFn = d.toast || (() => {});
    const noticeFn = d.showNotice || (() => {});
    const hideFn = d.hideNotice || (() => {});
    const retryFn = d.retry;
    return {
      report(err) {
        const f = classifySendError(err);
        toastFn(f.toast);
        noticeFn(f.notice, { retry: true, action: retryFn });
        return f;
      },
      clear() {
        hideFn();
      },
    };
  }

  function createCompositionGate(opts) {
    const o = opts || {};
    const now = o.now || (() => Date.now());
    const grace = o.graceMs == null ? COMPOSE_END_GRACE_MS : o.graceMs;
    let composing = false;
    let endedAt = -Infinity;
    return {
      start() {
        composing = true;
      },
      end() {
        composing = false;
        endedAt = now();
      },
      isComposing() {
        return composing;
      },
      /** true 才允许回车发送；Shift+Enter 永远不发送（换行）。 */
      allowEnter(ev) {
        const e = ev || {};
        if (e.key !== "Enter" || e.shiftKey) return false;
        // 合成态标记：显式 compositionstart，以及 WebView 的 isComposing / 229 兜底
        if (composing || e.isComposing || e.keyCode === 229 || e.which === 229) return false;
        // compositionend 后同一击键的残留回车
        if (now() - endedAt < grace) return false;
        return true;
      },
    };
  }

  const api = {
    MAX_INPUT,
    COMPOSE_END_GRACE_MS,
    clampInput,
    truncationLine,
    classifySendError,
    createFailureUI,
    createCompositionGate,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.XMResilience = api;
})(typeof window !== "undefined" ? window : globalThis);
