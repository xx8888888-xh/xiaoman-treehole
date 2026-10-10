#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-2 主动引用记忆 · 端到端验收（路线图 docs/PRODUCT_ROADMAP.md）

验收标准（路线图原文）：
  注入测试集（6 轮历史 + 回访话题），召回引用率 ≥80%，不张冠李戴
  ① 相关记忆值出现在回复或 hook → 召回率 ≥80%
  ② 回访 A 话题时 B 话题记忆值不出现（张冠李戴 = 0）
  ③ 同轮不双提、同记忆 6 轮内不重复提（三重闸门频控）

覆盖三组：
  G1 服务端主验收：铺 6 轮历史（5 键全提取）→ 4 类回访 → 冷却窗口 → 总额闸门
  G2 服务端 greet 防双提：开场引用打标记 → 紧邻轮不重提 → 冷却解除后再提
  G3 离线兜底同源：mock_engine.js（node 直调）跑同款回访剧本

前置：scripts/dev_up.sh（mock 8902；G3 需 node）
退出码：任一检查点 FAIL → 1；全部通过 → 0。
"""
import json, os, subprocess, sys, uuid, urllib.request

def _load_env():
    """端口/主机集中配置唯一真源：scripts/env.sh（经 bash source 读取）"""
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c", f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_MOCK_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return out[0], int(out[1])
    except Exception:
        return "127.0.0.1", 8902

_HOST, _PORT = _load_env()
CHAT_URL = f"http://{_HOST}:{_PORT}/v1/chat/completions"
TIMEOUT = 10

RESULTS = []
def check(name, ok, info=""):
    RESULTS.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" | {info}" if info else ""))

def chat(text, session_id):
    payload = {"messages": [{"role": "user", "content": text}], "session_id": session_id}
    req = urllib.request.Request(CHAT_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        content = json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
    return json.loads(content)

# 记忆值（铺历史用，断言引用它们）
NICK, PET, EVENT, BOSS, BUSY = "阿秋", "橘猫", "下周要期中考试", "张三", "赶稿"
NON_TARGET = {"学习": [BOSS, PET, BUSY], "工作": [EVENT, PET, BUSY],
              "宠物": [EVENT, BOSS, BUSY], "闲聊": [BOSS, PET, BUSY]}


def g1_server():
    """G1 服务端主验收：铺历史 → 4 类回访 → 冷却 → 总额"""
    sid = "p02-g1-" + uuid.uuid4().hex
    # --- 铺 6 轮历史（昵称/宠物/大事/老板/在忙 + 1 轮元问题短路不计轮）---
    seeds = [("叫我阿秋就行", NICK), ("我养了一只橘猫", PET), ("下周要期中考试", EVENT),
             ("我们老板张三", BOSS), ("我在赶稿", BUSY)]
    got = {}
    for text, val in seeds:
        d = chat(text, sid)
        got.update(d.get("memory_updates") or {})
    chat("你是AI吗", sid)  # 元问题短路：第 6 条历史，不计轮不触发 hook 决策
    miss = [k for k, v in [("昵称", NICK), ("宠物", PET), ("大事", EVENT),
                           ("老板", BOSS), ("在忙", BUSY)] if got.get(k) != v]
    check("G1-0 铺历史5键全提取", not miss,
          f"got={json.dumps(got, ensure_ascii=False)}" + (f" miss={miss}" if miss else ""))
    # cleanName 对齐副验收："我们老板张三吧"式尾缀不进值（前端同源同表）
    d = chat("我们老板张三吧", "p02-cleanname-" + uuid.uuid4().hex)
    check("G1-0b 老板姓名尾缀清洗(张三吧→张三)", (d.get("memory_updates") or {}).get("老板") == BOSS,
          json.dumps(d.get("memory_updates") or {}, ensure_ascii=False))
    # 引导完成后铺历史轮不应回提（闸门0：提取后隔 2 轮；当轮刚提取不提）
    # 轮 3/4/5 的 hook 在上面循环里没存——重验：新 session 走一遍关键轮
    sid2 = "p02-g1b-" + uuid.uuid4().hex
    for text, _ in seeds[:5]:
        r = chat(text, sid2)
        if "你上次说" in (r.get("hook") or ""):
            check("G1-0c 铺历史轮无复读式回提", False, f"轮『{text}』hook={r['hook']!r}")
            break
    else:
        check("G1-0c 铺历史轮无复读式回提", True)

    # --- 4 类回访（turn 6/7/8）+ 填充(9-11) + 闲聊兜底(12) + 总额闸门(13) ---
    visits = [("学习", "最近复习得好累", EVENT),   # insomnia → 大事
              ("工作", "今天上班好烦", BOSS),       # work → 老板
              ("宠物", "路过宠物店看了一眼", PET)]   # 宠物词表 → 宠物
    hits = {}
    for cat, text, expect in visits:
        d = chat(text, sid)
        blob = (d.get("hook") or "") + (d.get("reply") or "")
        hits[cat] = expect in blob
        leak = [v for v in NON_TARGET[cat] if v in blob]
        check(f"G1-1{chr(9312+visits.index((cat, text, expect)))} 回访·{cat}话题命中{expect}",
              hits[cat] and not leak,
              f"hook={d.get('hook')!r}" + (f" 张冠李戴={leak}" if leak else ""))
    # 冷却窗口：大事已在学习轮回提（turn 6），填充轮 9-11 不重提
    for i in range(3):
        d = chat("看了部失恋主题的电影", sid)  # love：无映射，兜底大事被 6 轮冷却挡住
        check(f"G1-2{chr(9312+i)} 冷却窗口填充轮{i+1}不重提大事",
              EVENT not in (d.get("hook") or ""), f"hook={d.get('hook')!r}")
    # 闲聊兜底（turn 12）：距 turn 6 = 6 轮，冷却解除 → 第 2 次提大事（第 4 次回提，总额满）
    d = chat("今晚吃了顿火锅", sid)
    blob = (d.get("hook") or "") + (d.get("reply") or "")
    check("G1-3 闲聊兜底回提大事(冷却解除)", EVENT in blob, f"hook={d.get('hook')!r}")
    # 总额闸门（turn 13）：4 次已满，insomnia 不再回提
    d = chat("又熬夜了", sid)
    check("G1-4 总额闸门(4次已满不再回提)", EVENT not in (d.get("hook") or ""), f"hook={d.get('hook')!r}")
    # 召回率：4 类回访全命中（学习/工作/宠物/闲聊）→ 100%
    rate = (sum(hits.values()) + 1) / 4  # +1 = 闲聊兜底
    check("G1-5 断言①召回率≥80%", rate >= 0.8, f"rate={rate:.0%}")


def g2_greet_dedup():
    """G2 greet 开场引用防双提：服务端同步打标记"""
    sid = "p02-g2-" + uuid.uuid4().hex
    chat("叫我小夏", sid)            # 轮1 昵称
    chat("我养了一只橘猫", sid)       # 轮2 宠物（hook=ONBOARD_EVENT）
    chat("下周要期中考试", sid)       # 轮3 大事（当轮提取不提）
    d = chat("晚上好呀", sid)         # 轮4 greet：前端开场引用 → 服务端标记大事/宠物
    blob = (d.get("hook") or "") + (d.get("reply") or "")
    check("G2-1 greet轮服务端不双提", EVENT not in blob and PET not in blob, f"hook={d.get('hook')!r}")
    d = chat("最近好累", sid)         # 轮5 insomnia：大事 hit=4，距 1 <6 冷却挡
    check("G2-2 greet后紧邻轮不重提", EVENT not in (d.get("hook") or ""), f"hook={d.get('hook')!r}")
    for _ in range(4):               # 轮6-9 填充
        chat("随便聊聊今天", sid)
    d = chat("又失眠了", sid)         # 轮10 insomnia：距 hit=4 已 6 轮 → 冷却解除
    check("G2-3 冷却解除后回提大事", EVENT in ((d.get("hook") or "") + (d.get("reply") or "")),
          f"hook={d.get('hook')!r}")


def g3_offline_engine():
    """G3 离线兜底同源：node 直调 mock_engine.js（浏览器无 window 依赖）"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = r"""
const fs = require('fs');
const src = fs.readFileSync(ROOT + '/web/js/mock_engine.js', 'utf8');
const MockEngine = new Function('window', src + '; return MockEngine;')(undefined);
const out = [];
const say = (history, t) => {
  history.push({role: 'user', content: t});
  const r = MockEngine.reply(history);
  history.push({role: 'assistant', content: r.reply + (r.hook ? '||' + r.hook : '')});
  return r;
};
const H = [];
const seeds = ['叫我阿秋就行', '我养了一只橘猫', '下周要期中考试', '我们老板张三', '我在赶稿'];
let seeded = {};
for (const t of seeds) { const r = say(H, t); Object.assign(seeded, r.memory_updates); }
out.push({n: 'G3-0 5键全提取', ok: Object.keys(seeded).length >= 5, info: JSON.stringify(seeded)});
const r6 = say(H, '最近复习得好累');
out.push({n: 'G3-1 学习回提大事', ok: !!(r6.hook && r6.hook.includes('下周要期中考试')), info: r6.hook});
const r7 = say(H, '今天上班好烦');
out.push({n: 'G3-2 工作回提老板', ok: !!(r7.hook && r7.hook.includes('张三')), info: r7.hook});
const r8 = say(H, '路过宠物店看了一眼');
out.push({n: 'G3-3 宠物回提', ok: !!(r8.hook && r8.hook.includes('橘猫')), info: r8.hook});
const bad7 = ['下周要期中考试','橘猫','赶稿'].filter(v => (r7.hook||'').includes(v));
const bad8 = ['下周要期中考试','张三','赶稿'].filter(v => (r8.hook||'').includes(v));
out.push({n: 'G3-4 不张冠李戴', ok: bad7.length === 0 && bad8.length === 0, info: JSON.stringify([bad7, bad8])});
let cool_ok = true;
for (let i = 0; i < 3; i++) { const r = say(H, '看了部失恋主题的电影'); if ((r.hook||'').includes('期中考试')) cool_ok = false; }
out.push({n: 'G3-5 冷却窗口不重提', ok: cool_ok, info: ''});
const r12 = say(H, '今晚吃了顿火锅');
out.push({n: 'G3-6 闲聊兜底回提', ok: (r12.hook||'').includes('期中考试'), info: r12.hook});
const r13 = say(H, '又熬夜了');
out.push({n: 'G3-7 总额闸门', ok: !(r13.hook||'').includes('期中考试'), info: r13.hook});
console.log('RESULT_JSON:' + JSON.stringify(out));
""".replace("ROOT", json.dumps(root))
    try:
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON:")]
        if not line:
            check("G3 离线引擎可加载", False, (p.stderr or p.stdout)[-200:])
            return
        for item in json.loads(line[-1][len("RESULT_JSON:"):]):
            check(item["n"], item["ok"], item.get("info", ""))
    except FileNotFoundError:
        check("G3 node可用", False, "node 不在 PATH")


def g4_generic_guard():
    """G4 泛指不覆盖具体（P0-2 遗留守卫）：提取层信息劣化防护，服务端+离线双端"""
    # ---------- 服务端 ----------
    sid = "p02-g4a-" + uuid.uuid4().hex
    chat("我养了一只橘猫", sid)               # 轮1 存具体值"橘猫"（NAME hook 期）
    d = chat("我家那只猫拆家了", sid)         # 轮2 泛指"那只猫" → 守卫应拦截
    upd = d.get("memory_updates") or {}
    check("G4-1① 服务端泛指轮不产出宠物更新", "宠物" not in upd,
          json.dumps(upd, ensure_ascii=False))
    chat("叫我小夏", sid)                     # 轮3 补昵称（过 NAME 引导期）
    chat("下周要期中考试", sid)               # 轮4 补大事（过 EVENT 引导期）
    chat("今天随便聊聊", sid)                 # 轮5 填充（过大事提取间隔）
    d = chat("路过宠物店看了一眼", sid)       # 轮6 宠物话题回提 → 应引用"橘猫"非"那只猫"
    hook = d.get("hook") or ""
    check("G4-1② 服务端记忆未劣化(回提引用橘猫)", "橘猫" in hook and "那只猫" not in hook,
          f"hook={hook!r}")
    sid2 = "p02-g4b-" + uuid.uuid4().hex
    d = chat("我家有只猫", sid2)              # 首次提及：泛指也落库（无旧值可保护）
    upd = d.get("memory_updates") or {}
    check("G4-1③ 首次泛指可落库(无旧值)", "宠物" in upd,
          json.dumps(upd, ensure_ascii=False))
    d = chat("我养了一只英短猫", sid2)        # 具体新值 → 升级覆盖泛指旧值
    upd = d.get("memory_updates") or {}
    check("G4-1④ 具体值可升级覆盖泛指", upd.get("宠物") == "英短猫",
          json.dumps(upd, ensure_ascii=False))

    # ---------- 离线兜底（node 直调，与服务端同源同剧本） ----------
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = r"""
const fs = require('fs');
const src = fs.readFileSync(ROOT + '/web/js/mock_engine.js', 'utf8');
const MockEngine = new Function('window', src + '; return MockEngine;')(undefined);
const out = [];
const say = (history, t) => {
  history.push({role: 'user', content: t});
  const r = MockEngine.reply(history);
  history.push({role: 'assistant', content: r.reply + (r.hook ? '||' + r.hook : '')});
  return r;
};
const H = [];
say(H, '我养了一只橘猫');              // 轮1 存具体值
const r2 = say(H, '我家那只猫拆家了'); // 轮2 泛指 → 返回值守卫应拦截
out.push({n: 'G4-2① 离线泛指轮不产出宠物更新', ok: !r2.memory_updates['宠物'],
          info: JSON.stringify(r2.memory_updates)});
say(H, '叫我小夏');                    // 轮3 昵称
say(H, '下周要期中考试');              // 轮4 大事
say(H, '今天随便聊聊');                // 轮5 填充
const r6 = say(H, '路过宠物店看了一眼'); // 轮6 宠物回提 → 应引用"橘猫"
const h6 = r6.hook || '';
out.push({n: 'G4-2② 离线记忆未劣化(回提引用橘猫)', ok: h6.includes('橘猫') && !h6.includes('那只猫'),
          info: h6});
const H2 = [];
const s1 = say(H2, '我家有只猫');       // 首次泛指落库
out.push({n: 'G4-2③ 离线首次泛指可落库', ok: !!s1.memory_updates['宠物'],
          info: JSON.stringify(s1.memory_updates)});
const s2 = say(H2, '我养了一只英短猫'); // 具体升级覆盖
out.push({n: 'G4-2④ 离线具体值可升级覆盖', ok: s2.memory_updates['宠物'] === '英短猫',
          info: JSON.stringify(s2.memory_updates)});
console.log('RESULT_JSON:' + JSON.stringify(out));
""".replace("ROOT", json.dumps(root))
    try:
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON:")]
        if not line:
            check("G4 离线引擎可加载", False, (p.stderr or p.stdout)[-200:])
            return
        for item in json.loads(line[-1][len("RESULT_JSON:"):]):
            check(item["n"], item["ok"], item.get("info", ""))
    except FileNotFoundError:
        check("G4 node可用", False, "node 不在 PATH")


def g5_boss_guard():
    """G5 老板姓名可信度（P0-2 遗留守卫 II）：谓语垃圾不进老板键，三端防线"""
    # ---------- 服务端 ----------
    sid = "p02-g5a-" + uuid.uuid4().hex
    chat("我们老板张三", sid)                 # 轮1 存姓名"张三"（NAME hook 期）
    d = chat("我们老板又骂我", sid)           # 轮2 谓语垃圾 → 守卫拦截（基词不覆盖姓名）
    upd = d.get("memory_updates") or {}
    check("G5-1① 服务端垃圾谓语不产出老板更新(又骂我)", "老板" not in upd,
          json.dumps(upd, ensure_ascii=False))
    d = chat("我们老板今天心情不好", sid)     # 轮3 时间谓语 → 同上
    upd = d.get("memory_updates") or {}
    check("G5-1② 服务端时间谓语不产出老板更新", "老板" not in upd,
          json.dumps(upd, ensure_ascii=False))
    chat("叫我小夏", sid)                     # 轮4 补昵称（过 NAME 引导期）
    chat("下周要期中考试", sid)               # 轮5 补大事（过 EVENT 引导期）
    chat("今天随便聊聊", sid)                 # 轮6 填充（过提取间隔≥3轮）
    d = chat("今天上班好烦", sid)             # 轮7 work 回提 → 应引用"张三"非垃圾值
    hook = d.get("hook") or ""
    check("G5-1③ 服务端记忆未劣化(回提张三非乱语)",
          "张三" in hook and "又骂我" not in hook and "心情" not in hook, f"hook={hook!r}")
    sid2 = "p02-g5b-" + uuid.uuid4().hex
    d = chat("我们老板王总今天又作妖", sid2)  # 姓名+谓语连排 → 截断"王总"
    upd = d.get("memory_updates") or {}
    check("G5-2① 服务端混合句截断(→王总)", upd.get("老板") == "王总",
          json.dumps(upd, ensure_ascii=False))
    d = chat("我们老板又骂我", sid2)          # 垃圾不覆盖姓名
    upd = d.get("memory_updates") or {}
    check("G5-2② 服务端垃圾不覆盖姓名", "老板" not in upd,
          json.dumps(upd, ensure_ascii=False))
    sid3 = "p02-g5c-" + uuid.uuid4().hex
    d = chat("我们老板又骂我", sid3)          # 首次即垃圾 → 基词占位（回提模板可用）
    upd = d.get("memory_updates") or {}
    check("G5-3① 服务端首次垃圾占基词(老板)", upd.get("老板") == "老板",
          json.dumps(upd, ensure_ascii=False))
    d = chat("我们老板王总", sid3)            # 可信姓名升级覆盖基词
    upd = d.get("memory_updates") or {}
    check("G5-3② 服务端姓名可升级覆盖基词", upd.get("老板") == "王总",
          json.dumps(upd, ensure_ascii=False))

    # ---------- 离线兜底（node 直调，与服务端同源同剧本） ----------
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = r"""
const fs = require('fs');
const src = fs.readFileSync(ROOT + '/web/js/mock_engine.js', 'utf8');
const MockEngine = new Function('window', src + '; return MockEngine;')(undefined);
const out = [];
const say = (history, t) => {
  history.push({role: 'user', content: t});
  const r = MockEngine.reply(history);
  history.push({role: 'assistant', content: r.reply + (r.hook ? '||' + r.hook : '')});
  return r;
};
const H = [];
say(H, '我们老板张三');                 // 轮1 存姓名
const r2 = say(H, '我们老板又骂我');     // 轮2 谓语垃圾 → 返回值守卫应拦截
out.push({n: 'G5-4① 离线垃圾谓语不产出老板更新', ok: !r2.memory_updates['老板'],
          info: JSON.stringify(r2.memory_updates)});
const r3 = say(H, '我们老板今天心情不好'); // 轮3 时间谓语
out.push({n: 'G5-4② 离线时间谓语不产出老板更新', ok: !r3.memory_updates['老板'],
          info: JSON.stringify(r3.memory_updates)});
say(H, '叫我小夏');
say(H, '下周要期中考试');
say(H, '今天随便聊聊');
const r7 = say(H, '今天上班好烦');       // 轮7 work 回提 → 引用"张三"
out.push({n: 'G5-4③ 离线记忆未劣化(回提张三)', ok: (r7.hook||'').includes('张三'),
          info: r7.hook});
const H2 = [];
const s1 = say(H2, '我们老板王总今天又作妖'); // 截断
out.push({n: 'G5-5① 离线混合句截断(→王总)', ok: s1.memory_updates['老板'] === '王总',
          info: JSON.stringify(s1.memory_updates)});
const s2 = say(H2, '我们老板又骂我');       // 垃圾不覆盖姓名
out.push({n: 'G5-5② 离线垃圾不覆盖姓名', ok: !s2.memory_updates['老板'],
          info: JSON.stringify(s2.memory_updates)});
const H3 = [];
const s3 = say(H3, '我们老板又骂我');       // 首次垃圾占基词
out.push({n: 'G5-6① 离线首次垃圾占基词', ok: s3.memory_updates['老板'] === '老板',
          info: JSON.stringify(s3.memory_updates)});
const s4 = say(H3, '我们老板王总');         // 姓名升级
out.push({n: 'G5-6② 离线姓名可升级覆盖基词', ok: s4.memory_updates['老板'] === '王总',
          info: JSON.stringify(s4.memory_updates)});
console.log('RESULT_JSON:' + JSON.stringify(out));
""".replace("ROOT", json.dumps(root))
    try:
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON:")]
        if not line:
            check("G5 离线引擎可加载", False, (p.stderr or p.stdout)[-200:])
            return
        for item in json.loads(line[-1][len("RESULT_JSON:"):]):
            check(item["n"], item["ok"], item.get("info", ""))
    except FileNotFoundError:
        check("G5 node可用", False, "node 不在 PATH")


def main():
    print(f"目标服务: {CHAT_URL}")
    # 健康预检
    try:
        with urllib.request.urlopen(f"http://{_HOST}:{_PORT}/health", timeout=5) as r:
            assert json.loads(r.read().decode())["ok"] is True
    except Exception as e:
        print(f"[FAIL] mock 服务不可达: {e}（先跑 scripts/dev_up.sh）")
        return 1
    for fn in (g1_server, g2_greet_dedup, g3_offline_engine, g4_generic_guard, g5_boss_guard):
        try:
            fn()
        except Exception as e:
            check(fn.__name__ + " 异常", False, f"{type(e).__name__}: {e}")
    failed = [n for n, ok in RESULTS if not ok]
    print(f"[SUMMARY] 检查点 {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过"
          + (f"，FAIL: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
