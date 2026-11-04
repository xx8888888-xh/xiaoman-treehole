/* ============================================================
 * appearance.js · 外观预设与三个独立开关（纯函数 + DOM 应用）
 * ------------------------------------------------------------
 * 预设：
 *   minimal 极简：灰阶图标、零动画、大字号、扁平层级（首次启动默认）
 *   default 默认：当前样子（灰阶、无装饰动画、标准字号）
 *   lively  生动：彩色图标、仅状态转换的轻动效、标准字号
 * 开关：动画 / 字号（标准·大·特大）/ 图标灰阶
 *   —— 改任一开关即进入 custom，预设本身不会被就地改写。
 * 存储：localStorage("xiaoman_appearance_v1")，在本文件于 <head> 同步加载时
 *   立即写到 <html data-*>，保证首帧就是用户选择，不闪一下。
 * ============================================================ */
(function (root) {
  "use strict";

  const KEY = "xiaoman_appearance_v1";
  const FONTS = ["normal", "large", "xlarge"];
  const PRESETS = {
    minimal: { preset: "minimal", motion: false, font: "large", iconsGray: true, flat: true },
    default: { preset: "default", motion: false, font: "normal", iconsGray: true, flat: false },
    lively: { preset: "lively", motion: true, font: "normal", iconsGray: false, flat: false },
  };

  function has(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  function normalize(raw) {
    if (!raw || typeof raw !== "object" || !raw.preset) return resolvePreset("minimal");
    const src = raw;
    if (has(PRESETS, src.preset)) return resolvePreset(src.preset);
    // custom / 缺失 / 非法 → custom；但开关缺省时保持安全值（零动画、灰阶）
    return {
      preset: "custom",
      motion: src.motion === true,
      font: FONTS.indexOf(src.font) >= 0 ? src.font : "normal",
      iconsGray: src.iconsGray !== false,
      flat: src.flat === true,
    };
  }

  function resolvePreset(name) {
    const p = has(PRESETS, name) ? PRESETS[name] : PRESETS.minimal;
    return Object.assign({}, p);
  }

  function withSwitch(prefs, key, value) {
    const base = normalize(prefs);
    const next = Object.assign({}, base, { preset: "custom" });
    if (key === "font") next.font = FONTS.indexOf(value) >= 0 ? value : base.font;
    else if (key === "motion") next.motion = !!value;
    else if (key === "iconsGray") next.iconsGray = !!value;
    return next;
  }

  function read(store) {
    const ls = store || (typeof localStorage !== "undefined" ? localStorage : null);
    if (!ls) return resolvePreset("minimal");
    try {
      const raw = ls.getItem(KEY);
      if (raw) return normalize(JSON.parse(raw));
      // 兼容旧版单独的字号设置：保留用户已选的字号，其余走极简
      const legacy = ls.getItem("xiaoman_fs");
      if (FONTS.indexOf(legacy) >= 0) {
        return { preset: "custom", motion: false, font: legacy, iconsGray: true, flat: false };
      }
      return resolvePreset("minimal");
    } catch (e) {
      return resolvePreset("minimal");
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
    KEY, FONTS, PRESETS, normalize, resolvePreset, withSwitch, read, save, apply,
    setPreset, setSwitch, get,
  };

  root.Appearance = api;
  if (typeof module === "object" && module.exports) module.exports = api;

  // <head> 内同步执行：首帧即用户外观
  if (typeof document !== "undefined" && document.documentElement) apply(current);
})(typeof window !== "undefined" ? window : globalThis);
