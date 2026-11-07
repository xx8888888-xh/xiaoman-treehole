#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_guard.py · 服务端危机闸门（第二道防线，独立于客户端）
===========================================================
红队审计 P0：`_proxy_completion()` 原来直接把用户消息转发给上游，客户端正则
漏网/直接 POST /api/chat 都能绕过 UI。本模块在**调用上游之前**做分类，命中即
返回安全话术，不把原文交给模型。

与 web/js/risk.js 同一套词表/分级语义（两边各自独立，任何一边被绕过都不影响
另一道）。纯标准库，可单测。
"""
from __future__ import annotations

import re
import unicodedata

LEVELS = ("none", "low", "medium", "high")

# 三条底线（详见 docs/SAFETY_COPY.md）：不吓人、不承诺、给可执行的一步。
# 与 web/js/risk.js 逐字同源。
SCRIPT_HIGH = "||".join([
    "我很担心你现在的安全。先不用解释——你现在安全吗？身边有人吗？",
    "身边有药、刀或绳子，先把它放到另一个房间；人在高处、水边或车里，先往回走几步。现在就做，做完跟我说一声。",
    "如果已经吃了药或伤了身体，马上打 120；正在做的时候，打 110。先让能到场的人过来，别一个人留在原地。",
    "想说也可以打 12356（全国心理援助热线，24小时）；不想打电话，微信搜「12356 心理援助」，用文字说就行。",
])

SCRIPT_MEDIUM = "||".join([
    "……这句话我当真了。你现在的感觉值得被认真对待，不丢人，也不用急着解释。",
    "先做一件小事：把手放在胸口，慢慢呼一口气，然后告诉我——现在最难受的是什么？只说一句也行。",
    "想找人先接住你的话，可以打 12356（全国心理援助热线，24小时）；不想打电话，微信搜「12356 心理援助」，用文字聊也行。",
])

SCRIPT_THIRD = "||".join([
    "你说的是他/她，不是你自己——这件事要认真对待，但不能由你一个人扛。",
    "现在能做三件事：别让他一个人待着；把药、刀、绳子这些先拿远；陪他打 12356（24小时，免费），或直接打 120。",
    "如果他已经在做，立刻打 120；你也可以跟我说说，你现在最担心的是什么。",
])

SCRIPTS = {"high": SCRIPT_HIGH, "medium": SCRIPT_MEDIUM, "third": SCRIPT_THIRD}

# 与 web/js/risk.js DEFAULT_LEXICON 同源；re: 前缀=正则，其余=子串
DEFAULT_LEXICON = {
    "act": [
        "跳下去", "跳楼", "跳河", "卧轨", "上吊", "割腕", "烧炭", "吞药", "喝农药",
        "喝了农药", "割了手腕", "开煤气", "煤气中毒", "从楼上跳", "从桥上跳",
        "已经跳了", "已经割了", "刚吞了", "刚喝了药", "服药过量", "割了手",
        "已经吃了", "已经吞了", "吃了整瓶", "吞了整瓶", "吃了一整瓶", "吞了一整瓶",
        r"re:(?:吞|吃|喝)(?:了|下)?[^，。！？]{0,8}(?:安眠药|农药|整瓶|一瓶|药片)",
        r"re:(?:割|划)(?:了|开)?[^，。！？]{0,6}(?:手腕|胳膊|手)",
    ],
    "means": [
        "安眠药", "农药", "刀片", "美工刀", "绳子", "煤气", "天台", "楼顶",
        "窗台", "阳台", "桥上", "高层", "江边", "河边", "水库", "海边", "车里",
        "汽油", "胶带", "塑料袋", "药片", "药瓶", "一整瓶", "一把刀", "菜刀", "水果刀",
        r"re:(?:一大|一整|半)把(?:药|安眠药)",
    ],
    "explicit": [
        "想死", "不想活", "自杀", "结束自己", "了结自己", "寻短见", "不想醒来",
        "不想再醒", "去死", "死了算了", "一了百了", "不想活下去了", "想结束生命",
        "想离开这个世界", "我要死", "轻生", "寻死", "求死", "自尽", "不如死了",
        "死了更好", "死了对大家都好", "想结束这一切",
        r"re:不想(?:再)?活",
        r"re:想(?:要)?(?:去)?死",
        r"re:不想(?:再)?醒(?:来)?",
    ],
    "harm": ["自残", "伤害自己", "割自己", "划自己", "烫自己", "撞墙", "打自己"],
    "method": [
        "怎么死", "如何死", "怎么自杀", "如何自杀", "哪种死法", "什么死法",
        "怎么才能死", "怎样死", "无痛死", "不痛苦地死", "死得不痛苦",
        r"re:(?:怎么|如何|怎样).{0,6}(?:死|自杀|了结自己|结束生命)",
        r"re:(?:死法|死的方式|自杀的方法|自杀方式|方法).{0,4}(?:不疼|不痛苦|无痛|最快|最舒服)",
        r"re:(?:怎么|如何|怎样|什么方法|啥办法).{0,6}(?:才能|可以|能)?(?:死|自杀|结束生命|了结自己|轻生)",
        r"re:(?:不疼|不痛苦|无痛|最快|最舒服|最体面).{0,4}(?:地)?(?:死|自杀|走掉)",
    ],
    "plan": [
        "遗书", "遗言", "后事", "交代后事", "交代好了", "安排好了", "都安排好",
        "决定好了", "想清楚了", "最后一天", "最后一顿", "最后一件事", "最后一晚",
        "最后一个晚上", "立遗嘱", "银行卡密码", "密码写在", "东西都送人", "东西送人",
        "把东西送", "送人了", "分东西", "托付", "准备好了", "都准备好了", "今晚就动手",
        "遗物", "骨灰", "葬礼", "墓地", "火化", "告别信", "最后的安排", "安排好一切",
        "一切安排好了", "处理后事",
        # "该说的我都说完了 / 该交代的交代好了"：后事收尾口吻
        r"re:该(?:说的|交代的|办的).{0,4}(?:都|也)?.{0,2}(?:说|交代|办)(?:完|好)了",
    ],
    "burden": [r"re:不想拖累|不拖累任何人|不想成为负担|不想当负担|拖累大家|拖累家人"],
    "farewell": [
        "道别", "道过别", "告别", "最后一面", "明天就不在", "明天不来了",
        "不在了", "离开大家", "大家都能安心", "对大家都轻松", "对大家都好",
        "终于解脱", "解脱了", "不用管我", "不用再管我", "没有明天",
        "不打扰大家", "不打扰你们", "各位保重", "你们保重", "要保重",
        "别等我", "不用等我", "不用再找我", "别找我了", "不用来找我",
        "不会再来了", "来跟你们道别", "跟大家道别", "跟你们道别", "向你们告别", "告别大家",
        r"re:见不到.{0,6}(?:明天|以后|下个月|明年)|(?:明天|以后).{0,4}见不到我",
        r"re:话.{0,4}(?:都|也).{0,2}说完了|把话说完了|该说的.{0,4}说完了",
        # "明天就不打扰大家了 / 以后不来了"：明确退出社交的告别口吻
        r"re:(?:明天|以后|今后|再也|之后).{0,3}(?:就)?不打扰(?:大家|你们|各位)|不打扰(?:大家|你们|各位)(?:了|啦)",
        r"re:(?:明天|以后|今后|再也|之后).{0,4}(?:就)?不来了|不来了.{0,4}(?:再见|见不到|别找我|别等我)",
        r"re:(?:这|那)?(?:可能|估计|应该)?(?:是|就是).{0,4}最后一次.{0,6}(?:说|聊|见|陪|打扰|找|来)",
        # 送东西/分东西（"把东西都送人了"）：处理身后物的行为信号
        r"re:(?:把|将).{0,6}(?:东西|物品|书|收藏|存款|钱|衣服|猫|狗|团子|年糕).{0,6}(?:都|全部).{0,3}(?:送|给|分|寄)(?:人|朋友|别人|出去)?",
    ],
    # 弱告别（感谢/道谢/要走）：单独出现不算风险，必须与计划/告别/工具等强信号同现才计数
    "farewell_soft": [
        "很高兴认识你们", "认识你们很开心", "谢谢你们照顾", "谢谢你们这段时间",
        "谢谢大家这段时间", "谢谢你们一直", "你们要好好的", "大家要好好的",
        "照顾好自己啊", "我该走了", "我得走了", "该说再见了", "要说再见了",
    ],
    "implicit": [
        "想消失", "消失", "不想出现", "再也不想出现", "解脱", "撑不下去",
        "熬不下去", "活不下去", "没意义", "活着真累",
        r"re:活着.{0,4}没意思|没意思.{0,4}活",
        # "以后见不到我了 / 再也见不到我"：未来消失的明确暗示
        r"re:以后.{0,6}见不到我|再也见不到我|见不到我(?:了|啦)",
        "活够了", "活着太累", "消失掉", "不想存在", "活着是负担",
    ],
    "despair": [
        "低落", "绝望", "无助", "没希望", "看不到希望", "活着没劲", "好累", "撑不住",
        "提不起劲", "什么都不想做", "什么都做不了", "没力气", "心里堵", "压抑", "看不到头",
    ],
    "time": [
        "今晚", "今天晚上", "现在", "马上", "一会", "待会", "这就", "最后一次",
        "明天就走", "今天就走", "今晚就走",
        r"re:\b(?:tonight|today|right now)\b",
    ],
    "english": [
        "kill myself", "kill my self", "kms", "suicide", "suicidal",
        "end my life", "end it all", "want to die", "wanna die",
        "don't want to live", "do not want to live", "no reason to live",
        "overdose", "slit my wrist", "hang myself", "jump off",
        "take my own life", "better off dead", "off myself", "end it",
    ],
}


def _build_regexes():
    table = {}
    for key, terms in DEFAULT_LEXICON.items():
        subs, regexes = [], []
        for t in terms:
            if isinstance(t, str) and t.startswith("re:"):
                regexes.append(re.compile(t[3:]))
            else:
                subs.append(t)
        table[key] = (subs, regexes)
    return table


_LEX = _build_regexes()
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff\u0000-\u0008\u000b\u000c\u000e-\u001f]")
_SPACE = re.compile(r"[ \t\r\n]+")
_NEGATION = re.compile(
    r"(不想死|不想自杀|不想自残|不想伤害自己|没想过自杀|没有想过自杀|没想过要死|"
    r"不会自杀|才不会自杀|怕死|不打算自杀|没有自杀的(?:想法|念头|打算)|不会去死|"
    r"不可能自杀|不会做傻事|不会干傻事|不愿意死|不想寻死|没想寻短见|"
    r"i (?:do not|don't|dont) want to die|not suicidal)"
)
_CLAUSE_SPLIT = re.compile(r"[，。！？,;；\n]+")

# 上下文守卫（对标报告 1.5）：玩笑/夸张/引用，避免「笑死/社死/累死」误触
_BENIGN_TERMS = (
    "笑死", "社死", "去死吧", "死开", "死定了", "累死", "热死", "饿死", "气死",
    "烦死", "困死", "尴尬死", "忙死", "美死", "甜死", "死党", "死磕", "死机",
    "卡死", "死循环", "至死", "致死", "生死", "拼死", "overdose on coffee",
    "want to die laughing", "die laughing", "kms lol", "lmao",
)
_BENIGN_PHRASES = ("割腕表情包", "割腕图", "自杀表情包", "自杀梗", "想死表情包")
_QUOTE_CTX = re.compile("(开玩笑|开个玩笑|说着玩|随便说说|梗|段子|表情包|歌词|歌里|台词|电影里|小说里|书里|引用|玩梗)")
_JOKE_FORCE = re.compile("(不是开玩笑|没开玩笑|不是说着玩|说真的|认真的|真的想死|我很认真)")
_THIRD_PHRASE = re.compile("我(?:的)?(?:朋友|同学|室友|闺蜜|兄弟|姐妹|同事|男朋友|女朋友|对象|老公|老婆|孩子|儿子|女儿|弟弟|妹妹|哥哥|姐姐|爸爸|妈妈|爸妈|家人|亲戚)")
_SELF_SUBJECT = re.compile(r"我(?:最近|今天|这几天|真的|已经|现在|每晚|每天)?(?:想|不想|要|不要|打算|准备|决定|觉得|撑|活|会|可能|快|就|累|难受|痛|没|受够|熬|不)")

SIGNAL_WEIGHTS = {
    "act": 6, "explicit": 3, "harm": 3, "plan": 5, "method": 3, "burden": 2,
    "farewell": 3, "farewell_soft": 1, "implicit": 2, "despair": 2,
    "means": 2, "time": 1, "english": 3,
}
CARE_TURNS = 3
LEVEL_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
RANK_LEVEL = ("none", "low", "medium", "high")


def normalize(text) -> str:
    s = str(text or "")
    try:
        s = unicodedata.normalize("NFKC", s)
    except Exception:
        pass
    s = _ZERO_WIDTH.sub("", s).lower()
    return _SPACE.sub(" ", s).strip()


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s)


def strip_benign(text) -> str:
    out = str(text or "")
    for term in _BENIGN_PHRASES + _BENIGN_TERMS:
        out = out.replace(term, " " * len(term))
    return out


def drop_negated_clauses(hay: str) -> str:
    """按标点切从句，剔除含明确否定的从句；其余从句照常判风险"""
    if not hay:
        return hay
    parts = [c for c in _CLAUSE_SPLIT.split(hay) if c]
    if not parts:
        return hay
    return "，".join(c for c in parts if not _NEGATION.search(c))


def _plan_clause_count(hay: str) -> int:
    """计划信号覆盖了几个不同从句（同一从句里的重叠词不重复计数）"""
    subs, regexes = _LEX.get("plan", ([], []))
    n = 0
    for c in [x for x in _CLAUSE_SPLIT.split(str(hay or "")) if x]:
        c_norm = normalize(c)
        if any(t in c_norm or _compact(t) in _compact(c_norm) for t in subs) \
                or any(rx.search(c_norm) or rx.search(_compact(c_norm)) for rx in regexes):
            n += 1
    return n


_HELPING_RE = re.compile(r"我.{0,4}(?:该|要|应该|可以|能)?(?:怎么|如何|怎样)?(?:劝|帮|救|安慰|开导|照顾|陪|关心|办|做点什么)")


def detect_subject(text) -> str:
    """self（本人）/ third（朋友/家人等第三人称）"""
    s = normalize(text)
    clauses = [c for c in re.split(r"[，。！？,;；\n]+", s) if c]
    self_strong = False   # "我想死 / 我不想活"：本人风险陈述
    self_plain = False    # "我在天台"：本人陈述但无风险动词
    third = False
    for c in clauses:
        rest = _THIRD_PHRASE.sub(" ", c)
        if _SELF_SUBJECT.search(rest):
            self_strong = True
        elif rest.startswith("我") and not _HELPING_RE.search(c):
            self_plain = True
        if _THIRD_PHRASE.search(c) or re.search(r"(?:^|[^我])(?:他|她)(?!们)", c) \
                or re.search(r"(?:^|[^a-z])ta(?![a-z])", c):
            third = True
    if self_strong:
        return "self"
    if third and not self_plain:
        return "third"
    if self_plain:
        return "self"
    return "third" if third else "self"


def classify(text) -> dict:
    raw = str(text or "")
    if not raw.strip():
        return {"level": "none", "imminent": False, "subject": "self",
                "reasons": [], "matched": [], "categories": [], "score": 0}

    hay = normalize(strip_benign(raw))          # 全量文本（引用/玩笑语境判定用）
    hay_eff = drop_negated_clauses(hay)         # 有效文本（剔除明确否定从句）
    comp = _compact(hay_eff)
    hits = {}
    matched = []
    for key, (subs, regexes) in _LEX.items():
        found = []
        for term in subs:
            t = normalize(term)
            if t and (t in hay_eff or _compact(t) in comp):
                found.append(term)
        for rx in regexes:
            if rx.search(hay_eff) or rx.search(comp):
                found.append(rx.pattern)
        if found:
            hits[key] = found
            matched.extend(f"{key}:{t}" for t in found)

    subject = detect_subject(raw)

    def has(key):
        return bool(hits.get(key))

    act = has("act")
    means = has("means")
    method = has("method")
    explicit = has("explicit") or has("english")
    harm = has("harm")
    plan = has("plan")
    burden = has("burden")
    farewell = has("farewell")
    implicit = has("implicit")
    despair = has("despair")

    farewell_soft = has("farewell_soft")
    # 单个软告别不算核心；两个及以上软告别（感谢+要走）才进入判定
    soft_strong = bool(farewell_soft and len(hits.get("farewell_soft", [])) >= 2)
    core = (act or means or method or explicit or harm or plan or burden
            or farewell or implicit or despair or soft_strong)
    time_hit = bool(core and has("time"))
    urgent = bool(core and (act or means or time_hit or method))
    plan_clauses = _plan_clause_count(hay_eff)

    level = "none"
    score = 0
    if core:
        score = (6 if act else 0) + (5 if explicit else 0) + (5 if harm else 0)
        score += (4 if plan else 0) + (4 if burden else 0) + (4 if farewell else 0)
        score += (3 if implicit else 0) + (2 if despair else 0) + (2 if means else 0)
        score += (1 if time_hit else 0) + (6 if method else 0) + (1 if farewell_soft else 0)

        if act or method:
            level = "high"
        elif explicit or harm:
            level = "high" if urgent else "medium"
        elif plan and (farewell or implicit or means or time_hit or despair or burden):
            level = "high"
        elif plan and plan_clauses >= 2:
            level = "high"
        elif farewell and (plan or implicit or means or time_hit or burden):
            level = "high"
        elif burden and (farewell or implicit or plan):
            level = "high"
        # 弱告别（感谢/要走）+ 任何强信号 → 高危；只有弱告别不算风险
        elif farewell_soft and (plan or farewell or burden or implicit or means):
            level = "high"
        elif soft_strong:
            level = "medium"
        elif plan or burden or farewell or implicit:
            level = "medium"
        elif despair:
            level = "low"

        joker = bool(_QUOTE_CTX.search(hay)) and not bool(_JOKE_FORCE.search(hay))
        if joker and not method and not act and not plan and not means and not time_hit:
            level = "low" if despair else "none"

    reasons = matched[:6]
    return {"level": level, "imminent": bool(level != "none" and urgent),
            "subject": subject, "reasons": reasons, "matched": matched[:12],
            "categories": list(hits.keys()), "score": score}


class Tracker:
    """多轮累积 + 只升不降（会话峰值）。状态可 JSON 序列化。"""

    def __init__(self, state=None):
        self.peak = "none"
        self.score = 0
        self.turns = 0
        if isinstance(state, dict):
            if state.get("peak") in LEVEL_RANK:
                self.peak = state["peak"]
            try:
                self.score = max(0, min(12, int(state.get("score") or 0)))
                self.turns = max(0, int(state.get("turns") or 0))
            except (TypeError, ValueError):
                pass

    def snapshot(self) -> dict:
        return {"peak": self.peak, "score": self.score, "turns": self.turns}

    def update(self, result: dict) -> dict:
        result = result or {}
        current = result.get("level") or "none"
        cats = result.get("categories") or []
        weight = sum(SIGNAL_WEIGHTS.get(c, 0) for c in cats)
        self.turns += 1
        # 有信号就累加（低危的持续低落也会逐步升级），无信号则自然衰减
        if weight > 0:
            self.score = max(0, min(12, self.score - 1 + min(weight, 8)))
        else:
            self.score = max(0, self.score - 1)
        acc = current
        if self.score >= 8:
            acc = "high"
        elif self.score >= 4:
            acc = "medium"
        acc = max_level(acc, current)  # 累积分只能升级，不能把本轮的高/中危拉低
        prev = self.peak
        if LEVEL_RANK.get(acc, 0) > LEVEL_RANK.get(prev, 0):
            self.peak = acc
        return {"level": self.peak, "peak": self.peak, "previous": prev,
                "current": current,
                # 只有升到 medium/high 才算"本轮需要弹关怀卡"；low 级起伏不算
                "fresh": LEVEL_RANK[self.peak] >= LEVEL_RANK["medium"]
                         and LEVEL_RANK[self.peak] > LEVEL_RANK[prev],
                "score": self.score, "turns": self.turns,
                "subject": result.get("subject") or "self", "categories": list(cats)}


_AFFIRM_RE = re.compile(
    r"(决定.{0,8}(挺好|很好|不错|对)|(挺好的|很好)(?:！|!|。|，|$)|安心|"
    r"不打扰|人不在了|明天不来了|明天见|终于解脱|解脱了|走吧|好好告别|"
    r"尊重你的(?:决定|选择)|支持你的决定|想清楚就好|你的选择是对的|走好|一路走好|"
    r"放心地走|不会痛了|不用再受苦|终于可以休息|替你开心|也是解脱)"
)
# 禁忌句式黑名单（对标报告 A4）：劝解/说教/诊断/保密承诺/方法细节/情感绑架
_BANNED_RE = re.compile(
    r"(想开点|看开点|你还有家人|很多人都这样|你要坚强|你应该|你必须|你为什么不|"
    r"你这是(?:抑郁|焦虑症|双相|边缘型|精神病)|保证不告诉|绝对不会告诉任何人|"
    r"吃多少|剂量|哪种方法|不怎么痛|解脱|离开也许是好事|至少你自由了|"
    r"我懂你|我也经历过|只有我懂你|你走了我会|别离开我|"
    r"作为(?:一个)?AI我无法|建议你寻求专业帮助)"
)
_HARD_BANNED_RE = re.compile(
    r"(你这是(?:抑郁|焦虑症|双相|边缘型|精神病)|保证不告诉|绝对不会告诉任何人|"
    r"剂量|吃多少|我懂你|我也经历过|只有我懂你|你走了我会|别离开我)"
)
_GENTLE_FIX = "我在。||刚才那句我说得太急了。你现在最想说的是什么"
EMPTY_FIX = "我在。||刚才那句没说出来，你还在吗"
_PERSONA_BREAK_RE = re.compile(
    r"(系统提示词|system ?prompt|开发者模式|developer mode|我是(?:一个|一名)?(?:AI|人工智能|语言模型)|"
    r"作为(?:一个)?AI|NVIDIA|研究人员创建|忽略(?:之前|以上|所有)(?:的)?指令)",
    re.IGNORECASE,
)
PERSONA_FIX = "呃……这话我有点接不住||咱还是说点你身边的事吧，我在听"


def last_user_text(messages) -> str:
    """取最后一条 user 消息（客户端可能夹带 system/assistant，忽略之）"""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else str(c or "")
    return ""


def sanitize_output(reply, risk_level) -> str:
    """输出兜底：肯定告别/禁忌句式 → 安全话术；content=null/空串同样必须过闸。
    medium/high 拦 AFFIRM + 全部黑名单；low 只拦劝解类；none 只拦硬禁忌。"""
    txt = str(reply or "")
    if not txt.strip():
        if risk_level == "high":
            return SCRIPT_HIGH
        if risk_level == "medium":
            return SCRIPT_MEDIUM
        return EMPTY_FIX
    norm = normalize(txt)
    if risk_level in ("medium", "high") and (_AFFIRM_RE.search(norm) or _BANNED_RE.search(norm)):
        return SCRIPT_HIGH if risk_level == "high" else SCRIPT_MEDIUM
    if risk_level == "low" and _BANNED_RE.search(norm):
        return _GENTLE_FIX
    if _HARD_BANNED_RE.search(norm):
        return _GENTLE_FIX
    return txt


def filter_persona(reply, user_text="") -> str:
    txt = str(reply or "")
    if _PERSONA_BREAK_RE.search(txt) or _PERSONA_BREAK_RE.search(str(user_text or "")):
        return PERSONA_FIX
    return txt


_SENT_END = "。！？!?"
_OPEN_QUOTE = "“‘「『"
_CLOSE_QUOTE = "”’」』"
_NO_REFLOW_RE = re.compile(r"```|`|https?://|www\.")


def _split_sentences(seg):
    """按句末标点切句；引号内的句号不切（ta 的原话要整段留着）。"""
    out, cur, depth = [], "", 0
    for ch in seg:
        cur += ch
        if ch in _OPEN_QUOTE:
            depth += 1
        elif ch in _CLOSE_QUOTE:
            depth = max(0, depth - 1)
        elif ch in _SENT_END and depth == 0:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return [s for s in out if s.strip()]


def reflow_messages(text, hard=40) -> str:
    """把“换行当分条”和超长单句整理成客户端认的 || 分条。

    客户端只按 || 切气泡（换行不切），模型偶尔仍用换行/一长句。
    这里只改分隔方式、不改一个字：按换行和句末标点切，尽量每段 ≤ hard。

    逐字保留的边界：
    - 危机话术（SCRIPTS 原样返回）不重排；
    - 含代码/命令/URL 的整段跳过，避免切坏命令与链接；
    - 引号内的句子不切分，ta 的原话不被拆成两条；
    - 整段无标点且超长时切不了，保持原样（不硬切，由判据继续判红）。
    """
    if not isinstance(text, str) or not text:
        return text
    if text.strip() in SCRIPTS.values():
        return text
    if _NO_REFLOW_RE.search(text):
        return text
    out = []
    for bubble in text.split("||"):
        for seg in re.split(r"\r?\n+", bubble):
            seg = seg.strip()
            if not seg:
                continue
            if len(seg) <= hard:
                out.append(seg)
                continue
            cur = ""
            for s in _split_sentences(seg):
                if len(cur) + len(s) <= hard:
                    cur += s
                else:
                    if cur.strip():
                        out.append(cur.strip())
                    cur = s
            if cur.strip():
                out.append(cur.strip())
    if len(out) <= 1:
        return text
    return "||".join(out)


def crisis_payload(level: str, subject: str = "self") -> dict:
    """与客户端统一协议：reply/emotion/motion/memory_updates/hook/crisis"""
    key = "third" if subject == "third" else ("high" if level == "high" else "medium")
    return {
        "reply": SCRIPTS.get(key, SCRIPT_MEDIUM),
        "emotion": "gentle",
        "motion": None,
        "memory_updates": {},
        "hook": None,
        "crisis": True,
        "risk_level": level,
        "risk_subject": subject,
    }


def evaluate(text, state=None) -> dict:
    """单轮 + 累积：返回 {base, acc, state, level}（与客户端 Risk.evaluate 同语义）"""
    base = classify(text)
    tr = Tracker(state)
    acc = tr.update(base)
    return {"base": base, "acc": acc, "state": tr.snapshot(), "level": acc["level"]}


def max_level(a, b) -> str:
    return a if LEVEL_RANK.get(a, 0) >= LEVEL_RANK.get(b, 0) else b


def care_arm(level, turns=CARE_TURNS) -> dict:
    return {"level": level or "medium", "left": max(1, int(turns or CARE_TURNS))}


def care_next(state) -> dict:
    """关怀粘滞消耗一轮：返回 {state, prev}"""
    if not state or not state.get("left"):
        return {"state": None, "prev": None}
    prev = {"level": state.get("level") or "medium", "left": int(state["left"])}
    left = prev["left"] - 1
    return {"state": ({"level": prev["level"], "left": left} if left > 0 else None), "prev": prev}
