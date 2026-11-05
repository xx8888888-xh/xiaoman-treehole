/* mock_engine.test.js · 离线引擎与在线（server/mock_api.py）对齐专项
 * 覆盖：
 * 1) 拒绝建议守卫：说"不用给我方案"后，回复里不再出现被拒的词
 * 2) 同输入 5 次：唯一度 ≥4、句长差 ≥2、相邻回复首 4 字不撞（先算候选再随机的确定性保证）
 * 3) 记忆写入轮：介绍"我家猫叫年糕"当场接住 1 次，随后 2 轮静默，冷却满再提
 * 4) 详情回声：食物/宠物细节被接住、连续两轮不重复、拒绝场景不回声、记忆值仍走记忆池
 * 5) 会话隔离：reply(history, sid) 两会话互不串味、刷新后恢复、resetSession 清幽灵、无 sid 仍可用
 * 每例 new Function 重新加载一份引擎源码，隔离引擎实例；会话状态按 sid 隔离。
 * 无浏览器依赖，CI 直接 `node --test` 可跑。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SRC = fs.readFileSync(path.join(__dirname, "..", "js", "mock_engine.js"), "utf8");

function freshEngine() {
  return new Function("window", `${SRC}\nreturn MockEngine;`)(undefined);
}

function ask(engine, history, text, sid) {
  history.push({ role: "user", content: text });
  const r = engine.reply(history, sid);
  history.push({ role: "assistant", content: r.reply });
  return r;
}

/** 临时给引擎装一份 localStorage（模拟浏览器刷新后仍能读回）；用例结束即卸载 */
function withStorage(fn) {
  const mem = new Map();
  globalThis.localStorage = {
    getItem: k => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    removeItem: k => mem.delete(k),
    clear: () => mem.clear(),
  };
  try {
    return fn(mem);
  } finally {
    delete globalThis.localStorage;
  }
}

const BANNED = /方案|建议|步骤|分析|道理|鸡汤/;


test("拒绝建议：回复不再出现被拒的词（30 次随机抽句零违规）", () => {
  const engine = freshEngine();
  const history = [];
  for (let i = 0; i < 30; i++) {
    const r = ask(engine, history, "不用给我方案");
    assert.ok(!BANNED.test(r.reply), `第 ${i + 1} 次仍出现被拒词：${r.reply}`);
    history.length = 0; // 每轮独立输入，排除上下文层干扰
  }
});

test("拒绝建议：别的拒绝说法同样生效（说人话 / 别劝我）", () => {
  for (const input of ["说人话", "别劝我"]) {
    const engine = freshEngine();
    const history = [];
    for (let i = 0; i < 10; i++) {
      const r = ask(engine, history, input);
      assert.ok(!BANNED.test(r.reply), `"${input}" 第 ${i + 1} 次出现被拒词：${r.reply}`);
      history.length = 0;
    }
  }
});

test("同输入 5 次：唯一度、句长差、相邻首 4 字三项均不塌陷", () => {
  const INPUTS = ["今天下午在阳台发呆了一会儿", "刚把阳台的花浇了一遍", "楼下那棵树开花了"];
  for (const input of INPUTS) {
    const engine = freshEngine();
    const history = [];
    const outs = [];
    for (let i = 0; i < 5; i++) {
      outs.push(ask(engine, history, input).reply);
      history.length = 0;
    }
    assert.ok(new Set(outs).size >= 4, `唯一度不足：${JSON.stringify(outs)}`);
    const lens = outs.map(s => s.length);
    assert.ok(Math.max(...lens) - Math.min(...lens) >= 2, `句长差 <2：${JSON.stringify(lens)}`);
    for (let i = 1; i < outs.length; i++) {
      assert.notEqual(outs[i].slice(0, 4), outs[i - 1].slice(0, 4),
        `相邻回复首 4 字重复：${outs[i - 1]} / ${outs[i]}`);
    }
  }
});

test("记忆写入轮：当场接住 1 次，随后 2 轮静默，冷却满再提", () => {
  const engine = freshEngine();
  const history = [];
  const replies = [
    ask(engine, history, "我家猫叫年糕").reply,   // 写入轮：必须接住
    ask(engine, history, "你猜年糕在干嘛").reply, // 冷却 1：静默
    ask(engine, history, "年糕今天拆家了").reply, // 冷却 2：静默
    ask(engine, history, "年糕是不是欠揍").reply, // 冷却满：可再提
    ask(engine, history, "年糕最近乖不乖").reply  // 再提后重新进冷却
  ];
  assert.match(replies[0], /年糕/, "写入轮必须当场接住");
  assert.doesNotMatch(replies[1], /年糕/, "冷却期不应连提");
  assert.doesNotMatch(replies[2], /年糕/, "冷却期不应连提");
  assert.match(replies[3], /年糕/, "冷却结束后应可再提");
  assert.doesNotMatch(replies[4], /年糕/, "再提之后重新进入冷却");
});

test("普通话题不受拒绝守卫影响（工作池仍有完整回复可选）", () => {
  const engine = freshEngine();
  const history = [];
  for (let i = 0; i < 6; i++) {
    const r = ask(engine, history, "今天又被老板骂了");
    assert.ok(r.reply && r.reply.length > 0);
    history.length = 0;
  }
});

// ---------------- W11 详情回声（与服务端 extract_detail/detail_pool 同源） ----------------

test("详情回声：食物/宠物细节被接住，不落回通用追问池", () => {
  const engine = freshEngine();
  const food = ask(engine, [], "今天中午吃了关东煮", "echo-food").reply;
  assert.match(food, /关东煮/, "食物细节必须原样接住");
  assert.match(food, /听着就馋了|我这会儿也有点想吃|哪家的|好这口|连吃三天/,
    `应命中食物详情池，而不是 generic：${food}`);

  // 用未写入记忆的动物描述（金毛不会被宠物记忆正则捕获），验证详情池的动作回声
  const pet = ask(freshEngine(), [], "楼下金毛又拆家了", "echo-pet").reply;
  assert.match(pet, /金毛/, `宠物本体必须接住：${pet}`);
  assert.match(pet, /拆家/, `动作细节必须接住：${pet}`);
});

test("详情回声：连续两轮同一细节不复读（句首 4 字也不撞）", () => {
  const engine = freshEngine();
  const history = [];
  const outs = [
    ask(engine, history, "今天中午吃了关东煮", "echo-dedupe").reply,
    ask(engine, history, "今天中午吃了关东煮", "echo-dedupe").reply,
  ];
  assert.notEqual(outs[0], outs[1], `连续两轮应换句：${outs}`);
  assert.notEqual(outs[0].slice(0, 4), outs[1].slice(0, 4), "连续两轮句首不应重复");
});

test("详情回声：拒绝场景让位，不夹带建议词、也不回声实体", () => {
  const engine = freshEngine();
  const r = ask(engine, [], "今天中午吃了关东煮，但不用给我建议", "echo-refuse");
  assert.ok(!BANNED.test(r.reply), `拒绝后不应出现被拒词：${r.reply}`);
  assert.ok(!r.reply.includes("关东煮"), `拒绝建议时详情回声应让位：${r.reply}`);
});

test("记忆类细节仍走记忆池，不走详情池", () => {
  const engine = freshEngine();
  const history = [];
  ask(engine, history, "我家猫叫年糕", "echo-mem");            // 写入轮（当场接住）
  ask(engine, history, "今天上班有点忙", "echo-mem");           // 冷却 1
  ask(engine, history, "晚上随便吃点", "echo-mem");             // 冷却 2
  const r = ask(engine, history, "你猜我家年糕在干嘛", "echo-mem").reply;
  assert.match(r, /年糕/, `存过的宠物名应被记忆池接住：${r}`);
  assert.match(r, /赖在你腿上|它今天乖不乖|想吐槽/,
    `应命中宠物记忆感知池，而不是追问回声：${r}`);
});

// ---------------- 会话作用域：状态按 sid 隔离 ----------------

test("两会话并行：引导节奏、记忆冷却各自独立，不串味", () => {
  const engine = freshEngine();
  const a = [];
  let ra = null;
  for (let i = 0; i < 4; i++) ra = ask(engine, a, "今天中午吃了关东煮", "sid-a");
  assert.ok(!/怎么称呼你/.test(ra.hook || ""), "A 已问过称呼，不该重复问");
  const rb = ask(engine, [], "今天中午吃了关东煮", "sid-b");
  assert.match(rb.hook || "", /怎么称呼你/, "B 是新会话，仍应从称呼引导开始");

  // A 写入宠物记忆，B 没有：B 不该被 A 的记忆感染
  ask(engine, a, "我家猫叫年糕", "sid-a");
  const cross = ask(engine, [], "你猜我家年糕在干嘛", "sid-b");
  assert.ok(!cross.reply.includes("年糕"), `B 无年糕记忆，不应被 A 串味：${cross.reply}`);
});

test("刷新恢复：同一 sid 的最近回复与引导进度从 localStorage 读回", () => withStorage(() => {
  const history = [];
  const e1 = freshEngine();
  const before = [];
  for (let i = 0; i < 3; i++) before.push(ask(e1, history, "今天中午吃了关东煮", "persist-sid").reply);

  const e2 = freshEngine();                 // 模拟刷新：引擎实例重建，存储保留
  const r = ask(e2, history.slice(), "今天中午吃了关东煮", "persist-sid");
  assert.ok(!before.includes(r.reply), `刷新后仍应避开刷新前用过的句子：${r.reply}`);
  assert.ok(!/怎么称呼你/.test(r.hook || ""), "askedName 已恢复，不该重新问称呼");
}));

test("删除会话：resetSession 同步清掉引擎状态与持久化键", () => withStorage(mem => {
  const key = "xiaoman_engine_state::gone-sid";
  const engine = freshEngine();
  ask(engine, [], "今天中午吃了关东煮", "gone-sid");
  assert.ok(mem.has(key), "会话进行中应有持久化状态");

  engine.resetSession("gone-sid");
  assert.ok(!mem.has(key), "删除后持久化键必须清掉，不留幽灵");

  const r = ask(engine, [], "今天中午吃了关东煮", "gone-sid");
  assert.match(r.hook || "", /怎么称呼你/, "状态清空后应像新会话一样重新引导");
  engine.resetSession("never-existed");   // 清不存在的会话不应抛错
}));

test("无 sid 调用：退化为默认会话且行为不变", () => {
  const engine = freshEngine();
  const history = [];
  const outs = [];
  for (let i = 0; i < 5; i++) {
    outs.push(ask(engine, history, "今天中午吃了关东煮").reply);
    history.length = 0;
  }
  assert.ok(new Set(outs).size >= 4, `默认会话同样保持不重样：${outs}`);
  const r = engine.reply([{ role: "user", content: "随便说点什么" }]);
  assert.ok(r.reply && r.reply.length > 0);
});
