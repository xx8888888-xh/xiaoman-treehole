#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory_slots.py · 服务端长期记忆槽（W11）

链路：用户文本 →（规则/模型 memory_updates）提取 → 槽 → 检索 → 注入下一轮 system。

槽结构（一条记忆 = 一个槽）：
  id            稳定随机 id（删除/覆盖用）
  key           类别（昵称/宠物/老板/大事/在忙/喜好/关系/生日/记事/自定义）
  value         记忆内容（原话片段，≤80 字）
  kind          pin（钉子户，永远优先）/ fact / preference / event
  emotion       情绪权重 1-5（越贴近 ta 的情绪越不容易被淘汰）
  confidence    置信度 0-1（规则提取 1.0，模型提取 0.9）
  source        rule / model / explicit
  created       首次入库时间（epoch 秒）
  updated       最近一次改写时间
  last_used     最近一次被注入的时间
  uses          被注入次数

容量：每用户 MAX_SLOTS=64；钉子户（PIN_KEYS 或 kind=pin）最多留 MAX_PIN_SLOTS=16。
淘汰：钉子户不参与淘汰；其余槽按「情绪权重 ×2 + 最近度 0-3 + 使用次数 ×0.5」打分，
      分数最低者先出局（LRU 与情绪权重的混合策略，见 docs/MEMORY_CONTRACT.md）。

用户否定：ta 说“忘了/别记/删掉这个”，本模块删槽并返回删除结果；
由调用方回一句答应（“行，删了”），提示词层保证之后不再提。

纯标准库；不依赖 web / mock_api，可单测。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger("memory_slots")

MAX_SLOTS = 64
MAX_PIN_SLOTS = 16
MAX_VALUE_LEN = 80
MAX_KEY_LEN = 20
MAX_INJECT = 6
PIN_KEYS = ("昵称", "生日", "重要日", "大事", "宠物")

_LOCK = threading.RLock()


# ---------------- 规则提取 ----------------

_NAME_SUFFIX = ("就行", "就好", "好了", "可以", "吧", "呀", "啦", "哈", "呢", "哦", "啊", "呗")

EXTRACT_PATTERNS = [
    # (key, kind, regex, group)
    ("昵称", "pin", re.compile(r"(?:我叫|叫我|你可以叫我)\s*([一-龥A-Za-z]{1,6})"), 1),
    # 宠物：只认“养/有”或“名字叫”，避免把“看到一只特别胖的橘猫”这种描述当记忆
    ("宠物", "pin", re.compile(
        r"(?:猫|狗|兔子?)\s*(?:其实|原来|现在)?\s*(?:叫|名字叫|取名叫|取名)\s*"
        r"([一-龥A-Za-z]{1,4}?)"
        r"(?=不是|才|已经|今年|三岁|啦|啊|呀|呢|吧|[，。、！？,.!?\s]|$)"), 1),
    ("宠物", "pin", re.compile(
        r"(?:养了|养|有一只|有只|我家有?)\s*(?:[一两]?[只条个]?)?\s*"
        r"(橘猫|英短|美短|布偶|金毛|萨摩|边牧|猫|狗|兔子?)"), 1),
    ("老板", "fact", re.compile(
        r"(?:老板|领导|上司)(?:叫|是|姓)\s*([一-龥A-Za-z]{1,4})"), 1),
    ("大事", "event", re.compile(
        r"((?:(?:下周|下个月|下学期|明天|后天|月底|年底|这周|周末|马上|快要)\s*"
        r"(?:要|得|准备|打算)?\s*"
        r"(?:考试|考研|复试|面试|答辩|搬家|入职|报到|体检|比赛|演出|交稿|交方案|领证|结婚)"
        r"|(?:要|得|准备|打算)\s*"
        r"(?:考试|考研|复试|面试|答辩|搬家|入职|报到|体检|比赛|演出|交稿|交方案|领证|结婚)))"),
     1),
    ("在忙", "fact", re.compile(
        r"(?:我在|正在|最近在|这几天在)\s*(加班|赶稿|开会|搬家|复习|写论文|找工作|备考|出差)"), 1),
    # 反复出现的身体/睡眠状态，是"重复话题"里最需要连续性的记忆
    ("状态", "fact", re.compile(r"(失眠|睡不着|熬夜|焦虑|头疼|感冒|低烧|胃疼)"), 1),
    ("喜好", "preference", re.compile(
        r"我(?:很|特别|最|超)?((?:喜欢|爱|讨厌|不喜欢|怕)\s*[一-龥A-Za-z0-9]{1,10})"), 1),
    ("关系", "fact", re.compile(
        r"(我(?:的)?(?:男朋友|女朋友|对象|老公|老婆|前任))"), 1),
    ("生日", "pin", re.compile(
        r"(?:我)?(?:的)?生日(?:是|在|:)?\s*(\d{1,2}月\d{1,2}[日号]|\d{4}-\d{1,2}-\d{1,2})"), 1),
    ("记事", "pin", re.compile(
        r"(?:记住|帮我记(?:一下|着|住)?|记一下)\s*[，,:：]?\s*(.{2,40})"), 1),
]

# 用户否定 / 删除意图
_DELETE_WORD_RE = re.compile(
    r"(?:忘了|忘记|别记|不要记|不用记|删掉|删了|抹掉|去掉|别再提|不要提|不想让你记)")
_DELETE_TARGET_RE = re.compile(
    r"(?:忘了|忘记|别记|不要记|不用记|删掉|删了|抹掉|去掉|别再提|不要提|不想让你记)"
    r"(?:我)?(?:说的|写的|那个|这个|关于)?\s*([一-龥A-Za-z0-9]{1,12})")
_DELETE_PREFIX_RE = re.compile(
    r"把\s*([一-龥A-Za-z0-9]{1,12})\s*(?:删掉|删了|忘了|去掉|别记)")
_DELETE_DEMO_RE = re.compile(r"(?:删掉|删了|别记|忘了)(?:这个|那个|刚才说的|刚刚说的|上面那个)")
_DELETE_SAFE_RE = re.compile(r"别忘(?:了|记)")

# 同一只宠物改口：只认代词“它/这只/那只”，避免把“我家狗叫煤球”
# 误当成把已有的猫改名（不同物种要走共存分支）。
_PET_RENAME_RE = re.compile(
    r"(?:它|这只|那只)\s*(?:其实|原来|现在)?\s*(?:的名字)?\s*(?:叫|名叫)\s*"
    r"(?!得|的|着|了|很|起)([一-龥A-Za-z]{1,4}?)"
    r"(?=不是|才|已经|啦|啊|呀|呢|吧|[，。、！？,.!?\s]|$)")

_NEG_WORDS = ("难受", "难过", "累", "哭", "焦虑", "失眠", "吵架", "分手", "离职",
              "失业", "没钱", "生病", "去世", "崩溃", "压力", "烦", "委屈", "孤独",
              "害怕", "疼", "痛")
_POS_WORDS = ("开心", "高兴", "升职", "录取", "加薪", "通过", "成功", "考上", "旅行",
              "生日", "结婚", "恋爱", "涨", "惊喜")

_PET_GENERIC_STRIP = set("一这那每某该个小条只家我有养了的")

# 话题别名：中文没有分词，用户说"我家猫"时"猫"与"宠物"槽不字面重合，
# 用一份小别名表补语义关联（只用于检索打分，不影响存储内容）。
TOPIC_ALIASES = {
    "宠物": ("猫", "狗", "兔", "宠物", "毛孩", "铲屎"),
    "老板": ("老板", "领导", "上司", "上班", "工作"),
    "大事": ("考试", "面试", "答辩", "搬家", "入职", "体检", "比赛", "演出"),
    "在忙": ("忙", "加班", "赶稿", "开会", "备考"),
    "状态": ("失眠", "睡不着", "睡", "熬夜", "累", "难受", "头疼"),
    "关系": ("男朋友", "女朋友", "对象", "老公", "老婆", "前任"),
    "喜好": ("喜欢", "讨厌", "爱吃", "怕"),
    "生日": ("生日",),
    "昵称": ("叫我", "称呼", "名字"),
}

# 敏感槽门控（W22）：身体/睡眠这类状态默认不回带，只有用户本轮自己把这一领域
# 摆到桌面上（点名失眠/熬夜/累/困等）才允许提。判据看「本轮受邀」，不是会话级解禁：
# 会话级解禁会让 S04 在 T1 提过"失眠"后，T6 的"考试好难"也变成已解禁。
SENSITIVE_KEYS = ("状态",)
_SENSITIVE_INVITE = {
    "状态": re.compile(
        r"(失眠|睡不着|睡不好|睡不踏实|没睡好|熬夜|整宿|睁着眼|做梦|焦虑|"
        r"头疼|头痛|感冒|低烧|发烧|胃疼|胃病|身体|生病|不舒服|难受|医院|体检|"
        r"吃药|精力|犯困|疲惫|疲|累|困|睡|健康)"),
}


def is_sensitive(key: str, value: str = "") -> bool:
    """该槽是否属敏感类（默认不回带）。"""
    return key in SENSITIVE_KEYS


def is_invited(key: str, value: str, text: str) -> bool:
    """用户本轮是否主动提及了该敏感话题；非敏感槽恒为 True。"""
    if not is_sensitive(key, value):
        return True
    text = text or ""
    if not text:
        return False
    if value and str(value) in text:
        return True
    pat = _SENSITIVE_INVITE.get(key)
    return bool(pat and pat.search(text))


def mentioned_in_text(key: str, value: str, text: str) -> bool:
    """本轮用户已经自己说出了这条记忆（原值或宠物名）→ 不该再回带。
    同轮复述会白占回带名额（RECALL_MAX），也是复读机。"""
    text = text or ""
    v = str(value or "").strip()
    if not text or not v:
        return False
    if len(v) >= 2 and v in text:
        return True
    if key == "宠物":
        for e in _pet_entries(v):
            name = e.get("name") or ""
            if len(name) >= 2 and name in text:
                return True
    return False


NAMED_KEYS = ("昵称", "宠物", "老板", "家人", "室友")


def named_values(pairs, text: str) -> list:
    """本轮用户亲口点名的实体记忆值（[(key, 值显示)]）。

    只认结构里真实存在的值：实体槽整值（≥2 字）；宠物按条目名字（≥2 字）。
    泛指“猫/狗”不算点名（那不是名字），也不按物种词表去猜。
    用途：生成后的确定性校验（用户点名了，回复就必须出现），不是提示词请求。
    """
    text = text or ""
    out, seen = [], set()
    for key, value in (pairs or []):
        if key not in NAMED_KEYS:
            continue
        v = str(value or "").strip()
        if not v:
            continue
        if key == "宠物":
            for e in _pet_entries(v):
                name = str(e.get("name") or "").strip()
                if len(name) >= 2 and not _pet_generic(name) and name in text:
                    if name not in seen:
                        seen.add(name)
                        out.append((key, name))
            continue
        # 基词/别名（"老板""加班"这类）不是"点名"，避免把普通词硬插进回复
        if v == key or v in TOPIC_ALIASES.get(key, ()):
            continue
        if len(v) >= 2 and v in text and v not in seen:
            seen.add(v)
            out.append((key, v))
    return out


def _clean_name(v: str) -> str:
    for suf in _NAME_SUFFIX:
        if v.endswith(suf) and len(v) > len(suf):
            return v[: -len(suf)]
    return v


def _clean_value(v: str) -> str:
    v = (v or "").strip().strip("，。！？,.!?;；：:\"'“”‘’")
    return v[:MAX_VALUE_LEN]


def _pet_generic(v: str) -> bool:
    """'那只猫/我家猫/一只狗'→True；'橘猫/年糕'→False（防泛指覆盖具体）"""
    if not v:
        return True
    core = "".join(c for c in v if c not in _PET_GENERIC_STRIP)
    return core in ("猫", "狗", "兔", "兔子")


# 多宠物：一个 宠物 槽里用「物种：名字」存多条，全角分号隔开。
# 例：猫：年糕；狗：煤球。老数据里的「年糕」「橘猫」仍按单条解析。
_PET_SEP = "；"
_PET_KV = "："
_PET_SPECIES_WORDS = ("兔子", "猫", "狗", "兔")


def _pet_entries(value: str) -> list:
    out = []
    for part in str(value or "").split(_PET_SEP):
        part = part.strip()
        if not part:
            continue
        species, name = "", part
        if _PET_KV in part:
            species, name = [x.strip() for x in part.split(_PET_KV, 1)]
        if species or name:
            out.append({"species": species, "name": name})
    return out


def _pet_value(entries: list) -> str:
    parts = []
    for e in entries:
        if e.get("species") and e.get("name"):
            parts.append("%s%s%s" % (e["species"], _PET_KV, e["name"]))
        elif e.get("name") or e.get("species"):
            parts.append(e.get("name") or e.get("species"))
    return _PET_SEP.join(parts)


def _pet_specific(entry: dict) -> bool:
    name = (entry or {}).get("name") or ""
    return bool(name) and not _pet_generic(name)


def _pet_display(value: str) -> str:
    """注入用：猫叫年糕、狗叫煤球（物种和名字都在）。"""
    parts = []
    for e in _pet_entries(value):
        sp, name = e["species"], e["name"]
        if sp and name and name != sp:
            parts.append("%s叫%s" % (sp, name))
        else:
            parts.append(name or sp)
    return "、".join(p for p in parts if p)


def text_species(text: str) -> set:
    """本轮文本提到的物种（与槽位共用同一张物种表；「兔子」命中后不再重复计「兔」）。"""
    t = text or ""
    out = {w for w in _PET_SPECIES_WORDS if w in t}
    if "兔子" in out:
        out.discard("兔")
    return out


def species_label(text: str) -> str:
    """把文本里的物种归一成可落盘的字符串（供 mock 这类扁平结构保存物种字段）。"""
    found = text_species(text)
    return "、".join(w for w in _PET_SPECIES_WORDS if w in found)


def pet_species(value: str) -> set:
    """从槽位结构取物种，不猜：
    - 带名字的条目取「物种：名字」的物种段（产品的结构化物种字段）；
    - 只有泛称（没有名字）的条目，值本身即物种（“狗”“养只狗”）；
    - 具体名字但没有物种段（“年糕”“橘猫”）→ 空集：结构里没有物种信息。
    """
    out = set()
    for e in _pet_entries(value):
        sp = str(e.get("species") or "").strip()
        if sp:
            out |= text_species(sp)
            continue
        name = str(e.get("name") or "").strip()
        if name and _pet_generic(name):
            out |= text_species(name)
    return out


def pet_specific(value: str) -> bool:
    """槽里是否有具体名字（泛称“猫/狗/养只狗”不算）——泛称不是“记得你”，不该主动回带。"""
    return any(_pet_specific(e) for e in _pet_entries(value))


def pet_species_mismatch(value: str, text: str, species_hint: str = "") -> bool:
    """槽位物种与用户本轮物种对不上 → True。

    species_hint 供扁平结构（如 mock 的会话级物种字段）补充物种；
    任一侧没有物种信息时返回 False：无法判定就不误杀，也绝不凭空补一个物种。
    """
    slot_sp = pet_species(value)
    if not slot_sp and species_hint:
        slot_sp = text_species(species_hint)
    user_sp = text_species(text)
    if not slot_sp or not user_sp:
        return False
    return not (slot_sp & user_sp)


def _merge_pet_value(existing: dict, value: str, source: str) -> str:
    """多宠物合并：不同物种共存；同物种/单只改名=纠正；模型不许覆盖 ta 明说的名字。"""
    old = [dict(e) for e in _pet_entries(existing.get("value", ""))]
    merged = old
    for ne in _pet_entries(value):
        sp, name = ne["species"], ne["name"]
        if not _pet_specific(ne):
            # 泛指（猫/狗/橘猫这类没名字的）：同物种已有记录就不动，避免糊掉具体名
            if sp and any(e["species"] == sp for e in merged):
                continue
            if not sp and merged:
                continue
            merged.append(ne)
            continue
        same_name = next((e for e in merged if e["name"] == name), None)
        if same_name:
            if sp:
                same_name["species"] = sp
            continue
        same_sp = next((e for e in merged if sp and e["species"] == sp), None)
        if same_sp:
            if (source == "model" and existing.get("source") == "rule"
                    and _pet_specific(same_sp)):
                continue
            same_sp["name"] = name
            continue
        if not sp and len(merged) == 1:
            # 只有一只、且新说法没带物种：当成同一只改名（纠正）
            if (source == "model" and existing.get("source") == "rule"
                    and _pet_specific(merged[0])):
                continue
            merged[0]["name"] = name
            continue
        if not sp:
            # 多只且没带物种：不猜是哪只，宁可不记也不乱覆盖
            continue
        merged.append(ne)
    return _pet_value(merged)


# 小满自己的猫名（见 prompt_core.PERSONA_RULES）。ta 的宠物有具体名字时，
# 回复里出现它就是把两个宠物混成一个——名字错一次都不能有，输出侧确定性兜底。
OWN_PET_NAMES = ("团子",)


def guard_own_pet_name(reply: str, pet_names=None) -> str:
    """ta 的宠物有具体名字时，把回复里小满自己的猫名改成泛称“猫”。

    只改名、不删内容：避免兜底把整句/整条气泡吃掉。
    若 ta 的宠物本身就叫“团子”，则不动（那是 ta 的名字）。
    """
    names = [str(n).strip() for n in (pet_names or []) if str(n or "").strip()]
    names = [n for n in names if not _pet_generic(n)]
    if not reply or not names:
        return reply
    out = reply
    for own in OWN_PET_NAMES:
        if own in names or own not in out:
            continue
        out = re.sub(r"叫\s*%s" % own, "", out)
        out = out.replace("我家%s" % own, "我家猫")
        out = out.replace(own, "猫")
    out = re.sub(r"猫{2,}", "猫", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def infer_emotion(key: str, value: str) -> int:
    """情绪权重 1-5：钉子户最高；负面经历高于正面；中性默认 2。"""
    if key in ("昵称", "生日"):
        return 5
    if key in ("大事", "记事"):
        weight = 4
    elif key in ("宠物", "关系", "老板"):
        weight = 3
    else:
        weight = 2
    text = "%s%s" % (key, value)
    if any(w in text for w in _NEG_WORDS):
        weight += 1
    elif any(w in text for w in _POS_WORDS):
        weight += 1
    return max(1, min(5, weight))


def _norm_target(t: str) -> str:
    return (t or "").strip().strip("的了呀吧啊哦的了")


# 墓碑：用户说过“忘了/别记/删了”之后，值/唯一别名不再出现在候选与回复里。
# 与槽文件分离落盘，只存 value + aliases + key + ts + reason，不存用户原句。
TOMBSTONE_VERSION = 1
_GENERIC_FORGET = {"猫", "狗", "它", "他", "她", "这个", "那个", "这件", "那件",
                   "宠物", "东西", "事", "一下", "上面那个", "刚才说的"}

# 复活门（保守侧）：必须同时满足“有再提及意图”且“无负面评价”。
# 两类错误代价不对称：该复活没复活只是继续藏名字；不该复活却复活等于
# 让“我叫它忘掉”失效。若将来发现用户明显想恢复却一直没恢复，方向是补确认词，
# 绝不放宽成“出现即复活”。
_REVIVE_INTENT_RE = re.compile(
    "又|还是|仍然|依然|其实|新养|养了只|叫|名字|记得|想起|刚才说|怎么不提|怎么不说")
_REVIVE_NEGATIVE_RE = re.compile("不喜欢|讨厌|烦|恨|别再|不想再")


def detect_deletions(text: str) -> list:
    """返回用户要求删除的记忆指代（值/类别/指代词）。'别忘了带钥匙'不算删除。"""
    text = text or ""
    if _DELETE_SAFE_RE.search(text):
        return []
    out = []
    if _DELETE_DEMO_RE.search(text):
        out.append("__last__")
    for m in _DELETE_PREFIX_RE.finditer(text):
        t = _norm_target(m.group(1))
        if t and t not in ("这个", "那个", "一下"):
            out.append(t)
    for m in _DELETE_TARGET_RE.finditer(text):
        t = _norm_target(m.group(1))
        if t and t not in ("这个", "那个", "的", "了", "一下"):
            out.append(t)
    return out


def extract_slots(text: str) -> dict:
    """规则提取：返回 {key: value}；不做入库与覆盖判断。"""
    text = text or ""
    out = {}
    for key, _kind, pat, group in EXTRACT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            val = m.group(group)
        except IndexError:
            continue
        val = _clean_value(val)
        if not val:
            continue
        if key == "昵称":
            val = _clean_name(val)
            if len(val) < 1:
                continue
        if key == "记事" and val in ("这个", "那个", "一下", "住", "着"):
            continue
        if key == "宠物" and _pet_generic(val) and any(
                k == "宠物" and not _pet_generic(v) for k, v in out.items()):
            continue
        if key == "宠物" and not _pet_generic(val) and "叫" in m.group(0):
            # “我家猫叫年糕”→“猫：年糕”；物种留着，多只共存时才能分清谁是谁
            sp = next((x for x in _PET_SPECIES_WORDS if m.group(0).startswith(x)), "")
            if sp:
                val = sp + _PET_KV + val
        out[key] = val
    return out


# ---------------- 槽存储 ----------------

def _now() -> int:
    return int(time.time())


def _sid_hash(sid: str) -> str:
    return hashlib.sha1(str(sid or "").encode("utf-8")).hexdigest()[:16]


def _recency_score(ts: int, now: int | None = None) -> float:
    now = now or _now()
    days = max(0.0, (now - int(ts or 0)) / 86400.0)
    if days < 1:
        return 3.0
    if days < 7:
        return 2.5
    if days < 30:
        return 1.5
    if days < 90:
        return 0.8
    return 0


def _age_label(ts: int, now: int | None = None) -> str:
    now = now or _now()
    days = int(max(0, now - int(ts or 0)) / 86400)
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 7:
        return "%d 天前" % days
    if days < 30:
        return "%d 周前" % (days // 7)
    return "%d 个月前" % (days // 30)


def _new_slot(key: str, value: str, kind: str, source: str,
              emotion: int | None = None, confidence: float = 1.0) -> dict:
    now = _now()
    return {
        "id": "%d_%s" % (now, hashlib.sha1(("%s|%s|%f" % (key, value, time.time())).encode())
                         .hexdigest()[:6]),
        "key": key[:MAX_KEY_LEN],
        "value": value[:MAX_VALUE_LEN],
        "kind": kind,
        "emotion": int(emotion if emotion is not None else infer_emotion(key, value)),
        "confidence": round(float(confidence), 2),
        "source": source,
        "created": now,
        "updated": now,
        "last_used": 0,
        "uses": 0,
    }


def _sanitize(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or "").strip()[:MAX_KEY_LEN]
    value = _clean_value(str(raw.get("value") or ""))
    if not key or not value:
        return None
    out = {
        "id": str(raw.get("id") or _sid_hash("%s|%s" % (key, value))),
        "key": key,
        "value": value,
        "kind": str(raw.get("kind") or ("pin" if key in PIN_KEYS else "fact")),
        "emotion": max(1, min(5, int(raw.get("emotion") or infer_emotion(key, value)))),
        "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 1.0))),
        "source": str(raw.get("source") or "rule"),
        "created": int(raw.get("created") or _now()),
        "updated": int(raw.get("updated") or _now()),
        "last_used": int(raw.get("last_used") or 0),
        "uses": max(0, int(raw.get("uses") or 0)),
    }
    return out


def _is_pin(slot: dict) -> bool:
    return slot.get("kind") == "pin" or slot.get("key") in PIN_KEYS


def _evict_score(slot: dict, now: int) -> float:
    return (slot.get("emotion", 2) * 2.0
            + _recency_score(slot.get("last_used") or slot.get("updated"), now)
            + slot.get("uses", 0) * 0.5)


def prune_slots(slots: list, max_slots: int = MAX_SLOTS,
                max_pins: int = MAX_PIN_SLOTS) -> list:
    """容量淘汰：钉子户按分数留前 max_pins；其余按分数留最高分的。"""
    if len(slots) <= max_slots:
        return list(slots)
    now = _now()
    pins = sorted([s for s in slots if _is_pin(s)],
                  key=lambda s: _evict_score(s, now), reverse=True)[:max_pins]
    others = sorted([s for s in slots if not _is_pin(s)],
                    key=lambda s: _evict_score(s, now), reverse=True)
    room = max(0, max_slots - len(pins))
    keep = pins + others[:room]
    keep.sort(key=lambda s: s.get("updated", 0), reverse=True)
    return keep


def _grams(text: str) -> set:
    text = (text or "").lower()
    grams = set()
    for seg in re.findall(r"[一-龥]+", text):
        for i in range(len(seg) - 1):
            grams.add(seg[i:i + 2])
    for word in re.findall(r"[a-z0-9]{2,}", text):
        grams.add(word)
    return grams


def _overlap_count(slot: dict, query_grams: set, query_text: str = "") -> int:
    hay = ("%s%s" % (slot.get("key", ""), slot.get("value", ""))).lower()
    n = sum(1 for g in query_grams if g in hay)
    if query_text and any(a in query_text for a in TOPIC_ALIASES.get(slot.get("key"), ())):
        n += 2  # 语义别名命中（"我家猫" ↔ 宠物槽）
    return n


def _match_score(slot: dict, query_grams: set, now: int, query_text: str = "") -> float:
    overlap = _overlap_count(slot, query_grams, query_text)
    score = min(overlap, 6) * 1.4
    score += slot.get("emotion", 2) * 0.8
    score += _recency_score(slot.get("last_used") or slot.get("updated"), now)
    score += slot.get("uses", 0) * 0.3
    if _is_pin(slot):
        score += 4.0
    return score


class SlotStore:
    """每用户一份槽列表；root=None 时纯内存（测试/无落盘部署）。"""

    VERSION = 1

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else None
        self._mem = {}
        self._tomb = {}
        self._turns = {}
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    # ---- 持久化 ----
    def _path(self, sid: str) -> Path:
        return self.root / ("slots-%s.json" % _sid_hash(sid))

    def _tomb_path(self, sid: str) -> Path:
        return self.root / ("suppress-%s.json" % _sid_hash(sid))

    def _load_tomb(self, sid: str) -> list:
        if sid in self._tomb:
            return self._tomb[sid]
        items = []
        if self.root:
            p = self._tomb_path(sid)
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    raw = data.get("items") if isinstance(data, dict) else data
                    if isinstance(data, dict) and sid not in self._turns:
                        self._turns[sid] = int(data.get("turn") or 0)
                    for it in raw or []:
                        if not isinstance(it, dict):
                            continue
                        value = str(it.get("value") or "").strip()
                        if not value:
                            continue
                        items.append({
                            "key": str(it.get("key") or ""),
                            "value": value,
                            "aliases": [str(a) for a in (it.get("aliases") or []) if str(a)],
                            "ts": int(it.get("ts") or 0),
                            "reason": str(it.get("reason") or "user_forget"),
                        })
                except (OSError, ValueError, TypeError):
                    items = []  # 墓碑损坏按空处理，不阻断对话
        self._tomb[sid] = items
        return items

    def _save_tomb(self, sid: str, items: list):
        self._tomb[sid] = items
        if not self.root:
            return
        p = self._tomb_path(sid)
        tmp = p.with_suffix(".tmp")
        payload = {"version": TOMBSTONE_VERSION, "updated": _now(),
                   "turn": int(self._turns.get(sid, 0)), "items": items}
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass

    def _load(self, sid: str) -> list:
        if sid in self._mem:
            return self._mem[sid]
        slots = []
        if self.root:
            p = self._path(sid)
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw_list = data.get("slots") or []
                    else:
                        raw_list = data if isinstance(data, list) else []
                    for raw in raw_list:
                        s = _sanitize(raw)
                        if s:
                            slots.append(s)
                except (OSError, ValueError, TypeError):
                    slots = []  # 损坏文件按空库处理，不阻断对话
        slots = prune_slots(slots)
        self._mem[sid] = slots
        return slots

    def _save(self, sid: str, slots: list):
        slots = prune_slots(slots)
        self._mem[sid] = slots
        if not self.root:
            return
        p = self._path(sid)
        tmp = p.with_suffix(".tmp")
        payload = {"version": self.VERSION, "updated": _now(), "slots": slots}
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            # 落盘失败不影响本轮对话；下次写会再试
            pass

    # ---- 读 ----
    def all(self, sid: str) -> list:
        with _LOCK:
            return [dict(s) for s in self._load(sid)]

    def get(self, sid: str, key: str) -> dict | None:
        with _LOCK:
            for s in self._load(sid):
                if s["key"] == key:
                    return dict(s)
        return None

    def pet_names(self, sid: str) -> list:
        """ta 宠物的具体名字（“橘猫”“年糕”算，泛称“猫/狗”不算）；多只都返回。"""
        names = []
        for s in self.all(sid):
            if s["key"] != "宠物":
                continue
            for e in _pet_entries(s["value"]):
                name = e["name"] or e["species"]
                if name and not _pet_generic(name):
                    names.append(name)
        return names

    # ---- 写 ----
    def upsert(self, sid: str, key: str, value: str, kind: str | None = None,
               source: str = "rule", emotion: int | None = None,
               confidence: float = 1.0) -> dict | None:
        key = (key or "").strip()[:MAX_KEY_LEN]
        value = _clean_value(value)
        if not key or not value:
            return None
        kind = kind or ("pin" if key in PIN_KEYS else "fact")
        with _LOCK:
            slots = self._load(sid)
            existing = next((s for s in slots if s["key"] == key), None)
            # 宠物多只共存：不同物种追加；同物种/单只改名按纠正处理；
            # 泛指不覆盖具体，模型不许覆盖 ta 明说的名字（见 _merge_pet_value）。
            if existing and key == "宠物":
                merged = _merge_pet_value(existing, value, source)
                if merged == existing["value"]:
                    return dict(existing)
                value = merged
            now = _now()
            if existing:
                existing.update({
                    "value": value, "kind": kind, "source": source,
                    "emotion": int(emotion if emotion is not None
                                   else infer_emotion(key, value)),
                    "confidence": round(float(confidence), 2),
                    "updated": now,
                })
                slot = existing
            else:
                slot = _new_slot(key, value, kind, source, emotion, confidence)
                slots.insert(0, slot)
            self._save(sid, slots)
            return dict(slot)

    def apply_updates(self, sid: str, updates: dict, source: str = "model") -> dict:
        applied = {}
        if not isinstance(updates, dict):
            return applied
        for key, value in updates.items():
            if value is None:
                continue
            slot = self.upsert(sid, str(key), str(value), source=source, confidence=0.9)
            if slot:
                applied[slot["key"]] = slot["value"]
        return applied

    def delete(self, sid: str, ids=None, key=None, value=None) -> list:
        removed = []
        with _LOCK:
            slots = self._load(sid)
            keep = []
            for s in slots:
                # 宠物多只时按条目删：'把年糕删了'只删年糕，不动煤球
                if value and s["key"] == "宠物" and not ids and not key:
                    entries = _pet_entries(s["value"])
                    matched = [e for e in entries
                               if value in e["name"] or value in e["species"]]
                    if matched:
                        for e in matched:
                            removed.append({"key": "宠物",
                                            "value": e["name"] or e["species"]})
                        left = [e for e in entries if e not in matched]
                        if left:
                            s["value"] = _pet_value(left)
                            s["updated"] = _now()
                            keep.append(s)
                        continue
                hit = False
                if ids and s["id"] in ids:
                    hit = True
                elif key and (s["key"] == key or key in s["key"] or s["key"] in key):
                    hit = True
                elif value and (value in s["value"] or s["value"] in value
                                or value in s["key"] or s["key"] in value):
                    hit = True
                if hit:
                    removed.append(dict(s))
                else:
                    keep.append(s)
            if removed:
                self._save(sid, keep)
        return removed

    def delete_from_text(self, sid: str, text: str) -> list:
        """用户否定/删除：'忘了我的生日吧' / '把年糕删了' / '删掉这个'。"""
        targets = detect_deletions(text)
        if not targets:
            return []
        removed = []
        for t in targets:
            if t == "__last__":
                with _LOCK:
                    slots = self._load(sid)
                    if slots:
                        newest = max(slots, key=lambda s: s.get("updated", 0))
                        removed.extend(self.delete(sid, ids=[newest["id"]]))
                continue
            removed.extend(self.delete(sid, value=t))
        # 去重（同一条可能被多个指代命中；宠物条目没有 id，用 key+value）
        seen, uniq = set(), []
        for s in removed:
            sig = s.get("id") or (s.get("key"), s.get("value"))
            if sig not in seen:
                seen.add(sig)
                uniq.append(s)
        if uniq:
            self._record_forget(sid, uniq, targets)
        return uniq

    def _record_forget(self, sid: str, removed: list, targets: list):
        """删槽后写墓碑：只存 value + 用户亲口说过的唯一别名 + key/ts/reason。"""
        with _LOCK:
            items = self._load_tomb(sid)
            now = _now()
            for s in removed:
                value = str(s.get("value") or "").strip()
                if not value:
                    continue
                aliases = set()
                for t in targets:
                    if not t or t == "__last__" or t in _GENERIC_FORGET:
                        continue
                    if t != value and t in value:
                        aliases.add(t)
                entry = {"key": str(s.get("key") or ""), "value": value,
                         "aliases": sorted(aliases), "ts": now,
                         "reason": "user_forget", "expires_turn": 0}
                items = [x for x in items
                         if not (x["key"] == entry["key"] and x["value"] == value)]
                items.append(entry)
            self._save_tomb(sid, items)

    def record_correction(self, sid: str, key: str, old_value: str):
        """改口：旧值进抑制表 20 轮（reason=correction，与 user_forget 分开）。"""
        old_value = str(old_value or "").strip()
        if not old_value:
            return
        with _LOCK:
            items = self._load_tomb(sid)
            turn = int(self._turns.get(sid, 0))
            entry = {"key": str(key or ""), "value": old_value, "aliases": [],
                     "ts": _now(), "reason": "correction",
                     "expires_turn": turn + 20}
            items = [x for x in items
                     if not (x["key"] == entry["key"] and x["value"] == old_value)]
            items.append(entry)
            self._save_tomb(sid, items)

    def tick(self, sid: str) -> int:
        """每收到一条用户文本，会话轮次 +1（改口墓碑按轮次过期）。"""
        with _LOCK:
            self._turns[sid] = int(self._turns.get(sid, 0)) + 1
            if self._load_tomb(sid):
                self._save_tomb(sid, self._load_tomb(sid))
            return self._turns[sid]

    def revive_from_user_input(self, sid: str, text: str) -> list:
        """只有用户亲口打出该值、且句子有再提及意图、无负面评价，才撤销墓碑。"""
        if not text:
            return []
        if detect_deletions(text):
            return []
        if not _REVIVE_INTENT_RE.search(text) or _REVIVE_NEGATIVE_RE.search(text):
            return []
        with _LOCK:
            items = self._load_tomb(sid)
            revived, keep = [], []
            for it in items:
                if any(t and t in text for t in [it["value"]] + it["aliases"]):
                    revived.append(it)
                else:
                    keep.append(it)
            if revived:
                self._save_tomb(sid, keep)
                for it in revived:
                    logger.info("tombstone_revived(user_input): key=%s value=%s",
                                it.get("key"), it.get("value"))
            return revived

    def _active_items(self, sid: str) -> list:
        cur = int(self._turns.get(sid, 0))
        out = []
        for it in self._load_tomb(sid):
            exp = int(it.get("expires_turn") or 0)
            if it.get("reason") == "correction" and exp and cur >= exp:
                continue
            out.append(it)
        return out

    def forgotten_items(self, sid: str) -> list:
        with _LOCK:
            return [dict(x) for x in self._load_tomb(sid)]

    def tomb_buffer(self, sid: str, cap: int = 6) -> int:
        """流式保留窗口：按本会话墓碑最长值/别名动态取值（2–cap 字）。"""
        lens = [len(t) for it in self._active_items(sid)
                for t in [it["value"]] + it["aliases"] if t]
        return max(2, min(cap, max(lens) if lens else 2))

    def _filter_tombstones(self, sid: str, slots: list) -> list:
        """生成前过滤候选：命中墓碑的值/别名不进 system 记忆段。"""
        items = self._active_items(sid)
        if not items:
            return slots
        out = []
        for s in slots:
            if s.get("key") == "宠物":
                entries = _pet_entries(s["value"])
                alive = []
                for e in entries:
                    name = e["name"] or e["species"]
                    hit = False
                    for it in items:
                        if it["key"] != "宠物":
                            continue
                        for t in [it["value"]] + it["aliases"]:
                            if t and (t == name or (len(t) >= 2 and t in name)):
                                hit = True
                                break
                        if hit:
                            break
                    if not hit:
                        alive.append(e)
                if not alive:
                    continue
                if len(alive) != len(entries):
                    s = dict(s, value=_pet_value(alive))
                out.append(s)
                continue
            dead = False
            for it in items:
                if it["key"] == "宠物":
                    continue
                for t in [it["value"]] + it["aliases"]:
                    if t and (t == s["value"] or t in s["value"] or s["value"] in t):
                        dead = True
                        break
                if dead:
                    break
            if not dead:
                out.append(s)
        return out

    def scrub_reply(self, sid: str, reply: str) -> str:
        """生成后兜底：命中墓碑值/别名的分句直接删掉；全删则返回空串。"""
        if not reply:
            return reply
        toks = []
        for it in self._active_items(sid):
            toks.extend([it["value"]] + it["aliases"])
        toks = [t for t in toks if t]
        if not toks:
            return reply
        parts = []
        for part in reply.split("||"):
            segs = re.findall(r"[^。！？!?；;\n]+[。！？!?；;\n]?", part)
            keep = [sg for sg in segs if not any(t in sg for t in toks)]
            joined = "".join(keep).strip()
            if joined:
                parts.append(joined)
        return "||".join(parts)

    def rename_pet(self, sid: str, name: str, text: str = "") -> dict | None:
        """同一只改口：'其实它叫年糕不是团子'。保留物种，只换名字。
        多只时只改文本里提到的旧名字；找不到旧名字就不猜（避免把别的宠物改名）。"""
        name = _clean_value(name)
        if not name:
            return None
        with _LOCK:
            slots = self._load(sid)
            slot = next((s for s in slots if s["key"] == "宠物"), None)
            if not slot:
                return None
            entries = _pet_entries(slot["value"])
            target = next((e for e in entries
                           if e["name"] and e["name"] in (text or "")), None)
            if target is None and len(entries) == 1:
                target = entries[0]
            if target is None or target["name"] == name:
                return None
            old_name = target["name"]
            if old_name:
                self.record_correction(sid, "宠物", old_name)
            target["name"] = name
            slot["value"] = _pet_value(entries)
            slot["source"] = "rule"
            slot["updated"] = _now()
            slot["emotion"] = infer_emotion("宠物", slot["value"])
            self._save(sid, slots)
            return dict(slot)

    def ingest(self, sid: str, text: str) -> dict:
        """一轮用户文本：先推进轮次/复活墓碑，再处理删除、改口，最后提取入库。"""
        self.tick(sid)
        self.revive_from_user_input(sid, text)
        deleted = self.delete_from_text(sid, text)
        m = _PET_RENAME_RE.search(text)
        renamed = self.rename_pet(sid, m.group(1), text) if m else None
        updates = {"宠物": renamed["value"]} if renamed else {}
        for key, value in extract_slots(text).items():
            slot = self.upsert(sid, key, value, source="rule", confidence=1.0)
            if slot:
                updates[key] = slot["value"]
        return {"updates": updates, "deleted": [{"key": s["key"], "value": s["value"]}
                                                for s in deleted]}

    # ---- 检索 / 注入 ----
    def recall(self, sid: str, query: str = "", limit: int = MAX_INJECT,
               touch: bool = True) -> list:
        with _LOCK:
            slots = self._load(sid)
            slots = self._filter_tombstones(sid, slots)
            now = _now()
            q_grams = _grams(query)
            if q_grams:
                scored = []
                for s in slots:
                    overlap = _overlap_count(s, q_grams, query)
                    # 钉子户（昵称/大事/宠物/生日）始终注入：用户本轮不提名字，
                    # 模型也要看得到，才能隔 1-2 轮主动带回；其余槽按关联度过滤。
                    if overlap == 0 and not _is_pin(s):
                        continue
                    scored.append((s, _match_score(s, q_grams, now, query)))
                scored.sort(key=lambda x: x[1], reverse=True)
                picks = [s for s, _ in scored[:limit]]
                # 一条都不相关时，退回最近用过的 1-2 条钉子户，保证"记得你"
                if not picks:
                    picks = [s for s in sorted(slots, key=lambda s: _evict_score(s, now),
                                               reverse=True) if _is_pin(s)][:2]
            else:
                picks = sorted(slots, key=lambda s: (_is_pin(s),
                                                     s.get("last_used") or s.get("updated")),
                               reverse=True)[:limit]
            # 敏感槽门控：用户本轮没主动提这一领域（如只说"考试好难"）就整条跳过
            picks = [s for s in picks
                     if is_invited(s["key"], s.get("value") or "", query)]
            # 宠物物种门控：用户本轮聊猫、槽里是狗 → 这条宠物不回带（按槽位物种判，
            # 物种未知时放行，见 pet_species_mismatch）
            picks = [s for s in picks
                     if not (s.get("key") == "宠物"
                             and pet_species_mismatch(s.get("value") or "", query))]
            if touch:
                ids = {s["id"] for s in picks}
                for s in slots:
                    if s["id"] in ids:
                        s["last_used"] = now
                        s["uses"] = s.get("uses", 0) + 1
                if picks:
                    self._save(sid, slots)
            return [dict(s) for s in picks]

    def render(self, sid: str, query: str = "", limit: int = MAX_INJECT) -> str:
        """渲染为可注入 system 的若干行（不含标题；标题由 prompt_core 加）。"""
        slots = self.recall(sid, query, limit)
        if not slots:
            return ""
        now = _now()
        lines = []
        for s in slots:
            age = _age_label(s.get("last_used") or s.get("updated"), now)
            shown = _pet_display(s["value"]) if s["key"] == "宠物" else s["value"]
            lines.append("- %s：%s（%s）" % (s["key"], shown, age))
        return "\n".join(lines)

    def inject_block(self, sid: str, query: str = "", limit: int = MAX_INJECT) -> str:
        body = self.render(sid, query, limit)
        if not body:
            return ""
        return "【你记得的关于 ta 的事】\n%s" % body

    # ---- 运维 ----
    def clear(self, sid: str):
        with _LOCK:
            self._save(sid, [])

    def export(self, sid: str) -> dict:
        with _LOCK:
            return {"version": self.VERSION, "slots": self.all(sid)}

    def import_(self, sid: str, data: dict) -> int:
        raw = data.get("slots") if isinstance(data, dict) else data
        slots = [s for s in (_sanitize(x) for x in (raw or [])) if s]
        with _LOCK:
            self._save(sid, slots[:MAX_SLOTS])
        return len(self._mem[sid])
