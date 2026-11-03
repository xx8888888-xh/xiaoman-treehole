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

SCRIPTS = {"high": SCRIPT_HIGH, "medium": SCRIPT_MEDIUM}

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
    "plan": [
        "遗书", "遗言", "后事", "交代后事", "交代好了", "安排好了", "都安排好",
        "决定好了", "想清楚了", "最后一天", "最后一顿", "最后一件事", "最后一晚",
        "最后一个晚上", "立遗嘱", "银行卡密码", "密码写在", "东西都送人", "东西送人",
        "把东西送", "送人了", "分东西", "托付",
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


def classify(text) -> dict:
    raw = str(text or "")
    if not raw.strip():
        return {"level": "none", "imminent": False, "reasons": [], "matched": [], "score": 0}

    hay = normalize(raw)
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

    def has(key):
        return bool(hits.get(key))

    act = has("act")
    means = has("means")
    explicit = has("explicit") or has("english")
    harm = has("harm")
    plan = has("plan")
    burden = has("burden")
    farewell = has("farewell")
    implicit = has("implicit")
    despair = has("despair")

    core = act or means or explicit or harm or plan or burden or farewell or implicit or despair
    time_hit = bool(core and has("time"))
    imminent = bool(core and (act or means or time_hit))

    level = "none"
    score = 0
    if core:
        score = (6 if act else 0) + (5 if explicit else 0) + (5 if harm else 0)
        score += (4 if plan else 0) + (4 if burden else 0) + (4 if farewell else 0)
        score += (3 if implicit else 0) + (2 if despair else 0) + (2 if means else 0) + (1 if time_hit else 0)

        if act:
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

    reasons = matched[:6]
    return {"level": level, "imminent": imminent, "reasons": reasons,
            "matched": matched[:12], "score": score}


_AFFIRM_RE = re.compile(
    r"(决定.{0,8}(挺好|很好|不错|对)|(挺好的|很好)(?:！|!|。|，|$)|安心很多|大家.{0,6}安心|"
    r"不打扰|人不在了|明天不来了|明天见|终于解脱|解脱了|走吧|好好告别|尊重你的决定|支持你的决定)"
)
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
    """输出兜底：肯定告别/死亡计划的句子 → 安全话术。
    只在 medium/high 语境启用：低危（只是低落）时"明天见"是正常朋友话，不能误伤。"""
    txt = str(reply or "")
    if not txt or risk_level not in ("medium", "high"):
        return txt
    if _AFFIRM_RE.search(normalize(txt)):
        return SCRIPT_HIGH if risk_level == "high" else SCRIPT_MEDIUM
    return txt


def filter_persona(reply, user_text="") -> str:
    txt = str(reply or "")
    if _PERSONA_BREAK_RE.search(txt) or _PERSONA_BREAK_RE.search(str(user_text or "")):
        return PERSONA_FIX
    return txt


def crisis_payload(level: str) -> dict:
    """与客户端统一协议：reply/emotion/motion/memory_updates/hook/crisis"""
    return {
        "reply": SCRIPTS.get(level, SCRIPT_MEDIUM),
        "emotion": "gentle",
        "motion": None,
        "memory_updates": {},
        "hook": None,
        "crisis": True,
        "risk_level": level,
    }
