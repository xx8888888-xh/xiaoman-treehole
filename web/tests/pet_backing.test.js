const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const Pet = require("../js/pet_core.js");

const PET_JS = fs.readFileSync(path.join(__dirname, "..", "js", "pet.js"), "utf8");
const ART_RATIO = 469 / 1080;   // 桌宠立绘宽高比（manifest canvas）

/** 与 pet.js 一致：CSS 高 = 300% 窗口，后备高按 dpr 取整（可选 vendor 的 dpr 上限 2） */
function geom(stageW, stageH, dpr, cap) {
  const d = cap === undefined ? dpr : Math.min(cap, dpr);
  const cssH = stageH * 3;
  const fullH = Math.max(240, Math.min(1600, Math.round(cssH * d)));
  const fullW = Math.max(120, Math.round(fullH * ART_RATIO));
  const canvasCssW = cssH * fullW / fullH;
  return {
    fullW, fullH,
    canvasCssW, canvasCssH: cssH,
    windowCssW: stageW, windowCssH: stageH,
    canvasTop: 8, dpr: fullH / cssH, margin: 2,
  };
}

/** 与实现独立的可见区复算（后备像素，未取整） */
function visibleOf(g) {
  const k = g.fullH / g.canvasCssH;
  const vw = Math.min(g.canvasCssW, g.windowCssW);
  const vh = Math.min(g.canvasCssH, Math.max(0, g.windowCssH - g.canvasTop));
  const left = (g.canvasCssW - vw) / 2;
  return { x: left * k, y: 0, w: vw * k, h: vh * k };
}

const WINDOWS = [[140, 180], [180, 180], [240, 160]];
const DPRS = [1, 1.5, 2, 2.75, 3];

test("backingRect：3 档窗型 × 5 档 dpr 均完整包含可见区且尺寸为正", () => {
  for (const [w, h] of WINDOWS) {
    for (const dpr of DPRS) {
      for (const cap of [undefined, 2]) {
        const g = geom(w, h, dpr, cap);
        const b = Pet.backingRect(g);
        const vis = visibleOf(g);
        const tag = `${w}x${h}@${dpr}${cap ? "(cap2)" : ""}`;
        assert.ok(Number.isInteger(b.x) && b.x >= 0, `x 非法 ${tag}: ${b.x}`);
        assert.ok(Number.isInteger(b.y) && b.y >= 0, `y 非法 ${tag}: ${b.y}`);
        assert.ok(Number.isInteger(b.w) && b.w >= 1, `w 非法 ${tag}: ${b.w}`);
        assert.ok(Number.isInteger(b.h) && b.h >= 1, `h 非法 ${tag}: ${b.h}`);
        assert.ok(b.x + b.w <= b.fullW, `右边越界 ${tag}`);
        assert.ok(b.y + b.h <= b.fullH, `下边越界 ${tag}`);
        // 可见 rect 完全包含于 backing（含 floor/ceil 误差容差）
        assert.ok(vis.x >= b.x - 1e-9, `左边裁到可见区 ${tag}: ${b.x} < ${vis.x}`);
        assert.ok(vis.y >= b.y - 1e-9, `上边裁到可见区 ${tag}: ${b.y} < ${vis.y}`);
        assert.ok(vis.x + vis.w <= b.x + b.w + 1e-9, `右边裁到可见区 ${tag}: ${b.x + b.w} < ${vis.x + vis.w}`);
        assert.ok(vis.y + vis.h <= b.y + b.h + 1e-9, `下边裁到可见区 ${tag}: ${b.y + b.h} < ${vis.y + vis.h}`);
      }
    }
  }
});

test("backingRect：面积显著小于整块 3 倍画布，且不比 clip 多白画", () => {
  for (const [w, h] of WINDOWS) {
    for (const dpr of DPRS) {
      const g = geom(w, h, dpr, 2);          // 生产路径：vendor fit 的 dpr 上限 2
      const b = Pet.backingRect(g);
      const tag = `${w}x${h}@${dpr}`;
      const ratio = b.area / b.fullArea;
      assert.ok(ratio < 0.35, `后备面积应远小于整块画布 ${tag}: ${ratio}`);
      if (w === 140) assert.ok(ratio < 0.25, `140x180 应压到 25% 以内 ${tag}: ${ratio}`);
      // 与渲染 clip 一致：后备不应比 clip 多出一大圈
      const clip = Pet.clipRect({
        modelW: g.fullW, modelH: g.fullH,
        canvasW: g.canvasCssW, canvasH: g.canvasCssH,
        stageW: g.windowCssW, stageH: g.windowCssH,
        canvasTop: g.canvasTop, margin: g.margin,
      });
      assert.ok(b.area <= clip.w * clip.h * 1.01 + 4, `后备比 clip 多太多 ${tag}`);
      assert.strictEqual(b.fullArea, b.fullW * b.fullH);
      assert.strictEqual(b.area, b.w * b.h);
    }
  }
});

test("backingRect：1:1 映射后四角对齐，CSS 盒把后备原样投影回舞台", () => {
  const g = geom(140, 180, 2.75);
  const b = Pet.backingRect(g);
  const vis = visibleOf(g);
  const k = g.fullH / g.canvasCssH;
  const stageLeft = (g.windowCssW - g.canvasCssW) / 2;

  // 只平移不缩放：四角映射 = 原坐标 - 裁切原点
  assert.strictEqual(b.kx, 1);
  assert.strictEqual(b.ky, 1);
  assert.strictEqual(b.tx, -b.x);
  assert.strictEqual(b.ty, -b.y);
  const map = (x, y) => ({ x: b.kx * x + b.tx, y: b.ky * y + b.ty });
  assert.deepStrictEqual(map(b.x, b.y), { x: 0, y: 0 });
  assert.deepStrictEqual(map(b.x + b.w, b.y + b.h), { x: b.w, y: b.h });
  // 可见区四角在"旧整块画布坐标系"里仍落在同一舞台像素上
  for (const [x, y] of [
    [vis.x, vis.y], [vis.x + vis.w, vis.y],
    [vis.x, vis.y + vis.h], [vis.x + vis.w, vis.y + vis.h],
  ]) {
    const p = map(x, y);
    const stageBefore = stageLeft + x / k;
    const stageAfter = stageLeft + b.css.left + p.x / k;
    assert.ok(Math.abs(stageBefore - stageAfter) < 1e-9, `舞台坐标漂移 (${x},${y})`);
    assert.ok(p.x >= -1e-9 && p.x <= b.w + 1e-9, `横向落在后备外 (${x},${y})`);
    assert.ok(p.y >= -1e-9 && p.y <= b.h + 1e-9, `纵向落在后备外 (${x},${y})`);
  }
  // CSS 盒按 k 反投影，宽度/高度与后备严格一致（避免拉伸）
  assert.ok(Math.abs(b.css.width * b.k - b.w) < 1e-9, "CSS 宽与后备不一致");
  assert.ok(Math.abs(b.css.height * b.k - b.h) < 1e-9, "CSS 高与后备不一致");
  assert.ok(Math.abs(b.css.left * b.k - b.x) < 1e-9, "CSS left 与裁切原点不一致");
  assert.ok(Math.abs(b.css.top * b.k - b.y) < 1e-9, "CSS top 与裁切原点不一致");
});

test("backingRect：resize 重算幂等，切窗型后仍包含新可见区", () => {
  const g1 = geom(140, 180, 2);
  const a = Pet.backingRect(g1);
  const a2 = Pet.backingRect(g1);
  assert.deepStrictEqual(a2, a, "同尺寸重复计算应完全一致");

  const g2 = geom(180, 180, 2);
  const b = Pet.backingRect(g2);
  const vis = visibleOf(g2);
  assert.notStrictEqual(b.w, a.w, "窗口变宽后后备宽度应重算");
  assert.ok(vis.x >= b.x - 1e-9 && vis.x + vis.w <= b.x + b.w + 1e-9, "180x180 可见区应被包含");
  assert.ok(vis.y >= b.y - 1e-9 && vis.y + vis.h <= b.y + b.h + 1e-9, "180x180 可见区应被包含");

  const back = Pet.backingRect(geom(140, 180, 2));
  assert.deepStrictEqual(back, a, "尺寸回到原值后应得到同一结果");
});

test("backingRect：dpr 变化不产生负尺寸/零尺寸，且不超出画布", () => {
  for (const [w, h] of WINDOWS) {
    let prev = null;
    for (const dpr of DPRS) {
      const b = Pet.backingRect(geom(w, h, dpr));
      assert.ok(b.w > 0 && b.h > 0, `dpr=${dpr} 出现零/负尺寸`);
      assert.ok(Number.isFinite(b.w) && Number.isFinite(b.h), `dpr=${dpr} 出现非有限尺寸`);
      assert.ok(b.x + b.w <= b.fullW && b.y + b.h <= b.fullH, `dpr=${dpr} 越界`);
      if (prev) assert.ok(b.w >= 1 && b.h >= 1, "切换 dpr 后仍须有有效后备");
      prev = b;
    }
  }
  // 极端：窗口比 canvasTop 还矮 → 可见高 0，也必须保留 1px 以上
  const tiny = Pet.backingRect({
    fullW: 469, fullH: 1080, canvasCssW: 234.5, canvasCssH: 540,
    windowCssW: 140, windowCssH: 6, canvasTop: 8, dpr: 2, margin: 2,
  });
  assert.ok(tiny.w >= 1 && tiny.h >= 1, "零可见高度不应产生零后备");
});

test("backingRect：缺省/零几何退化为整块画布，不产生 NaN", () => {
  const empty = Pet.backingRect(null);
  assert.strictEqual(empty.w, 1);
  assert.strictEqual(empty.h, 1);
  assert.ok(Number.isFinite(empty.css.width) && Number.isFinite(empty.css.height));
  const zeroWin = Pet.backingRect({
    fullW: 469, fullH: 1080, canvasCssW: 234.5, canvasCssH: 540,
    windowCssW: 0, windowCssH: 0, dpr: 2,
  });
  assert.deepStrictEqual([zeroWin.x, zeroWin.y, zeroWin.w, zeroWin.h], [0, 0, 469, 1080]);
});

test("pet.js：后备裁切 + 变换 + resize/DPI 重建均已接线", () => {
  assert.ok(PET_JS.includes("core.backingRect("), "应调用 PetCore.backingRect");
  assert.ok(PET_JS.includes("ctx.setTransform("), "绘制前必须映射到新后备");
  assert.ok(PET_JS.includes("core.clipRect("), "取景 clip 语义应保留");
  assert.ok(PET_JS.includes("canvas.width = b.w"), "canvas.width 应设为 backing 宽");
  assert.ok(PET_JS.includes("window.ResizeObserver"), "应复用 ResizeObserver");
  assert.ok(PET_JS.includes('addEventListener("resize", onWindowResize)'), "应监听 window.resize");
  const debounce = PET_JS.match(/resizeTimer = setTimeout\([\s\S]*?\}, (\d+)\);/);
  assert.ok(debounce && Number(debounce[1]) >= 60, "resize 应 debounce");
  assert.ok(PET_JS.includes('scheduleRebuild("dpr")'), "DPI 变化应由兜底检测触发重建");
  assert.ok(PET_JS.includes("const CLIP_MARGIN_PX = 2;"), "2px 余量语义保留");
});
