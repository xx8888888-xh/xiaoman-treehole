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
  const jump = doc.createElement("button");
  jump.className = "jump-bottom hidden";
  Pager.mount(Object.assign({ host, typing, scroll, build, doc, jump }, opts));
  return { doc, host, typing, scroll, jump };
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

test("整页重建后句柄 el 指向新节点（逐字 paint 不会写到已脱离文档的旧节点）", () => {
  const { host } = setup();
  pushN(Pager, 3);
  const h = Pager.push({ text: "先说半句", who: "them" });
  const old = h.el;
  assert.ok(old, "push 后应有节点");
  Pager.jumpLatest();                       // 整页重建（bottom）
  assert.notEqual(h.el, old, "重建必须把句柄指向新节点");
  assert.ok(host.findAll("msg-row").includes(h.el), "句柄节点应仍在文档里");
  h.rec.text = "先说半句，再补后半句";       // 重建后才到达的增量（流式收尾）
  const bubble = h.el.children[0];
  bubble.textContent = h.rec.text;
  assert.equal(h.el.children[0].textContent, "先说半句，再补后半句");
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
  // 加载中是骨架（三条静态占位线），不再是文字；语义走 aria-label
  assert.ok(barBefore.className.includes("is-loading"));
  assert.equal(barBefore.findAll("sk-line").length, 3, "骨架三条线");
  assert.equal(barBefore.getAttribute("aria-busy"), "true");
  assert.equal(barBefore.getAttribute("aria-label"), "正在翻出更早的消息");
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

test("回到底部浮钮：上翻出现、新消息带条数、点击回底后收起", () => {
  const { scroll, jump } = setup();
  pushN(Pager, 30);
  assert.equal(jump.className.includes("hidden"), true, "跟随最新时浮钮隐藏");
  // 用户滚到历史中段（离底 500px）
  scroll.scrollHeight = 1200;
  scroll.clientHeight = 400;
  scroll.scrollTop = 300;
  scroll.onscroll();
  assert.equal(jump.className.includes("hidden"), false, "上翻历史后浮钮出现");
  assert.equal(jump.getAttribute("aria-label"), "回到最新消息");
  pushN(Pager, 3, 30);   // 上翻期间来 3 条新消息：浮钮不消失，条数写进 aria-label
  assert.equal(jump.className.includes("hidden"), false, "新消息不打断阅读");
  assert.equal(jump.getAttribute("aria-label"), "回到最新消息（3 条新消息）");
  jump.onclick();
  assert.equal(jump.className.includes("hidden"), true, "回底后自动收起");
  assert.equal(Pager.stats().last, "m32");
});

test("短会话（<=VIEW）上翻后新消息：底栏与浮钮按未渲染条数提示", () => {
  const { host, scroll, jump } = setup();
  pushN(Pager, 3);
  const bottomBar = host.findAll("window-tip").pop();
  scroll.scrollHeight = 900;
  scroll.clientHeight = 400;
  scroll.scrollTop = 100;
  scroll.onscroll();
  pushN(Pager, 1, 3);
  assert.equal(bottomBar.textContent, "↓ 1 条新消息", "新消息未进窗口，条数不能算成 0");
  assert.equal(jump.getAttribute("aria-label"), "回到最新消息（1 条新消息）");
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

/* ==================== 历史分页专项（w9） ==================== */

const mk = (id) => ({ text: `m${id - 1}`, who: id % 2 ? "me" : "them", id, at: 1000 + id - 1 });

function topBarOf(host) {
  return host.children[0];
}

test("空历史：有 loader 也不触发加载，顶栏隐藏，节点数恒定", async () => {
  let calls = 0;
  const { host } = setup({ loader: () => { calls += 1; return []; } });
  assert.equal(Pager.stats().total, 0);
  assert.equal(await Pager.older(), false);
  assert.equal(calls, 0, "没有记录时不应去查库");
  assert.equal(topBarOf(host).className.includes("hidden"), true);
  assert.equal(rows(host).length, 0, "空历史没有任何消息节点");
});

test("恰好 30 条：翻到最旧再触发 loader，显示「没有更早了」且节点恒定", async () => {
  let calls = 0;
  const { host } = setup({ loader: () => { calls += 1; return []; } });
  pushN(Pager, 30);
  const before = countNodes(host);
  assert.equal(Pager.stats().start, 16);
  assert.equal(await Pager.older(), true);   // 6
  assert.equal(await Pager.older(), true);   // 0
  assert.equal(Pager.stats().start, 0);
  assert.equal(rowTexts(host)[0], "m0");
  assert.equal(countNodes(host), before);
  assert.equal(await Pager.older(), false);
  assert.equal(calls, 1, "到底时才查一次库");
  assert.equal(Pager.stats().exhausted, true);
  assert.equal(topBarOf(host).textContent, "没有更早了");
  assert.equal(topBarOf(host).className.includes("hidden"), false, "到底提示保持可见");
  assert.equal(countNodes(host), before);
});

test("31 条：翻出唯一一条更早记录，再翻确认到底（不重复）", async () => {
  const { host } = setup({
    loader: (oldest) => Promise.resolve(oldest && oldest.id === 2 ? [mk(1)] : [])
  });
  for (const r of Array.from({ length: 30 }, (_, i) => mk(i + 2))) Pager.push(r);
  const before = countNodes(host);
  while (Pager.stats().start > 0) await Pager.older();
  assert.equal(await Pager.older(), true);   // 取到 m0
  assert.equal(Pager.stats().total, 31);
  assert.equal(Pager.stats().start, 1);
  assert.equal(Pager.stats().first, "m0");
  assert.equal(countNodes(host), before);
  assert.equal(await Pager.older(), true);    // 回到窗口 0，看到 m0
  assert.equal(rowTexts(host)[0], "m0");
  assert.equal(await Pager.older(), false);   // loader 空 → 到底
  assert.equal(Pager.stats().total, 31, "没有重复插入");
  assert.equal(Pager.stats().exhausted, true);
});

test("300 条：首屏 30 → 逐页上翻到 m0，不重不漏不跳条", async () => {
  const all = Array.from({ length: 300 }, (_, i) => mk(i + 1));
  const loader = (oldest) => {
    const idx = all.findIndex(r => r.id === oldest.id);
    return Promise.resolve(all.slice(Math.max(0, idx - 10), idx));
  };
  const { host } = setup({ loader });
  for (const r of all.slice(270)) Pager.push(Object.assign({}, r));
  const base = countNodes(host);
  assert.equal(Pager.stats().total, 30);
  assert.equal(Pager.stats().last, "m299");
  let guard = 0;
  while (guard++ < 100) {
    const moved = await Pager.older();
    assert.equal(countNodes(host), base, `第 ${guard} 次翻页节点数恒定`);
    if (!moved && Pager.stats().exhausted) break;
  }
  assert.equal(Pager.stats().total, 300, "30 → 300 全部取回且无重复");
  assert.equal(Pager.stats().first, "m0");
  assert.equal(Pager.stats().exhausted, true);
  Pager.jumpLatest();
  assert.equal(Pager.stats().last, "m299");
  while (Pager.stats().start > 0) await Pager.older();
  assert.equal(rowTexts(host)[0], "m0", "滚动回顶能看到最早一条");
});

test("200 轮上下滚动（加载 + 回底交替）DOM 节点数保持恒定", async () => {
  const all = Array.from({ length: 300 }, (_, i) => mk(i + 1));
  const loader = (oldest) => {
    const idx = all.findIndex(r => r.id === oldest.id);
    return Promise.resolve(all.slice(Math.max(0, idx - 10), idx));
  };
  const { host } = setup({ loader });
  for (const r of all.slice(270)) Pager.push(Object.assign({}, r));
  const base = countNodes(host);
  const samples = [];
  let steps = 0;
  while (steps < 200) {
    await Pager.older();
    samples.push(countNodes(host));
    steps += 1;
    if (Pager.stats().start === 0 && Pager.stats().exhausted) {
      Pager.jumpLatest();          // 回底再用新消息/回滚继续，模拟 200 轮上下滚动
      samples.push(countNodes(host));
      steps += 1;
    }
  }
  assert.equal(new Set(samples).size, 1, "200 轮里节点数只有一种取值");
  assert.equal(samples[0], base);
  assert.equal(Pager.stats().rendered, Pager.VIEW);
  assert.equal(Pager.stats().total, 300);
});



