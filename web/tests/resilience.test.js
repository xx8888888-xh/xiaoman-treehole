/* resilience.test.js · R7 韧性三件套：失败可感知 / 合成态回车 / 超长粘贴 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const R = require("../js/resilience.js");
const WEB = path.join(__dirname, "..");

test("R7-1 三类失败都有可爱风文案，且都带「再发一次」入口", () => {
  ["timeout", "network", "http", "limit", "other"].forEach((kind) => {
    const f = R.classifySendError({ kind });
    assert.equal(f.kind, kind);
    assert.equal(f.retry, true, `${kind} 必须可重发`);
    assert.ok(f.toast.length > 0 && f.notice.length > 0, `${kind} 文案不能为空`);
    // 不把技术术语砸给用户，也不弹系统 alert
    assert.ok(!/HTTP|Error|undefined|null|\d{3}/.test(f.toast + f.notice), `${kind} 文案混入术语`);
    assert.match(f.notice, /再发一次/);
  });
});

test("R7-1 失败时同时出现 toast + 常驻提示条，点「再发一次」回调恰好一次", () => {
  let toasts = 0;
  let notices = 0;
  let hide = 0;
  let retries = 0;
  let shownOpts = null;
  const ui = R.createFailureUI({
    toast: () => { toasts += 1; },
    showNotice: (_t, opts) => { notices += 1; shownOpts = opts; },
    hideNotice: () => { hide += 1; },
    retry: () => { retries += 1; },
  });
  const f = ui.report({ kind: "network" });
  // 修前：非 limit 失败只有 3s toast，重发入口取决于排队条是否被关掉（关掉时 0 个）
  // 修后：toast 1 + 常驻提示条 1，重发入口与排队条状态无关
  assert.equal(toasts, 1);
  assert.equal(notices, 1);
  assert.deepEqual(shownOpts, { retry: true, action: shownOpts.action });
  assert.equal(typeof shownOpts.action, "function");
  shownOpts.action();
  assert.equal(retries, 1);
  ui.clear();
  assert.equal(hide, 1);
  assert.equal(f.kind, "network");
});

test("R7-2 合成态期间（isComposing 未置位）的回车不发送", () => {
  const gate = R.createCompositionGate();
  gate.start();
  // 旧判据 `!isComposing && keyCode !== 229` 在这个场景会放行 → 修前发送 1 次
  const ev = { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13, which: 13 };
  assert.equal(gate.isComposing(), true);
  assert.equal(gate.allowEnter(ev), false);
  gate.end();
  // compositionend 后同一击键的残留回车也不放行
  assert.equal(gate.allowEnter(ev), false);
});

test("R7-2 合成事件之外的回车正常发送，Shift+Enter 永远换行", () => {
  let t = 1000;
  const gate = R.createCompositionGate({ now: () => t, graceMs: 30 });
  gate.start();
  gate.end();                       // endedAt = 1000
  t = 1060;                         // 过了残留窗口
  assert.equal(gate.allowEnter({ key: "Enter", shiftKey: false, keyCode: 13 }), true);
  assert.equal(gate.allowEnter({ key: "Enter", shiftKey: true, keyCode: 13 }), false);
  assert.equal(gate.allowEnter({ key: "Enter", isComposing: true }), false);
  assert.equal(gate.allowEnter({ key: "Enter", keyCode: 229 }), false);
});

test("R7-3 粘贴 5000 字按 500 截断，丢字数量可告知", () => {
  const raw = "字".repeat(5000);
  const r = R.clampInput(raw);
  assert.equal(r.max, 500);
  assert.equal(r.text.length, 500);
  assert.equal(r.length, 500);
  assert.equal(r.dropped, 4500);
  assert.equal(r.truncated, true);
  assert.ok(!/undefined|null/.test(R.truncationLine(r.max)));
  // 没超上限不截断
  const small = R.clampInput("你好".repeat(100));
  assert.equal(small.truncated, false);
  assert.equal(small.dropped, 0);
});

test("R7-3 截断按码点切，不会切坏 emoji（代理对）", () => {
  const r = R.clampInput("🙂".repeat(3000));
  assert.equal(Array.from(r.text).length, 500);
  assert.equal(r.dropped, 2500);
  assert.ok(!/[\uD800-\uDBFF]$/.test(r.text), "末位不能是半个代理对");
});

test("R7-3 粘贴 = 已有内容 + 剪贴板，组合后仍封顶 500", () => {
  const cur = "a".repeat(400);
  const pasted = "b".repeat(5000);
  const r = R.clampInput(cur + pasted);
  assert.equal(r.text.length, 500);
  assert.equal(r.dropped, 4900);
  assert.equal(r.text.slice(0, 400), cur);
});

test("R7-3 布局契约：气泡可断词、聊天区独立滚动、输入栏高度封顶且不被顶出", () => {
  const css = fs.readFileSync(path.join(WEB, "css/style.css"), "utf8");
  const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
  const block = (sel) => {
    const i = css.indexOf(sel);
    assert.ok(i >= 0, `缺少 ${sel}`);
    const start = css.indexOf("{", i);
    const end = css.indexOf("}", start);
    return css.slice(start, end);
  };
  const bubble = block(".bubble");
  assert.match(bubble, /max-width/);
  assert.match(bubble, /overflow-wrap:\s*break-word/);

  const chatScroll = block("#chatScroll");
  assert.match(chatScroll, /overflow-y:\s*auto/);

  const textInput = block("#textInput");
  assert.match(textInput, /max-height:\s*132px/);
  assert.match(textInput, /overflow-y:\s*auto/);

  // 输入栏是 chatScroll 的兄弟（不在聊天滚动区内），不会被长会话顶出视口
  const inputbarAt = html.indexOf('class="inputbar"');
  const chatScrollClose = html.indexOf('id="chatScroll"');
  assert.ok(inputbarAt > chatScrollClose, "inputbar 应在 chatScroll 之后");
  const chatAreaAt = html.indexOf('id="chatArea"');
  assert.ok(chatAreaAt >= 0 && inputbarAt > chatAreaAt, "inputbar 与 chatScroll 同属 #chatArea");
  assert.match(html, /id="textInput"[^>]*maxlength="500"/);
});
