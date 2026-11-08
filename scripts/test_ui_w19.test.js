/* ============================================================
 * scripts/test_ui_w19.test.js · w19 界面接线静态验收
 * ------------------------------------------------------------
 * app.js 是整页 IIFE，本地不允许起 headless 浏览器（用户硬约束），
 * 所以沿用仓库既有做法（pet_core.test.js）：对真实源码做接线断言。
 * 覆盖：单条消息「长按 + 左滑」菜单、删除二次确认、编辑后统计/记忆同步、
 *       设置页「清空使用统计」二次确认与图表归零入口。
 * 运行：node --test scripts/test_ui_w19.test.js
 * ============================================================ */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const APP = fs.readFileSync(path.join(__dirname, "..", "web", "js", "app.js"), "utf8");

test("单条消息：长按 450ms 与左滑 ≥36px 都能展开菜单", () => {
  assert.match(APP, /setTimeout\(\(\) => toggleMsgMenu\(row, rec\), 450\)/, "长按 450ms 应打开菜单");
  assert.match(APP, /dx <= -36 && Math\.abs\(dx\) > Math\.abs\(dy\) \* 1\.5/, "左滑手势要有阈值且不抢竖向滚动");
  assert.match(APP, /if \(\!swiped && dx <= -36[\s\S]{0,220}toggleMsgMenu\(row, rec\)/);
  assert.match(APP, /Math\.abs\(dy\) > 12\) disarm\(\)/, "竖向滚动应取消长按");
});

test("删除有二次确认：两次点击才真正删", () => {
  assert.match(APP, /if \(\!armed\)[\s\S]{0,160}再点一次删除/, "第一次点击只进入待确认态");
  assert.match(APP, /async function deleteMsg/, "删除逻辑单独成函数，供二次确认调用");
});

test("编辑后统计与记忆同步：先回退旧文案，再按新文案记，并保留仍被提到的记忆", () => {
  assert.match(APP, /Stats\.unbump\(old, at \|\| undefined\); Stats\.bump\(text, at \|\| undefined\)/, "编辑需按同一时间点回退+重记");
  assert.match(APP, /purgeMemory\(old, text\)/, "编辑需清旧记忆且把新文案作为保留条件");
  assert.match(APP, /function purgeMemory\(fragment, keepText\)/, "purgeMemory 要支持 keepText");
  assert.match(APP, /if \(keep && keep\.includes\(val\)\) continue;/, "新文案仍提到的结构化记忆不能误删");
  assert.match(APP, /if \(keep && keep\.includes\(t\)\) continue;/, "新文案仍提到的文本记忆不能误删");
});

test("设置页「清空使用统计」：挂在记录与隐私区，二次确认 5 秒，清完重绘（空态不产生 NaN）", () => {
  assert.match(APP, /function injectStatsClearSetting\(\)/, "应有设置页注入函数");
  assert.match(APP, /document\.querySelector\("#settingsModal \.backup-field"\)/, "入口挂在设置页记录与隐私块");
  assert.match(APP, /btn\.id = "statsClearSetting"/);
  assert.match(APP, /settingsStatsArmed \? "再点一次，确认清空" : "清空使用统计"/);
  assert.match(APP, /settingsStatsTimer = setTimeout\([\s\S]{0,200}, 5000\)/, "5 秒不点第二次自动撤销");
  assert.match(APP, /Stats\.clear\(\);[\s\S]{0,120}renderStats\(\);/, "清空后立即重绘图表");
  assert.match(APP, /injectStatsClearSetting\(\);[\s\S]{0,80}openPanel/, "打开设置时必须已注入");
});

test("统计重绘的空态分支先于图表计算（清空后不会出现 NaN）", () => {
  const src = APP.slice(APP.indexOf("function renderStats"));
  const empty = src.indexOf("if (!s.msgs)");
  const bars = src.indexOf("const max = Math.max(1, ...s.last7");
  assert.ok(empty > 0 && bars > 0 && empty < bars, "无数据时必须提前走空态，不进入柱状计算");
});
