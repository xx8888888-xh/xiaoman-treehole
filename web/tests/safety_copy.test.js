/* safety_copy.test.js · 安全话术「文档 ↔ UI」逐字一致（防文档改了界面没改）
 * ------------------------------------------------------------
 * 唯一措辞来源：docs/SAFETY_COPY.md（中危 / 高危表）。
 * 断言链路：文档表格 → Risk.SCRIPTS（web/js/risk.js）→ app.js 实际取用。
 * 同源服务端断言见 server/tests/test_safety_copy.py。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..");
const DOC = fs.readFileSync(path.join(ROOT, "docs", "SAFETY_COPY.md"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "web", "js", "app.js"), "utf8");
const Risk = require("../js/risk.js");

/** 取某个二级标题下表格里的「文案」列（按 # 顺序） */
function copyRows(sectionTitle) {
  const marker = `## ${sectionTitle}`;
  const start = DOC.indexOf(marker);
  assert.ok(start >= 0, `docs/SAFETY_COPY.md 缺少章节：${sectionTitle}`);
  const rest = DOC.slice(start + marker.length);
  const next = rest.indexOf("\n## ");
  const block = next >= 0 ? rest.slice(0, next) : rest;
  return block.split(/\r?\n/)
    .map(line => line.match(/^\|\s*\d+\s*\|\s*(.+?)\s*\|\s*$/))
    .filter(Boolean)
    .map(m => m[1].trim());
}

test("中危 UI 文案与 SAFETY_COPY.md 逐字一致", () => {
  const rows = copyRows("中危（medium）");
  assert.equal(rows.length, 3, "中危应为 3 段");
  assert.equal(Risk.SCRIPTS.medium, rows.join("||"));
});

test("高危 UI 文案与 SAFETY_COPY.md 逐字一致", () => {
  const rows = copyRows("高危（high）");
  assert.equal(rows.length, 4, "高危应为 4 段");
  assert.equal(Risk.SCRIPTS.high, rows.join("||"));
});

test("UI 实际取用同一常量：危机直出与关怀卡都走 Risk.SCRIPTS", () => {
  assert.match(APP, /const CARE_SCRIPT = Risk\.SCRIPTS\.medium/, "关怀卡应取 medium 话术");
  assert.match(APP, /const script = Risk\.SCRIPTS\[risk\.level\]/, "危机直出应按分级取话术");
});

test("文档自检项落地：中危首段不甩热线、全文不升级急救", () => {
  const medium = Risk.SCRIPTS.medium.split("||");
  assert.ok(!medium[0].includes("12356") && !medium[0].includes("120") && !medium[0].includes("110"),
    "中危第一段不能出现热线/急救号码");
  const all = Risk.SCRIPTS.medium;
  assert.ok(!all.includes("120") && !all.includes("110"), "中危全文不出现 120/110");
  assert.ok(all.includes("最难受的是什么"), "中危要含追问，把话头交回");
  assert.ok(!/会好的|我保证|我不走/.test(all), "中危不能给兑现不了的承诺");
});
