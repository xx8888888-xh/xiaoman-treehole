/* errors.test.js · 边界错误必须"说人话 + 给行动"，不能白屏/卡死 */
const test = require("node:test");
const assert = require("node:assert/strict");

global.window = globalThis;
const XMErrors = require("../js/errors.js");

test("429：说明限流且自动排队，可重试", () => {
  const d = XMErrors.describe({ status: 429 });
  assert.equal(d.kind, "limit");
  assert.match(d.title, /限流/);
  assert.match(d.hint, /排队|等/);
  assert.equal(d.retry, true);
});

test("5xx：明确是服务端问题，不吓用户", () => {
  const d = XMErrors.describe({ status: 500 });
  assert.equal(d.kind, "server");
  assert.match(d.title, /500/);
  assert.match(d.hint, /排队|本机/);

  const d503 = XMErrors.describe({ res: { status: 503 } });
  assert.equal(d503.kind, "server", "也支持 Response 挂在 res 上");
});

test("401/403：指向设置里的 Key，而不是笼统失败", () => {
  const d = XMErrors.describe({ status: 401 });
  assert.equal(d.kind, "auth");
  assert.match(d.hint, /Key/);
  assert.equal(d.retry, false, "Key 不对时重试没意义");
  assert.equal(XMErrors.describe({ status: 403 }).kind, "auth");
});

test("断网 / 超时 / 404 各有独立说法", () => {
  assert.equal(XMErrors.describe(new TypeError("Failed to fetch")).kind, "network");
  assert.match(XMErrors.describe(new TypeError("Failed to fetch")).hint, /自动重发/);
  const abort = new Error("timeout"); abort.name = "AbortError";
  assert.equal(XMErrors.describe(abort).kind, "timeout");
  assert.equal(XMErrors.describe({ status: 404 }).kind, "notfound");
});

test("模型坏 JSON：降级但不打断，不要求重试", () => {
  const d = XMErrors.describe({ kind: "parse" });
  assert.equal(d.kind, "parse");
  assert.equal(d.retry, false);
  assert.match(d.hint, /兜底|不会断/);
});

test("存储满：说清楚怎么救（先备份再清）", () => {
  const quota = new Error("boom"); quota.name = "QuotaExceededError";
  assert.equal(XMErrors.isQuota(quota), true);
  assert.equal(XMErrors.isQuota({ code: 22 }), true);
  assert.equal(XMErrors.isQuota(new Error("other")), false);
  const d = XMErrors.describe(quota);
  assert.equal(d.kind, "quota");
  assert.match(d.hint, /备份/);
  assert.match(XMErrors.storageText("提醒"), /提醒/, "提示里要指出是哪块没存上");
});

test("IndexedDB 读不出来：给重试入口，不白屏", () => {
  const d = XMErrors.describe({ kind: "idb" });
  assert.equal(d.kind, "idb");
  assert.equal(d.retry, true);
  assert.match(d.hint, /重试/);
});

test("未知异常也有兜底文案（永不为空）", () => {
  for (const e of [undefined, null, "", {}, new Error("???")]) {
    const d = XMErrors.describe(e);
    assert.ok(d.title && d.hint, "任何异常都要有 title + hint");
  }
});
