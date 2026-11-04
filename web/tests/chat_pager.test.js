/* chat_pager.js 单测：分页窗口、DOM 节点数常数、翻历史、占位不闪烁、语音标记不丢 */
const test = require("node:test");
const assert = require("node:assert/strict");
const Pager = require("../js/chat_pager.js");
const { FakeDocument, countNodes } = require("./chat_dom_stub.js");

function build(rec) {
  const doc = new FakeDocument(); // 只是构造函数来源，真实渲染时按记录重建节点
  const row = doc.createElement("div");
  row.className = "msg-row " + (rec.who || "them");
  const bubble = doc.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = rec.text;
  row.appendChild(bubble);
  if (rec.spoken) {
    const meta = doc.createElement("span");
    meta.className = "meta";
    meta.textContent = "🔊 语音已播";
    bubble.appendChild(meta);
  }
  return row;
}

function setup(opts = {}) {
  const doc = new FakeDocument();
  const host = doc.createElement("div");
  host.className = "messages";
  const typing = doc.createElement("div");
  typing.className = "msg-row hidden";
  host.appendChild(typing);
  const scroll = doc.createElement("div");
  Pager.mount(Object.assign({ host, typing, scroll, build, doc }, opts));
  return { doc, host, typing, scroll };
}

function pushN(p, n, from = 0) {
  for (let i = 0; i < n; i++) p.push({ text: `m${from + i}`, who: i % 2 ? "me" : "them" });
}

function rows(host) {
  return host.findAll("msg-row").filter((r) => / (me|them)$/.test(r.className));
}

function rowTexts(host) {
  return rows(host).map((r) => (r.children.length ? r.children[0].textContent : ""));
}

test("接口齐备：mount/push/older/jumpLatest/markVoice/stats；PAGE=10, VIEW=14", () => {
  for (const k of ["mount", "push", "older", "jumpLatest", "markVoice", "stats"]) {
    assert.equal(typeof Pager[k], "function", k);
  }
  assert.equal(Pager.PAGE, 10);
  assert.equal(Pager.VIEW, 14);
});

test("200 轮（400 条）后 DOM 节点数仍在常数级，且与 100 条时一致", () => {
  const { host } = setup();
  pushN(Pager, 100);
  const at100 = countNodes(host);
  assert.ok(at100 <= 40, `100 条时节点数 ${at100}`);
  pushN(Pager, 300, 100);
  const at400 = countNodes(host);
  assert.equal(at400, at100, "400 条与 100 条节点数相同（分页窗口固定）");
  assert.equal(Pager.stats().rendered, Pager.VIEW);
  assert.equal(Pager.stats().total, 400);
  // 只渲染最新一页
  const texts = rowTexts(host);
  assert.equal(texts.length, Pager.VIEW);
  assert.equal(texts[texts.length - 1], "m399");
  assert.equal(texts[0], "m386");
});

test("向上翻页：节点数不变，窗口前移，能回到更早的消息", async () => {
  const { host } = setup();
  pushN(Pager, 40);
  const before = countNodes(host);
  assert.equal(Pager.stats().start, 26);
  assert.equal(await Pager.older(), true);
  assert.equal(Pager.stats().start, 16);
  assert.equal(countNodes(host), before);
  assert.equal(rowTexts(host)[0], "m16");
  // 连翻到开头后不再前移，也不报错
  while (Pager.stats().start > 0) assert.equal(await Pager.older(), true);
  assert.equal(Pager.stats().start, 0);
  assert.equal(rowTexts(host)[0], "m0");
  assert.equal(countNodes(host), before);
});

test("翻页占位不闪烁：同一条占位节点从等待到完成始终在原地", async () => {
  let release = null;
  const { host } = setup({
    loader: () => new Promise((res) => { release = res; })
  });
  pushN(Pager, Pager.VIEW);           // 正好一页，start=0 → 走 loader
  const barBefore = host.children[0];
  const pending = Pager.older();
  assert.equal(host.children[0], barBefore, "占位节点未被重建");
  assert.equal(barBefore.textContent, "正在翻出更早的消息…");
  assert.equal(barBefore.className.includes("hidden"), false);
  release(Array.from({ length: 10 }, (_, i) => ({ text: `old${i}`, who: "them" })));
  await pending;
  assert.equal(host.children[0], barBefore, "加载完成后仍是同一个占位节点");
  assert.equal(Pager.stats().total, 24);
  assert.equal(Pager.stats().start, 10, "保持当前阅读位置");
  assert.ok(countNodes(host) <= 40, "加载后节点数仍为常数");
  assert.equal(barBefore.textContent, "↑ 更早的消息（还有 10 条）");
  // 再翻一页就能看到加载进来的更早记录
  assert.equal(await Pager.older(), true);
  assert.equal(rowTexts(host)[0], "old0");
});

test("用户翻历史时新消息不打断：只提示条数，回到底部才显示最新", () => {
  const { host, scroll } = setup();
  pushN(Pager, 30);
  const before = countNodes(host);
  // 模拟用户滚到顶部（离底很远）
  scroll.scrollHeight = 1200;
  scroll.clientHeight = 400;
  scroll.scrollTop = 0;
  const startBefore = Pager.stats().start;
  pushN(Pager, 3, 30);
  assert.equal(Pager.stats().start, startBefore, "窗口不因新消息跳动");
  assert.equal(countNodes(host), before);
  assert.equal(Pager.stats().total, 33);
  const bottomBar = host.findAll("window-tip").pop();
  assert.equal(bottomBar.className.includes("hidden"), false);
  assert.equal(bottomBar.textContent, "↓ 3 条新消息");
  Pager.jumpLatest();
  assert.equal(Pager.stats().start, 19);
  const texts = rowTexts(host);
  assert.equal(texts[texts.length - 1], "m32");
});

test("markVoice 写回记录：滑出窗口后重新渲染仍带「语音已播」", async () => {
  const { host } = setup();
  pushN(Pager, 20);
  const handle = Pager.push({ text: "hi", who: "them" });
  assert.equal(Pager.markVoice(handle), true);
  assert.equal(host.findAll("meta").length, 1);
  // 再来 14 条把 hi 挤出窗口，再向上翻一页
  pushN(Pager, 14, 100);
  assert.equal(host.findAll("meta").length, 0, "已滑出窗口");
  assert.equal(await Pager.older(), true);
  const texts = rowTexts(host);
  assert.ok(texts.includes("hi"), "回到历史能看到 hi");
  assert.equal(host.findAll("meta").length, 1, "重渲染后语音标记不丢");
});

