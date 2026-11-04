// crisis.test.js · w1 危机模块专项（三档分流/上下文过滤/多轮累积/隐性告别/响应分级/输出复检）
// 与 server/tests/test_crisis.py 同源，两端口径必须一致。
const test = require("node:test");
const assert = require("node:assert/strict");
const Risk = require("../js/risk.js");

const RANK = { none: 0, low: 1, medium: 2, high: 3 };
const rank = (l) => RANK[l] || 0;

test("三档分流：中文口语正例（低/中/高）", () => {
  assert.equal(Risk.classify("我今晚就跳下去").level, "high");
  assert.equal(Risk.classify("怎么死最快").level, "high");
  assert.equal(Risk.classify("我刚吞了一整瓶安眠药").level, "high");
  assert.equal(Risk.classify("等我不在了，你帮我照顾年糕").level, "medium");
  assert.equal(Risk.classify("我撑不下去了").level, "medium");
  assert.equal(Risk.classify("今天加班到十点，好累").level, "low");
});

test("上下文过滤：否定从句只剔除自己，不吞掉后半句风险", () => {
  assert.equal(Risk.classify("我不会自杀").level, "none");
  assert.equal(Risk.classify("我不想死，就是有点累").level, "none");
  assert.equal(Risk.classify("I don't want to die").level, "none");
  assert.equal(Risk.classify("我没有自杀的想法，只是想把东西送人").level, "medium");
  // "不会自杀" 是否定，"但我想死" 是真实意念：必须保留
  assert.equal(Risk.classify("我不会自杀，但我想死").level, "medium");
});

test("上下文过滤：第三人称要处理，但不当作本人高危", () => {
  const third = Risk.classify("我朋友想死");
  assert.equal(third.level, "medium");
  assert.equal(third.subject, "third");
  // "我该怎么劝他" 是助人者视角，不能把主语判成本人
  const helper = Risk.classify("他又说想死，我该怎么劝他");
  assert.equal(helper.subject, "third");
  assert.equal(helper.level, "medium");
  assert.equal(Risk.classify("我朋友说他不想死").level, "none");
  // 第三人称但已经在行动：仍须高危响应
  const imminent = Risk.classify("我朋友现在在天台上准备跳下去");
  assert.equal(imminent.level, "high");
  assert.equal(imminent.subject, "third");
});

test("上下文过滤：玩笑/歌词/台词不误触；问方法永远最高档", () => {
  assert.equal(Risk.classify("歌词里唱我想死").level, "none");
  assert.equal(Risk.classify("电影台词说我不想活了").level, "none");
  assert.equal(Risk.classify("开玩笑的，去死吧").level, "none");
  assert.equal(Risk.classify("歌词里唱怎么死最快").level, "high");
  assert.equal(Risk.classify("我朋友问我怎么自杀").level, "high");
});

test("隐性告别：送东西/道别/不来了/不打扰/最后一次/计划性", () => {
  const mustFlag = [
    "明天就不打扰大家了",
    "谢谢你们这段时间的照顾，我该走了",
    "我把我的书都送给朋友了，跟大家道个别",
    "这可能是最后一次跟你们说话了",
    "我把东西都送人了，不用再来找我",
    "我今晚在天台上，药已经准备好了",
  ];
  for (const text of mustFlag) {
    assert.ok(rank(Risk.classify(text).level) >= 2, `应至少中危：${text}`);
  }
  // 普通礼貌性告别 / 日常生活不能升级
  const normal = [
    "很高兴认识你们，今晚就到这里",
    "我该走了，明天见",
    "我把旧书捐给图书馆了",
  ];
  for (const text of normal) {
    assert.ok(rank(Risk.classify(text).level) < 2, `不应中危：${text}`);
  }
});

test("多轮累积：只升不降，且持续低落会升级", () => {
  let state = null;
  let r = Risk.evaluate("我不想活了", state);
  state = r.state;
  assert.equal(r.base.level, "medium");
  assert.equal(r.level, "medium");
  assert.equal(r.acc.fresh, true);

  r = Risk.evaluate("今天天气不错", state);
  state = r.state;
  assert.equal(r.base.level, "none");
  assert.equal(r.level, "medium", "中危后不得回落到低危");
  assert.equal(r.acc.fresh, false);

  r = Risk.evaluate("好累", state);
  state = r.state;
  assert.equal(r.base.level, "low");
  assert.equal(r.level, "medium", "峰值只升不降");

  r = Risk.evaluate("我在天台上，准备跳下去", state);
  state = r.state;
  assert.equal(r.level, "high");
  assert.equal(r.acc.fresh, true);

  r = Risk.evaluate("今天吃什么", state);
  assert.equal(r.level, "high", "高危后普通轮次也不得降档");
  assert.equal(r.acc.fresh, false);

  // 连续 4 轮低落：前 3 轮仍低危，第 4 轮累积升中危并 fresh
  let s = null;
  const levels = [];
  const fresh = [];
  for (let i = 0; i < 4; i++) {
    const e = Risk.evaluate("好累", s);
    s = e.state;
    levels.push(e.level);
    fresh.push(e.acc.fresh);
  }
  assert.deepEqual(levels, ["low", "low", "low", "medium"]);
  assert.deepEqual(fresh, [false, false, false, true]);
  // 状态可 JSON 往返（客户端 localStorage / 服务端内存共用）
  const restored = Risk.createTracker(JSON.parse(JSON.stringify(s)));
  assert.deepEqual(restored.snapshot(), s);
});

test("关怀语境粘住 3 轮", () => {
  let s = Risk.careArm("high", 3);
  assert.deepEqual(s, { level: "high", left: 3 });
  const lefts = [];
  for (let i = 0; i < 3; i++) {
    const n = Risk.careNext(s);
    lefts.push(n.prev.left);
    s = n.state;
  }
  assert.deepEqual(lefts, [3, 2, 1]);
  assert.equal(s, null);
  assert.deepEqual(Risk.careNext(null), { state: null, prev: null });
});

test("高危/中危响应分级：话术要素齐全", () => {
  const high = Risk.SCRIPTS.high;
  assert.ok(high.includes("你现在安全吗") || high.includes("安全吗"));
  assert.ok(high.includes("身边有人"));
  assert.ok(high.includes("120"));
  assert.ok(high.includes("110"));
  assert.ok(high.includes("12356"));
  assert.ok(high.includes("微信"), "不愿打电话的人要有文字渠道");
  // 高危第一句先问安全，不先给大道理
  assert.ok(high.split("||")[0].includes("安全吗"));

  const medium = Risk.SCRIPTS.medium;
  const first = medium.split("||")[0];
  assert.ok(!first.includes("12356"), "中危不直接甩热线");
  assert.ok(medium.includes("最难受的是什么"), "中危要引导求助/追问");
  assert.ok(!medium.includes("120") && !medium.includes("110"), "中危不上升为急救指令");
  assert.ok(medium.includes("12356"), "保留可选求助渠道（非首句）");

  const third = Risk.SCRIPTS.third;
  assert.ok(third.includes("120") && third.includes("12356"));
});

test("输出侧复检：肯定告别语/null/空串/结构化 JSON 全部过闸", () => {
  assert.equal(Risk.filterDangerous("嗯，挺好的，尊重你的选择", "high"), Risk.SCRIPTS.high);
  assert.equal(Risk.filterDangerous("安心去吧，你终于解脱了", "high"), Risk.SCRIPTS.high);
  assert.equal(Risk.filterDangerous("那明天我就不打扰大家啦~", "medium"), Risk.SCRIPTS.medium);
  assert.equal(Risk.filterDangerous("明天见！", "low"), "明天见！");
  assert.equal(Risk.filterDangerous(null, "high"), Risk.SCRIPTS.high);
  assert.equal(Risk.filterDangerous("", "medium"), Risk.SCRIPTS.medium);
  assert.equal(Risk.filterDangerous("   ", "none"), Risk.EMPTY_FIX);
  assert.ok(Risk.EMPTY_FIX.trim().length > 0);
  // 结构化 JSON 里出现肯定性表述，也必须整体降级成安全话术
  const payload = JSON.stringify({ reply: "你终于解脱了，挺好的", emotion: "gentle" });
  assert.equal(Risk.filterDangerous(payload, "high"), Risk.SCRIPTS.high);
});
