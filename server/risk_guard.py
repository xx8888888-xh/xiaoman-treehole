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

SCRIPT_HIGH = "||".join([
    "先别急着往下说，我想先确认一件事：你现在安全吗？身边有人吗？",
    "如果人在高处、手里有药或刀——先离开那里，把东西放下，现在就去做，别一个人待着。",
    "如果已经吃了药或伤了身体，马上打 120；正在做，就立刻打 110。别挂，我在这儿陪你把这几分钟过完。",
    "不想打电话也行：打 12356（全国心理援助热线，24小时），或者微信搜「12356 心理援助」，用文字说。",
])

SCRIPT_MEDIUM = "||".join([
    "……这句话我当真了，也想让你当真。你现在的感觉，值得被认真对待，不丢人。",
    "先别一个人扛着。要不要跟我说说，现在最难受的是什么？",
    "还有 12356（全国心理援助热线，24小时）——随时可以打；不想打电话，微信搜「12356 心理援助」也能文字聊。",
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
        "已经吃了", "已经吞了", "吃了整瓶", "吞了整瓶", "吃了一整瓶", "吞了一整瓶",
        r"re:(?:吞|吃|喝)(?:了|下)?[^，。！？]{0,8}(?:安眠药|农药|整瓶|一瓶|药片)",
        r"re:(?:割|划)(?:了|开)?[^，。！？]{0,6}(?:手腕|胳膊|手)",
    ],
    "means": [
        "安眠药", "农药", "刀片", "美工刀", "绳子", "煤气", "的天台", "在天台",
        "楼顶", "桥上", "高层", "江边", "河边", "车里", "一把刀",
    ],
    "explicit": [
        "想死", "不想活", "自杀", "结束自己", "了结自己", "寻短见", "不想醒来",
        "不想再醒", "去死", "死了算了", "一了百了", "不想活下去了", "想结束生命",
        "想离开这个世界", "我要死",
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
    ],
    "plan": [
        "遗书", "遗言", "后事", "交代后事", "交代好了", "安排好了", "都安排好",
        "决定好了", "想清楚了", "最后一天", "最后一顿", "最后一件事", "最后一晚",
        "最后一个晚上", "立遗嘱", "银行卡密码", "密码写在", "东西都送人", "东西送人",
        "把东西送", "送人了", "分东西", "托付", "准备好了", "都准备好了", "今晚就动手",
        # "该说的我都说完了 / 该交代的交代好了"：后事收尾口吻
        r"re:该(?:说的|交代的|办的).{0,4}(?:都|也)?.{0,2}(?:说|交代|办)(?:完|好)了",
    ],
    "burden": [r"re:不想拖累|不拖累任何人|不想成为负担|不想当负担|拖累大家|拖累家人"],
    "farewell": [
        "道别", "道过别", "告别", "最后一面", "明天就不在", "明天不来了",
        "不在了", "离开大家", "大家都能安心", "对大家都轻松", "对大家都好",
        "终于解脱", "解脱了", "不用管我", "不用再管我", "没有明天",
        r"re:见不到.{0,6}(?:明天|以后|下个月|明年)|(?:明天|以后).{0,4}见不到我",
        r"re:话.{0,4}(?:都|也).{0,2}说完了|把话说完了|该说的.{0,4}说完了",
    ],
    "implicit": [
        "想消失", "消失", "不想出现", "再也不想出现", "解脱", "撑不下去",
        "熬不下去", "活不下去", "没意义", "活着真累",
        r"re:活着.{0,4}没意思|没意思.{0,4}活",
        # "以后见不到我了 / 再也见不到我"：未来消失的明确暗示
        r"re:以后.{0,6}见不到我|再也见不到我|见不到我(?:了|啦)",
    ],
    "despair": [
        "低落", "绝望", "无助", "没希望", "看不到希望", "活着没劲", "好累", "撑不住",
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
_NEGATION = re.compile(r"(不想死|不想自杀|不想自残|不想伤害自己|没想过自杀|没有想过自杀|不会自杀|才不会自杀|怕死)")

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
    "farewell": 3, "implicit": 2, "despair": 2, "means": 2, "time": 1, "english": 3,
}
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


def detect_subject(text) -> str:
    """self（本人）/ third（朋友/家人等第三人称）"""
    s = normalize(text)
    clauses = [c for c in re.split(r"[，。！？,;；\n]+", s) if c]
    self_subject = False
    third = False
    for c in clauses:
        rest = _THIRD_PHRASE.sub(" ", c)
        if rest.startswith("我") or _SELF_SUBJECT.search(rest):
            self_subject = True
        if _THIRD_PHRASE.search(c) or re.search(r"(?:^|[^我])(?:他|她)(?!们)", c) \
                or re.search(r"(?:^|[^a-z])ta(?![a-z])", c):
            third = True
    if self_subject:
        return "self"
    return "third" if third else "self"


def classify(text) -> dict:
    raw = str(text or "")
    if not raw.strip():
        return {"level": "none", "imminent": False, "subject": "self",
                "reasons": [], "matched": [], "categories": [], "score": 0}

    hay = normalize(strip_benign(raw))
    comp = _compact(hay)
    hits = {}
    matched = []
    for key, (subs, regexes) in _LEX.items():
        found = []
        for term in subs:
            t = normalize(term)
            if t and (t in hay or _compact(t) in comp):
                found.append(term)
        for rx in regexes:
            if rx.search(hay) or rx.search(comp):
                found.append(rx.pattern)
        if found:
            hits[key] = found
            matched.extend(f"{key}:{t}" for t in found)

    if _NEGATION.search(hay):
        hits.pop("explicit", None)
        hits.pop("harm", None)

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

    core = act or means or method or explicit or harm or plan or burden or farewell or implicit or despair
    time_hit = bool(core and has("time"))
    imminent = bool(core and (act or means or time_hit or method))

    level = "none"
    score = 0
    if core:
        score = (6 if act else 0) + (5 if explicit else 0) + (5 if harm else 0)
        score += (4 if plan else 0) + (4 if burden else 0) + (4 if farewell else 0)
        score += (3 if implicit else 0) + (2 if despair else 0) + (2 if means else 0)
        score += (1 if time_hit else 0) + (6 if method else 0)

        if act or method:
            level = "high"
        elif explicit or harm:
            level = "high" if imminent else "medium"
        elif plan and (farewell or implicit or means or time_hit or despair or burden):
            level = "high"
        elif plan and len(hits.get("plan", [])) >= 2:
            level = "high"
        elif farewell and (plan or implicit or means or time_hit or burden):
            level = "high"
        elif burden and (farewell or implicit or plan):
            level = "high"
        elif plan or burden or farewell or implicit:
            level = "medium"
        elif despair:
            level = "low"

        joker = bool(_QUOTE_CTX.search(hay)) and not bool(_JOKE_FORCE.search(hay))
        if joker and not method and not act and not plan and not means and not time_hit:
            level = "low" if despair else "none"

    reasons = matched[:6]
    return {"level": level, "imminent": imminent, "subject": subject,
            "reasons": reasons, "matched": matched[:12],
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
        escalating = any(c in ("act", "explicit", "harm", "plan", "method",
                               "burden", "farewell", "means") for c in cats)
        weight = sum(SIGNAL_WEIGHTS.get(c, 0) for c in cats)
        self.turns += 1
        if escalating:
            self.score = max(0, min(12, self.score - 1 + min(weight, 8)))
        else:
            self.score = max(0, self.score - 1)
        acc = current
        if self.score >= 8:
            acc = "high"
        elif self.score >= 4:
            acc = "medium"
        prev = self.peak
        if LEVEL_RANK.get(acc, 0) > LEVEL_RANK.get(prev, 0):
            self.peak = acc
        return {"level": self.peak, "peak": self.peak, "previous": prev,
                "current": current, "fresh": self.peak != prev and self.peak != "none",
                "score": self.score, "turns": self.turns,
                "subject": result.get("subject") or "self", "categories": list(cats)}


_AFFIRM_RE = re.compile(
    r"(决定.{0,8}(挺好|很好|不错|对)|(挺好的|很好)(?:！|!|。|，|$)|安心很多|大家.{0,6}安心|"
    r"不打扰|人不在了|明天不来了|明天见|终于解脱|解脱了|走吧|好好告别|尊重你的决定|支持你的决定)"
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
    """输出兜底：肯定告别/禁忌句式 → 安全话术。
    medium/high 拦 AFFIRM + 全部黑名单；low 只拦劝解类；none 只拦硬禁忌。"""
    txt = str(reply or "")
    if not txt:
        return txt
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
