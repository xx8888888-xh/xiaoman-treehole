const test = require("node:test");
const assert = require("node:assert");

const Appearance = require("../js/appearance.js");

function fakeStore(initial) {
  const map = Object.assign({}, initial || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(map, k) ? map[k] : null),
    setItem: (k, v) => { map[k] = String(v); },
    _map: map,
  };
}

test("首次启动默认极简：零动画 + 大字号 + 灰阶 + 扁平", () => {
  const p = Appearance.normalize(null);
  assert.equal(p.preset, "minimal");
  assert.equal(p.motion, false);
  assert.equal(p.font, "large");
  assert.equal(p.iconsGray, true);
  assert.equal(p.flat, true);
});

test("三档预设解析：默认/生动各自的开关映射", () => {
  const d = Appearance.resolvePreset("default");
  assert.deepEqual(d, { preset: "default", motion: false, font: "normal", iconsGray: true, flat: false });
  const l = Appearance.resolvePreset("lively");
  assert.equal(l.motion, true);
  assert.equal(l.font, "normal");
  assert.equal(l.iconsGray, false);
  assert.equal(l.flat, false);
});

test("非法预设回落极简；非法字号回落标准（在自定义里）", () => {
  assert.equal(Appearance.resolvePreset("nope").preset, "minimal");
  const p = Appearance.normalize({ preset: "custom", font: "huge", motion: true });
  assert.equal(p.preset, "custom");
  assert.equal(p.font, "normal");
  assert.equal(p.motion, true);
});

test("改任一开关都进入自定义，且不就地改写预设", () => {
  const live = Appearance.resolvePreset("lively");
  const off = Appearance.withSwitch(live, "motion", false);
  assert.equal(off.preset, "custom");
  assert.equal(off.iconsGray, false);   // 预设的其他值保留
  assert.equal(off.motion, false);
  const big = Appearance.withSwitch(live, "font", "xlarge");
  assert.equal(big.font, "xlarge");
  assert.equal(big.preset, "custom");
  // 原对象未被改写
  assert.equal(live.motion, true);
});

test("存储读写：预设切换后刷新仍保持；损坏 JSON 回落极简", () => {
  const store = fakeStore();
  Appearance.save(Appearance.resolvePreset("lively"), store);
  const read = Appearance.read(store);
  assert.equal(read.preset, "lively");
  assert.equal(read.motion, true);
  assert.equal(read.iconsGray, false);

  store.setItem(Appearance.KEY, "{oops");
  assert.equal(Appearance.read(store).preset, "minimal");
});

test("兼容旧版单独的字号设置：保留字号，其余走极简", () => {
  const store = fakeStore({ xiaoman_fs: "xlarge" });
  const p = Appearance.read(store);
  assert.equal(p.preset, "custom");
  assert.equal(p.font, "xlarge");
  assert.equal(p.motion, false);
  assert.equal(p.iconsGray, true);
});
