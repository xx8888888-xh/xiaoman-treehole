/* ============================================================
 * icons.js · 全站唯一图标源（24px 网格 / 1.5px 描边 / 单一线宽）
 * ------------------------------------------------------------
 * 为什么单独一个文件：
 *  - 第三轮视觉要求「一套自绘 SVG」，不允许 emoji 与不同描边混用；
 *  - index.html 里只写 data-icon="名字"，由这里实例化，图标路径只有一份；
 *  - 纯函数（icon / names / stroke），可在 node:test 里直接测，不依赖 DOM。
 *
 * 约定：viewBox 固定 0 0 24 24；stroke-width 1.5；fill none；
 *      linecap/linejoin 都是 round；颜色一律 currentColor（跟随主题与灰阶开关）。
 * ============================================================ */
(function (root) {
  "use strict";

  const GRID = 24;
  const STROKE = 1.5;

  /* 键名即 index.html 里的 data-icon 值；值是若干条 path 的 d */
  const ICONS = {
    close: ["M7 7 L17 17", "M17 7 L7 17"],
    settings: ["M4 7 L20 7", "M4 12 L20 12", "M4 17 L20 17"],
    memory: ["M7 4.5 L17 4.5 L17 20 L12 16.4 L7 20 Z"],
    widget: ["M9 4 L9 9 L4 9", "M15 20 L15 15 L20 15"],
    send: ["M4.5 11.8 L19.5 5 L13.6 19.2 L10.9 13 Z", "M10.9 13 L19.5 5"],
    mic: ["M9.5 7.2 A2.5 2.5 0 0 1 14.5 7.2 L14.5 11.4 A2.5 2.5 0 0 1 9.5 11.4 Z",
      "M6 11.6 A6 6 0 0 0 18 11.6", "M12 17.6 L12 20"],
    check: ["M5 12.4 L9.6 17 L19 7.4"],
    bell: ["M12 4.4 C8.6 4.4 6.2 7 6.2 10.2 L6.2 13.2 L4.8 16.2 L19.2 16.2 L17.8 13.2 L17.8 10.2 C17.8 7 15.4 4.4 12 4.4 Z",
      "M10.2 19.2 A2 2 0 0 0 13.8 19.2"],
    clock: ["M5 12 A7 7 0 1 0 19 12 A7 7 0 1 0 5 12", "M12 8.4 L12 12.5 L15 14.4"],
  };

  /* 设置/记忆两个图标上的圆点（不写进 ICONS，避免把结构混进路径列表） */
  const DOTS = {
    settings: [
      { cx: 9.5, cy: 7, r: 2.4 },
      { cx: 15.5, cy: 12, r: 2.4 },
      { cx: 8, cy: 17, r: 2.4 },
    ],
  };

  function names() { return Object.keys(ICONS); }

  /** 返回一段可直接插入 DOM 的 SVG 字符串；未知名字返回空串（调用方保留文字兜底） */
  function icon(name) {
    const paths = ICONS[name];
    if (!paths) return "";
    const parts = paths.map((d) => `<path d="${d}"/>`);
    for (const c of (DOTS[name] || [])) {
      parts.push(`<circle cx="${c.cx}" cy="${c.cy}" r="${c.r}"/>`);
    }
    return `<svg class="icon" width="${GRID}" height="${GRID}" viewBox="0 0 24 24" fill="none"`
      + ` stroke="currentColor" stroke-width="${STROKE}" stroke-linecap="round"`
      + ` stroke-linejoin="round" aria-hidden="true" focusable="false">${parts.join("")}</svg>`;
  }

  /** 把 [data-icon] 元素实例化；只替换一次，保留元素的属性与可见性 */
  function hydrate(node) {
    const host = node || (typeof document !== "undefined" ? document : null);
    if (!host || typeof host.querySelectorAll !== "function") return 0;
    let n = 0;
    for (const el of host.querySelectorAll("[data-icon]")) {
      if (el.dataset.iconDone === "1") continue;
      const markup = icon(el.dataset.icon);
      if (!markup) continue;
      el.innerHTML = markup;
      el.dataset.iconDone = "1";
      n++;
    }
    return n;
  }

  const api = { GRID, STROKE, ICONS, names, icon, hydrate };
  const isNode = typeof module === "object" && module.exports;
  root.XMIcons = api;
  if (isNode) module.exports = api;

  /* 浏览器内：脚本在 body 末尾，DOM 已解析完，立即实例化一次 */
  if (root && typeof document !== "undefined" && !isNode) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => hydrate(document), { once: true });
    } else {
      hydrate(document);
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
