/* mock_engine.test.js · 离线引擎与在线（server/mock_api.py）对齐专项
 * 覆盖：
 * 1) 拒绝建议守卫：说"不用给我方案"后，回复里不再出现被拒的词
 * 2) 同输入 5 次：唯一度 ≥4、句长差 ≥2、相邻回复首 4 字不撞（先算候选再随机的确定性保证）
 * 3) 记忆写入轮：介绍"我家猫叫年糕"当场接住 1 次，随后 2 轮静默，冷却满再提
 * 每例 new Function 重新加载一份引擎源码，隔离模块级 recentReplies/memAwareTs/onbTurns。
 * 无浏览器依赖，CI 直接 `node --test` 可跑。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SRC = fs.readFileSync(path.join(__dirname, "..", "js", "mock_engine.js"), "utf8");

function freshEngine() {
  return new Function("window", SRC + "\nreturn MockEngine;")(undefined);
}

function ask(engine, history, text) {
  history.push({ role: "user", content: text });
  const r = engine.reply(history);
  history.push({ role: "assistant", content: r.reply });
  return r;
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
