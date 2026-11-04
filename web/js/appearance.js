/* ============================================================
 * appearance.js · 外观预设与三个独立开关（纯函数 + DOM 应用）
 * ------------------------------------------------------------
 * 预设：
 *   minimal 极简（首次启动默认）：灰阶图标（#2B2B2B，黑色只用于 1px 边框）、
 *           零动画、大字号（正文 17px / 次要 15px / 标题 20px）、
 *           左对齐、层级扁平、无背景装饰
 *   default 默认：灰阶图标、正常字号（正文 16px），仅 120–200ms 状态过渡
 *   lively  生动：彩色图标、正常字号，仅 120–200ms 状态过渡
 *   三种预设都禁止循环装饰动画、自动播放与出声。
 * 开关：大字号 / 动画 / 图标灰阶（三个独立布尔开关）
 *   —— 改任一开关即进入 custom，预设模板本身不会被就地改写。
 * 存储：localStorage("xiaoman_appearance_v1")，本文件在 <head> 同步执行，
 *   首帧即写入 <html data-*>，刷新不闪回。
 * prefers-reduced-motion：只作默认值与提示依据；用户手动选择优先。
 *   系统偏好变化时派发 appearance:system-motion，由设置页安静提示，不擅自覆盖。
 * ============================================================ */
(function (root) {
  "use strict";

  const KEY = "xiaoman_appearance_v1";
  const FONTS = ["normal", "large"];
  const DEFAULT_PRESET = "minimal";
  const PRESETS = {
    minimal: { preset: "minimal", motion: false, font: "large", iconsGray: true, flat: true },
    default: { preset: "default", motion: true, font: "normal", iconsGray: true, flat: false },
    lively: { preset: "lively", motion: true, font: "normal", iconsGray: false, flat: false },
  };

  function has(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  /** 字号只有标准/大两档；旧版 xlarge 迁移为大字号 */
  function fontOf(value) {
    return value === "large" || value === "xlarge" ? "large" : "normal";
  }

  function resolvePreset(name) {
    const p = has(PRESETS, name) ? PRESETS[name] : PRESETS[DEFAULT_PRESET];
    return Object.assign({}, p);
  }

  function normalize(raw) {
    if (!raw || typeof raw !== "object" || !raw.preset) return resolvePreset(DEFAULT_PRESET);
    if (has(PRESETS, raw.preset)) return resolvePreset(raw.preset);
    // custom / 缺失 / 非法 → custom；开关缺省时保持安全值（零动画、灰阶、标准字号）
    return {
      preset: "custom",
      motion: raw.motion === true,
      font: fontOf(raw.font),
      iconsGray: raw.iconsGray !== false,
      flat: raw.flat === true,
    };
  }

  function withSwitch(prefs, key, value) {
    const base = normalize(prefs);
    const next = Object.assign({}, base, { preset: "custom" });
    if (key === "font") next.font = fontOf(value);
    else if (key === "motion") next.motion = !!value;
    else if (key === "iconsGray") next.iconsGray = !!value;
    return next;
  }

  function read(store) {
    const ls = store || (typeof localStorage !== "undefined" ? localStorage : null);
    if (!ls) return resolvePreset(DEFAULT_PRESET);
    try {
      const raw = ls.getItem(KEY);
      if (raw) return normalize(JSON.parse(raw));
      // 兼容旧版单独的字号设置：保留用户已选字号，其余走极简
      const legacy = ls.getItem("xiaoman_fs");
      if (legacy && FONTS.concat("xlarge").indexOf(legacy) >= 0) {
        return { preset: "custom", motion: false, font: fontOf(legacy), iconsGray: true, flat: false };
      }
      return resolvePreset(DEFAULT_PRESET);
    } catch (e) {
      return resolvePreset(DEFAULT_PRESET);
    }
  }

  function save(prefs, store) {
    const ls = store || (typeof localStorage !== "undefined" ? localStorage : null);
    const p = normalize(prefs);
    if (ls) {
      try { ls.setItem(KEY, JSON.stringify(p)); } catch (e) { /* 隐私模式忽略 */ }
    }
    return p;
  }

  /** 系统是否偏好减少动态效果（不参与决策，只用于默认值与提示） */
  function prefersReducedMotion(scope) {
    const w = scope || root;
    try {
      return !!(w && typeof w.matchMedia === "function" && w.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) {
      return false;
    }
  }

  /** 监听系统偏好变化；只更新 data-sys-motion 并回调，绝不改写用户选择 */
  function watchReducedMotion(handler, scope) {
    const w = scope || root;
    if (!w || typeof w.matchMedia !== "function") return function () {};
    const mql = w.matchMedia("(prefers-reduced-motion: reduce)");
    const emit = (matches) => {
      const d = typeof document !== "undefined" && document.documentElement ? document.documentElement.dataset : null;
      if (d) d.sysMotion = matches ? "reduce" : "no-preference";
      if (typeof handler === "function") handler(!!matches);
    };
    const listener = (e) => emit(!!e.matches);
    try {
      if (mql && typeof mql.addEventListener === "function") {
        mql.addEventListener("change", listener);
        return function () { mql.removeEventListener("change", listener); };
      }
      if (mql && typeof mql.addListener === "function") {
        mql.addListener(listener);
        return function () { mql.removeListener(listener); };
      }
    } catch (e) { /* 老 WebView 无 matchMedia 监听 */ }
    return function () {};
  }

  let current = read();

  function apply(prefs, doc) {
    const d = doc || (typeof document !== "undefined" ? document : null);
    const p = normalize(prefs);
    current = p;
    if (d && d.documentElement) {
      const set = d.documentElement.dataset;
      set.preset = p.preset;
      set.motion = p.motion ? "on" : "off";
      set.font = p.font;
      set.icons = p.iconsGray ? "gray" : "color";
      set.flat = p.flat ? "on" : "off";
      set.sysMotion = prefersReducedMotion() ? "reduce" : "no-preference";
    }
    if (root && typeof root.dispatchEvent === "function" && typeof CustomEvent === "function") {
      try { root.dispatchEvent(new CustomEvent("appearance:change", { detail: p })); } catch (e) { /* 老 WebView */ }
    }
    return p;
  }

  function setPreset(name) {
    return apply(save(resolvePreset(name)));
  }

  function setSwitch(key, value) {
    return apply(save(withSwitch(current, key, value)));
  }

  function get() {
    return Object.assign({}, current);
  }

  const api = {
    KEY, FONTS, PRESETS, DEFAULT_PRESET,
    fontOf, normalize, resolvePreset, withSwitch, read, save,
    prefersReducedMotion, watchReducedMotion, apply, setPreset, setSwitch, get,
  };

  root.Appearance = api;
  if (typeof module === "object" && module.exports) module.exports = api;

  // <head> 内同步执行：首帧即用户外观；系统偏好变化只提示不改写
  if (typeof document !== "undefined" && document.documentElement) {
    apply(current);
    watchReducedMotion(function (reduced) {
      if (root && typeof root.dispatchEvent === "function" && typeof CustomEvent === "function") {
        try {
          root.dispatchEvent(new CustomEvent("appearance:system-motion", {
            detail: { reduced: !!reduced, prefs: get() },
          }));
        } catch (e) { /* 老 WebView */ }
      }
    });
  }
})(typeof window !== "undefined" ? window : globalThis);
