/* 时间分隔落位 + 状态区贴底：用 node 测试覆盖交互契约（不启动浏览器）
 * - 分组规则：同分钟 / 5 分钟内不重复，满 5 分钟或跨天才出现
 * - 分页边界：窗口首条要能拿到窗口外的前一条（合并同一天 / 跨天带日期）
 * - 状态条出现：pinned 时贴回底部；用户主动上滑时不强行拉回
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Pager = require("../js/chat_pager.js");
const TD = require("../js/time_divider.js");
const { FakeDocument } = require("./chat_dom_stub.js");

// 以"今天零点"为基准构造时间，测试不会随运行日期漂移
const NOW = new Date();
const MIDNIGHT = new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate()).getTime();
const MIN = 60 * 1000;

function dividerBuild(calls) {
  return (rec, i, prev, prevAt) => {
    if (calls) calls.push({ i, prevAt, at: rec.at });
    const doc = new FakeDocument();
    const row = doc.createElement("div");
    row.className = "msg-row " + (rec.who || "them");
    row.textContent = rec.text;
    const label = TD.label(prevAt, rec.at);
    if (!label) return row;
    const unit = doc.createElement("div");
    unit.className = "msg-unit";
    const div = doc.createElement("div");
    div.className = "msg-divider";
    div.textContent = label;
    unit.appendChild(div);
    unit.appendChild(row);
    return unit;
  };
}

function plainBuild(rec) {
  const doc = new FakeDocument();
  const row = doc.createElement("div");
  row.className = "msg-row " + (rec.who || "them");
  row.textContent = rec.text;
  return row;
}

function setup(build, opts = {}) {
  const doc = new FakeDocument();
  const host = doc.createElement("div");
  const typing = doc.createElement("div");
  typing.className = "msg-row hidden";
  host.appendChild(typing);
  const scroll = doc.createElement("div");
  Pager.mount(Object.assign({ host, typing, scroll, build, doc }, opts));
  return { host, scroll };
}

/** 渲染窗口的第一行（host.children[0] 是顶栏占位） */
function firstRow(host) {
  return host.children[1];
}

/* ==================== 1. 分组规则 ==================== */

test("同分钟 / 5 分钟内不重复插分隔，满 5 分钟才出一条", () => {
  const { host } = setup(dividerBuild());
  const base = MIDNIGHT + 14 * 60 * MIN;   // 14:00
  const recs = [
    { text: "a", who: "them", at: base },
    { text: "b", who: "me", at: base + 30 * 1000 },
    { text: "c", who: "them", at: base + 4 * MIN },
    { text: "d", who: "me", at: base + 9 * MIN },
    { text: "e", who: "them", at: base + 10 * MIN }
  ];
  for (const r of recs) Pager.push(r);
  const units = host.findAll("msg-unit");
  assert.equal(units.length, 1, "只有 d 与前一条差满 5 分钟");
  assert.equal(units[0].children[0].textContent, "14:09");
  assert.equal(host.findAll("msg-divider").length, 1);
});

test("窗口持续滑动：每行最多一条分隔，标签与自身时刻一致、不重复不错位", () => {
  const calls = [];
  const { host } = setup(dividerBuild(calls));
  const base = MIDNIGHT + 13 * 60 * MIN;
  for (let i = 0; i < 40; i++) {
    Pager.push({ text: "m" + i, who: i % 2 ? "me" : "them", at: base + i * 10 * MIN });
  }
  const windowOf = () => host.findAll("msg-unit").map(u => u.children[0].textContent);
  const labels = windowOf();
  assert.equal(labels.length, Pager.VIEW, "窗口首行也拿到窗口外的前一条，14 行都有分隔");
  assert.equal(new Set(labels).size, labels.length, "10 分钟一档，标签互不重复");
  assert.equal(host.findAll("msg-divider").length, Pager.VIEW, "没有重复插入");
  // 增量滑窗再推 5 条：旧分隔随行一起移出，不会叠加
  for (let i = 40; i < 45; i++) Pager.push({ text: "m" + i, who: "them", at: base + i * 10 * MIN });
  assert.equal(host.findAll("msg-divider").length, Pager.VIEW, "滑动后分隔数仍等于行数");
  // 最后一行走增量分支，prevAt 必须是前一条记录的时间
  const last = calls.filter(c => c.i === 44).pop();
  assert.ok(last, "最后一行被重建过");
  assert.equal(last.prevAt, base + 43 * 10 * MIN, "增量分支也带上一条时间戳");
});

/* ==================== 2. 分页边界 ==================== */

test("翻页边界：窗口首条与前一条同一天 5 分钟内 → 合并，不插分隔", () => {
  const calls = [];
  const { host } = setup(dividerBuild(calls));
  const base = MIDNIGHT + 15 * 60 * MIN;
  for (let i = 0; i < 16; i++) {
    Pager.push({ text: "m" + i, who: "them", at: base + i * 3 * MIN });
  }
  assert.equal(Pager.stats().start, 2, "窗口已滑到 [2,16)");
  const first = firstRow(host);
  assert.ok(first.className.includes("msg-row"), "首条直接是消息行，没有多余分隔");
  assert.equal(host.findAll("msg-divider").length, 0);
  const built = calls.filter(c => c.i === 2).pop();
  assert.equal(built.prevAt, base + 1 * 3 * MIN, "首条的参照是窗口外的 m1");
});

test("翻页边界：窗口首条跨天 → 带上「今天 HH:MM」", () => {
  const { host } = setup(dividerBuild());
  const recs = [
    { text: "y0", who: "them", at: MIDNIGHT - 20 * MIN },
    { text: "y1", who: "them", at: MIDNIGHT - 1 * MIN }
  ];
  for (let i = 0; i < 14; i++) {
    recs.push({ text: "t" + i, who: "them", at: MIDNIGHT + (2 + i * 10) * MIN });
  }
  for (const r of recs) Pager.push(r);
  assert.equal(Pager.stats().start, 2);
  const first = firstRow(host);
  assert.equal(first.className, "msg-unit");
  assert.equal(first.children[0].textContent, "今天 00:02");
});

test("加载更早一页：首条与前一条同一天 5 分钟内 → 合并", async () => {
  const calls = [];
  const older = [
    { text: "p0", who: "them", at: MIDNIGHT + 45 * MIN },
    { text: "p1", who: "them", at: MIDNIGHT + 48 * MIN },
    { text: "p2", who: "them", at: MIDNIGHT + 51 * MIN },
    { text: "p3", who: "them", at: MIDNIGHT + 57 * MIN }
  ];
  const { host } = setup(dividerBuild(calls), { loader: () => Promise.resolve(older) });
  for (let i = 0; i < 14; i++) {
    Pager.push({ text: "m" + i, who: "them", at: MIDNIGHT + (60 + i * 3) * MIN });
  }
  assert.equal(await Pager.older(), true);
  assert.equal(Pager.stats().start, 4, "更早的一页接在窗口上方");
  const first = firstRow(host);
  assert.ok(first.className.includes("msg-row"), "同一天且只差 3 分钟：合并成一段");
  assert.equal(host.findAll("msg-divider").length, 0);
  const built = calls.filter(c => c.i === 4).pop();
  assert.equal(built.prevAt, MIDNIGHT + 57 * MIN, "首条用新接上来的 p3 作参照");
});

test("加载更早一页：首条跨天 → 「今天 HH:MM」而不是错位到别的日期", async () => {
  const older = [
    { text: "p0", who: "them", at: MIDNIGHT - 19 * MIN },
    { text: "p1", who: "them", at: MIDNIGHT - 10 * MIN },
    { text: "p2", who: "them", at: MIDNIGHT - 1 * MIN }
  ];
  const { host } = setup(dividerBuild(), { loader: () => Promise.resolve(older) });
  for (let i = 0; i < 14; i++) {
    Pager.push({ text: "m" + i, who: "them", at: MIDNIGHT + (2 + i * 10) * MIN });
  }
  assert.equal(await Pager.older(), true);
  assert.equal(Pager.stats().start, 3);
  const first = firstRow(host);
  assert.equal(first.className, "msg-unit");
  assert.equal(first.children[0].textContent, "今天 00:02");
});

/* ==================== 3. 状态区出现后贴底 ==================== */

test("状态条出现：原本贴底时 followBottom 立即回底，最新一行不被顶出", () => {
  const { scroll } = setup(plainBuild);
  for (let i = 0; i < 14; i++) Pager.push({ text: "m" + i, who: "them", at: MIDNIGHT + i });
  scroll.scrollHeight = 900;
  scroll.clientHeight = 400;
  scroll.scrollTop = 500;       // 贴底：900 - 500 - 400 = 0
  scroll.onscroll();            // 用户没有上滑，pinned 保持 true
  scroll.clientHeight = 340;    // 状态条出现 → 对话区少了 60px
  assert.ok(scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight > 48,
    "布局变化后确实被顶出了半行以上");
  assert.equal(Pager.followBottom(), true);
  assert.equal(scroll.scrollTop, scroll.scrollHeight, "视口贴回底部");
});

test("用户主动上滑：状态条出现也不打断阅读，不强行拉回", () => {
  const { scroll } = setup(plainBuild);
  for (let i = 0; i < 14; i++) Pager.push({ text: "m" + i, who: "them", at: MIDNIGHT + i });
  scroll.scrollHeight = 900;
  scroll.clientHeight = 400;
  scroll.scrollTop = 500;
  scroll.onscroll();            // 仍在底部
  scroll.scrollTop = 100;       // 用户往上看历史
  scroll.onscroll();
  scroll.clientHeight = 340;    // 状态条出现
  assert.equal(Pager.followBottom(), false, "pinned=false 时不抢滚动位置");
  assert.equal(scroll.scrollTop, 100, "阅读位置保持不变");
  // 用户自己滚回底部后，跟随能力恢复
  scroll.scrollTop = 560;
  scroll.onscroll();
  assert.equal(Pager.followBottom(), true);
  assert.equal(scroll.scrollTop, scroll.scrollHeight);
});

test("没有滚动容器时 followBottom 安全返回 false", () => {
  setup(plainBuild, { scroll: null });
  assert.equal(Pager.followBottom(), false);
});

test("app.js 把状态区高度变化接在 ResizeObserver 上并调用 followBottom", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "js", "app.js"), "utf8");
  assert.match(app, /new ResizeObserver/);
  assert.match(app, /\$\("statusStack"\)/);
  assert.match(app, /ChatPager\.followBottom/);
});
