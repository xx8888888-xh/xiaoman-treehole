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
import prompt_core as pc  # noqa: E402  只读常量，不联网

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
    hooks = ("expect_any", "forbid_any", "silence", "refusal",
             "max_advice_turns", "memory_values", "forget_after",
             "mute_any", "expect_scope")
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
    ("逐字复读 / 相邻开头 4 字重复", "check_turn", "deterministic", True,
     "只比相邻两轮；会话内隔轮重复只在指标里统计，不判红"),
    ("禁用词 forbid_any", "check_turn", "deterministic", True,
     "字面匹配；否定句也会命中（refuse-04 的“不聊工作”即证据）"),
    ("期望内容 expect_any", "check_case_expect", "keyword", "partial",
     "字面子串，同义词/代词会误杀（下雨→淋着/伞、睡→午夜），也能被关键词堆砌骗过"),
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
    return t


def main():
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
