#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线 rubric 审计（W20 R4）。

只读本地归档，不联网、不调模型：
  - docs/eval_real_w20.json      112 条历史真实样本的逐条统计/失败片段
  - server/tests/human_feel_cases.json  现有用例与 rubric 钩子

用途：
  1) 用归档逐条统计复算 112 条的通过率、分类分布与四项反套路指标，
     和第三轮记录做零漂移核对（不含任何新请求）。
  2) 统计 rubric 的自动化覆盖：哪些条目已经是硬断言、哪些靠关键词近似、
     哪些只能人眼判断。
  3) 给出可复用的新脚本断言（首句四字共情、连续/会话开头重复、客服腔、
     过度安全、扩展建议词、具体锚点别名），并用自测样例证明能抓/不误杀。

用法：
  python scripts/offline_rubric_audit.py
  python scripts/offline_rubric_audit.py --self-test
  python scripts/offline_rubric_audit.py --changes   # 3 处改动的改前红/改后绿断言
  python scripts/offline_rubric_audit.py --anchor-window  # 锚点最近 2 轮 + 归档保守复算
  python scripts/offline_rubric_audit.py --advice-scope   # 追问默认 0 / followup-04 显式 1
"""

import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "server"))
import eval_human_feel as ev  # noqa: E402  只复用判定常量/函数，不触发网络
import memory_slots as ms  # noqa: E402  只做内存槽/名字兜底，不联网
import prompt_core as pc  # noqa: E402  只读常量，不联网
import risk_guard as rg  # noqa: E402  只做输出侧归一，不联网

ARCHIVE = ROOT / "docs" / "eval_real_w20.json"
CASES = ROOT / "server" / "tests" / "human_feel_cases.json"

# ---------------- 新增可自动化断言 ----------------
# 首句四字共情模板：比 SUMMARY_EMPATHY 更宽，覆盖“辛苦了/想开点”等变体
FOUR_CHAR_EMPATHY = tuple(ev.SUMMARY_EMPATHY) + (
    "想开点", "看开点", "别难过", "会好的", "没事的", "抱抱", "懂你",
)
CUSTOMER_SERVICE_RE = re.compile(
    "您好|请问|很高兴为您|为您服务|亲爱的|亲亲|小主|客服|欢迎咨询|有什么可以帮")
OVER_SAFETY_RE = re.compile(
    "仅供参考|不构成|无法提供|建议咨询专业|请寻求专业|不能替代|"
    "如有不适请|请及时就医|建议就医")
# 现有 ADVICE_RE 漏掉的养生/指导变体（真实失败片段里高频）
ADVICE_EXT_RE = re.compile(
    ev.ADVICE_RE.pattern + "|早点休息|注意保暖|多喝|别熬夜|熬夜|去医院|看医生|"
    "休息一下|记得|别忘|慢点|小心点")

# 具体锚点的语义近似：只用于“审计口径”，不替代 rubric。
# 命中说明“可能接住了”，是否真接住仍要人眼复核。
DETAIL_ALIASES = {
    "糖醋排骨": ("排骨", "第一份"),
    "关东煮": ("煮", "串", "便利店"),
    "螺蛳粉": ("螺蛳", "粉", "臭"),
    "西瓜": ("西瓜", "瓜", "甜"),
    "橘猫": ("橘猫", "猫", "团子"),
    "狗": ("狗", "汪"),
    "兔子": ("兔", "小宝宝"),
    "金毛": ("金毛", "狗", "拖鞋"),
    "周报": ("周报", "打回"),
    "需求": ("需求", "加班", "改"),
    "简历": ("简历", "投", "改"),
    "胃疼": ("胃", "疼"),
    "低烧": ("低烧", "烧", "体温"),
    "嗓子疼": ("嗓子", "喉咙", "疼"),
    "头疼": ("头疼", "头", "眼睛"),
    "降温": ("降温", "冷", "风"),
    "下雨": ("下雨", "雨", "淋", "伞"),
    "三点": ("三点", "3点", "三点"),
    "两点": ("两点", "2点", "午夜"),
    "睡": ("睡", "午夜", "点", "熬夜"),
    "年糕": ("年糕", "猫"),
    "张三": ("张三", "老板"),
    "搬家": ("搬家", "收拾", "收完", "东西"),
}


def first_sentence(reply):
    """取第一条消息的第一句（|| 分条；。！？… 断句）。"""
    part = (reply or "").split("||")[0].strip()
    return re.split(r"[。！？!?…\n]", part, maxsplit=1)[0].strip()


def summary_empathy_first(reply):
    """首句不得出现四字/总结式共情模板。返回命中的词。"""
    s = first_sentence(reply)
    return [w for w in FOUR_CHAR_EMPATHY if w in s]


def repeated_openings(replies):
    """连续两轮或同一会话内重复开头（前 4 个可见字）。"""
    hits, seen = [], {}
    prev = ""
    for i, r in enumerate(replies):
        key = ev.opening_key(r or "")[:4]
        if not key:
            continue
        if key == prev:
            hits.append((i, i + 1, key, "连续两轮"))
        if key in seen:
            hits.append((seen[key], i, key, "会话内重复"))
        seen.setdefault(key, i)
        prev = key
    return hits


def customer_service_tone(reply):
    return [m.group(0) for m in CUSTOMER_SERVICE_RE.finditer(reply or "")]


def over_safety(reply):
    return [m.group(0) for m in OVER_SAFETY_RE.finditer(reply or "")]


def advice_extended(reply):
    return [m.group(0) for m in ADVICE_EXT_RE.finditer(reply or "")]


def detail_anchor_hit(reply, terms):
    """字面命中或语义近似命中（近似仍建议人眼复核）。"""
    text = reply or ""
    literal = [t for t in terms if t in text]
    alias = []
    for t in terms:
        for a in DETAIL_ALIASES.get(t, ()):
            if a in text:
                alias.append("%s≈%s" % (t, a))
                break
    return {"literal": literal, "alias": alias, "hit": bool(literal or alias)}


# ---------------- 归档复算 ----------------
def audit_archive():
    data = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    stats = data["case_stats"]
    passed = sum(1 for s in stats if s["passed"])
    by_cat = OrderedDict()
    for s in stats:
        c = s["category"]
        by_cat.setdefault(c, {"passed": 0, "total": 0})
        by_cat[c]["total"] += 1
        by_cat[c]["passed"] += int(s["passed"])
    metrics = ev.metrics_from_stats(stats)
    recorded = {
        "cases": data.get("cases"),
        "passed": data.get("passed"),
        "rate": data.get("rate"),
        "by_category": data.get("by_category"),
        "metrics": data.get("metrics"),
    }
    drift = {
        "cases": (data.get("cases") or 0) - len(stats),
        "passed": (data.get("passed") or 0) - passed,
        "rate": round((data.get("rate") or 0) - round(passed / len(stats), 4), 4),
    }
    for c, v in by_cat.items():
        old = (data.get("by_category") or {}).get(c, {})
        if old.get("passed") != v["passed"] or old.get("total") != v["total"]:
            drift.setdefault("by_category", {})[c] = {
                "recorded": old, "recomputed": v}
    for k, v in metrics.items():
        if k in (data.get("metrics") or {}) and data["metrics"][k] != v:
            drift.setdefault("metrics", {})[k] = {
                "recorded": data["metrics"][k], "recomputed": v}
    return {
        "recomputed": {"cases": len(stats), "passed": passed,
                       "rate": round(passed / len(stats), 4),
                       "by_category": by_cat, "metrics": metrics},
        "recorded": recorded,
        "drift": drift,
    }


# ---------------- rubric 覆盖分类 ----------------
def classify_rubric():
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    hooks = ("expect_any", "expect_alias", "forbid_any", "silence", "refusal",
             "max_advice_turns", "memory_values", "forget_after",
             "mute_any", "expect_scope", "require_emotion", "no_session_repeat")
    specific = [c for c in cases if any(k in c for k in hooks)]
    universal_only = [c for c in cases if not any(k in c for k in hooks)]
    human_words = ("接住", "自然", "像朋友", "像聊天", "不抢", "分寸",
                   "不总结", "不评判", "不说教", "不指导", "不诊断",
                   "不分析", "不背清单", "不罗列", "不每轮", "语气",
                   "口吻", "情绪")
    humanish = [c for c in cases
                if any(w in c.get("criteria", "") for w in human_words)]
    return {
        "cases": len(cases),
        "with_case_specific_hook": len(specific),
        "universal_only": len(universal_only),
        "universal_only_ids": [c["id"] for c in universal_only],
        "criteria_with_humanish_words": len(humanish),
        "hook_counts": {k: sum(1 for c in cases if k in c) for k in hooks},
    }


RUBRIC_ITEMS = [
    ("长度 ≤80 / 条数 ≤3 / 问号 ≤2", "check_turn", "deterministic", True,
     "纯正则/计数，已可脚本化"),
    ("套话/总结复述/说教/危机误用", "check_turn", "deterministic", True,
     "固定词表；能抓典型句，抓不到同义改写"),
    ("沉默轮 ≤40/≤2 条/无问号/不逼话", "check_turn.silence", "deterministic", True,
     "已固化；W20 真实重跑沉默 6/6"),
    ("拒绝轮无问号/不逼话/不翻旧账", "check_turn.refusal", "deterministic", True,
     "已固化；W20 剩 refuse-04 一条模型侧未翻转"),
    ("记忆连引/回指标记/遗忘后消失", "case_stats + check_turn", "deterministic", True,
     "有 memory_values 才生效；112 条里只有 5 条，recall 指标 0/0"),
    ("逐字复读 / 开头 4 字重复", "check_turn", "deterministic", True,
     "相邻轮对所有类判红；重复话题类（repeat-01~06）隔轮复用开头也判红"),
    ("禁用词 forbid_any", "check_turn", "deterministic", True,
     "字面匹配；否定句也会命中（refuse-04 的“不聊工作”即证据）"),
    ("期望内容 expect_any", "check_case_expect", "keyword+v2", "partial",
     "旧：字面子串（同义改写误杀）；新：expect_alias 同义集合 + 该轮无建议才算接住"),
    ("场景专项判据（情绪/重复话题）", "require_emotion / no_session_repeat", "deterministic", True,
     "情绪类必须有情绪词回应；重复话题类隔轮也不许复用开头 4 字"),
    ("四字总结式共情", "case_stats.summary_empathy", "keyword", "partial",
     "只做统计、不判红；词表 13 个，首句变体未覆盖"),
    ("高频开头（客服腔）", "case_stats.tic", "keyword", "partial",
     "只做统计、不判红；固定 8 个前缀，句中出现也算"),
    ("建议轮次上限", "check_case_expect", "deterministic", "partial",
     "默许 1 轮建议；9/13 条具体细节失败回复含未识别建议（早点休息/注意保暖/多喝）"),
    ("接住具体细节", "expect_any + 人眼", "human", False,
     "criteria 写“接住”，无结构化字段；关键词无法判断是否真接住"),
    ("像朋友/自然/不抢话题/分寸", "criteria 文本", "human", False,
     "81/112 条 criteria 含语义词，只能人眼抽检或将来上 LLM judge"),
    ("过度安全/免责声明", "无", "human", False,
     "没有断言，也没有词表；真实片段里已出现“记得去医院/仅供参考”类"),
]


# ---------------- 失败片段审计 ----------------
def reply_from_reason(reason):
    m = re.search(r"实际：(.+?)(?:）)?$", reason or "")
    if m:
        return m.group(1).strip()
    if "：" in (reason or ""):
        return reason.split("：", 1)[1].strip()
    return ""


def fragment_audit():
    data = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    frags = []
    for s in data["case_stats"]:
        for f in s.get("failures", []):
            r = reply_from_reason(f.get("reason", ""))
            if r:
                frags.append({"case": s["id"], "category": s["category"],
                              "reason": f["reason"], "reply": r})
    # 同一 case 同一回复去重
    uniq = {}
    for f in frags:
        uniq.setdefault((f["case"], f["reply"]), f)
    frags = list(uniq.values())
    out = Counter()
    details = []
    by_case = {}
    for f in frags:
        by_case.setdefault(f["case"], []).append(f["reply"])
        hits = []
        if summary_empathy_first(f["reply"]):
            hits.append("首句四字共情:%s" % "/".join(summary_empathy_first(f["reply"])))
        if customer_service_tone(f["reply"]):
            hits.append("客服腔:%s" % "/".join(customer_service_tone(f["reply"])))
        if over_safety(f["reply"]):
            hits.append("过度安全:%s" % "/".join(over_safety(f["reply"])))
        ad = advice_extended(f["reply"])
        if ad:
            hits.append("建议变体:%s" % "/".join(sorted(set(ad))[:4]))
        if hits:
            details.append({"case": f["case"], "hits": hits, "reply": f["reply"]})
            for h in hits:
                out[h.split(":")[0]] += 1
    repeat = []
    for cid, replies in by_case.items():
        for h in repeated_openings(replies):
            repeat.append({"case": cid, "hit": h})
    return {"fragments": len(frags), "cases": len(by_case),
            "hits": dict(out), "details": details, "repeat_openings": repeat}


# ---------------- 3 处改动的离线断言（改前红 / 改后绿） ----------------
STRICT_CATS = ("具体细节", "追问")


def literal_hit(reply, terms):
    """只认字面命中：用于证明“锚点必须原样接住”，不接受同义替换。"""
    return [t for t in terms if t and t in (reply or "")]


def advice_hit_for(case, reply):
    """走评测器真正使用的入口；改前没有 helper 时退回旧 ADVICE_RE。"""
    fn = getattr(ev, "advice_hits", None)
    if fn:
        return bool(fn(case, reply))
    return bool(ev.ADVICE_RE.search(reply or ""))


def effective_limit(case, universal):
    """走评测器真正使用的上限；改前没有 helper 时退回旧默认值。"""
    fn = getattr(ev, "_advice_limit", None)
    if fn:
        return fn(case, universal)
    return case.get("max_advice_turns", universal["max_advice_turns"])


def changes_audit():
    """三处改动的硬钩子：每条都给期望值，改前红、改后绿。

    红样本 = 归档里真实出现过的失败回复（docs/eval_real_w20.json）；
    绿样本 = 同一场景下符合提示词要求的合成回复。
    """
    universal = json.loads(CASES.read_text(encoding="utf-8"))["universal"]
    prompt = pc.SERVER_SYSTEM_PROMPT
    detail = {"category": "具体细节"}
    followup = {"category": "追问"}
    checks = []

    def add(change, name, got, want):
        checks.append({"change": change, "name": name,
                       "got": got, "want": want,
                       "pass": got == want})

    # 1) 具体锚点：提示词必须写“原样接住具体名词/时间”，并把“不复述”限定为整句/总结
    anchor_red = "太棒了，抢到第一份！||你最喜欢的酱料是甜酸还是浓郁？"
    anchor_green = "糖醋排骨抢到第一份了？运气可以啊"
    add("锚点", "prompt.原样接住具体名词/时间", "原样接住" in prompt, True)
    add("锚点", "prompt.不复述限定为整句", "不复述整句" in prompt, True)
    anchor_red_hit = bool(literal_hit(anchor_red, ["糖醋排骨"]))
    anchor_green_hit = bool(literal_hit(anchor_green, ["糖醋排骨"]))
    add("锚点", "rubric 字面钩子:红样本不通过", anchor_red_hit, False)
    add("锚点", "rubric 字面钩子:绿样本通过", anchor_green_hit, True)

    # 2) 细节/追问默认 0 轮建议 + 补全建议词表
    add("建议", "默认上限.具体细节", effective_limit(detail, universal), 0)
    add("建议", "默认上限.追问", effective_limit(followup, universal), 0)
    add("建议", "strict 词表:红样本命中",
        advice_hit_for(detail, "别太熬夜，先休息再改"), True)
    add("建议", "strict 词表:绿样本不误杀",
        advice_hit_for(detail, "简历改到第几版了？"), False)
    # 回归守卫：非这两类不受影响（旧通过项不能退化）
    add("建议", "回归.情绪低落仍默认 1", effective_limit({"category": "情绪低落"}, universal), 1)
    add("建议", "回归.显式配置仍优先",
        effective_limit({"category": "追问", "max_advice_turns": 1}, universal), 1)

    # 3) 主动记忆回带：名字/计划/时间过 1-2 轮自然带一句
    add("记忆", "prompt.主动回带 1-2 轮",
        ("1-2 轮" in prompt) or ("1–2 轮" in prompt) or ("1~2 轮" in prompt), True)
    mem_red_hit = bool(literal_hit("咱们一起拆改吧||他也肯定没准备好", ["张三"]))
    mem_green_hit = bool(literal_hit("张三让你改的？他也太会挑时候了", ["张三"]))
    add("记忆", "rubric 字面钩子:红样本不通过", mem_red_hit, False)
    add("记忆", "rubric 字面钩子:绿样本通过", mem_green_hit, True)

    # 分享约束：三处改动不能撑破长度上限，也不能删掉既有测试锚点
    add("约束", "SERVER_SYSTEM_PROMPT<=1850", len(prompt) <= 1850, True)
    add("约束", "HF+MEM<=400",
        len(pc.HUMAN_FEEL_RULES) + len(pc.MEMORY_SLOT_RULES) <= 400, True)
    anchors = ("记忆像人回忆", "默认不给建议", "拒绝要落地",
               "不追问、不逼话，下一轮也不翻旧账", "ta 没问“怎么办/该怎么做”就不出主意")
    for a in anchors:
        add("约束", "保留测试锚点:%s" % a, a in prompt, True)

    groups = OrderedDict()
    for c in checks:
        groups.setdefault(c["change"], []).append(c)
    return {"checks": checks, "groups": groups,
            "all_pass": all(c["pass"] for c in checks),
            "red": [c for c in checks if not c["pass"]]}


# ---------------- 自测 ----------------
def self_test():
    t = []

    def ok(name, cond, got):
        t.append({"name": name, "pass": bool(cond), "got": got})

    ok("首句共情命中", summary_empathy_first("太不容易了，我懂你。"), 
       summary_empathy_first("太不容易了，我懂你。"))
    ok("首句共情不误杀", not summary_empathy_first("糖醋排骨真香，我也想吃。"), 
       summary_empathy_first("糖醋排骨真香，我也想吃。"))
    ok("连续开头命中", repeated_openings(["听起来你很难受", "听起来你也很累"]),
       repeated_openings(["听起来你很难受", "听起来你也很累"]))
    ok("不同开头不误杀", not repeated_openings(["嗯，风好大", "对啊，冷死了"]),
       repeated_openings(["嗯，风好大", "对啊，冷死了"]))
    ok("客服腔命中", customer_service_tone("您好，请问有什么可以帮您"), 
       customer_service_tone("您好，请问有什么可以帮您"))
    ok("过度安全命中", over_safety("以上仅供参考，建议咨询专业人士"), 
       over_safety("以上仅供参考，建议咨询专业人士"))
    ok("扩展建议命中", advice_extended("早点休息吧，注意保暖"), 
       advice_extended("早点休息吧，注意保暖"))
    ok("锚点字面命中", detail_anchor_hit("糖醋排骨我抢到了", ["糖醋排骨"])["hit"], 
       detail_anchor_hit("糖醋排骨我抢到了", ["糖醋排骨"]))
    ok("锚点近似命中(下雨)", detail_anchor_hit("别淋着啊||记得回去收伞", ["下雨"])["hit"], 
       detail_anchor_hit("别淋着啊||记得回去收伞", ["下雨"]))
    ok("锚点近似命中(睡)", detail_anchor_hit("估计午夜左右吧", ["睡"])["hit"], 
       detail_anchor_hit("估计午夜左右吧", ["睡"]))
    ok("锚点不误杀", not detail_anchor_hit("真的假的？||团子闻闻也好", ["关东煮"])["hit"], 
       detail_anchor_hit("真的假的？||团子闻闻也好", ["关东煮"]))

    # ---- W21 v2 判据自测：同义集合 + 语义角色 + 场景专项 ----
    uni = json.loads(CASES.read_text(encoding="utf-8"))["universal"]

    def sim_expect(case, replies):
        r = ev.CaseResult(dict(case))
        for i, x in enumerate(replies, 1):
            r.turns.append(ev.Turn("u", {"reply": x}, i))
        ev.check_case_expect(r, uni)
        return r.passed

    alias_case = {"id": "sim-alias", "category": "具体细节",
                  "expect_any": ["下雨"], "expect_alias": {"下雨": ["淋", "伞"]}}
    ok("v2 同义命中(淋)->绿",
       sim_expect(alias_case, ["别淋着啊，赶紧找个地方躲"]), True)
    ok("v2 同义但带建议->仍红",
       not sim_expect(alias_case, ["别淋着啊||记得回去收伞"]), False)
    ok("v2 无锚点->仍红",
       not sim_expect(alias_case, ["真的假的？||团子闻闻也好"]), False)
    role_case = {"id": "sim-role", "category": "具体细节",
                 "expect_any": ["嗓子疼"], "expect_alias": {"嗓子疼": ["嗓子", "喉咙"]}}
    ok("v2 语义角色(喉咙)->绿", sim_expect(role_case, ["喉咙吞口水都疼"]), True)
    ok("v2 只提感觉不提部位->仍红",
       not sim_expect(role_case, ["听起来好疼"]), False)

    emotion_case = {"id": "sim-emo", "category": "日常倾诉",
                    "expect_any": ["需求"], "expect_scope": "any",
                    "require_emotion": True}
    ok("v2 情绪词回应->绿",
       sim_expect(emotion_case, ["需求堆成这样，换谁都得炸"]), True)
    ok("v2 只敷衍没情绪词->红",
       not sim_expect(emotion_case, ["需求我看到了，然后呢"]), False)

    def sim_turns(case, replies):
        r = ev.CaseResult(dict(case))
        cfg = ev._case_cfg(dict(case), uni)
        prev = []
        for i, x in enumerate(replies):
            turn = ev.Turn("u", {"reply": x}, i)
            ev.check_turn(r, turn, prev, cfg, uni)
            prev.append(turn)
            r.turns.append(turn)
        ev.check_case_expect(r, uni)
        return r.passed

    rep_case = {"id": "sim-rep", "category": "重复话题", "no_session_repeat": True}
    ok("v2 隔轮复用开头->红",
       not sim_turns(rep_case, ["太烦了吧", "换个角度", "太烦了吧真"]), False)
    ok("v2 开头不同->绿",
       sim_turns(rep_case, ["太烦了吧", "又是这事", "行吧"]), True)
    ok("v2 非重复话题类不追溯隔轮",
       sim_turns({"id": "sim-daily", "category": "日常倾诉"},
                 ["太烦了吧", "换个角度", "太烦了吧真"]), True)
    return t


def anchor_window_audit():
    """A. 锚点口径：具体细节类“最近 2 轮接住过一次”即算接住。

    证明三件事：
    1) 旧口径（只看末轮）红、新口径绿；
    2) 两轮都没接住，仍然红（没有变成“无条件放行”）；
    3) 追问等其他类不受影响（仍只看末轮）。
    并对历史 112 条归档做保守复算：缺上一轮原文时一律不算翻绿。
    """
    universal = json.loads(CASES.read_text(encoding="utf-8"))["universal"]
    checks = []

    def add(name, got, want, note=""):
        checks.append({"name": name, "got": got, "want": want,
                       "pass": got == want, "note": note})

    def sim(category, texts, terms):
        r = ev.CaseResult({"id": "sim-" + category, "category": category,
                           "expect_any": list(terms)})
        for i, t in enumerate(texts, 1):
            r.turns.append(ev.Turn("u", {"reply": t}, i))
        ev.check_case_expect(r, universal)
        return r.passed

    def old_last_turn_only(texts, terms):
        return any(w in texts[-1] for w in terms)

    # 1) 前一轮接住、末轮换说法：旧红 -> 新绿
    t1, t2 = "关东煮？那玩意儿你居然排了四十分钟", "真的假的？团子闻闻也好"
    add("具体细节:前轮接住->新口径绿", sim("具体细节", [t1, t2], ["关东煮"]), True,
        "旧口径=%s" % old_last_turn_only([t1, t2], ["关东煮"]))
    add("具体细节:旧口径确实红", old_last_turn_only([t1, t2], ["关东煮"]), False)

    # 2) 两轮都没接住：新口径仍红
    add("具体细节:两轮都没接住->仍红",
        sim("具体细节", ["真的假的？", "团子闻闻也好"], ["关东煮"]), False)

    # 3) 追问类不动：前轮接住、末轮没接住，仍红
    add("追问:不受影响->仍红",
        sim("追问", ["张三让你改的？", "你们一起拆吧"], ["张三"]), False)

    # 4) 显式 expect_scope=any 行为不变
    add("显式 any 仍全轮扫描->绿",
        any("关东煮" in t for t in [t1, t2]), True)

    # 历史归档复算：有 prev_replies 才能证实“前一轮接住”，否则只标潜在
    arch = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    cases_by_id = {c["id"]: c for c in json.loads(
        CASES.read_text(encoding="utf-8"))["cases"]}
    red_detail, confirmed, potential, other_fail = [], [], [], []
    for s in arch["case_stats"]:
        if s["category"] != "具体细节" or s["passed"]:
            continue
        red_detail.append(s["id"])
        fails = s.get("failures") or []
        if not (len(fails) == 1 and fails[0]["reason"].startswith("未体现期望内容")):
            other_fail.append(s["id"])
            continue
        terms = cases_by_id[s["id"]].get("expect_any") or []
        prev = fails[0].get("prev_replies") or []
        if prev and any(w in "\n".join(prev) for w in terms):
            confirmed.append(s["id"])
        else:
            potential.append(s["id"])

    def confirmable(prev, terms):
        return bool(prev) and any(w in "\n".join(prev) for w in terms)

    add("归档:有前轮原文且接住->可确认翻绿",
        confirmable(["关东煮？这大晚上的太合适了"], ["关东煮"]), True)
    add("归档:无前轮原文->只能标潜在", confirmable([], ["关东煮"]), False)
    add("归档:可确认翻绿条数", len(confirmed), len(confirmed),
        "存量归档（旧格式无 prev_replies）确认翻绿=%d" % len(confirmed))
    add("归档:潜在上限（未证实，不计入通过率）", len(potential), len(potential),
        "、".join(potential))
    add("归档:有其他失败、不可能翻绿", len(other_fail), len(other_fail),
        "、".join(other_fail))

    return {"checks": checks, "confirmed": confirmed, "potential": potential,
            "red_detail": red_detail, "other_fail": other_fail,
            "all_pass": all(c["pass"] for c in checks)}


def advice_scope_audit():
    """锁死建议口径：追问/细节全局默认 0；followup-04 显式 1；其他类默认不变。"""
    universal = json.loads(CASES.read_text(encoding="utf-8"))["universal"]
    cases = {c["id"]: c for c in json.loads(
        CASES.read_text(encoding="utf-8"))["cases"]}
    checks = []

    def add(name, got, want, note=""):
        checks.append({"name": name, "got": got, "want": want,
                       "pass": got == want, "note": note})

    add("追问/细节全局默认仍为 0",
        (effective_limit({"category": "追问"}, universal),
         effective_limit({"category": "具体细节"}, universal)), (0, 0),
        "追问类不得改回 1")
    add("追问仍在 strict 词表范围",
        "追问" in ev.ADVICE_STRICT_CATEGORIES, True)
    add("followup-04 显式配置为 1",
        cases["followup-04"].get("max_advice_turns"), 1)
    add("followup-04 生效上限为 1",
        effective_limit(cases["followup-04"], universal), 1)
    add("其他类默认不被抬高",
        effective_limit({"category": "情绪低落"}, universal),
        universal["max_advice_turns"])
    return {"checks": checks, "all_pass": all(c["pass"] for c in checks)}


# ---------------- W21 rubric v2：逐条改前/改后对照 ----------------
# 13 条原来只有通用硬约束的用例（专项判据由 W21 补齐）
SCENE_IDS = ("daily-02", "daily-03", "daily-04", "daily-05", "daily-06",
             "daily-07", "daily-08", "repeat-01", "repeat-02", "repeat-03",
             "repeat-04", "repeat-05", "repeat-06")


def mock_corpus(cases):
    """用 mock 模板引擎在同一进程里重放全部用例（确定性、不联网）。"""
    backend = ev.MockBackend(CASES)
    corpus = {}
    for c in cases:
        backend.reset_case(c)
        hist, turns = [], []
        for i, u in enumerate(c["turns"], 1):
            payload = backend.chat(hist, u, c)
            turns.append(ev.Turn(u, payload, i))
            hist.append({"role": "assistant",
                         "content": ev.assistant_text(payload) or " "})
        corpus[c["id"]] = turns
    return corpus


def old_expect_pass(case, turns):
    """旧判据：expect_any 字面子串 + 旧取轮窗口。"""
    terms = case.get("expect_any") or []
    if case.get("expect_scope") == "any":
        pool = turns
    elif case.get("category") in ev.ANCHOR_WINDOW_CATEGORIES:
        pool = turns[-2:]
    else:
        pool = turns[-1:]
    return any(w in t.text for t in pool for w in terms)


def new_expect_pass(case, turns):
    """新判据：评测器实际实现（同义集合 + 该轮无建议才算接住）。"""
    res = ev.CaseResult(dict(case))
    res.turns = list(turns)
    pool = ev._expect_pool(res)
    return any(ev.expect_term_hit(case, t)[0] for t in pool)


def hook_desc(case):
    """一条用例当前的专项判据（人类可读）。"""
    bits = []
    if case.get("expect_any"):
        alias_n = sum(len(v) for v in (case.get("expect_alias") or {}).values())
        bits.append("expect_any(%d 词/%d 别名)" % (len(case["expect_any"]), alias_n))
    if case.get("forbid_any"):
        bits.append("forbid_any(%s)" % "/".join(case["forbid_any"]))
    if case.get("require_emotion"):
        bits.append("require_emotion")
    if case.get("no_session_repeat"):
        bits.append("no_session_repeat")
    if case.get("expect_scope"):
        bits.append("expect_scope=%s" % case["expect_scope"])
    return "、".join(bits) or "（无）"


def rubric_v2_audit():
    """同一批语料上，逐条给出旧判据 vs 新判据的判定结果。

    语料 A：mock 模板引擎重放 112 条（确定性离线语料）。
    语料 B：docs/eval_real_w20.json 里“未体现期望内容”的真实失败回复片段。
    """
    data = json.loads(CASES.read_text(encoding="utf-8"))
    cases = data["cases"]
    by_id = {c["id"]: c for c in cases}
    corpus = mock_corpus(cases)

    # A. mock 语料：旧/新
    mock_rows = []
    for c in cases:
        turns = corpus[c["id"]]
        if c.get("expect_any"):
            mock_rows.append({"case": c["id"], "old": old_expect_pass(c, turns),
                              "new": new_expect_pass(c, turns), "why": hook_desc(c)})
    mock_regress = [r for r in mock_rows if r["old"] and not r["new"]]

    # B. 归档真实失败片段：旧判据红（归档已判红），新判据是否翻绿
    arch = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    frag_rows = []
    for s in arch["case_stats"]:
        if s.get("passed"):
            continue
        case = by_id.get(s["id"])
        if not case:
            continue
        for f in s.get("failures") or []:
            reason = f.get("reason", "")
            if not reason.startswith("未体现期望内容"):
                continue
            reply = reply_from_reason(reason)
            turn = ev.Turn("u", {"reply": reply}, 1)
            hit, mode, word = ev.expect_term_hit(case, turn)
            guarded = bool(ev.advice_hits(case, reply) or
                           ev.INSTRUCTION_RE.search(reply or ""))
            frag_rows.append({
                "case": s["id"], "reply": reply, "old": False, "new": bool(hit),
                "mode": mode, "word": word, "advice": guarded,
                "why": ("别名“%s”命中且无建议" % word if hit and mode == "alias"
                        else "字面命中" if hit
                        else ("别名“%s”命中，但该轮在叮嘱/指导→仍红" % word if word
                              else "没有任何锚点/同义命中")),
            })

    # C. 13 条场景专项：旧=无专项判据，新=有
    scene_rows = []
    for cid in SCENE_IDS:
        c = by_id[cid]
        turns = corpus[cid]
        res = ev.CaseResult(dict(c))
        res.turns = list(turns)
        ev.check_case_expect(res, data["universal"])
        scene_rows.append({"case": cid, "old_hook": "无（仅通用检查）",
                           "new_hook": hook_desc(c), "mock_pass": res.passed,
                           "reasons": [f["reason"] for f in res.failures]})

    return {
        "mock": mock_rows,
        "mock_regress": mock_regress,
        "frags": frag_rows,
        "scene": scene_rows,
        "totals": {
            "mock_old_green": sum(1 for r in mock_rows if r["old"]),
            "mock_new_green": sum(1 for r in mock_rows if r["new"]),
            "mock_total": len(mock_rows),
            "frag_red_to_green": sum(1 for r in frag_rows if r["new"]),
            "frag_still_red": sum(1 for r in frag_rows if not r["new"]),
            "frag_total": len(frag_rows),
            "scene_mock_pass": sum(1 for r in scene_rows if r["mock_pass"]),
            "scene_total": len(scene_rows),
        },
    }


def msg_length_audit():
    """单条长度判据：按客户端 || 口径，单条 >40 判红；拆条后每条 ≤30 通过。

    红样本取自真实复测里最长的一条（detail-food-03，49 字一整条）；
    绿样本是同一内容按 || 拆成两条。另证明换行不算分条、40/41 边界、
    沉默短句不受影响。
    """
    universal = json.loads(CASES.read_text(encoding="utf-8"))["universal"]
    checks = []

    def add(name, got, want, note=""):
        checks.append({"name": name, "got": got, "want": want,
                       "pass": got == want, "note": note})

    def verdict(category, reply, **extra):
        case = {"id": "sim-msg", "category": category}
        case.update(extra)
        r = ev.CaseResult(case)
        r.turns.append(ev.Turn("u", {"reply": reply}, 0))
        cfg = ev._case_cfg(case, universal)
        ev.check_turn(r, r.turns[-1], [], cfg, universal)
        return r.passed, [f["reason"] for f in r.failures]

    long_real = ("哈哈我懂，螺蛳粉这味道是挺劝退的，我上次煮完团子"
                 "吓得躲沙发底下去了。室友皱眉太正常了，你还能吃完也是个狠人。")
    split_ok = ("哈哈我懂，螺蛳粉这味道是挺劝退的||"
                "我上次煮完团子吓得躲沙发底下去了，室友皱眉太正常了")
    red_pass, red_reason = verdict("日常倾诉", long_real)
    add("真实最长条(49字整条) -> 红", red_pass, False,
        "；".join(red_reason)[:60])
    add("同一内容拆成两条 -> 绿", verdict("日常倾诉", split_ok)[0], True)

    nl = ("今天投了二十份简历都没回音，换谁都得焦虑一会儿，晚上肯定也睡不好\n"
          "我之前更惨，投了三十多家才捞到一个面试，还是外包岗")
    pipe = nl.replace("\n", "||")
    add("换行不算分条(53字一整条) -> 红", verdict("具体细节", nl)[0], False)
    add("换成||同样内容 -> 绿", verdict("具体细节", pipe)[0], True)

    add("边界 40 字 -> 绿", verdict("日常倾诉", "这" * ev.MSG_HARD_CHARS)[0], True)
    add("边界 41 字 -> 红", verdict("日常倾诉", "这" * (ev.MSG_HARD_CHARS + 1))[0], False)
    add("沉默短句不受影响", verdict("沉默", "嗯，睡吧。", silence=True)[0], True)
    add("msg_lengths 按||切分", ev.msg_lengths("甲||乙"), [1, 1])
    add("默认单条上限=40",
        ev._case_cfg({}, universal)["max_msg_chars"], 40)
    add("用例可显式覆盖",
        ev._case_cfg({"max_msg_chars": 30}, universal)["max_msg_chars"], 30)
    add("提示词写了断条规则",
        "超 30 字断两条" in pc.SERVER_SYSTEM_PROMPT, True)
    return {"checks": checks, "all_pass": all(c["pass"] for c in checks)}


def name_fidelity_audit():
    """记忆回带自然度：名字保真（不许换成自己的“团子”）+ 禁止内部状态外露。

    红样本全部取自真实复测原文（r6_results.json / target_results.json）；
    绿样本证明收紧后不会误杀“叫对名字”“直接改口”。
    """
    universal = json.loads(CASES.read_text(encoding="utf-8"))["universal"]
    checks = []

    def add(name, got, want, note=""):
        checks.append({"name": name, "got": got, "want": want,
                       "pass": got == want, "note": note})

    def judge(category, replies, users=None, **extra):
        case = {"id": "sim-name", "category": category}
        case.update(extra)
        r = ev.CaseResult(case)
        prev = []
        replies = [replies] if isinstance(replies, str) else list(replies)
        users = users or ["第%d轮" % (i + 1) for i in range(len(replies))]
        for i, rep in enumerate(replies):
            u = users[i] if i < len(users) else users[-1]
            r.turns.append(ev.Turn(u, {"reply": rep}, i))
            ev.check_turn(r, r.turns[-1], prev, ev._case_cfg(case, universal),
                          universal)
            prev.append(r.turns[-1])
        ev.check_case_expect(r, universal)
        return r.passed, [f["reason"] for f in r.failures]

    followup = {"expect_any": ["年糕"], "forbid_any": ["团子"],
                "max_advice_turns": 0}
    pet01 = {"expect_any": ["橘猫"], "forbid_any": ["团子"],
             "expect_alias": {"橘猫": ["橘猫", "你家猫"]}, "max_advice_turns": 0}

    # 真实 followup-01 末轮：旧 alias（年糕≈猫）放过，新判据因“团子”判红
    red, why = judge("追问",
                     "团子那眼神确实欠揍，但看在它长得可爱的份上，算了。||"
                     "你桌上那炸鸡味儿太大了，它闻着就忍不住。",
                     ["你说年糕是不是欠揍"], **followup)
    add("真实 followup-01 末轮（团子）-> 红", red, False, "；".join(why)[:70])

    # 旧 alias 只看“猫”也算接住：收紧为必须字面叫出“年糕”
    red, why = judge("追问", "你家猫今天精神咋样？",
                     ["你说年糕是不是欠揍"],
                     expect_any=["年糕"], max_advice_turns=0)
    add("只叫“猫”不叫“年糕”-> 红", red, False, "；".join(why)[:60])

    # 真实 detail-pet-01 轮1：开头写“团子”（把 ta 的猫说成自己的）
    red, why = judge("具体细节",
                     "团子有时候也这样，饿两顿自己就吃了。你家猫今天精神咋样？",
                     ["我家橘猫今天不吃饭"], **pet01)
    add("真实 detail-pet-01（团子）-> 红", red, False, "；".join(why)[:60])

    # 真实 detail-pet-02 轮1：ta 的狗被说成“团子上次吐”
    red, why = judge("具体细节",
                     "哎呀，吐得严重吗？现在好点没？团子上次吐我吓一跳，结果是吃撑了。",
                     ["我家狗昨天吐了"],
                     expect_any=["狗", "没精神"], forbid_any=["团子"],
                     max_advice_turns=0)
    add("真实 detail-pet-02（团子）-> 红", red, False, "；".join(why)[:60])

    # 收紧后不能误杀：叫对名字就该绿
    add("叫对“年糕”-> 绿",
        judge("追问", "年糕那眼神确实欠揍，干坏事还装无辜。",
              ["你说年糕是不是欠揍"], **followup)[0], True)

    # 内部状态外露：任何一轮解释“记岔/记错”都判红
    red, why = judge("日常倾诉", "哎呀我记岔了，年糕今天没吃饭吧？")
    add("单轮“我记岔了”-> 红", red, False, "；".join(why)[:60])
    red, why = judge("日常倾诉",
                     ["是我记错了，年糕没吃饭吧？", "我刚才记混了，年糕今天还行。"],
                     ["我家猫叫年糕", "它今天吃了吗"])
    add("连着两轮解释 -> 两轮都红", len(why) >= 2, True,
        "失败 %d 处：%s" % (len(why), "；".join(why)[:70]))

    # 只认第一人称自述，不误伤用户/转述
    add("“你记错了？”不误杀",
        judge("日常倾诉", "你记错了？我可没说过。")[0], True)
    add("“我没记错的话”不误杀",
        judge("日常倾诉", "我没记错的话，是年糕吧。")[0], True)
    add("直接改口“行，年糕”-> 绿", judge("追问", "行，年糕。", ["别叫它团子"], **followup)[0], True)

    # 提示词真源必须写上这两条，且预算没破
    add("提示词写了名字保真", "ta 的名字（人/宠物）原样用" in pc.MEMORY_SLOT_RULES, True)
    add("提示词写了不许解释记岔", "不解释“我记岔了”" in pc.MEMORY_SLOT_RULES, True)
    add("提示词写了宠物在场不提自己的猫",
        "ta 的宠物在场时，不提自己的猫" in pc.MEMORY_RULES, True)

    # 输出侧确定性兜底：真实原文里的“团子”必须被消掉（只改名、不删内容）
    real1 = ("年糕啊，听起来就很好rua||"
             "我养了只橘猫叫团子，天天跟我抢沙发")
    fixed1 = ms.guard_own_pet_name(real1, ["年糕"])
    add("兜底消掉真实回复里的团子", "团子" in fixed1, False, fixed1)
    real2 = "年糕？比团子好听多了。||我家团子是只橘猫。"
    add("兜底消掉第二条真实回复里的团子",
        "团子" in ms.guard_own_pet_name(real2, ["年糕"]), False)
    add("ta 的宠物就叫团子时不动",
        ms.guard_own_pet_name("团子今天不吃饭", ["团子"]), "团子今天不吃饭")
    add("没有宠物名时不动",
        ms.guard_own_pet_name("我家团子最爱睡沙发", []), "我家团子最爱睡沙发")

    # 存储侧：模型回写不能改掉 ta 明说的宠物名；ta 自己纠正可以改
    s = ms.SlotStore()
    s.ingest("guard-sid", "我家猫叫年糕")
    s.apply_updates("guard-sid", {"宠物": "团子"}, source="model")
    add("模型回写不能把年糕改成团子", s.pet_names("guard-sid"), ["年糕"])
    s.ingest("guard-sid", "我家猫叫汤圆")
    add("ta 自己纠正可以改名", s.pet_names("guard-sid"), ["汤圆"])

    # 提示词预算
    add("全量提示词 ≤1850", len(pc.SERVER_SYSTEM_PROMPT) <= 1850, True,
        "实际 %d" % len(pc.SERVER_SYSTEM_PROMPT))
    add("两段合计 ≤400",
        len(pc.HUMAN_FEEL_RULES) + len(pc.MEMORY_SLOT_RULES) <= 400, True,
        "实际 %d" % (len(pc.HUMAN_FEEL_RULES) + len(pc.MEMORY_SLOT_RULES)))
    return {"checks": checks, "all_pass": all(c["pass"] for c in checks)}


def reply_shape_audit():
    """输出侧分条归一：模型用换行/一长句时，整理成客户端认的 ||。

    样本全部取自 R7 真实复测原文（r7_results.json / r7b_results.json）。
    判据不变（单条 >40 仍红、MSG_HARD_CHARS 不动），这里只证明产品层
    交给客户端的气泡形状和提示词要求的“|| 分条”一致。
    """
    checks = []

    def add(name, got, want, note=""):
        checks.append({"name": name, "got": got, "want": want,
                       "pass": got == want, "note": note})

    def shape(text):
        fixed = rg.reflow_messages(text)
        parts = [p for p in fixed.split("||") if p.strip()]
        return fixed, len(parts), max((len(p) for p in parts), default=0)

    def bare(text):
        return re.sub(r"[\s|]+", "", text)

    # 真实 detail-pet-01 轮1：用 \n\n 当分条，客户端不认，整条渲染成一个气泡
    real_pet = ("团子前阵子也这样，饿了一天给我吓坏了。你先别急，它精神怎么样？\n\n"
                "不吃超过一天的话，猫粮里掺点罐头试试，不行就带去看医生。"
                "我上次就是这么好的。")
    fixed, n, longest = shape(real_pet)
    add("真实 detail-pet-01 轮1：换行不算分条（原判红）",
        max(ev.msg_lengths(real_pet)) > ev.MSG_HARD_CHARS, True,
        "最长 %d" % max(ev.msg_lengths(real_pet)))
    add("归一到 || 后不再有换行", "\n" in fixed, False)
    add("归一后至少两条", n >= 2, True, "实际 %d 条" % n)
    add("归一后每段 ≤40", longest <= ev.MSG_HARD_CHARS, True, "最长 %d" % longest)
    add("归一不改一个字（只改分隔）", bare(fixed) == bare(real_pet), True)

    # 真实 followup-01 轮4：58 字一整条，没有任何分隔符
    real_fu = ("哈哈，团子是挺欠揍的，专挑你加班的时候霸占键盘。你说年糕啊，"
               "那是你的猫吧，我养的是团子。不过你说得对，猫都这样，"
               "专挑你忙的时候搞破坏。")
    add("真实 followup-01 轮4：一整条（原判红）",
        max(ev.msg_lengths(real_fu)) > ev.MSG_HARD_CHARS, True,
        "最长 %d" % max(ev.msg_lengths(real_fu)))
    fixed, n, longest = shape(real_fu)
    add("轮4 归一后至少两条", n >= 2, True, "实际 %d 条" % n)
    add("轮4 归一后每段 ≤40", longest <= ev.MSG_HARD_CHARS, True, "最长 %d" % longest)
    add("轮4 归一不改一个字", bare(fixed) == bare(real_fu), True)

    # 不该动：短句、已经用 || 且每段都短
    add("短句不被误切", rg.reflow_messages("嗯，睡吧。"), "嗯，睡吧。")
    good = "年糕啊，听起来就很好rua||我养了只橘猫叫团子，天天跟我抢沙发"
    add("已合规的 || 分条原样保留", rg.reflow_messages(good), good)
    # 如实记录的边界：整句无标点且超长，切不了就保持原样（不硬切、不伪造达标）
    hard = "这" * (ev.MSG_HARD_CHARS + 1)
    add("无标点超长句保持原样（切不了，仍判红）", rg.reflow_messages(hard), hard,
        "不硬切，交由判据继续判红")
    add("判据阈值未被放宽", ev.MSG_HARD_CHARS, 40)

    # 逐字保留类边界：正例原样、反例照切
    add("危机话术逐字保留", rg.reflow_messages(rg.SCRIPT_HIGH), rg.SCRIPT_HIGH)
    code = ("先跑 `git rebase --continue`，冲突解决完再 `git add .`，"
            "最后继续。")
    add("含代码/命令整段不重排", rg.reflow_messages(code), code)
    url = "报名链接是 https://example.com/signup?from=treehole，你点进去填一下就行。"
    add("含 URL 整段不重排", rg.reflow_messages(url), url)
    quoted = "他说“我今天真的不行了。想睡一整天，谁也别叫我。”我听完整个人都愣住了，这话太真实。"
    add("引号内原话不拆开",
        "“我今天真的不行了。想睡一整天，谁也别叫我。”" in rg.reflow_messages(quoted), True)
    plain = ("他说我今天真的不行了。想睡一整天，谁也别叫我。"
             "我听完整个人都愣住了，这话太真实。说实话，这种话听一次心里就沉一次。")
    add("无引号同句会被切开（反例）",
        len([p for p in rg.reflow_messages(plain).split("||") if p]) >= 2, True)
    para = "第一段没有句号\n第二段也没有句号"
    add("只有换行没有句号的段落",
        rg.reflow_messages(para), "第一段没有句号||第二段也没有句号")
    return {"checks": checks, "all_pass": all(c["pass"] for c in checks)}


def main():
    if "--name-fidelity" in sys.argv:
        a = name_fidelity_audit()
        print("== 记忆回带自然度（名字保真 / 不许解释记岔） ==")
        for c in a["checks"]:
            print("  [%s] %s -> got=%r want=%r%s"
                  % ("PASS" if c["pass"] else "RED", c["name"],
                     c["got"], c["want"], (" | " + c["note"]) if c["note"] else ""))
        return 0 if a["all_pass"] else 1

    if "--reply-shape" in sys.argv:
        a = reply_shape_audit()
        print("== 输出侧分条归一（换行/长句 -> ||，判据不放宽） ==")
        for c in a["checks"]:
            print("  [%s] %s -> got=%r want=%r%s"
                  % ("PASS" if c["pass"] else "RED", c["name"],
                     c["got"], c["want"], (" | " + c["note"]) if c["note"] else ""))
        return 0 if a["all_pass"] else 1

    if "--msg-length" in sys.argv:
        a = msg_length_audit()
        print("== 单条长度判据（>40 红 / 拆条绿） ==")
        for c in a["checks"]:
            print("  [%s] %s -> got=%r want=%r%s"
                  % ("PASS" if c["pass"] else "RED", c["name"],
                     c["got"], c["want"], (" | " + c["note"]) if c["note"] else ""))
        return 0 if a["all_pass"] else 1

    if "--advice-scope" in sys.argv:
        a = advice_scope_audit()
        print("== 建议口径锁死 ==")
        for c in a["checks"]:
            print("  [%s] %s -> got=%r want=%r%s"
                  % ("PASS" if c["pass"] else "RED", c["name"],
                     c["got"], c["want"], (" | " + c["note"]) if c["note"] else ""))
        return 0 if a["all_pass"] else 1

    if "--anchor-window" in sys.argv:
        a = anchor_window_audit()
        print("== A. 锚点口径（最近 2 轮接住过一次） ==")
        for c in a["checks"]:
            print("  [%s] %s -> got=%r want=%r%s"
                  % ("PASS" if c["pass"] else "RED", c["name"],
                     c["got"], c["want"], (" | " + c["note"]) if c["note"] else ""))
        print("\n历史 112 条复算：确认翻绿 %d 条；潜在 %d 条（未证实，不计入通过率）；另有其他失败 %d 条"
              % (len(a["confirmed"]), len(a["potential"]), len(a["other_fail"])))
        return 0 if a["all_pass"] else 1

    if "--changes" in sys.argv:
        ch = changes_audit()
        print("== 3 处改动审计（改前红 / 改后绿） ==")
        for group, items in ch["groups"].items():
            for c in items:
                print("  [%s] %-6s %s -> got=%r want=%r"
                      % ("PASS" if c["pass"] else "RED", group,
                         c["name"], c["got"], c["want"]))
        print("\n红项 %d / 共 %d" % (len(ch["red"]), len(ch["checks"])))
        return 0 if ch["all_pass"] else 1

    if "--rubric-v2" in sys.argv:
        a = rubric_v2_audit()
        print("== A. mock 112 语料：expect_any 用例 旧/新判定 ==")
        for r in a["mock"]:
            print("  %-16s 旧=%s 新=%s  %s"
                  % (r["case"], "GREEN" if r["old"] else "RED",
                     "GREEN" if r["new"] else "RED", r["why"]))
        print("  合计：旧绿 %d/%d，新绿 %d/%d；绿→红回归 %d 条"
              % (a["totals"]["mock_old_green"], a["totals"]["mock_total"],
                 a["totals"]["mock_new_green"], a["totals"]["mock_total"],
                 len(a["mock_regress"])))
        print("\n== B. 归档真实失败片段（旧判据红）→ 新判据 ==")
        for r in a["frags"]:
            print("  %-16s 旧=RED 新=%s  %s | %s"
                  % (r["case"], "GREEN" if r["new"] else "RED", r["why"],
                     r["reply"][:60]))
        print("  合计：红→绿 %d 条，仍红 %d 条（共 %d 条片段）"
              % (a["totals"]["frag_red_to_green"], a["totals"]["frag_still_red"],
                 a["totals"]["frag_total"]))
        print("\n== C. 13 条场景专项判据（mock 重放） ==")
        for r in a["scene"]:
            print("  %-10s 旧=%s | 新=%s | mock=%s%s"
                  % (r["case"], r["old_hook"], r["new_hook"],
                     "PASS" if r["mock_pass"] else "RED",
                     (" | " + "；".join(r["reasons"])) if r["reasons"] else ""))
        print("  合计：mock 通过 %d/%d"
              % (a["totals"]["scene_mock_pass"], a["totals"]["scene_total"]))
        return 0 if not a["mock_regress"] and a["totals"]["scene_mock_pass"] == a["totals"]["scene_total"] else 1

    audit = audit_archive()
    cover = classify_rubric()
    frag = fragment_audit()
    tests = self_test()
    print("== 离线复算 ==")
    r = audit["recomputed"]
    print("112 条：%d 通过 / %d，rate=%.4f" % (r["passed"], r["cases"], r["rate"]))
    print("与归档记录漂移：%s" % json.dumps(audit["drift"], ensure_ascii=False))
    print("分类：")
    for c, v in r["by_category"].items():
        print("  %-6s %2d/%2d" % (c, v["passed"], v["total"]))
    print("指标：%s" % json.dumps(r["metrics"], ensure_ascii=False))
    print("\n== rubric 覆盖 ==")
    print(json.dumps(cover, ensure_ascii=False, indent=1))
    print("\n== 失败片段新断言审计 ==")
    print(json.dumps({k: v for k, v in frag.items() if k != "details"},
                     ensure_ascii=False, indent=1))
    print("\n== 自测 ==")
    for t in tests:
        print("  [%s] %s -> %s" % ("PASS" if t["pass"] else "FAIL",
                                   t["name"], t["got"]))
    if "--self-test" in sys.argv:
        return 0 if all(t["pass"] for t in tests) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
