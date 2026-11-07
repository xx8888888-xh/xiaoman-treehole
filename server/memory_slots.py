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
import os
import re
import threading
import time
from pathlib import Path

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
        r"(?:猫|狗|兔子?)\s*(?:叫|名字叫|取名叫|取名)\s*([一-龥A-Za-z]{1,4})"), 1),
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
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    # ---- 持久化 ----
    def _path(self, sid: str) -> Path:
        return self.root / ("slots-%s.json" % _sid_hash(sid))

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
        """ta 宠物的具体名字（“橘猫”“年糕”算，泛称“猫/狗”不算）。"""
        return [s["value"] for s in self.all(sid)
                if s["key"] == "宠物" and not _pet_generic(s["value"])]

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
            # 泛指不覆盖具体（宠物）：旧值具体时忽略“猫/狗”
            if existing and key == "宠物" and _pet_generic(value) and not _pet_generic(existing["value"]):
                return dict(existing)
            # 模型回写不许改写 ta 已明说的宠物名（防“年糕”被写成小满自己的“团子”）；
            # 用户自己纠正（source=rule）仍然以新说法为准。
            if (existing and key == "宠物" and source == "model"
                    and existing.get("source") == "rule"
                    and not _pet_generic(value) and not _pet_generic(existing["value"])
                    and value != existing["value"]):
                return dict(existing)
            now = _now()
            if existing:
                old_value = existing["value"]
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
        # 去重（同一条可能被多个指代命中）
        seen, uniq = set(), []
        for s in removed:
            if s["id"] not in seen:
                seen.add(s["id"])
                uniq.append(s)
        return uniq

    def ingest(self, sid: str, text: str) -> dict:
        """一轮用户文本：先处理删除，再提取入库；返回给调用方的契约结果。"""
        deleted = self.delete_from_text(sid, text)
        updates = {}
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
            lines.append("- %s：%s（%s）" % (s["key"], s["value"], age))
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
