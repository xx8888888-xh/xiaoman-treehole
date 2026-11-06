const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const Pet = require("../js/pet_core.js");

const PET_HTML = fs.readFileSync(path.join(__dirname, "..", "pet.html"), "utf8");
const PET_JS = fs.readFileSync(path.join(__dirname, "..", "js", "pet.js"), "utf8");
const PET_CSS = fs.readFileSync(path.join(__dirname, "..", "css", "pet.css"), "utf8");
const INDEX_HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const APP_JS = fs.readFileSync(path.join(__dirname, "..", "js", "app.js"), "utf8");
const SERVICE = fs.readFileSync(
  path.join(__dirname, "..", "..", "android", "app", "src", "main", "java", "com", "xiaoman", "treehole", "FloatWindowService.java"),
  "utf8"
);
const MANIFEST = fs.readFileSync(
  path.join(__dirname, "..", "..", "android", "app", "src", "main", "AndroidManifest.xml"),
  "utf8"
);

/* ---------- 外观映射 ---------- */

test("appearance：预设开关 → 桌宠开关（含安全缺省）", () => {
  assert.deepStrictEqual(Pet.appearance({ motion: true, font: "large", iconsGray: false, flat: true }), {
    motion: true,
    largeFont: true,
    grayIcons: false,
    flat: true,
  });
  assert.deepStrictEqual(Pet.appearance(null), {
    motion: false,
    largeFont: false,
    grayIcons: true,
    flat: false,
  });
  // 与 appearance.js normalize 一致：旧版 xlarge 归入大字号
  assert.strictEqual(Pet.appearance({ motion: false, font: "xlarge" }).largeFont, true);
});

/* ---------- 手势 ---------- */

test("gesture：位移优先判拖动，超时长判长按，其余为点击", () => {
  assert.strictEqual(Pet.gesture(1, -2, 100), "tap");
  assert.strictEqual(Pet.gesture(30, 0, 100), "drag");
  assert.strictEqual(Pet.gesture(0, -30, 100), "drag");
  assert.strictEqual(Pet.gesture(2, 2, Pet.LONG_MS), "long");
  assert.strictEqual(Pet.gesture(40, 40, Pet.LONG_MS), "drag");
});

test("isDoubleTap：仅有效的时间窗口内成立", () => {
  assert.strictEqual(Pet.isDoubleTap(0, 1000), false);
  assert.strictEqual(Pet.isDoubleTap(1000, 1200), true);
  assert.strictEqual(Pet.isDoubleTap(1000, 1000), false);
  assert.strictEqual(Pet.isDoubleTap(1000, 1500), false);
});

/* ---------- 提醒节流 ---------- */

test("reminderDecision：只挑到期待办，已完成的忽略", () => {
  const now = 10_000_000;
  const list = [
    { id: "a", text: "吃药", at: now - 5000, done: false },
    { id: "b", text: "早睡", at: now - 9000, done: true },
    { id: "c", text: "喝水", at: now + 60_000, done: false },
  ];
  const d = Pet.reminderDecision(list, now, {});
  assert.strictEqual(d.id, "a");
  assert.strictEqual(d.text, "吃药");
});

test("reminderDecision：全局冷却 / 单条冷却 / 每小时上限三重节流", () => {
  const now = 10_000_000;
  const due = [{ id: "a", text: "吃药", at: now - 1000 }];
  // 刚弹过 → 全局冷却
  assert.strictEqual(Pet.reminderDecision(due, now, { lastAt: now - 1000 }), null);
  // 同一条 10 分钟内不重复
  assert.strictEqual(Pet.reminderDecision(due, now, { ids: { a: now - 60_000 } }), null);
  // 但另一条可以
  const other = [{ id: "b", text: "喝水", at: now - 1000 }];
  assert.ok(Pet.reminderDecision(other, now, { ids: { a: now - 60_000 } }));
  // 一小时 3 条上限
  assert.strictEqual(
    Pet.reminderDecision(due, now, { lastAt: now - 200_000, recent: [now - 1000, now - 2000, now - 3000] }),
    null
  );
});

test("recordNotice：写入冷却状态并清理过期 id", () => {
  const now = 10_000_000;
  const next = Pet.recordNotice(
    { lastAt: now - 4_000_000, ids: { old: now - 4_000_000, keep: now - 1000 }, recent: [now - 4_000_000] },
    { id: "new" },
    now,
    { perIdCooldownMs: 10_000 }
  );
  assert.strictEqual(next.lastAt, now);
  assert.strictEqual(next.ids.new, now);
  assert.strictEqual(next.ids.old, undefined);
  assert.ok(next.ids.keep > 0);
  assert.ok(next.recent.includes(now));
  assert.ok(!next.recent.includes(now - 500_000));
});

test("suppressByPresence：主 App 90s 内活跃时让位，过期/缺失时接手", () => {
  const now = 10_000_000;
  assert.strictEqual(Pet.suppressByPresence(now - 10_000, now), true);
  assert.strictEqual(Pet.suppressByPresence(now - 120_000, now), false);
  assert.strictEqual(Pet.suppressByPresence(0, now), false);
});

/* ---------- 文案 / 布局 ---------- */

test("bubbleText：加提醒前缀并截断", () => {
  assert.strictEqual(Pet.bubbleText("吃药", 46), "提醒：吃药");
  assert.strictEqual(Pet.bubbleText("  多   喝\n水  ", 46), "提醒：多 喝 水");
  const long = Pet.bubbleText("一".repeat(80), 20);
  assert.ok(long.length <= 20);
  assert.ok(long.endsWith("…"));
});

test("clampPosition：不越出屏幕", () => {
  assert.deepStrictEqual(Pet.clampPosition(-30, -30, 200, 200, 720, 1280), { x: 0, y: 0 });
  assert.deepStrictEqual(Pet.clampPosition(999, 9999, 200, 200, 720, 1280), { x: 520, y: 1080 });
});

test("bubbleLayout：优先上方，顶部不够放下方，水平钳制", () => {
  const screen = { width: 1080, height: 2400 };
  const bubble = { width: 480, height: 120 };
  assert.deepStrictEqual(
    Pet.bubbleLayout({ x: 500, y: 500, width: 200, height: 200 }, screen, bubble, 10),
    { x: 360, y: 370 }
  );
  // 顶部空间不足 → 放到下方
  assert.deepStrictEqual(
    Pet.bubbleLayout({ x: 500, y: 40, width: 200, height: 200 }, screen, bubble, 10),
    { x: 360, y: 250 }
  );
  // 左侧越界 → 钳到 10
  assert.deepStrictEqual(
    Pet.bubbleLayout({ x: 0, y: 500, width: 100, height: 200 }, screen, bubble, 10),
    { x: 10, y: 370 }
  );
});

/* ---------- 取景 clip ---------- */

const ART_RATIO = 469 / 1080;   // 桌宠立绘宽高比（manifest canvas）

/** 按引擎 fit() 的规则造一个画布盒子（CSS 高 = 300% 窗口，dpr 上限 2） */
function floatBox(stageW, stageH, dpr, margin) {
  const cssH = stageH * 3;
  const modelH = Math.max(240, Math.min(1600, Math.round(cssH * Math.min(2, dpr))));
  return {
    modelW: Math.round(modelH * ART_RATIO), modelH,
    canvasW: cssH * ART_RATIO, canvasH: cssH,
    stageW, stageH, canvasTop: 8, margin,
  };
}

/** 与实现独立的"可见矩形"复算（后备像素，且不超出后备缓冲） */
function visibleOf(box) {
  const k = box.modelH / box.canvasH;
  const vw = Math.min(box.canvasW, box.stageW);
  const vh = Math.min(box.canvasH, Math.max(0, box.stageH - box.canvasTop));
  const left = (box.canvasW - vw) / 2;
  return {
    l: Math.max(0, Math.min(box.modelW, left * k)),
    t: 0,
    r: Math.min(box.modelW, (left + vw) * k),
    b: Math.min(box.modelH, vh * k),
  };
}

test("clipRect：可见区完整落在取景窗口内（四档窗型 × dpr）", () => {
  const windows = [[140, 180], [240, 160], [100, 120], [360, 640]];
  for (const [w, h] of windows) {
    for (const dpr of [1, 1.5, 2]) {
      const box = floatBox(w, h, dpr, 2);
      const clip = Pet.clipRect(box);
      const vis = visibleOf(box);
      const tag = `${w}x${h}@${dpr}`;
      assert.ok(clip.x <= vis.l, `左边裁到可见区 ${tag}: ${clip.x} < ${vis.l}`);
      assert.ok(clip.y <= vis.t, `上边裁到可见区 ${tag}`);
      assert.ok(clip.x + clip.w >= vis.r, `右边裁到可见区 ${tag}: ${clip.x + clip.w} < ${vis.r}`);
      assert.ok(clip.y + clip.h >= vis.b, `下边裁到可见区 ${tag}: ${clip.y + clip.h} < ${vis.b}`);
      // 余量 = 2px + floor/ceil 取整（最多 1px），不应再超出
      assert.ok(vis.l - clip.x <= 3 + 1e-9, `左边余量过大 ${tag}: ${vis.l - clip.x}`);
      assert.ok(clip.x + clip.w - vis.r <= 3 + 1e-9, `右边余量过大 ${tag}: ${clip.x + clip.w - vis.r}`);
      assert.ok(clip.y + clip.h - vis.b <= 3 + 1e-9, `下边余量过大 ${tag}: ${clip.y + clip.h - vis.b}`);
    }
  }
});

test("clipRect：2px 余量相比 6px 明显收紧，可见区外 < 3%", () => {
  const box = floatBox(140, 180, 2, 2);
  const vis = visibleOf(box);
  const visArea = (vis.r - vis.l) * vis.b;
  const tight = Pet.clipRect(box);
  // 旧实现（第四轮实测版）：round 取整、左右各 6px、上下各 2*6px
  const legacy = (function (b, margin) {
    const k = b.modelH / b.canvasH;
    const vw = Math.min(b.canvasW, b.stageW);
    const vh = Math.min(b.canvasH, b.stageH - b.canvasTop);
    const x = Math.max(0, Math.round(((b.canvasW - vw) / 2) * k) - margin);
    return {
      x: x,
      w: Math.min(b.modelW - x, Math.round(vw * k) + margin * 2),
      h: Math.min(b.modelH, Math.round(vh * k) + margin * 2),
    };
  })(box, 6);
  const tightArea = tight.w * tight.h;
  const legacyArea = legacy.w * legacy.h;
  // 旧 6px 余量白画的面积约 8%（实测 rasterOutside 7.3%），新 2px 余量压到 3% 以内
  assert.ok(legacyArea - visArea > visArea * 0.07, `旧余量外溢 ${(legacyArea - visArea) / visArea}`);
  assert.ok(tightArea - visArea < visArea * 0.03, `新余量外溢 ${(tightArea - visArea) / visArea}`);
  assert.ok(tightArea < legacyArea - visArea * 0.05, "收紧后光栅面积应至少降 5%");
});

test("clipRect：尺寸缺失时退化为整块画布（保持旧行为）", () => {
  assert.deepStrictEqual(
    Pet.clipRect({ modelW: 469, modelH: 1080, canvasW: 0, canvasH: 0, stageW: 0, stageH: 0 }),
    { x: 0, y: 0, w: 469, h: 1080 }
  );
  assert.deepStrictEqual(Pet.clipRect(null), { x: 0, y: 0, w: 0, h: 0 });
});

test("interactionFor：头部/身体命中区与稳定文案", () => {
  const head = Pet.interactionFor(0.2, 0);
  assert.strictEqual(head.area, "head");
  assert.strictEqual(head.emotion, "shy");
  assert.strictEqual(head.motion, "Shake");
  assert.strictEqual(head.line, "诶嘿，被你发现了");

  const body = Pet.interactionFor(0.9, 0);
  assert.strictEqual(body.area, "body");
  assert.strictEqual(body.emotion, "surprised");
  assert.strictEqual(body.motion, "HappyJump");
  assert.strictEqual(body.line, "痒——");
});

/* ---------- 资产与静音/外观/权限契约（静态检查） ---------- */

test("pet 页面：引入核心脚本、外观预设与提醒；不引入 TTS/音频", () => {
  assert.ok(PET_HTML.includes("js/pet_core.js"));
  assert.ok(PET_HTML.includes("js/pet.js"));
  assert.ok(PET_HTML.includes("js/appearance.js"));
  assert.ok(PET_HTML.includes("js/reminders.js"));
  assert.ok(!/<audio[\s>]/i.test(PET_HTML));
  assert.ok(!/tts\.js/i.test(PET_HTML));
  // 静音硬约束：宠物页不允许任何发声入口
  assert.ok(!/\bspeechSynthesis\b/.test(PET_JS));
  assert.ok(!/new\s+Audio\s*\(/.test(PET_JS));
  assert.ok(!/\.speak\s*\(/.test(PET_JS));
});

test("pet.js：交互/提醒/外观/原生桥全部接线", () => {
  assert.ok(PET_JS.includes("AndroidPet"));
  assert.ok(PET_JS.includes("BRIDGE.ping"));
  assert.ok(PET_JS.includes("pet-reminder"));
  assert.ok(PET_JS.includes("pet-ready"));
  assert.ok(PET_JS.includes("Reminders.due"));
  assert.ok(PET_JS.includes("appearance:change"));
  assert.ok(PET_JS.includes("setStyle"));
  assert.ok(PET_JS.includes("tickReminders"));
});

test("pet.css：透明背景 + 零动画/大字号/扁平预设接线", () => {
  assert.ok(PET_CSS.includes("background: transparent"));
  assert.ok(PET_CSS.includes('html[data-motion="off"]'));
  assert.ok(PET_CSS.includes('html[data-font="large"]'));
  assert.ok(PET_CSS.includes('html[data-flat="true"]'));
});

test("pet.js：取景余量收到 2px 并交给 PetCore.clipRect；静止补帧降到 20s", () => {
  const margin = PET_JS.match(/const CLIP_MARGIN_PX = (\d+);/);
  assert.ok(margin, "pet.js 应保留 CLIP_MARGIN_PX 常量");
  assert.strictEqual(Number(margin[1]), 2);
  assert.ok(PET_JS.includes("core.clipRect("), "取景矩形应由 PetCore.clipRect 计算（可单测）");
  const breath = PET_JS.match(/const BREATH_WAKE_MS = (\d+);/);
  assert.ok(breath, "pet.js 应保留 BREATH_WAKE_MS 常量");
  assert.ok(Number(breath[1]) >= 20000, `静止补帧间隔应 ≥20s，实际 ${breath && breath[1]}ms`);
});

test("pet.css 暗色：气泡文字 ≥4.5:1、描边 ≥3:1（WCAG 非文本）", () => {
  assert.ok(PET_CSS.includes("@media (prefers-color-scheme: dark)"));
  const lum = (hex) => {
    const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const lin = rgb.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  };
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  // 与 pet.css 的暗色块保持同值：底 #201D1A、字 #F4EFE7、边 #9A938A
  assert.ok(ratio("#F4EFE7", "#201D1A") >= 4.5, "气泡文字对比度不足");
  assert.ok(ratio("#9A938A", "#201D1A") >= 3.0, "描边非文本对比度不足");
});

test("设置页：悬浮入口已改为桌宠文案，并引导授权后自动续开", () => {
  assert.ok(INDEX_HTML.includes("悬浮小满（桌宠）"));
  assert.ok(INDEX_HTML.includes("开启桌宠"));
  assert.ok(INDEX_HTML.includes("显示在其他应用上层"));
  assert.ok(APP_JS.includes("xiaoman_float_pending_v1"));
  assert.ok(APP_JS.includes("__appVisibility"));
  assert.ok(APP_JS.includes("xiaoman_app_visible_v1"));
});

test("原生服务：权限/降级/关闭/静音/桌宠入口齐全", () => {
  assert.ok(MANIFEST.includes("SYSTEM_ALERT_WINDOW"));
  assert.ok(MANIFEST.includes("foregroundServiceType=\"specialUse\""));
  assert.ok(SERVICE.includes("MainActivity.PET_URL"));
  assert.ok(SERVICE.includes("Settings.canDrawOverlays(this)"));
  assert.ok(SERVICE.includes("setSoundEffectsEnabled(false)"));
  assert.ok(SERVICE.includes("START_STICKY"));
  assert.ok(SERVICE.includes("fallbackToBall"));
  assert.ok(SERVICE.includes("ACTION_HIDE"));
  assert.ok(SERVICE.includes("onRenderProcessGone"));
  assert.ok(SERVICE.includes("onConfigurationChanged"));
  assert.ok(SERVICE.includes("addAction(0, \"关闭悬浮\""));
  assert.ok(SERVICE.includes("PixelFormat.TRANSLUCENT"));
});
