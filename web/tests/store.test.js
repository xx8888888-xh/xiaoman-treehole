/* store.test.js · ChatStore 历史分页游标专项
 * 覆盖：空历史、恰好 30、31、300、同毫秒多条、游标到底、prune 上限。
 * 用内存 IndexedDB 桩（idb_stub.js）验证 (at,id) 双键游标不重不漏。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const { FakeIndexedDB, FakeKeyRange } = require("./idb_stub.js");

global.indexedDB = new FakeIndexedDB();
global.IDBKeyRange = FakeKeyRange;
const Store = require("../js/store.js");

let sidSeq = 0;
const nextSid = () => "s" + (++sidSeq);

async function seed(sid, n, atOf) {
  for (let i = 0; i < n; i++) {
    await Store.append(sid, {
      role: i % 2 ? "assistant" : "user",
      content: "m" + i,
      at: atOf ? atOf(i) : 1000 + i
    });
  }
}

/** 从最近 30 条开始，按 10 条一页向上翻到底；返回每页内容（升序） */
async function pageBack(sid, firstLimit, pageSize) {
  const pages = [];
  const first = await Store.list(sid, firstLimit);
  pages.push(first);
  let cursor = first.length ? { at: first[0].at, id: first[0].id } : null;
  while (cursor) {
    const older = await Store.listBefore(sid, pageSize, cursor);
    if (!older.length) break;
    pages.unshift(older);
    const firstOld = older[0];
    cursor = { at: firstOld.at, id: firstOld.id };
  }
  return pages;
}

test("空历史：list / listBefore / count 都返回空", async () => {
  const sid = nextSid();
  assert.deepEqual(await Store.list(sid, 30), []);
  assert.deepEqual(await Store.listBefore(sid, 10, { at: 1000, id: 1 }), []);
  assert.deepEqual(await Store.listBefore(sid, 10, null), []);
  assert.equal(await Store.count(sid), 0);
});

test("恰好 30 条：首屏一页装下，再往上没有更早的了", async () => {
  const sid = nextSid();
  await seed(sid, 30);
  const msgs = await Store.list(sid, 30);
  assert.equal(msgs.length, 30);
  assert.equal(msgs[0].content, "m0");
  assert.equal(msgs[29].content, "m29");
  assert.ok(msgs.every((m, i) => i === 0 || m.id > msgs[i - 1].id), "升序且带自增 id");
  const older = await Store.listBefore(sid, 10, { at: msgs[0].at, id: msgs[0].id });
  assert.equal(older.length, 0, "30 条时游标已到最旧");
  assert.equal(await Store.count(sid), 30);
});

test("31 条：首屏 30 条 + 上翻恰好取到第 31 条（不重复）", async () => {
  const sid = nextSid();
  await seed(sid, 31);
  const msgs = await Store.list(sid, 30);
  assert.equal(msgs[0].content, "m1");
  const older = await Store.listBefore(sid, 10, { at: msgs[0].at, id: msgs[0].id });
  assert.equal(older.length, 1);
  assert.equal(older[0].content, "m0");
  assert.equal(older[0].id, msgs[0].id - 1, "同一会话内自增 id 连续（前一条）");
  const again = await Store.listBefore(sid, 10, { at: older[0].at, id: older[0].id });
  assert.equal(again.length, 0);
  const pages = await pageBack(sid, 30, 10);
  const flat = pages.flat();
  assert.equal(flat.length, 31);
  assert.deepEqual(flat.map(m => m.content), Array.from({ length: 31 }, (_, i) => "m" + i));
});

test("300 条：30 → 300 分页不重、不跳、不丢（按时间升序闭合）", async () => {
  const sid = nextSid();
  await seed(sid, 300);
  const pages = await pageBack(sid, 30, 10);
  const flat = pages.flat();
  assert.equal(flat.length, 300);
  assert.deepEqual(flat.map(m => m.content), Array.from({ length: 300 }, (_, i) => "m" + i));
  const ids = flat.map(m => m.id);
  assert.equal(new Set(ids).size, 300, "id 无重复");
  for (let i = 1; i < ids.length; i++) assert.equal(ids[i], ids[i - 1] + 1, "同会话 id 连续无跳条");
  for (let i = 1; i < flat.length; i++) {
    assert.ok(flat[i].at >= flat[i - 1].at, "严格按 at 升序（同毫秒由 id 兜底）");
  }
});

test("同一毫秒 40 条：双键游标逐条翻出，不重不漏", async () => {
  const sid = nextSid();
  await seed(sid, 40, () => 5000);
  const recent = await Store.list(sid, 30);
  assert.equal(recent.length, 30);
  for (let i = 1; i < recent.length; i++) {
    assert.ok(recent[i].id > recent[i - 1].id, "同毫秒按自增 id 升序");
  }
  const pages = await pageBack(sid, 30, 10);
  const flat = pages.flat();
  assert.equal(flat.length, 40);
  assert.deepEqual(flat.map(m => m.content), Array.from({ length: 40 }, (_, i) => "m" + i));
  assert.equal(new Set(flat.map(m => m.id)).size, 40);
});

test("游标缺 id 的退化分支：只返回严格更早的时间（不把游标自身带回）", async () => {
  const sid = nextSid();
  await seed(sid, 12, () => 7000);
  const res = await Store.listBefore(sid, 10, { at: 7000 });
  assert.equal(res.length, 0, "同一毫秒没有严格更早的记录");
  // 时间不同：能正常翻到上一毫秒
  const sid2 = nextSid();
  await seed(sid2, 6, i => 7000 + i);
  const res2 = await Store.listBefore(sid2, 10, { at: 7003 });
  assert.deepEqual(res2.map(m => m.content), ["m0", "m1", "m2"]);
});

test("listBefore 无游标：返回最旧的一页（升序）", async () => {
  const sid = nextSid();
  await seed(sid, 20);
  const oldest = await Store.listBefore(sid, 10, null);
  assert.deepEqual(oldest.map(m => m.content), Array.from({ length: 10 }, (_, i) => "m" + i));
});

test("prune 上限 300：只删最旧的，游标尾部仍可翻到", async () => {
  const sid = nextSid();
  await seed(sid, 320);
  assert.equal(await Store.count(sid), 320);
  const removed = await Store.prune(sid, Store.MAX_KEEP);
  assert.equal(removed, 20);
  assert.equal(await Store.count(sid), 300);
  const msgs = await Store.list(sid, 30);
  assert.equal(msgs[29].content, "m319");
  assert.equal(msgs[0].content, "m290");
});

test("单条删除/编辑：remove / update / removeMatch 只动目标那条", async () => {
  const sid = nextSid();
  await seed(sid, 4);
  const before = await Store.list(sid, 30);
  const target = before[2];
  const total = await Store.count(sid);
  assert.equal(await Store.remove(target.id), true);
  assert.equal(await Store.count(sid), total - 1);
  const rest = await Store.list(sid, 30);
  assert.equal(rest.length, 3);
  assert.ok(!rest.some(m => m.id === target.id), "被删的不能留在库里");
  assert.equal(await Store.remove(target.id), false, "重复删除返回 false，不误删别的");

  assert.equal(await Store.update(rest[0].id, "改过的"), true);
  const after = await Store.list(sid, 30);
  assert.equal(after[0].content, "改过的");
  assert.equal(after[0].at, rest[0].at, "编辑保留时间与游标键");

  assert.equal(await Store.removeMatch(sid, rest[1].at, rest[1].content), true);
  assert.equal(await Store.count(sid), 2);
});
