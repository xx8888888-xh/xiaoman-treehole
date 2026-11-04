const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const Appearance = require("../js/appearance.js");

const CSS = fs.readFileSync(path.join(__dirname, "..", "css", "style.css"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

function fakeStore(initial) {
  const map = Object.assign({}, initial || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(map, k) ? map[k] : null),
    setItem: (k, v) => { map[k] = String(v); },
    _map: map,
  };
}

/* ---------- 对比度工具（WCAG 2.x 相对亮度） ---------- */
function relLum(hex) {
  const h = hex.replace("#", "");
  const rgb = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const f = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
}
function contrast(a, b) {
  const x = relLum(a);
  const y = relLum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
function varsOf(block) {
  const out = {};
  const re = /--([\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;/g;
  let m;
  while ((m = re.exec(block))) out[m[1]] = m[2];
  return out;
}
const ROOT_VARS = varsOf(CSS.match(/:root\s*\{([\s\S]*?)\}/)[1]);
const DARK_VARS = varsOf(CSS.match(/body\.theme-dark\s*\{([\s\S]*?)\}/)[1]);
const DARK_SYS_VARS = varsOf(
  CSS.match(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{[\s\S]*?body:not\(\.theme-light\)\s*\{([\s\S]*?)\}/)[1],
);
const AA_PAIRS = [
  ["text", "bg"], ["text-2", "bg"], ["text-3", "bg"], ["icon", "bg"],
  ["icon-accent", "surface-2"], ["text", "panel"], ["text", "bubble-them"],
  ["text", "bubble-me"], ["text", "field"], ["text", "btn"],
  ["toast-text", "toast-bg"],
];

/* ---------- 1. 首次启动默认极简 ---------- */
test("首次启动默认极简：零动画 + 大字号 + 灰阶 + 扁平", () => {
  const p = Appearance.normalize(null);
  assert.equal(p.preset, "minimal");
  assert.equal(p.motion, false);
  assert.equal(p.font, "large");
  assert.equal(p.iconsGray, true);
  assert.equal(p.flat, true);
  // 无存储时 read 也回极简
  assert.equal(Appearance.read(fakeStore()).preset, "minimal");
});

/* ---------- 2. 三种预设映射 ---------- */
test("三档预设：极简零动画/大字号，默认与生动仅 160ms 过渡", () => {
  assert.deepEqual(Appearance.resolvePreset("minimal"),
    { preset: "minimal", motion: false, font: "large", iconsGray: true, flat: true });
  assert.deepEqual(Appearance.resolvePreset("default"),
    { preset: "default", motion: true, font: "normal", iconsGray: true, flat: false });
  assert.deepEqual(Appearance.resolvePreset("lively"),
    { preset: "lively", motion: true, font: "normal", iconsGray: false, flat: false });
  // 预设模板只读：改返回值不能污染模板
  const d = Appearance.resolvePreset("default");
  d.motion = false;
  assert.equal(Appearance.resolvePreset("default").motion, true);
  assert.equal(Appearance.resolvePreset("nope").preset, "minimal");
});

/* ---------- 3. 三个独立开关 → 自定义 ---------- */
test("三个独立开关：任一改动即 custom，且保留预设其他值", () => {
  const live = Appearance.resolvePreset("lively");
  const noMotion = Appearance.withSwitch(live, "motion", false);
  assert.equal(noMotion.preset, "custom");
  assert.equal(noMotion.motion, false);
  assert.equal(noMotion.iconsGray, false);
  assert.equal(noMotion.font, "normal");

  const bigFont = Appearance.withSwitch(live, "font", "large");
  assert.equal(bigFont.preset, "custom");
  assert.equal(bigFont.font, "large");

  const gray = Appearance.withSwitch(live, "iconsGray", true);
  assert.equal(gray.preset, "custom");
  assert.equal(gray.iconsGray, true);

  // 仅接受两档字号；旧 xlarge 迁到大字号
  assert.equal(Appearance.withSwitch(live, "font", "xlarge").font, "large");
  assert.equal(Appearance.fontOf("xlarge"), "large");
  assert.equal(Appearance.fontOf("huge"), "normal");
  assert.deepEqual(Appearance.FONTS, ["normal", "large"]);
  // 原对象未被就地改写
  assert.equal(live.motion, true);
});

/* ---------- 4. 持久化 ---------- */
test("持久化：预设/自定义写入后重读保持；损坏 JSON 回落极简", () => {
  const store = fakeStore();
  Appearance.save(Appearance.resolvePreset("lively"), store);
  const live = Appearance.read(store);
  assert.equal(live.preset, "lively");
  assert.equal(live.motion, true);
  assert.equal(live.iconsGray, false);

  const custom = Appearance.withSwitch(Appearance.resolvePreset("minimal"), "font", "normal");
  Appearance.save(custom, store);
  const again = Appearance.read(store);
  assert.equal(again.preset, "custom");
  assert.equal(again.font, "normal");
  assert.equal(again.motion, false);
  assert.equal(again.iconsGray, true);

  store.setItem(Appearance.KEY, "{oops");
  assert.equal(Appearance.read(store).preset, "minimal");
});

test("兼容旧版字号设置：xlarge 迁到大字号，其余走极简", () => {
  const store = fakeStore({ xiaoman_fs: "xlarge" });
  const p = Appearance.read(store);
  assert.equal(p.preset, "custom");
  assert.equal(p.font, "large");
  assert.equal(p.motion, false);
  assert.equal(p.iconsGray, true);
  const normal = Appearance.read(fakeStore({ xiaoman_fs: "normal" }));
  assert.equal(normal.font, "normal");
});

/* ---------- 5. 首帧应用 / 刷新不闪回 ---------- */
test("apply 写 <html data-*>：极简首帧就是大字号零动画", () => {
  const doc = { documentElement: { dataset: {} } };
  Appearance.apply(Appearance.resolvePreset("minimal"), doc);
  assert.deepEqual(doc.documentElement.dataset, {
    preset: "minimal", motion: "off", font: "large", icons: "gray", flat: "on",
    sysMotion: Appearance.prefersReducedMotion() ? "reduce" : "no-preference",
  });
  Appearance.apply(Appearance.resolvePreset("lively"), doc);
  assert.equal(doc.documentElement.dataset.icons, "color");
  assert.equal(doc.documentElement.dataset.motion, "on");
});

/* ---------- 6. prefers-reduced-motion：只提示，手动优先 ---------- */
test("系统减少动效只作提示依据：手动选生动仍保留动效", () => {
  const fakeWin = { matchMedia: () => ({ matches: true }) };
  assert.equal(Appearance.prefersReducedMotion(fakeWin), true);
  assert.equal(Appearance.prefersReducedMotion({ matchMedia: () => ({ matches: false }) }), false);
  assert.equal(Appearance.prefersReducedMotion({}), false);
  assert.equal(Appearance.normalize({ preset: "lively" }).motion, true);
  assert.equal(Appearance.normalize({ preset: "default" }).motion, true);
});

test("系统偏好变化只回调通知，不改写用户选择", () => {
  let trigger = null;
  let removed = false;
  const mql = {
    matches: false,
    addEventListener: (_n, fn) => { trigger = fn; },
    removeEventListener: () => { removed = true; },
  };
  const fakeWin = { matchMedia: () => mql };
  let notified = null;
  const unwatch = Appearance.watchReducedMotion((v) => { notified = v; }, fakeWin);
  trigger({ matches: true });
  assert.equal(notified, true);
  unwatch();
  assert.equal(removed, true);
});

/* ---------- 7. CSS：仅极简零动画；默认/生动 160ms；无循环/自发动画 ---------- */
test("仅极简模式命中零动画，默认/生动只允许 120–200ms 状态过渡", () => {
  const off = CSS.match(/html\[data-motion="off"\][\s\S]*?\{([\s\S]*?)\}/);
  assert.ok(off, "缺少 data-motion=off 的零动画规则");
  assert.match(off[1], /animation:\s*none\s*!important/);
  assert.match(off[1], /transition:\s*none\s*!important/);

  const durations = [...CSS.matchAll(/transition:[^;]*?(\d+)ms/g)].map((m) => Number(m[1]));
  assert.ok(durations.length > 0, "默认/生动缺少状态过渡");
  for (const ms of durations) {
    assert.ok(ms >= 120 && ms <= 200, `过渡时长 ${ms}ms 不在 120–200ms 内`);
  }
  assert.match(CSS, /html\[data-motion="on"\]/);

  // 三种预设都不许循环装饰动画 / 自动播放
  assert.doesNotMatch(CSS, /@keyframes/);
  assert.doesNotMatch(CSS, /animation-iteration-count\s*:\s*infinite/);
  assert.doesNotMatch(CSS, /\bautoplay\b/i);
  assert.doesNotMatch(HTML, /<audio\b/i);
  assert.doesNotMatch(HTML, /\bautoplay\b/i);
});

/* ---------- 8. 字号与设置页结构 ---------- */
test("大字号即 17/15/20px，标准字号 16px；旧 xlarge 已移除", () => {
  assert.match(CSS, /--fs:\s*1;/);
  assert.match(CSS, /html\[data-font="normal"\]\s*\{\s*--fs:\s*0\.94117647;/);
  assert.doesNotMatch(CSS, /data-font="xlarge"/);
  assert.match(CSS, /body\s*\{[^}]*font-size:\s*calc\(17px \* var\(--fs\)\)/);
});

test("设置页外观区块：三预设 + 三个独立开关，appearance.js 先于样式表", () => {
  for (const id of ["apPresets", "apFont", "apMotion", "apIcons"]) {
    assert.match(HTML, new RegExp(`id="${id}"`));
  }
  for (const preset of ["minimal", "default", "lively"]) {
    assert.match(HTML, new RegExp(`data-preset="${preset}"`));
  }
  assert.doesNotMatch(HTML, /id="apFonts"/);
  assert.doesNotMatch(HTML, /data-font=/);
  assert.ok(HTML.indexOf("js/appearance.js") < HTML.indexOf("css/style.css"),
    "appearance.js 必须在样式表之前同步执行，否则刷新会闪回");
  // 设置页不得有自动聚焦/打开即播放的元素
  assert.doesNotMatch(HTML, /autofocus/i);
});

/* ---------- 9. 对比度 AA + 焦点环 ---------- */
test("所有预设与明暗主题正文对比度达 AA（≥4.5:1）", () => {
  for (const [label, vars] of [["浅色", ROOT_VARS], ["深色(手动)", DARK_VARS], ["深色(跟随系统)", DARK_SYS_VARS]]) {
    for (const [fg, bg] of AA_PAIRS) {
      assert.ok(vars[fg] && vars[bg], `${label} 缺少变量 --${fg} / --${bg}`);
      const ratio = contrast(vars[fg], vars[bg]);
      assert.ok(ratio >= 4.5, `${label} --${fg} on --${bg} = ${ratio.toFixed(2)}:1 < 4.5`);
    }
    // 生动模式的彩色图标同样要过 AA
    assert.ok(contrast(vars["icon-accent"], vars["surface-2"]) >= 4.5);
    assert.ok(contrast(vars["icon-accent"], vars["bg"]) >= 4.5);
  }
});

test("键盘焦点环 2px 可见", () => {
  assert.match(CSS, /:focus-visible\s*\{[^}]*outline:\s*2px solid\s+var\(--focus\)/);
});
