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
  const re = /--([\w-]+)\s*:\s*(#[0-9A-Fa-f]{6}|var\(--[\w-]+\))\s*;/g;
  let m = re.exec(block);
  while (m) {
    out[m[1]] = m[2];
    m = re.exec(block);
  }
  // 一层层解 var(--x) 别名（如 --icon-accent: var(--accent)）
  for (let pass = 0; pass < 4; pass++) {
    for (const key of Object.keys(out)) {
      const ref = out[key].match(/^var\(--([\w-]+)\)$/);
      if (ref && out[ref[1]]) out[key] = out[ref[1]];
    }
  }
  return out;
}
const ROOT_VARS = varsOf(CSS.match(/:root\s*\{([\s\S]*?)\}/)[1]);
const DARK_VARS = varsOf(CSS.match(/body\.theme-dark\s*\{([\s\S]*?)\}/)[1]);
const AA_PAIRS = [
  ["text", "bg"], ["text-2", "bg"], ["text-3", "bg"], ["icon", "bg"],
  ["icon-accent", "surface-2"], ["text", "panel"], ["text", "bubble-them"],
  ["text", "bubble-me"], ["text", "field"], ["text-2", "surface-2"],
  ["text-3", "panel"], ["text-3", "field"], ["text-3", "surface"], ["text-3", "surface-2"], ["accent-ink", "accent"],
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

/* ---------- 7. CSS：仅极简零动画；其余过渡全部令牌化 ---------- */
test("仅极简模式命中零动画，其余过渡只允许 --dur-* 令牌（按压 80–120ms / 状态 120–200ms）", () => {
  const off = CSS.match(/html\[data-motion="off"\][\s\S]*?\{([\s\S]*?)\}/);
  assert.ok(off, "缺少 data-motion=off 的零动画规则");
  assert.match(off[1], /animation:\s*none\s*!important/);
  assert.match(off[1], /transition:\s*none\s*!important/);

  // 三档时长必须收敛成令牌，不允许在 transition 里裸写 ms
  const press = Number(CSS.match(/--dur-press:\s*(\d+)ms/)[1]);
  const state = Number(CSS.match(/--dur-state:\s*(\d+)ms/)[1]);
  const base = Number(CSS.match(/--dur-base:\s*(\d+)ms/)[1]);
  const slow = Number(CSS.match(/--dur-slow:\s*(\d+)ms/)[1]);
  assert.ok(press >= 80 && press <= 120, `按压时长 ${press}ms 必须落在 80–120ms`);
  for (const ms of [state, base]) {
    assert.ok(ms >= 120 && ms <= 200, `状态时长 ${ms}ms 不在 120–200ms 内`);
  }
  assert.ok(slow >= state && slow <= 320, "面板一次性入场不得慢于 320ms");

  assert.doesNotMatch(CSS, /transition:[^;{}]*\d+ms/, "过渡时长必须引用 --dur-* 令牌");
  const transitions = [...CSS.matchAll(/transition:\s*([^;{}]+);/g)]
    .map((m) => m[1].trim())
    .filter((t) => !t.startsWith("none"));
  assert.ok(transitions.length >= 10, "默认/生动缺少状态过渡");
  for (const t of transitions) {
    assert.match(t, /var\(--dur-/, `过渡未走令牌：${t}`);
  }
  assert.match(CSS, /html\[data-motion="on"\]/);

  // 三种预设都不许循环装饰动画 / 自动播放；只允许一次性面板入场
  const keyframes = [...CSS.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]).sort();
  assert.deepEqual(keyframes, ["modal-in", "panel-in"], "只允许抽屉/弹层的一次性入场");
  assert.doesNotMatch(CSS, /animation-iteration-count\s*:\s*infinite/);
  assert.doesNotMatch(CSS, /\bautoplay\b/i);
  assert.doesNotMatch(HTML, /<audio\b/i);
  assert.doesNotMatch(HTML, /\bautoplay\b/i);
  // 骨架屏必须是静态占位：没有 shimmer / 转圈（注释先剔除，只看真实声明）
  const noComment = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(noComment, /\.stage-skeleton\s*\{/);
  assert.doesNotMatch(noComment, /shimmer|spin|rotate\(/i);
});

/* ---------- 8. 字号与设置页结构 ---------- */
test("大字号 18px / 标准字号 16px；字号只经由 --text-body 一处生效", () => {
  assert.match(CSS, /--fs:\s*1;/);
  assert.match(CSS, /--text-body:\s*calc\(16px \* var\(--fs\)\)/);
  assert.match(CSS, /html\[data-font="large"\]\s*\{\s*--fs:\s*1\.125;/);
  assert.doesNotMatch(CSS, /data-font="xlarge"/);
  assert.match(CSS, /body\s*\{[^}]*font-size:\s*var\(--text-body\)/);
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
test("暗色只有一份变量块：JS 解析 auto，CSS 只认 body.theme-dark", () => {
  assert.match(CSS, /body\.theme-dark\s*\{/, "缺少深色变量块");
  assert.doesNotMatch(CSS, /@media\s*\(prefers-color-scheme:\s*dark\)/,
    "不得再复制一份 @media 深色变量（auto 由 app.js 解析成 class）");
  assert.match(HTML, /body\.theme-dark|theme-dark/, "首帧脚本需写 theme-dark/light");
});

test("所有预设与明暗主题正文对比度达 AA（≥4.5:1）", () => {
  for (const [label, vars] of [["浅色", ROOT_VARS], ["深色", DARK_VARS]]) {
    for (const [fg, bg] of AA_PAIRS) {
      assert.ok(vars[fg] && vars[bg], `${label} 缺少变量 --${fg} / --${bg}`);
      const ratio = contrast(vars[fg], vars[bg]);
      assert.ok(ratio >= 4.5, `${label} --${fg} on --${bg} = ${ratio.toFixed(2)}:1 < 4.5`);
    }
    // 第三级文字在底色上必须 ≥4.5（任务硬指标）
    assert.ok(contrast(vars["text-3"], vars["bg"]) >= 4.5);
    // 生动模式的彩色图标同样要过 AA
    assert.ok(contrast(vars["icon-accent"], vars["surface-2"]) >= 4.5);
    assert.ok(contrast(vars["icon-accent"], vars["bg"]) >= 4.5);
  }
});

test("键盘焦点环 2px 可见", () => {
  assert.match(CSS, /:focus-visible\s*\{[^}]*outline:\s*2px solid\s+var\(--focus\)/);
});

/* ---------- 10. 第三轮：令牌阶梯 ---------- */
test("间距 6 档、字阶 5 档；间距/字号/行高不再出现零散 px", () => {
  const root = CSS.match(/:root\s*\{([\s\S]*?)\}/)[1];
  const spaces = [...root.matchAll(/--space-([\d]+)\s*:\s*\d+px/g)].map((m) => m[1]).sort();
  assert.deepEqual(spaces, ["1", "2", "3", "4", "5", "6"], "间距必须恰好收敛为 6 档");
  assert.doesNotMatch(CSS, /--space-(8|12)\s*:/, "旧档位 --space-8/12 必须删除");
  const sizes = [...root.matchAll(/--text-([a-z]+)\s*:/g)].map((m) => m[1]);
  // 5 档 + subtitle 兼容别名
  assert.deepEqual(sizes.sort(), ["body", "caption", "display", "label", "subtitle", "title"].sort());

  const noComment = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
  const decls = [];
  const re = /(?:^|[;{])\s*(padding(?:-top|-right|-bottom|-left)?|margin(?:-top|-right|-bottom|-left)?|gap|row-gap|column-gap|font-size|line-height)\s*:\s*([^;{}]+);/gm;
  let m = re.exec(noComment);
  while (m) { decls.push([m[1], m[2].trim()]); m = re.exec(noComment); }
  assert.ok(decls.length > 40, "扫描不到间距声明，正则失效");
  for (const [prop, value] of decls) {
    const cleaned = value.replace(/env\([^)]*\)/g, "").replace(/var\(--[a-z0-9-]+\)/gi, "");
    assert.doesNotMatch(cleaned, /\d+px/, `${prop}: ${value} 仍含裸 px，必须走令牌`);
  }
  // 字号只允许令牌
  for (const [prop, value] of decls.filter(([p]) => p === "font-size")) {
    assert.match(value, /var\(--text-|inherit/, `字号未令牌化：${value}`);
  }
  // 令牌名不许重复：--icon 曾同时是颜色和尺寸，暗色块一旦覆盖成颜色，
  // 宽度就会退化成 auto（空态记号被撑满整屏），测试必须拦住这类重名
  for (const block of [root, CSS.match(/body\.theme-dark\s*\{([\s\S]*?)\}/)[1]]) {
    const names = [...block.matchAll(/--([a-z0-9-]+)\s*:/g)].map((x) => x[1]);
    assert.equal(new Set(names).size, names.length,
      "同名令牌重复定义：" + names.filter((n, k) => names.indexOf(n) !== k).join(","));
  }
  // 尺寸与颜色分开命名，语义写死
  assert.match(root, /--icon-size:\s*20px/);
  assert.doesNotMatch(root, /--icon:\s*\d+px/, "--icon 是颜色，尺寸必须叫 --icon-size");
});

/* ---------- 11. 第三轮：图标统一 ---------- */
test("自绘 SVG 图标集：24px 网格 / 1.5px 描边，HTML 不再混用 emoji", () => {
  const Icons = require("../js/icons.js");
  assert.equal(Icons.GRID, 24);
  assert.equal(Icons.STROKE, 1.5);
  assert.ok(Icons.names().length >= 8, "图标集太小");
  for (const name of Icons.names()) {
    const svg = Icons.icon(name);
    assert.match(svg, /viewBox="0 0 24 24"/, `${name} 不是 24 网格`);
    assert.match(svg, /stroke-width="1\.5"/, `${name} 描边不是 1.5px`);
    assert.match(svg, /fill="none"/, `${name} 必须线描而非实心`);
    assert.match(svg, /<path d="M/, `${name} 缺少路径`);
  }
  assert.equal(Icons.icon("no-such-icon"), "");

  // HTML 里的 data-icon 必须都在图标集里；静态按钮不再内联第二套 SVG
  for (const [, name] of HTML.matchAll(/data-icon="([a-z-]+)"/g)) {
    assert.ok(Icons.names().includes(name), `data-icon="${name}" 不在图标集`);
  }
  const inlineSvgs = [...HTML.matchAll(/<svg\b/g)].length;
  assert.ok(inlineSvgs <= 1, `index.html 只允许空态自绘记号一处内联 SVG，实际 ${inlineSvgs} 处`);
  assert.doesNotMatch(HTML, /[\u{1F300}-\u{1FAFF}]/u, "不得使用 emoji");
  assert.doesNotMatch(HTML, /[\u{2600}-\u{27BF}]/u, "不得使用 emoji 符号");
});

/* ---------- 12. 第三轮：对比度实测表 ---------- */
test("双主题对比度量化：正文 ≥7:1，其余前景 ≥4.5:1", () => {
  const BODY_PAIRS = [
    ["text", "bg"], ["text", "panel"], ["text", "bubble-them"],
    ["text", "bubble-me"], ["text", "field"],
  ];
  const UI_PAIRS = [
    ["text-2", "bg"], ["text-2", "panel"], ["text-2", "surface-2"],
    ["text-3", "bg"], ["text-3", "panel"], ["text-3", "surface"],
    ["text-3", "surface-2"], ["text-3", "field"],
    ["icon", "bg"], ["icon", "panel"], ["icon-accent", "surface-2"],
    ["accent-ink", "accent"], ["accent", "accent-soft"],
    ["crisis", "crisis-soft"], ["toast-text", "toast-bg"],
  ];
  const rows = [];
  for (const [label, vars] of [["暖纸", ROOT_VARS], ["暖炭黑", DARK_VARS]]) {
    for (const [fg, bg] of BODY_PAIRS) {
      const r = contrast(vars[fg], vars[bg]);
      rows.push([label, `--${fg} / --${bg}`, r, 7]);
      assert.ok(r >= 7, `${label} 正文 --${fg} on --${bg} = ${r.toFixed(2)}:1 < 7`);
    }
    for (const [fg, bg] of UI_PAIRS) {
      const r = contrast(vars[fg], vars[bg]);
      rows.push([label, `--${fg} / --${bg}`, r, 4.5]);
      assert.ok(r >= 4.5, `${label} --${fg} on --${bg} = ${r.toFixed(2)}:1 < 4.5`);
    }
    // 组件边界线至少要能看见（≥3:1）
    assert.ok(contrast(vars["line-strong"], vars["bg"]) >= 3);
  }
  console.log("\n对比度实测（WCAG 2.x）");
  console.log("主题\t前景 / 背景\t实测\t门槛");
  for (const [theme, pair, r, need] of rows) {
    console.log(`${theme}\t${pair}\t${r.toFixed(2)}:1\t≥${need}`);
  }
  console.log(`最小正文 ${Math.min(...rows.filter((x) => x[3] === 7).map((x) => x[2])).toFixed(2)}:1`);
});

/* ---------- 13. 第三轮：层级与状态 ---------- */
test("空态/记忆页只有一个主焦点，次级说明收敛；发送态与录制态有明确外观", () => {
  // 空态：一个标题 + 一句说明 + 静态记号
  assert.equal([...HTML.matchAll(/class="empty-title"/g)].length, 1);
  assert.equal([...HTML.matchAll(/class="empty-sub"/g)].length, 1);
  assert.match(HTML, /class="empty-art"/);
  // 记忆抽屉：标题唯一，副说明压到一句（不再塞整段隐私说明）
  const drawer = HTML.match(/<div class="drawer-panel"[\s\S]*?id="memoryChips"/)[0];
  assert.equal([...drawer.matchAll(/<h2/g)].length, 1, "记忆页只能有一个标题");
  const sub = drawer.match(/class="drawer-sub">([^<]*)</)[1];
  assert.ok(sub.length <= 40, `记忆页副说明过长（${sub.length} 字），应弱化`);
  // 设置页：关于区压缩，隐私说明只留一句
  const about = HTML.match(/<section class="about">[\s\S]*?<\/section>/)[0];
  assert.equal([...about.matchAll(/<p/g)].length, 5, "关于区只保留 5 行");
  assert.doesNotMatch(about, /服务器也可能留一份上下文/);
  // 版本号唯一：全站只有一个 v0.6.0 文本节点，升级只改这一处
  assert.equal([...HTML.matchAll(/0\.6\.0/g)].length, 1, "版本号必须只有一个定义点");
  assert.match(about, /id="aboutVersion">v0\.6\.0</);
  // 更新说明只写已落地的事实，不写占位话术
  assert.match(about, /备份 v3 可完整还原/);
  assert.match(about, /消息可删可改/);
  assert.match(about, /5\.94 MiB 降到 0\.98 MiB/);
  assert.doesNotMatch(HTML, /即将支持|敬请期待|即将上线/);
  // 发送态 / 录制态
  assert.match(CSS, /#app:has\(#typingRow:not\(\.hidden\)\)\s*\.send-btn\[disabled\]/);
  assert.match(CSS, /\.inputbar\[data-recording="true"\]/);
  assert.match(CSS, /var\(--press-scale\)/);
});

test("横屏 + 软键盘：极矮视口（≤260px）必须收紧顶栏与输入条", () => {
  // 云端 AVD 实测（2280×1080，键盘弹起后 WebView 只剩 149 CSS px）：
  // 旧布局 topbar 64 + chatArea margin 28 + inputbar 79 会把输入框挤出可视区
  // （溢出 36 设备 px），聊天区只剩 12px。这条规则是修复的唯一入口。
  const mq = "@media (orientation: landscape) and (max-height: 260px)";
  assert.match(CSS, /@media \(orientation: landscape\) and \(max-height: 260px\)/);
  const block = CSS.split(mq)[1].split("/* ---------- 记忆抽屉空态")[0];
  assert.match(block, /\.topbar\s*\{\s*display:\s*none;\s*\}/, "顶栏未让位（32px 仍会挤掉气泡）");
  assert.match(block, /#chatArea\s*\{\s*margin:\s*0;\s*\}/, "聊天区外层 margin 未清零");
  assert.match(block, /\.inputbar\s*\{[\s\S]{0,120}var\(--space-1\)/, "输入条 padding 未压缩");
});
