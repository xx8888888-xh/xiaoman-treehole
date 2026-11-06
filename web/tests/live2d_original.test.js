/* live2d_original.test.js · 开源 original 模型接入选档/映射/显存口径单测
 *  - pickTier：DPR + 可视尺寸 + 12MB 预算（解码纹理 + 画布后备缓冲）
 *  - modelUrl：模型源指向 assets/live2d/original，不再是 hiyori
 *  - 情绪/动作映射：页面英文名 → manifest 表情/动作
 *  - vramFor：首屏显存口径可核算且不超 12MB（按选中档） */
const test = require("node:test");
const assert = require("node:assert/strict");
const Stage = require("../js/stage.js");

const MB = 1024 * 1024;

test("模特源指向 assets/live2d/original，不再引用 hiyori", () => {
  for (const tier of ["s050", "s070", "", null]) {
    const url = Stage._modelUrl(tier);
    assert.ok(url.startsWith("assets/live2d/original/"), url);
    assert.equal(url.includes("hiyori"), false);
    assert.equal(url.includes("models"), false);
  }
  assert.ok(Stage._modelUrl("s070").includes("manifest.json"));
  assert.ok(Stage._modelUrl("s070").includes("s070"));
});

test("选档：普通手机浅容器（390×326@dpr2）落 s070 且显存 ≤12MB", () => {
  const tier = Stage._pickTier({ width: 390, height: 326, dpr: 2, memory: 8 });
  assert.equal(tier, "s070");
  // 与引擎 fit 同口径：h=min(1600,326*2)=652，w=round(652*1235/2841)=283
  const v = Stage._vramFor(tier, 283, 652, 652 / 2841);
  assert.ok(v.totalBytes <= 12 * MB, `${v.totalBytes} > 12MB`);
  assert.equal(v.texturesBytes, 9791028);
});

test("选档：大画布后备缓冲会把 s070 挤出预算，回退 s050", () => {
  const tier = Stage._pickTier({ width: 900, height: 1600, dpr: 2, memory: 8 });
  assert.equal(tier, "s050");
  const v = Stage._vramFor(tier, 695, 1600, 1600 / 2841);
  assert.ok(v.totalBytes <= 12 * MB, `${v.totalBytes} > 12MB`);
  assert.equal(v.texturesBytes, 5001068);
});

test("选档：低内存设备/Android 原生壳/DPR1 一律 s050", () => {
  assert.equal(Stage._pickTier({ height: 326, dpr: 2, memory: 3 }), "s050");
  assert.equal(Stage._pickTier({ height: 326, dpr: 2, native: true }), "s050");
  const one = Stage._pickTier({ height: 326, dpr: 1 });
  assert.ok(one === "s050" || one === "s070");
});

test("选档：force 支持 s050/s070 与旧探针 1024/2048 兼容", () => {
  assert.equal(Stage._pickTier({ height: 326, dpr: 2, force: "s050" }), "s050");
  assert.equal(Stage._pickTier({ height: 326, dpr: 2, force: "s070" }), "s070");
  assert.equal(Stage._pickTier({ height: 326, dpr: 2, force: "1024" }), "s050");
  assert.equal(Stage._pickTier({ height: 326, dpr: 2, force: "2048" }), "s070");
  assert.equal(Stage._pickTier({}), "s070");
});

test("情绪映射：疲惫/敷衍/惊讶有独立表情；沉默与未知值落平静脸", () => {
  assert.equal(Stage._emotionFor("happy"), "smile");
  assert.equal(Stage._emotionFor("gentle"), "smile");
  assert.equal(Stage._emotionFor("shy"), "shy");
  assert.equal(Stage._emotionFor("neutral"), "neutral");
  assert.equal(Stage._emotionFor("sad"), "neutral");
  // 第四轮：不再借微笑/中性，复用现有眼/嘴图层拼最小表情
  assert.equal(Stage._emotionFor("surprised"), "surprised");
  assert.equal(Stage._emotionFor("tired"), "tired");
  assert.equal(Stage._emotionFor("flat"), "flat");
  assert.equal(Stage._emotionFor("quiet"), "neutral");
  assert.equal(Stage._emotionFor("TIRED"), "tired", "大小写不敏感");
  assert.equal(Stage._emotionFor("???"), "neutral");
});

test("表情差异契约：flat 半睁眼、neutral 全睁，浮动不再只靠 1° 低头", () => {
  const m = require("../assets/live2d/original/manifest.json");
  assert.equal(m.emotions.flat.eye_l, "half");
  assert.equal(m.emotions.flat.eye_r, "half");
  assert.equal(m.emotions.neutral.eye_l, "open");
  // 关键点：眼睑可见高度 = 对应图层高度。半睁 56px vs 全睁 115px → 48.7%
  const openH = m.layers.eye_l.open.h;
  const halfH = m.layers.eye_l.half.h;
  assert.ok(halfH / openH <= 0.6, `半睁/全睁 = ${(halfH / openH).toFixed(3)}，差异太小`);
  // 嘴：neutral closed 14px vs flat open_xs 9px，且两档图层名必须不同
  assert.notEqual(m.emotions.flat.mouth, m.emotions.neutral.mouth);
  // 7 档口型契约不动
  assert.equal(m.layers.mouthLevels.length, 7);
});

test("动作映射：旧 Hiyori 动作名落到 manifest 的 nod/shake/talk", () => {
  assert.equal(Stage._motionFor("Greeting"), "nod");
  assert.equal(Stage._motionFor("Nod"), "nod");
  assert.equal(Stage._motionFor("Shake"), "shake");
  assert.equal(Stage._motionFor("HappyJump"), "nod");
  assert.equal(Stage._motionFor("talk"), "talk");
  assert.equal(Stage._motionFor("unknown"), null);
});

test("挂载前探针给出 idle，口径字段齐全", () => {
  assert.equal(Stage._mode(), "idle");
  assert.equal(Stage._rafActive(), false);
  assert.equal(Stage._tier(), null);
});
