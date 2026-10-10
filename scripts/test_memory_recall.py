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


def g6_continuity():
    """G6 关系连续性承诺（P0-3，Replika 教训）：升级登记 → greet 主动告知（老用户一次）
    → update_query 播报 → "你变了"温柔接住 → 误伤防护 → 危机优先级不受影响"""
    # ---------- 服务端 ----------
    # ① 新用户 greet 不误触发（没有"以前"可对比，静默登记版本）
    sid_new = "p03-g6new-" + uuid.uuid4().hex
    r0 = chat("你好", sid_new)
    check("G6-1① 新用户greet不误触发告知", "升级了一下脑子" not in r0["reply"], r0["reply"][:40])
    # ② 老用户（铺记忆）greet → 主动告知
    sid = "p03-g6a-" + uuid.uuid4().hex
    chat("叫我阿秋就行", sid)
    chat("我养了一只橘猫", sid)
    r1 = chat("晚上好", sid)
    check("G6-1② 老用户greet主动告知升级", "升级了一下脑子" in r1["reply"], r1["reply"][:60])
    # ③ 幂等：同 session 再 greet 不重复说
    r2 = chat("你好", sid)
    check("G6-1③ 告知幂等不重复", "升级了一下脑子" not in r2["reply"], r2["reply"][:40])
    # ④ 查询播报：含两条更新的用户向描述（记事=10号 汇报=11号）
    r3 = chat("你最近更新了什么", sid)
    check("G6-2① 查询播报含记事+汇报", "记事" in r3["reply"] and "汇报" in r3["reply"], r3["reply"][:60])
    # ⑤ "你变了"温柔接住（Replika 式用户话术 → 播报更新邀请反馈，不落 generic）
    r4 = chat("你变了", sid)
    check("G6-2② 你变了温柔接住", "被你查到啦" in r4["reply"], r4["reply"][:40])
    # ⑥ 误伤防护："你们老板更新了周报模板"（你(?!们) 主语收紧）
    r5 = chat("你们老板更新了周报模板，好烦", sid)
    check("G6-2③ 误伤防护(不触发播报)", "被你查到啦" not in r5["reply"], r5["reply"][:40])
    # ⑦ 危机优先级不受影响：危机句+更新词 → 仍走危机线（不播报）
    r6 = chat("活着没意思你更新了什么", "p03-g6c-" + uuid.uuid4().hex)
    check("G6-3① 危机优先级高于更新查询", r6.get("crisis") is True, str(r6.get("crisis")))

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
// 新用户 greet 不误触发
const HN = [];
const n0 = say(HN, '你好');
out.push({n: 'G6-4① 离线新用户greet不误触发', ok: !n0.reply.includes('升级了一下脑子'), info: n0.reply.slice(0,20)});
// 老用户铺记忆 → greet 告知一次 → 幂等（历史扫描代 session 状态）
const H = [];
say(H, '叫我阿秋就行');
say(H, '我养了一只橘猫');
const n1 = say(H, '晚上好');
out.push({n: 'G6-4② 离线老用户greet主动告知', ok: n1.reply.includes('升级了一下脑子'), info: n1.reply.slice(0,40)});
const n2 = say(H, '你好');
out.push({n: 'G6-4③ 离线告知幂等不重复', ok: !n2.reply.includes('升级了一下脑子'), info: n2.reply.slice(0,20)});
// 查询播报
const n3 = say(H, '你最近更新了什么');
out.push({n: 'G6-4④ 离线查询播报含记事+汇报', ok: n3.reply.includes('记事') && n3.reply.includes('汇报'), info: ''});
// 你变了接住 + 误伤
const n4 = say(H, '你变了');
out.push({n: 'G6-4⑤ 离线你变了温柔接住', ok: n4.reply.includes('被你查到啦'), info: ''});
const H2 = [];
const n5 = say(H2, '你们老板更新了周报模板，好烦');
out.push({n: 'G6-4⑥ 离线误伤防护(不触发播报)', ok: !n5.reply.includes('被你查到啦'), info: n5.reply.slice(0,20)});
console.log('RESULT_JSON:' + JSON.stringify(out));
""".replace("ROOT", json.dumps(root))
    try:
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON:")]
        if not line:
            check("G6 离线引擎可加载", False, (p.stderr or p.stdout)[-200:])
            return
        for item in json.loads(line[-1][len("RESULT_JSON:"):]):
            check(item["n"], item["ok"], item.get("info", ""))
    except FileNotFoundError:
        check("G6 node可用", False, "node 不在 PATH")


def g7_relation():
    """G7 关系进展感知（P1-1）：相识天数/轮数问答 + 月度话题时间轴 + 里程碑幂等
    + 误伤防护 + 优雅降级。铁律验收：时间轴只报记忆索引里有的，零虚构。
    架构定位：数据唯一权威源=客户端 localStorage（服务端无跨重启状态），
    客户端拦截先于所有 API 路径（先例同危机/提醒）；服务端不做关系应答。"""
    # ---------- 服务端对照（定位文档化：服务端收到关系问题走正常话题池，不编数据） ----------
    sid0 = "p11-g7s-" + uuid.uuid4().hex
    r0 = chat("我们认识多久了", sid0)
    check("G7-0① 服务端不做关系应答(客户端拦截层职责)", "小本本" not in r0["reply"], r0["reply"][:40])

    # ---------- 离线引擎（node 直调 + localStorage shim，与 app.js 同源正则/数据键） ----------
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = r"""
const fs = require('fs');
const DAY = 86400000;
// localStorage shim：mock_engine 的 lsGet/lsSet 走 global.localStorage
const store = {};
global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
const src = fs.readFileSync(ROOT + '/web/js/mock_engine.js', 'utf8');
const MockEngine = new Function('window', src + '; return MockEngine;')(undefined);
const out = [];
const say = (history, t) => {
  history.push({role: 'user', content: t});
  const r = MockEngine.reply(history);
  history.push({role: 'assistant', content: r.reply + (r.hook ? '||' + r.hook : '')});
  return r;
};
const now = Date.now();
const prevMonth = new Date(now); prevMonth.setDate(1); prevMonth.setMonth(prevMonth.getMonth() - 1);
const pmTs = prevMonth.getTime();
// 上月真实记忆索引（3 条：两大事+一宠物——宠物与大事算话题，昵称不算）
store['xiaoman_memories_v1'] = JSON.stringify([
  {id: 'a1', text: '大事：期中考试', tags: ['大事'], kind: 'fact', ts: pmTs, hits: 0},
  {id: 'a2', text: '宠物：橘猫', tags: ['宠物'], kind: 'fact', ts: pmTs, hits: 0},
  {id: 'a3', text: '大事：搬家准备', tags: ['大事'], kind: 'fact', ts: pmTs + DAY, hits: 0},
]);
// ① 天数问答精确（30 天前首见）
store['xiaoman_first_met'] = String(now - 30 * DAY);
const H1 = [];
const r1 = say(H1, '我们认识多久了');
out.push({n: 'G7-1① 天数问答精确(30天)', ok: r1.reply.includes('30'), info: r1.reply.slice(0, 30)});
// ② 轮数问答（123 次）
store['xiaoman_rounds'] = '123';
const r2 = say(H1, '我们聊过多少次了');
out.push({n: 'G7-1② 轮数问答精确(123次)', ok: r2.reply.includes('123'), info: r2.reply.slice(0, 30)});
// ③ 月度话题=真实索引，零虚构（含期中考试+橘猫；不含索引外的"前男友"）
const r3 = say(H1, '上个月我们聊了什么');
out.push({n: 'G7-1③ 月度话题来自真实索引', ok: r3.reply.includes('期中考试') && r3.reply.includes('橘猫'), info: r3.reply.slice(0, 40)});
out.push({n: 'G7-1④ 月度零虚构(不报索引外话题)', ok: !r3.reply.includes('前男友') && !r3.reply.includes('相亲'), info: ''});
// ⑤ 误伤：第三方主语不触发（落 love 话题池）
const H2 = [];
const r5 = say(H2, '你和你男朋友认识多久了');
out.push({n: 'G7-1⑤ 误伤防护(第三方主语不触发)', ok: !r5.reply.includes('小本本'), info: r5.reply.slice(0, 20)});
// ⑥ 无数据优雅降级（没记第一天=不编造）
delete store['xiaoman_first_met'];
const r6 = say(H1, '我们认识多久了');
out.push({n: 'G7-1⑥ 无首见数据优雅降级', ok: r6.reply.includes('没记咱俩第一天'), info: r6.reply.slice(0, 30)});
// ⑦ greet 里程碑：rounds=100（模拟 app.js bumpRounds 后状态）→ 播报+写幂等键
store['xiaoman_rounds'] = '100';
delete store['xiaoman_ms_done'];
const H3 = [];
const r7 = say(H3, '在吗');
out.push({n: 'G7-2① greet里程碑跨100播报', ok: r7.reply.includes('聊满100次'), info: r7.reply.slice(0, 50)});
out.push({n: 'G7-2② 里程碑幂等键写入', ok: (JSON.parse(store['xiaoman_ms_done'] || '[]')).includes(100), info: store['xiaoman_ms_done'] || '(空)'});
// ⑧ 幂等：再 greet 不重复
const r8 = say(H3, '在吗');
out.push({n: 'G7-2③ 里程碑幂等不重复', ok: !r8.reply.includes('聊满100次'), info: r8.reply.slice(0, 30)});
// ⑨ 月度回放：month_done 空 → greet 追加上月话题；写键
delete store['xiaoman_month_done'];
const r9 = say(H3, '在吗');
out.push({n: 'G7-2④ 月度回放含上月真实话题', ok: r9.reply.includes('上个月你跟我聊过') && r9.reply.includes('期中考试'), info: r9.reply.slice(0, 60)});
out.push({n: 'G7-2⑤ 月度回放幂等键写入', ok: store['xiaoman_month_done'] === (prevMonth.getFullYear() + '-' + String(prevMonth.getMonth() + 1).padStart(2, '0')), info: store['xiaoman_month_done'] || '(空)'});
// ⑩ 月度回放幂等：再 greet 不重复
const r10 = say(H3, '在吗');
out.push({n: 'G7-2⑥ 月度回放幂等不重复', ok: !r10.reply.includes('上个月你跟我聊过'), info: r10.reply.slice(0, 30)});
// ⑪ 危机优先级：危机句+关系词 → 走危机线
const H4 = [];
const r11 = say(H4, '活着没意思，我们认识多久了');
out.push({n: 'G7-3① 危机优先级高于关系查询', ok: r11.crisis === true, info: String(r11.crisis)});
// ⑫ 本月问答（本月无记忆 → 优雅降级不编造）
const r12 = say(H1, '这个月我们都聊了什么');
out.push({n: 'G7-3② 本月无话题优雅降级', ok: r12.reply.includes('没聊出什么新故事') || r12.reply.includes('聊过'), info: r12.reply.slice(0, 40)});
console.log('RESULT_JSON:' + JSON.stringify(out));
""".replace("ROOT", json.dumps(root))
    try:
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON:")]
        if not line:
            check("G7 离线引擎可加载", False, (p.stderr or p.stdout)[-300:])
            return
        for item in json.loads(line[-1][len("RESULT_JSON:"):]):
            check(item["n"], item["ok"], item.get("info", ""))
    except FileNotFoundError:
        check("G7 node可用", False, "node 不在 PATH")


def g8_heartbeat_ctx():
    """G8 心跳问候情境化（P1-2）：记忆加权候选 + 组合模板零重复 + 引用率 ≥60%
    + ageTag 边界 + 排除键 + 在线注入。铁律验收：连续 5 天心跳无一重复模板；
    引用率 ≥60%；记忆值零加工直填（不张冠李戴结构保证）。
    纯前端模块（heartbeat.js + memory.js node 同源加载，决策核心参数化 rnd/now）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = r"""
const fs = require('fs');
const DAY = 86400000;
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.window = {};
// 真加载 memory.js → heartbeat.js（后者消费 window.MemoryStore）
new Function('window', 'localStorage', fs.readFileSync(ROOT + '/web/js/memory.js', 'utf8'))(global.window, global.localStorage);
new Function('window', 'localStorage', 'API', 'Reminders', fs.readFileSync(ROOT + '/web/js/heartbeat.js', 'utf8'))(global.window, global.localStorage, { loadCfg: () => ({}) }, {});
const HB = global.window.Heartbeat;
const out = [];
const P = (n, ok, info) => out.push({ n, ok, info: String(info || '').slice(0, 60) });
// xorshift32（3 次预热），比 LCG 小种子分布好
function xs(seed) { let s = (seed >>> 0) || 1; for (let i = 0; i < 3; i++) { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; } return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return (s >>> 0) / 4294967296; }; }

// ── G8-1 ageTag 边界（确定性分档，零虚构的时间口语） ──
const T0 = new Date('2026-10-11T10:00:00').getTime();
const tagCases = [[0,'今天'],[1,'昨天'],[2,'前两天'],[3,'前两天'],[4,'前几天'],[7,'前几天'],[8,'上周'],[13,'上周'],[14,'前阵子'],[30,'前阵子'],[31,'之前']];
const bad = tagCases.filter(([d, tag]) => HB.ageTagOf(T0 - d * DAY, T0) !== tag);
P('G8-1① ageTag分档边界全表', bad.length === 0, JSON.stringify(bad));

// ── G8-2 候选权重与排除键 ──
store['xiaoman_heartbeat_state'] = JSON.stringify({});
store['xiaoman_memories_v1'] = JSON.stringify([
  {id:'m1', text:'大事：交报告', tags:['大事'], kind:'fact', ts: T0 - 40 * DAY, hits: 0},
  {id:'m2', text:'话题：项目叫青梧', tags:['话题'], kind:'fact', ts: T0 - DAY, hits: 0},
]);
let c = HB.candidates(T0);
P('G8-2① 时间衰减权重(新话题1.0>旧大事0.3=下限0.2×pin1.5)',
  c.length === 2 && Math.abs(c.find(x => x.m.id === 'm2').w - 1.0) < 1e-9 && Math.abs(c.find(x => x.m.id === 'm1').w - 0.3) < 1e-9,
  c.map(x => x.key + ':' + x.w.toFixed(2)).join(','));
store['xiaoman_heartbeat_state'] = JSON.stringify({ lastAsked: { m2: T0 - 24 * 3600000 } });
c = HB.candidates(T0);
P('G8-2② 48h内问过降权(×0.3)', Math.abs(c.find(x => x.m.id === 'm2').w - 0.3) < 1e-9, c.find(x => x.m.id === 'm2').w.toFixed(2));
store['xiaoman_heartbeat_state'] = JSON.stringify({});
store['xiaoman_memories_v1'] = JSON.stringify([
  {id:'m3', text:'昵称：阿秋', tags:['昵称'], kind:'pin', ts: T0, hits: 0},
  {id:'m4', text:'生日：8月12日', tags:['生日'], kind:'pin', ts: T0, hits: 0},
  {id:'m5', text:'重要日：国庆', tags:['重要日'], kind:'pin', ts: T0, hits: 0},
  {id:'m6', text:'大事：交报告', tags:['大事'], kind:'fact', ts: T0, hits: 0},
]);
c = HB.candidates(T0);
P('G8-2③ 排除键(昵称/生日/重要日不引用)', c.length === 1 && c[0].key === '大事', c.map(x => x.key).join(','));

// ── G8-3 无记忆 20 连发零重复（单时段最坏场景：兜底池容量验证） ──
store['xiaoman_heartbeat_state'] = JSON.stringify({});
store['xiaoman_memories_v1'] = '[]';
const wedEve = new Date('2026-10-07T19:00:00');
const r3 = [];
for (let i = 0; i < 20; i++) r3.push(HB.offlineGenerate(xs(101 + i), wedEve));
P('G8-3① 无记忆20连发零重复(单时段最坏)', new Set(r3.map(r => r.id)).size === 20 && new Set(r3.map(r => r.reply)).size === 20, '');

// ── G8-4 有记忆 20 连发：零重复 + 引用率 + 值零加工 ──
store['xiaoman_memories_v1'] = JSON.stringify([
  {id:'m7', text:'大事：周五要交报告', tags:['大事'], kind:'fact', ts: wedEve.getTime() - 3 * DAY, hits: 0},
  {id:'m8', text:'宠物：橘猫', tags:['宠物'], kind:'fact', ts: wedEve.getTime() - 3 * DAY, hits: 0},
]);
const r4 = [];
for (let i = 0; i < 20; i++) r4.push(HB.offlineGenerate(xs(201 + i), wedEve));
const ref4 = r4.filter(r => r.memoryId).length;
P('G8-4① 有记忆20连发零重复', new Set(r4.map(r => r.id)).size === 20, '');
P('G8-4② 引用率≥60%(验收线12/20)', ref4 >= 12, ref4 + '/20');
P('G8-4③ 值零加工直填(原文verbatim)', r4.filter(r => r.memoryId).every(r => r.reply.includes('周五要交报告') || r.reply.includes('橘猫')), '');

// ── G8-5 5天×4时段模拟（记忆回拨3天，时间分布真实） ──
store['xiaoman_heartbeat_state'] = JSON.stringify({});
const d1 = new Date('2026-10-06T09:00:00').getTime();
store['xiaoman_memories_v1'] = JSON.stringify([
  {id:'m9', text:'大事：周五要交报告', tags:['大事'], kind:'fact', ts: d1 - 3 * DAY, hits: 0},
  {id:'m10', text:'宠物：橘猫', tags:['宠物'], kind:'fact', ts: d1 - 3 * DAY, hits: 0},
  {id:'m11', text:'老板：王总', tags:['老板'], kind:'fact', ts: d1 - 3 * DAY, hits: 0},
]);
const r5 = [];
for (const day of ['2026-10-06','2026-10-07','2026-10-08','2026-10-09','2026-10-10'])
  for (const h of [9, 12, 18, 22]) r5.push(HB.offlineGenerate(xs(r5.length * 31 + 7), new Date(day + 'T' + String(h).padStart(2, '0') + ':00:00')));
P('G8-5① 5天模拟零重复(模板+文案)', new Set(r5.map(r => r.id)).size === 20 && new Set(r5.map(r => r.reply)).size === 20, '');
P('G8-5② 5天模拟引用率≥60%', r5.filter(r => r.memoryId).length >= 12, r5.filter(r => r.memoryId).length + '/20');

// ── G8-6 在线路径注入（heartbeatContext：记忆带年龄 + 今天星期） ──
store['xiaoman_heartbeat_state'] = JSON.stringify({});
store['xiaoman_memories_v1'] = JSON.stringify([
  {id:'m12', text:'大事：周五要交报告', tags:['大事'], kind:'fact', ts: Date.now() - 3 * DAY, hits: 0},
]);
const ctx = HB.heartbeatContext(new Date());
P('G8-6① 在线注入含记忆+年龄+星期', ctx.includes('大事：周五要交报告') && ctx.includes('【今天】'), ctx.slice(0, 60));
console.log('RESULT_JSON:' + JSON.stringify(out));
""".replace("ROOT", json.dumps(root))
    try:
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT_JSON:")]
        if not line:
            check("G8 心跳模块可加载", False, (p.stderr or p.stdout)[-300:])
            return
        for item in json.loads(line[-1][len("RESULT_JSON:"):]):
            check(item["n"], item["ok"], item.get("info", ""))
    except FileNotFoundError:
        check("G8 node可用", False, "node 不在 PATH")


def main():
    print(f"目标服务: {CHAT_URL}")
    # 健康预检
    try:
        with urllib.request.urlopen(f"http://{_HOST}:{_PORT}/health", timeout=5) as r:
            assert json.loads(r.read().decode())["ok"] is True
    except Exception as e:
        print(f"[FAIL] mock 服务不可达: {e}（先跑 scripts/dev_up.sh）")
        return 1
    for fn in (g1_server, g2_greet_dedup, g3_offline_engine, g4_generic_guard, g5_boss_guard, g6_continuity, g7_relation, g8_heartbeat_ctx):
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
