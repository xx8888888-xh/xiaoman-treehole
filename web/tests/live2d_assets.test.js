/* live2d_assets.test.js · 模型资产与接入契约一致性
 *  防回归：层级顺序/口型 7 档/纹理档/兜底图/授权，任何缺文件都会在这里先红 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "assets", "live2d", "original");
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "manifest.json"), "utf8"));

const layerFiles = () => {
  const out = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (typeof node.file === "string") { out.push(node.file); return; }
    for (const v of Object.values(node)) walk(v);
  };
  walk(manifest.layers);
  return out;
};

test("清单画布与颈部锚点符合 original 契约（1235×2841 / 618,550）", () => {
  assert.equal(manifest.canvas.width, 1235);
  assert.equal(manifest.canvas.height, 2841);
  assert.equal(manifest.pivot.x, 618);
  assert.equal(manifest.pivot.y, 550);
});

test("口型为 7 档且顺序是 closed → open_l", () => {
  const levels = manifest.layers.mouthLevels;
  assert.equal(levels.length, 7, levels.join(","));
  assert.deepEqual(levels, ["closed", "open_xs", "open_s", "open_ms", "open_m", "open_ml", "open_l"]);
  for (const k of levels) assert.ok(manifest.layers.mouth[k], "缺嘴型层 " + k);
});

test("必需层级齐全：身体/羽织/头/独立前发/双眼三帧", () => {
  assert.ok(manifest.layers.body);
  assert.ok(manifest.layers.haori);
  assert.ok(manifest.layers.head);
  assert.ok(manifest.layers.hair_front, "缺独立 hair_front");
  for (const side of ["eye_l", "eye_r"]) {
    for (const frame of ["open", "half", "closed"]) assert.ok(manifest.layers[side][frame], side + "." + frame);
  }
});

test("两层纹理档与解码字节数（选档预算口径一致）", () => {
  assert.equal(manifest.tiers.s050.decoded, 5001068);
  assert.equal(manifest.tiers.s070.decoded, 9791028);
  assert.equal(manifest.tiers.s050.path, "art/tiers/s050/");
  assert.equal(manifest.tiers.s070.path, "art/tiers/s070/");
});

test("基准层 + 两档纹理文件全部存在（档内路径与基准同名）", () => {
  for (const rel of layerFiles()) {
    assert.ok(fs.existsSync(path.join(ROOT, rel)), "缺基准纹理 " + rel);
    for (const tier of ["s050", "s070"]) {
      const name = path.basename(rel);
      const tierPath = path.join(ROOT, "art", "tiers", tier, "layers", name);
      assert.ok(fs.existsSync(tierPath), "缺 " + tier + " 纹理 " + name);
    }
  }
});

test("三级兜底资产存在：full / silhouette(含纯色声明) / thumb", () => {
  assert.ok(fs.existsSync(path.join(ROOT, manifest.art.full)), "缺 full.png");
  assert.ok(fs.existsSync(path.join(ROOT, manifest.fallback.silhouette)), "缺 silhouette.png");
  assert.equal(manifest.fallback.color, "#2E6E74");
  assert.ok(fs.existsSync(path.join(ROOT, manifest.art.thumb)), "缺 thumb.png");
});

test("授权与来源登记：图像 CC-BY-4.0 / 引擎 MIT", () => {
  assert.equal(manifest.license.art, "CC-BY-4.0");
  assert.equal(manifest.license.code, "MIT");
  assert.equal(manifest.edition, "open");
  assert.ok(fs.existsSync(path.join(ROOT, "ATTRIBUTION.txt")));
});
