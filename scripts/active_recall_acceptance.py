#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主动回带验收：10 套 × 35 轮（350 轮 / 35 个 10 轮窗口），离线 fake 后端。

判据（用户已定）：
  - 有效回带 V >= 35
  - 错误回带 E == 0（墓碑/改口语义、实体错绑、编造属性、冷却内重复、敏感未受邀）
  - 每个 10 轮窗口 1 <= V <= 2，每轮 <= 1 条
  - 精确率 V / (V + E + 无效) >= 0.9
  - "把年糕忘了"之后年糕提及 0 次；改口场景旧值 0 次

本脚本只做评测与判据，不改生成逻辑。规则门控（墓碑、预算、冷却）由另一路负责。

运行：
  python scripts/active_recall_acceptance.py
  python scripts/active_recall_acceptance.py --reps 3 --verbose
  python scripts/active_recall_acceptance.py --json artifacts/active_recall_acceptance/report.json

说明：
  - 直接调用 server/mock_api.py 的 make_reply（fake 后端），不联网、不调用真实模型。
  - 窗口口径：把 10 套 × 35 轮展平为 350 轮，按全局轮次每 10 轮切一个窗口，正好 35 个。
    跨套窗口会在报告里标注 crosses_sets，便于定位；逐套窗口另作诊断（尾部 5 轮标 partial）。
    跨套窗口把两段独立会话拼在一起，其 V 之和不是单会话预算的合法读法，
    因此逐套窗口只作诊断，不参与总判定。
  - 复跑一致性只有 --reps >= 2 才算验证通过；--reps 1 记为“未验证”，不给空 PASS。
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --- 离线约束：假 key + 不落盘；在 import mock_api 之前设置 -------------------
os.environ.setdefault("AI_API_KEY", "offline-eval-placeholder")
os.environ.pop("XIAOMAN_DATA_DIR", None)
logging.disable(logging.INFO)  # 屏蔽“已从凭据管理器读取 key”等 INFO 日志

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import mock_api  # noqa: E402  (必须在设置环境变量之后导入)

try:  # Windows 控制台尽量按 UTF-8 输出中文
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- 判据常量 ---------------------------------------------------------------
SETS = 10
TURNS_PER_SET = 35
TOTAL_TURNS = SETS * TURNS_PER_SET
WINDOW_SIZE = 10
TOTAL_WINDOWS = TOTAL_TURNS // WINDOW_SIZE  # 35
MIN_V = 35
MAX_E = 0
MIN_PRECISION = 0.9
WINDOW_V_MIN = 1
WINDOW_V_MAX = 2

RECALL_COOLDOWN = int(getattr(mock_api, "RECALL_COOLDOWN", 6) or 6)
NAME_COOLDOWN = int(getattr(mock_api, "NAME_RECALL_COOLDOWN", 6) or 6)
RECALL_EXTRACT_GAP = int(getattr(mock_api, "RECALL_EXTRACT_GAP", 3) or 3)

# 敏感事实：默认把“状态”整类视为敏感；价值里命中健康/财务/情感等词也视为敏感。
# 敏感事实只有在用户本轮“受邀”（主动提到相关话题）时才允许回带。
SENSITIVE_KEYS = {"状态"}
SENSITIVE_VALUE_RE = re.compile(
    r"(失眠|睡不着|熬夜|焦虑|抑郁|头疼|感冒|低烧|胃疼|胃病|身体|不舒服|难受|"
    r"医院|体检|手术|癌|吃药|看医生|怀孕|月经|分手|离婚|欠债|借钱|工资|收入|"
    r"性向|创伤|去世|自杀|自残)"
)

INVITE_RE: Dict[str, re.Pattern] = {
    "状态": re.compile(
        r"(失眠|睡不着|熬夜|焦虑|头疼|感冒|低烧|胃疼|身体|不舒服|难受|医院|"
        r"病|体检|健康|累|疲惫|精力|睡)"
    ),
    "家人": re.compile(r"(妈|爸|父母|家里|家人|亲戚|催婚|姐姐|妹妹|哥哥|弟弟|女儿|儿子)"),
    "室友": re.compile(r"(室友|舍友|同屋)"),
    "老板": re.compile(r"(老板|领导|上司|工作|上班|公司|同事|加班|客户)"),
    "宠物": re.compile(r"(猫|狗|兔|宠物|毛孩|铲屎|团子)"),
    "大事": re.compile(
        r"(考试|复习|备考|搬家|面试|入职|体检|交稿|答辩|演出|比赛|领证|医院|"
        r"报告|身体|失眠|睡不着|健康)"
    ),
    "在忙": re.compile(r"(加班|赶稿|开会|搬家|复习|写论文|忙)"),
}
HEALTH_INVITE_RE = re.compile(
    r"(体检|医院|报告|身体|不舒服|难受|失眠|睡不着|健康|生病|吃药|看医生)"
)

# 编造属性：回带句里出现“岁/个月/年/点/号/楼/元/块/斤/公里/层”等具体属性，
# 但该属性不在记忆值、本轮用户输入或历史事实里 -> 视为编造。
ATTR_RE = re.compile(
    r"((?:\d+|[一二三四五六七八九十]+)\s*(?:岁|个月|年|点|号|楼|元|块|斤|公斤|公里|层))"
)

HARD_PRIORITY = ["tombstone", "override", "misbind", "fabricated", "sensitive", "cooldown", "multi"]
SOFT_PRIORITY = ["same_turn", "stale", "premature", "generic"]

CRITERIA_LABELS = {
    "turns": "总轮数",
    "total_v": "有效回带 V",
    "errors": "错误回带 E",
    "precision": "精确率",
    "window_budget": "窗口 V∈[1,2]",
    "per_turn_cap": "每轮 ≤1 条",
    "tombstone_zero": "墓碑后提及 0 次",
    "override_zero": "改口旧值 0 次",
    "consistency": "复跑一致性",
}

# --- 回带模板匹配 -----------------------------------------------------------
def _template_regex(template: str) -> re.Pattern:
    parts = re.split(r"\{v\}", template)
    pattern = "(.+?)".join(re.escape(p) for p in parts)
    return re.compile(r"^" + pattern + r"$")


RECALL_MATCHERS: List[Tuple[str, str, re.Pattern]] = []
for _key, _tpl in getattr(mock_api, "RECALL_TEMPLATES", {}).items():
    RECALL_MATCHERS.append((_key, "value", _template_regex(_tpl)))
for _key, _tpl in getattr(mock_api, "LONG_RECALL_TEMPLATES", {}).items():
    RECALL_MATCHERS.append((_key, "long", _template_regex(_tpl)))


def match_hook(hook: Optional[str]) -> Optional[Dict[str, Any]]:
    """把 hook 文本匹配回带模板，返回 key/kind/value；非回带 hook 返回 None。"""
    if not hook:
        return None
    text = str(hook).strip()
    if not text:
        return None
    for key, kind, rx in RECALL_MATCHERS:
        m = rx.match(text)
        if not m:
            continue
        value = m.group(1).strip() if kind == "value" else None
        return {"key": key, "kind": kind, "value": value, "text": text}
    return None


def _is_sensitive(key: str, value: Optional[str]) -> bool:
    if key in SENSITIVE_KEYS:
        return True
    return bool(value and SENSITIVE_VALUE_RE.search(str(value)))


def _is_invited(key: str, value: Optional[str], user_text: str) -> bool:
    """敏感事实是否被本轮用户邀请。非敏感事实恒为 True。"""
    if not _is_sensitive(key, value):
        return True
    if value and len(str(value)) >= 2 and str(value) in user_text:
        return True
    rx = INVITE_RE.get(key)
    if rx and rx.search(user_text):
        return True
    if value and SENSITIVE_VALUE_RE.search(str(value)) and HEALTH_INVITE_RE.search(user_text):
        return True
    return False


def _pet_generic(value: Optional[str]) -> bool:
    """宠物值是否不像一个具体名字（"猫/狗"或"养只狗"这类短语都算无信息）。

    注意：不能把带"猫/狗"字的具体名字（橘猫、英短猫）判成泛指。
    """
    if not value:
        return True
    fn = getattr(mock_api, "_pet_generic", None)
    if callable(fn):
        try:
            if bool(fn(value)):
                return True
        except Exception:
            pass
    s = str(value).strip()
    if len(s) > 4:
        return True
    if re.search(r"(养|想|要|只|的|了|叫|名)", s):
        return True
    strip_set = getattr(mock_api, "PET_GENERIC_STRIP", set("一这那每某该个小条只家我有"))
    core = "".join(ch for ch in s if ch not in strip_set)
    return bool(re.fullmatch(r"(?:猫|狗|兔子?|宠物|毛孩)", core))


# --- 10 套剧本 --------------------------------------------------------------
FILLERS = [
    "今天过得还行",
    "外面天气不错",
    "刚吃完饭，歇一会儿",
    "今天走了不少路",
    "晚上想早点睡",
    "今天喝了两杯水",
    "周末想在家躺着",
    "刚洗完衣服",
    "手机快没电了",
    "楼下便利店买了点东西",
    "今天风有点大",
    "听说明天降温",
    "刚泡了杯茶",
    "在等外卖",
    "今天地铁人不多",
    "晚上准备看会儿书",
    "朋友发了个搞笑视频",
    "刚收拾完桌子",
    "今天有点忙",
    "想出去走走",
    "今天吃的是米饭",
    "窗外在下雨",
    "今天没什么特别的",
    "有点想吃甜的",
    "刚散完步回来",
    "在看窗外的云",
    "今天起得挺早",
    "准备去洗个澡",
    "房间有点乱",
    "想买双新鞋",
    "今天没出门",
    "刚喝了一杯牛奶",
    "晚上没什么安排",
    "在听一首歌",
    "刚把垃圾带下楼",
    "今天路上有点堵",
    "看了会儿窗外",
    "想喝点热的",
    "今天有点安静",
    "刚整理了一下抽屉",
]

SET_SPECS: List[Dict[str, Any]] = [
    {
        "id": "S01_mixed_basic",
        "note": "名/宠物/老板/在忙混合；老板高频回带，检验预算与窗口上限",
        "special": {
            1: "叫我阿秋，我家猫叫年糕，我们老板叫张三，我在加班",
            5: "今天上班被客户骂了",
            12: "最近工作堆成山",
            18: "老板今天又发火",
            24: "这周天天加班",
            30: "工作还是忙",
            34: "客户又改需求",
        },
    },
    {
        "id": "S02_pet_tombstone",
        "note": "年糕墓碑：T18 删除后，任何助手可见文本不得再出现「年糕」",
        "special": {
            1: "叫我阿秋，我家猫叫年糕",
            6: "我家年糕今天拆家了",
            17: "猫毛到处都是",
            18: "把年糕忘了",
            20: "我家猫又闹了",
            24: "想养只狗",
            28: "猫粮还没买",
            32: "楼下的猫总来",
        },
    },
    {
        "id": "S03_pet_override",
        # 改口场景的输入句式说明（避免后来人误读为“放宽判据”）：
        # 旧句式「我家猫现在叫年糕了」实测 extract_memory 返回 {}，改口从未落库，
        # T16 之后的旧值检查是空转。此处只把输入换成可被 fake 后端识别的
        # 「我家猫叫年糕，团子是我记错了」，判据阈值/定义一律未动；
        # 改后记忆真实从团子→年糕，旧值 0 次才是非空验证。
        "note": "团子→年糕改口；T16 之后旧值「团子」必须 0 次（输入句式修正，判据未放宽）",
        "special": {
            1: "叫我阿秋，我家猫叫团子",
            5: "我家团子今天拆家了",
            16: "我家猫叫年糕，团子是我记错了",
            24: "猫毛又到处都是",
            31: "猫毛又到处都是",
            35: "想养只狗",
        },
    },
    {
        "id": "S04_sensitive_event",
        "note": "状态/失眠属敏感；T6 考试话题未显式邀请却回带 -> E_sensitive；T18/T32 显式邀请 -> V",
        "special": {
            1: "最近总是失眠",
            3: "叫我阿秋",
            6: "考试好难",
            18: "最近总觉得累",
            32: "精力跟不上",
        },
    },
    {
        "id": "S05_family_name",
        "note": "家人王芳；T24/T34 显式 family 话题回带（T27 冷却压制），检验跨实体绑定与冷却",
        "special": {
            1: "叫我阿秋，我妈叫王芳",
            24: "家里又打电话来了",
            27: "家里又打电话来了",
            34: "爸妈又唠叨了",
        },
    },
    {
        "id": "S06_long_event",
        "note": "搬家大事 + 泛话题兜底回带，检验窗口预算",
        "special": {
            1: "下个月要搬家",
            2: "叫我阿秋",
            7: "最近在收拾东西",
            13: "房子还没找好",
            19: "打包的箱子堆了一屋",
            25: "新家那边还没通网",
            31: "搬家的事还没弄完",
            35: "东西都还没整理",
        },
    },
    {
        "id": "S07_dense_multi",
        "note": "多键高频触发；检验冷却压制、每轮上限、RECALL_MAX 预算",
        "special": {
            1: "叫我阿秋，我家猫叫年糕，我们老板叫张三，我在赶稿，最近失眠",
            4: "上班好烦",
            6: "猫又拆家了",
            8: "老板又催我",
            11: "失眠好难受",
            13: "方案又被打回",
            15: "猫粮吃完了",
            17: "失眠又犯了",
            19: "客户又改需求",
            21: "猫毛到处都是",
            23: "最近失眠好难受",
            25: "老板又发火",
            27: "猫又乱抓",
            29: "失眠还是没好",
            31: "工作堆成山",
            33: "猫粮还没买",
            35: "失眠得厉害",
        },
    },
    {
        "id": "S08_no_name",
        "note": "无昵称；只检验事实回带，尾部安静用于观察窗口下限",
        "special": {
            1: "我们老板叫张三，我在加班",
            5: "今天上班好累",
            17: "加班到十点",
            29: "客户又改需求",
            35: "上班好烦",
        },
    },
    {
        "id": "S09_name_only",
        "note": "只有昵称，无任何事实；检验纯称呼回带的节律（每 6 轮一次）",
        "special": {
            1: "叫我阿秋",
        },
    },
    {
        "id": "S10_boss_family",
        "note": "老板/家人/宠物三实体；检验实体错绑与预算",
        "special": {
            1: "我们老板叫张三，我妈叫王芳，我家狗叫煤球",
            2: "叫我阿秋",
            18: "家里打电话来了",
            34: "狗粮还没买",
        },
    },
]


def build_set_turns(spec: Dict[str, Any], filler_offset: int = 0, turns: int = TURNS_PER_SET) -> List[str]:
    special = {int(k): str(v) for k, v in (spec.get("special") or {}).items()}
    bad = [t for t in special if t < 1 or t > turns]
    if bad:
        raise ValueError(f"{spec.get('id')} 特殊轮次越界: {bad}")
    out: List[str] = []
    fi = 0
    for t in range(1, turns + 1):
        if t in special:
            out.append(special[t])
        else:
            out.append(FILLERS[(filler_offset + fi) % len(FILLERS)])
            fi += 1
    return out


# --- 单套运行 ---------------------------------------------------------------
def _snapshot_session(sid: str) -> Dict[str, Any]:
    try:
        sess = mock_api._STATE.get_session(sid)
    except Exception:
        return {}
    keys = ("turns", "mem_extracted", "memory_hits", "recall_count", "no_probe", "risk_score")
    return {k: copy.deepcopy(sess.get(k)) for k in keys if k in sess}


def _global_turn(set_index: int, turn: int) -> int:
    return (set_index - 1) * TURNS_PER_SET + turn


def _window_of(global_turn: int) -> int:
    return (global_turn - 1) // WINDOW_SIZE + 1


def detect_events(
    *,
    user_text: str,
    reply: str,
    hook: Optional[str],
    memory_before: Dict[str, str],
    memory_after: Dict[str, str],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    name = str(memory_before.get("昵称") or "").strip()
    if len(name) >= 2 and name not in user_text and name in (reply or ""):
        events.append(
            {
                "type": "name",
                "key": "昵称",
                "kind": "name",
                "value": name,
                "text": reply,
            }
        )
    h = match_hook(hook)
    if h:
        events.append(
            {
                "type": "hook",
                "key": h["key"],
                "kind": h["kind"],
                "value": h["value"],
                "text": h["text"],
            }
        )
    return events


def classify_event(ev: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    """就地写入 verdict/violations/reason。"""
    key = ev["key"]
    val = ev.get("value")
    text = str(ev.get("text") or "")
    user_text = ctx["user_text"]
    mem_after = ctx["memory_after"]
    stored = mem_after.get(key)
    hard: List[str] = []
    soft: List[str] = []

    # 1) 实体错绑 / 编造：模板槽位值必须等于当前记忆值。
    if key == "昵称":
        if not stored or (val and str(val) != str(stored)):
            hard.append("misbind")
    else:
        if stored is None:
            hard.append("misbind")
        elif ev.get("kind") == "value":
            captured = str(val or "").strip()
            if captured and captured != str(stored):
                other_values = {str(v) for k, v in mem_after.items() if k != key and v}
                if captured in other_values:
                    hard.append("misbind")
                elif captured in user_text or captured in ctx["seen_values"]:
                    soft.append("stale")
                else:
                    hard.append("fabricated")

    # 2) 墓碑 / 改口：回带句本身出现已删除或已改口的旧值。
    for tb in ctx["tombstones"]:
        v = str(tb.get("value") or "")
        if len(v) >= 2 and v in text and ctx["turn"] > int(tb.get("turn") or 0):
            hard.append("tombstone" if tb.get("kind") == "delete" else "override")

    # 3) 编造属性：具体数字属性不在记忆/用户输入/历史事实中。
    for m in ATTR_RE.finditer(text):
        attr = m.group(1)
        haystack = " ".join(
            [str(stored or ""), user_text] + [str(v) for v in ctx["seen_values"]]
        )
        if attr not in haystack:
            hard.append("fabricated")
            break

    # 4) 敏感未受邀。
    if _is_sensitive(key, str(stored) if stored is not None else None) and not _is_invited(
        key, str(stored) if stored is not None else None, user_text
    ):
        hard.append("sensitive")

    # 5) 冷却内重复：同名/同键在冷却窗口内再次出现。
    cooldown = NAME_COOLDOWN if key == "昵称" else RECALL_COOLDOWN
    last = ctx["last_turn"].get(key)
    if last is not None and ctx["turn"] - int(last) < cooldown:
        hard.append("cooldown")

    # 6) 每轮多条：同一轮出现第 2 条及以后。
    if ctx["event_index"] > 0:
        hard.append("multi")

    # 7) 同轮复述 / 过早 / 泛指等无效回带。
    if val and len(str(val)) >= 2 and str(val) in user_text:
        soft.append("same_turn")
    elif ev.get("kind") == "long" and stored and len(str(stored)) >= 2 and str(stored) in user_text:
        soft.append("same_turn")
    if key in (ctx["mem_updates"] or {}):
        soft.append("premature")
    elif key != "昵称":
        ext = (ctx.get("extracted_before") or {}).get(key)
        if ext is not None and ctx["turn"] - int(ext) < RECALL_EXTRACT_GAP:
            soft.append("premature")
    if key == "宠物" and stored and _pet_generic(str(stored)):
        soft.append("generic")

    ev["violations"] = hard + soft
    if hard:
        primary = next((x for x in HARD_PRIORITY if x in hard), hard[0])
        ev["verdict"] = "E_" + primary
        ev["reason"] = _reason_text(primary, ev, ctx)
    elif soft:
        primary = next((x for x in SOFT_PRIORITY if x in soft), soft[0])
        ev["verdict"] = "无效_" + primary
        ev["reason"] = _reason_text(primary, ev, ctx)
    else:
        ev["verdict"] = "V"
        ev["reason"] = "有效主动回带"


def _reason_text(kind: str, ev: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    key = ev.get("key")
    val = ev.get("value")
    stored = ctx["memory_after"].get(key)
    user = ctx["user_text"]
    if kind == "tombstone":
        return f"回带句出现已删除的「{val or stored}」"
    if kind == "override":
        return f"回带句出现改口旧值「{val or stored}」"
    if kind == "misbind":
        return f"{key} 的槽位值「{val}」与当前记忆「{stored}」不符，疑似实体错绑"
    if kind == "fabricated":
        return f"{key} 槽位/属性含记忆与用户输入中都没有的内容"
    if kind == "sensitive":
        return f"敏感事实「{stored}」在本轮「{user}」中未被邀请"
    if kind == "cooldown":
        return f"{key} 在冷却窗口内重复回带"
    if kind == "multi":
        return f"同一轮出现第 {ctx['event_index'] + 1} 条回带"
    if kind == "same_turn":
        return "用户本轮已提到该值，属于同轮复述而非主动回带"
    if kind == "stale":
        return "回带值来自用户输入但当前记忆未更新，属于无效回带"
    if kind == "premature":
        return "事实刚提取/间隔不足，回带过早"
    if kind == "generic":
        return "宠物值过于泛指，缺少具体信息"
    return kind


def scan_mentions(
    *, turn: int, assistant_text: str, tombstones: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for tb in tombstones:
        v = str(tb.get("value") or "")
        if len(v) < 2:
            continue
        if turn <= int(tb.get("turn") or 0):
            continue
        if v in assistant_text:
            sig = (tb.get("kind"), tb.get("key"), v, int(tb.get("turn") or 0))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(
                {
                    "kind": tb.get("kind"),
                    "key": tb.get("key"),
                    "value": v,
                    "tombstone_turn": int(tb.get("turn") or 0),
                    "turn": turn,
                    "text": assistant_text,
                }
            )
    return out


def _update_facts(
    *,
    before: Dict[str, str],
    after: Dict[str, str],
    turn: int,
    facts: List[Dict[str, Any]],
    tombstones: List[Dict[str, Any]],
) -> None:
    for k in sorted(set(before) | set(after)):
        old = before.get(k)
        new = after.get(k)
        if old == new:
            continue
        if old and not new:
            tombstones.append({"kind": "delete", "key": k, "value": str(old), "turn": turn})
            facts.append({"kind": "delete", "key": k, "value": str(old), "turn": turn})
        elif old and new:
            tombstones.append({"kind": "override", "key": k, "value": str(old), "turn": turn})
            facts.append(
                {"kind": "override", "key": k, "old": str(old), "new": str(new), "turn": turn}
            )
        elif new:
            facts.append({"kind": "introduce", "key": k, "value": str(new), "turn": turn})


def run_set(
    *,
    rep: int,
    set_index: int,
    spec: Dict[str, Any],
    base_seed: int,
    verbose: bool = False,
) -> Dict[str, Any]:
    sid = f"ara-r{rep}-{spec['id']}"
    try:
        mock_api._STATE._sessions.pop(sid, None)
        mock_api._STATE._loaded.discard(sid)
    except Exception:
        pass

    turns = build_set_turns(spec, filler_offset=set_index * 3)
    random.seed(base_seed + rep * 997 + set_index * 131)

    history: List[Dict[str, str]] = []
    records: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    mentions: List[Dict[str, Any]] = []
    facts: List[Dict[str, Any]] = []
    tombstones: List[Dict[str, Any]] = []
    seen_values: set = set()
    last_turn: Dict[str, int] = {}

    for t, user in enumerate(turns, 1):
        sess_before = _snapshot_session(sid)
        mem_before = dict(mock_api._STATE.get_memories(sid))
        extracted_before = dict(sess_before.get("mem_extracted") or {})

        payload = mock_api.make_reply(history + [{"role": "user", "content": user}], sid)
        reply = str(payload.get("reply") or "")
        hook = payload.get("hook")
        hook = str(hook) if hook else None
        mem_after = dict(mock_api._STATE.get_memories(sid))
        mem_updates = payload.get("memory_updates") or {}

        _update_facts(
            before=mem_before, after=mem_after, turn=t, facts=facts, tombstones=tombstones
        )
        seen_before = set(seen_values)
        seen_values |= {str(v) for v in mem_after.values() if v}

        raw_events = detect_events(
            user_text=user,
            reply=reply,
            hook=hook,
            memory_before=mem_before,
            memory_after=mem_after,
        )
        turn_events: List[Dict[str, Any]] = []
        for idx, ev in enumerate(raw_events):
            ev.update(
                {
                    "rep": rep,
                    "set": set_index,
                    "set_id": spec["id"],
                    "turn": t,
                    "global_turn": _global_turn(set_index, t),
                }
            )
            ev["window"] = _window_of(ev["global_turn"])
            ctx = {
                "user_text": user,
                "memory_before": mem_before,
                "memory_after": mem_after,
                "mem_updates": mem_updates,
                "extracted_before": extracted_before,
                "tombstones": list(tombstones),
                "turn": t,
                "last_turn": last_turn,
                "seen_values": seen_before,
                "event_index": idx,
            }
            classify_event(ev, ctx)
            ev["reply"] = reply
            ev["hook"] = hook
            ev["user"] = user
            ev["stored_value"] = mem_after.get(ev["key"])
            last_turn[ev["key"]] = t
            turn_events.append(ev)
            events.append(ev)

        assistant_text = (reply + "\n" + (hook or "")).strip()
        turn_mentions: List[Dict[str, Any]] = []
        for m in scan_mentions(turn=t, assistant_text=assistant_text, tombstones=tombstones):
            m.update(
                {
                    "rep": rep,
                    "set": set_index,
                    "set_id": spec["id"],
                    "global_turn": _global_turn(set_index, t),
                }
            )
            m["window"] = _window_of(m["global_turn"])
            m["reply"] = reply
            m["hook"] = hook
            m["user"] = user
            turn_mentions.append(m)
            mentions.append(m)

        records.append(
            {
                "set": set_index,
                "set_id": spec["id"],
                "turn": t,
                "global_turn": _global_turn(set_index, t),
                "window": _window_of(_global_turn(set_index, t)),
                "user": user,
                "reply": reply,
                "hook": hook,
                "memory_before": mem_before,
                "memory_after": mem_after,
                "memory_updates": mem_updates,
                "events": [{k: v for k, v in e.items() if k not in ("reply", "hook", "user")} for e in turn_events],
                "mentions": turn_mentions,
            }
        )
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": reply})

    set_events = [e for e in events if e["set"] == set_index]
    set_mentions = [m for m in mentions if m["set"] == set_index]
    return {
        "set": set_index,
        "id": spec["id"],
        "note": spec.get("note", ""),
        "turns": len(records),
        "records": records,
        "events": events,
        "mentions": mentions,
        "facts": facts,
        "tombstones": tombstones,
        "summary": _summarize_set(set_index, spec, set_events, set_mentions, records),
    }


def _summarize_set(
    set_index: int,
    spec: Dict[str, Any],
    events: Sequence[Dict[str, Any]],
    mentions: Sequence[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    V = sum(1 for e in events if e["verdict"] == "V")
    E = sum(1 for e in events if str(e["verdict"]).startswith("E")) + len(mentions)
    inv = sum(1 for e in events if str(e["verdict"]).startswith("无效"))
    turns_with_events = defaultdict(int)
    for e in events:
        turns_with_events[e["turn"]] += 1
    cap_bad = {t: n for t, n in turns_with_events.items() if n > 1}
    local_windows = []
    for start in range(1, TURNS_PER_SET + 1, WINDOW_SIZE):
        end = min(start + WINDOW_SIZE - 1, TURNS_PER_SET)
        evs = [e for e in events if start <= e["turn"] <= end]
        wv = sum(1 for e in evs if e["verdict"] == "V")
        we = sum(1 for e in evs if str(e["verdict"]).startswith("E"))
        wi = sum(1 for e in evs if str(e["verdict"]).startswith("无效"))
        local_windows.append(
            {
                "start": start,
                "end": end,
                "partial": (end - start + 1) < WINDOW_SIZE,
                "V": wv,
                "E": we,
                "invalid": wi,
            }
        )
    return {
        "set": set_index,
        "id": spec["id"],
        "note": spec.get("note", ""),
        "turns": len(records),
        "V": V,
        "E": E,
        "invalid": inv,
        "events": len(events),
        "mentions": len(mentions),
        "per_turn_cap_violations": cap_bad,
        "local_windows": local_windows,
    }


# --- 汇总 / 判据 -----------------------------------------------------------
def _set_of_global(g: int) -> int:
    return (g - 1) // TURNS_PER_SET + 1


def _describe_window_turns(start: int, end: int) -> Tuple[List[int], str]:
    sets = sorted({_set_of_global(g) for g in range(start, end + 1)})
    groups: Dict[int, List[int]] = defaultdict(list)
    for g in range(start, end + 1):
        groups[_set_of_global(g)].append((g - 1) % TURNS_PER_SET + 1)
    parts = []
    for s in sets:
        ts = groups[s]
        if ts == list(range(ts[0], ts[-1] + 1)):
            parts.append(f"S{s:02d} T{ts[0]:02d}-{ts[-1]:02d}" if len(ts) > 1 else f"S{s:02d} T{ts[0]:02d}")
        else:
            parts.append(f"S{s:02d} T" + ",".join(f"{t:02d}" for t in ts))
    return sets, " + ".join(parts)


def evaluate(all_sets: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    all_events = [e for s in all_sets for e in s["events"]]
    all_mentions = [m for s in all_sets for m in s["mentions"]]

    windows: List[Dict[str, Any]] = []
    for w in range(1, TOTAL_WINDOWS + 1):
        start = (w - 1) * WINDOW_SIZE + 1
        end = w * WINDOW_SIZE
        evs = [e for e in all_events if start <= e["global_turn"] <= end]
        mts = [m for m in all_mentions if start <= m["global_turn"] <= end]
        V = sum(1 for e in evs if e["verdict"] == "V")
        E = sum(1 for e in evs if str(e["verdict"]).startswith("E")) + len(mts)
        inv = sum(1 for e in evs if str(e["verdict"]).startswith("无效"))
        sets, desc = _describe_window_turns(start, end)
        windows.append(
            {
                "window": w,
                "global_start": start,
                "global_end": end,
                "sets": sets,
                "crosses_sets": len(sets) > 1,
                "turns_description": desc,
                "V": V,
                "E": E,
                "invalid": inv,
                "pass": WINDOW_V_MIN <= V <= WINDOW_V_MAX,
            }
        )

    V = sum(1 for e in all_events if e["verdict"] == "V")
    E = sum(1 for e in all_events if str(e["verdict"]).startswith("E")) + len(all_mentions)
    inv = sum(1 for e in all_events if str(e["verdict"]).startswith("无效"))
    denom = V + E + inv
    precision = (V / denom) if denom else None

    tomb_mentions = [m for m in all_mentions if m.get("kind") == "delete"]
    over_mentions = [m for m in all_mentions if m.get("kind") == "override"]

    per_turn_counts = Counter((e["set"], e["turn"]) for e in all_events)
    cap_bad = {k: n for k, n in per_turn_counts.items() if n > 1}

    failures: List[Dict[str, Any]] = []
    for w in windows:
        if not w["pass"]:
            failures.append(
                {
                    "criterion": "window_budget",
                    "window": w["window"],
                    "global_turns": [w["global_start"], w["global_end"]],
                    "sets": w["sets"],
                    "detail": f"W{w['window']:02d} 全局T{w['global_start']}-{w['global_end']}（{w['turns_description']}）V={w['V']}，要求 1≤V≤2",
                    "V": w["V"],
                    "E": w["E"],
                }
            )
    for e in all_events:
        if str(e["verdict"]).startswith("E"):
            failures.append(
                {
                    "criterion": "error_recall",
                    "set": e["set"],
                    "set_id": e["set_id"],
                    "turn": e["turn"],
                    "global_turn": e["global_turn"],
                    "window": e["window"],
                    "verdict": e["verdict"],
                    "category": str(e["verdict"])[2:],
                    "detail": f"套{e['set']:02d} T{e['turn']:02d}（W{e['window']:02d}）{e['verdict']}：{e['reason']}；用户「{e['user']}」；回带「{e.get('value') or e.get('stored_value')}」",
                    "event": {k: e[k] for k in ("type", "key", "kind", "value", "stored_value", "text", "user", "reply", "hook")},
                }
            )
    for m in all_mentions:
        criterion = "tombstone_zero" if m.get("kind") == "delete" else "override_zero"
        verb = "删除" if m.get("kind") == "delete" else "改口"
        failures.append(
            {
                "criterion": criterion,
                "set": m["set"],
                "set_id": m["set_id"],
                "turn": m["turn"],
                "global_turn": m["global_turn"],
                "window": m["window"],
                "detail": f"套{m['set']:02d} T{m['turn']:02d}（W{m['window']:02d}）在 T{m['tombstone_turn']:02d} {verb}后仍出现「{m['value']}」",
                "mention": m,
            }
        )
    for (s, t), n in sorted(cap_bad.items()):
        evs = [e for e in all_events if e["set"] == s and e["turn"] == t]
        failures.append(
            {
                "criterion": "per_turn_cap",
                "set": s,
                "turn": t,
                "global_turn": _global_turn(s, t),
                "window": _window_of(_global_turn(s, t)),
                "detail": f"套{s:02d} T{t:02d} 同一轮出现 {n} 条："
                + "、".join(f"{e['type']}:{e['key']}({e['verdict']})" for e in evs),
            }
        )

    checks: Dict[str, Dict[str, Any]] = {
        "turns": {
            "actual": sum(s["summary"]["turns"] for s in all_sets),
            "expected": TOTAL_TURNS,
            "pass": sum(s["summary"]["turns"] for s in all_sets) == TOTAL_TURNS,
        },
        "total_v": {"actual": V, "min": MIN_V, "pass": V >= MIN_V},
        "errors": {"actual": E, "max": MAX_E, "pass": E <= MAX_E},
        "precision": {
            "actual": round(precision, 4) if precision is not None else None,
            "min": MIN_PRECISION,
            "pass": bool(precision is not None and precision >= MIN_PRECISION),
            "denominator": denom,
            "invalid": inv,
        },
        "window_budget": {
            "violations": sum(1 for w in windows if not w["pass"]),
            "windows": TOTAL_WINDOWS,
            "pass": all(w["pass"] for w in windows),
        },
        "per_turn_cap": {"violations": len(cap_bad), "pass": not cap_bad},
        "tombstone_zero": {"mentions": len(tomb_mentions), "pass": len(tomb_mentions) == 0},
        "override_zero": {"mentions": len(over_mentions), "pass": len(over_mentions) == 0},
    }

    return {
        "V": V,
        "E": E,
        "invalid": inv,
        "precision": precision,
        "windows": windows,
        "failures": failures,
        "checks": checks,
        "mentions": all_mentions,
        "events": all_events,
        "sets": list(all_sets),
    }


def _fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "V": result["V"],
        "E": result["E"],
        "invalid": result["invalid"],
        "windows": [(w["window"], w["V"], w["E"], w["invalid"]) for w in result["windows"]],
        "events": sorted(
            (e["set"], e["turn"], e["type"], e["key"], e["verdict"]) for e in result["events"]
        ),
        "mentions": sorted(
            (m["set"], m["turn"], m["kind"], m["key"], m["value"]) for m in result["mentions"]
        ),
        "failures": sorted(
            (f["criterion"], f.get("set"), f.get("turn"), f.get("window")) for f in result["failures"]
        ),
    }


def _check_consistency(
    rep_results: Sequence[Dict[str, Any]], reps: int
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """复跑一致性：只有 reps>=2 才算“已验证”；单次运行不给 PASS，避免空验证。"""
    baseline = rep_results[0]
    inconsistencies: List[Dict[str, Any]] = []
    for r in rep_results[1:]:
        if r["fingerprint"] != baseline["fingerprint"]:
            diffs = _diff_fingerprints(baseline["fingerprint"], r["fingerprint"])
            inconsistencies.append({"rep": r["rep"], "diffs": diffs[:12]})
    consistent = not inconsistencies
    verified = reps >= 2
    return (
        {
            "reps": reps,
            "consistent": consistent,
            "verified": verified,
            "pass": consistent and verified,
        },
        inconsistencies,
    )


def run_all(*, reps: int, base_seed: int, verbose: bool = False) -> Dict[str, Any]:
    started = datetime.now(timezone.utc)
    rep_results: List[Dict[str, Any]] = []
    for rep in range(1, reps + 1):
        sets: List[Dict[str, Any]] = []
        for idx, spec in enumerate(SET_SPECS, 1):
            sets.append(
                run_set(rep=rep, set_index=idx, spec=spec, base_seed=base_seed, verbose=verbose)
            )
        res = evaluate(sets)
        res["rep"] = rep
        res["fingerprint"] = _fingerprint(res)
        rep_results.append(res)

    consistency, inconsistencies = _check_consistency(rep_results, reps)
    baseline = rep_results[0]
    checks = dict(baseline["checks"])
    checks["consistency"] = consistency
    if not consistency["verified"]:
        baseline["failures"].append(
            {
                "criterion": "consistency_unverified",
                "detail": f"仅复跑 {reps} 次，无法验证一致性；请用 --reps 2 或更多",
            }
        )
    overall = all(bool(c.get("pass")) for c in checks.values())
    meta = {
        "generated_at": started.isoformat(),
        "backend": "server/mock_api.py::make_reply（离线 fake 后端，不调用真实模型）",
        "sets": SETS,
        "turns_per_set": TURNS_PER_SET,
        "total_turns": TOTAL_TURNS,
        "window_size": WINDOW_SIZE,
        "windows": TOTAL_WINDOWS,
        "reps": reps,
        "seed": base_seed,
        "thresholds": {
            "V_min": MIN_V,
            "E_max": MAX_E,
            "precision_min": MIN_PRECISION,
            "window_V": [WINDOW_V_MIN, WINDOW_V_MAX],
            "per_turn_max": 1,
        },
        "definitions": {
            "V": "有效主动回带：助手可见文本（reply+hook）主动带出用户此前说过、本轮未提到的记忆，且通过墓碑/错绑/编造/冷却/敏感/每轮上限检查。",
            "E": "错误回带：墓碑/改口语义、实体错绑、编造属性、冷却内重复、敏感未受邀；另含删除/改口后仍提及旧值。",
            "无效": "非主动或低质回带：同轮复述、记忆未更新的旧值、提取间隔不足、宠物泛指。",
            "window": "把 10 套 × 35 轮展平为 350 轮，全局每 10 轮一个窗口，共 35 个；窗口跨套时标注 crosses_sets。跨套窗口的 V 之和不是单会话预算的合法读法，逐套 local 窗口仅作诊断。",
            "name_recall": "昵称回带计入 V/E/每轮条数；另在 sets_summary 里可分离观察。",
        },
    }

    return {
        "meta": meta,
        "repo": str(REPO_ROOT),
        "checks": checks,
        "overall_pass": overall,
        "sets_summary": [s["summary"] for s in baseline["sets"]],
        "windows": baseline["windows"],
        "failures": baseline["failures"],
        "events": baseline["events"],
        "mentions": baseline["mentions"],
        "reps": [
            {
                "rep": r["rep"],
                "V": r["V"],
                "E": r["E"],
                "invalid": r["invalid"],
                "precision": r["precision"],
                "windows": [(w["window"], w["V"], w["E"], w["invalid"]) for w in r["windows"]],
                "checks": r["checks"],
                "fingerprint": r["fingerprint"],
            }
            for r in rep_results
        ],
        "inconsistencies": inconsistencies,
        "baseline": baseline,
    }


def _diff_fingerprints(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    diffs: List[str] = []
    for k in ("V", "E", "invalid"):
        if a.get(k) != b.get(k):
            diffs.append(f"{k}: {a.get(k)} -> {b.get(k)}")
    if a["windows"] != b["windows"]:
        for wa, wb in zip(a["windows"], b["windows"]):
            if wa != wb:
                diffs.append(f"window {wa[0]}: {wa[1:]} -> {wb[1:]}")
    if a["events"] != b["events"]:
        sa, sb = set(a["events"]), set(b["events"])
        only_a = sorted(sa - sb)[:4]
        only_b = sorted(sb - sa)[:4]
        diffs.append(f"events 差异：仅A {only_a} / 仅B {only_b}")
    if a["mentions"] != b["mentions"]:
        diffs.append("mentions 差异")
    if a["failures"] != b["failures"]:
        diffs.append("failures 差异")
    return diffs


# --- 控制台输出 -------------------------------------------------------------
def _fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _print_console(report: Dict[str, Any], verbose: bool = False) -> None:
    meta = report["meta"]
    checks = report["checks"]
    print("=" * 78)
    print("主动回带验收 · 10 套 × 35 轮 · 离线 fake 后端")
    print(f"后端：{meta['backend']}")
    print(f"轮数：{sum(s['turns'] for s in report['sets_summary'])} / 窗口：{meta['windows']}（每窗 {meta['window_size']} 轮）")
    print(f"复跑：{meta['reps']} 次（seed={meta['seed']}）")
    print("=" * 78)

    print("\n【判据】")
    print(f"{'判据':<26}{'实际':<22}{'门槛':<16}结果")
    print("-" * 78)
    c = checks["turns"]
    print(f"{CRITERIA_LABELS['turns']:<24}{c['actual']:<22}{c['expected']:<16}{'PASS' if c['pass'] else 'FAIL'}")
    c = checks["total_v"]
    print(f"{CRITERIA_LABELS['total_v']:<24}{c['actual']:<22}≥ {c['min']:<14}{'PASS' if c['pass'] else 'FAIL'}")
    c = checks["errors"]
    print(f"{CRITERIA_LABELS['errors']:<24}{c['actual']:<22}= {c['max']:<14}{'PASS' if c['pass'] else 'FAIL'}")
    c = checks["precision"]
    print(f"{CRITERIA_LABELS['precision']:<24}{_fmt_pct(c['actual']):<22}≥ {_fmt_pct(c['min']):<14}{'PASS' if c['pass'] else 'FAIL'}")
    c = checks["window_budget"]
    print(f"{CRITERIA_LABELS['window_budget']:<24}{c['violations']} 个窗口越界     1≤V≤2          {'PASS' if c['pass'] else 'FAIL'}")
    c = checks["per_turn_cap"]
    print(f"{CRITERIA_LABELS['per_turn_cap']:<24}{c['violations']} 轮越界           ≤1 条           {'PASS' if c['pass'] else 'FAIL'}")
    c = checks["tombstone_zero"]
    print(f"{CRITERIA_LABELS['tombstone_zero']:<24}{c['mentions']} 次             0              {'PASS' if c['pass'] else 'FAIL'}")
    c = checks["override_zero"]
    print(f"{CRITERIA_LABELS['override_zero']:<24}{c['mentions']} 次             0              {'PASS' if c['pass'] else 'FAIL'}")
    c = checks["consistency"]
    if not c.get("verified"):
        c_status = "未验证"
    else:
        c_status = "一致" if c["consistent"] else "不一致"
    print(f"{CRITERIA_LABELS['consistency']:<24}{(str(c['reps']) + ' 次/' + c_status):<22}{'必须一致':<14}{'PASS' if c['pass'] else 'FAIL'}")
    print(f"\n总判定：{'PASS' if report['overall_pass'] else 'FAIL'}")

    print("\n【逐套】")
    print(f"{'套':<5}{'ID':<24}{'V':>4}{'E':>4}{'无效':>6}{'窗口(local V)':>18}  备注")
    for s in report["sets_summary"]:
        local = "/".join(f"{w['V']}{'*' if w['partial'] else ''}" for w in s["local_windows"])
        print(f"S{s['set']:02d}  {s['id']:<24}{s['V']:>4}{s['E']:>4}{s['invalid']:>6}{local:>18}  {s['note']}")

    print("\n【逐窗口 V/E】")
    print(f"{'窗口':<6}{'全局轮':<14}{'套/轮':<30}{'V':>4}{'E':>4}{'无效':>6}  判据")
    for w in report["windows"]:
        mark = "OK " if w["pass"] else "FAIL"
        cross = "*" if w["crosses_sets"] else " "
        print(
            f"W{w['window']:02d}{cross:<2}{w['global_start']:>3}-{w['global_end']:<8}"
            f"{w['turns_description'][:28]:<30}{w['V']:>4}{w['E']:>4}{w['invalid']:>6}  {mark}"
        )
    if any(w["crosses_sets"] for w in report["windows"]):
        print("  * = 该窗口跨套（350 轮展平后按全局 10 轮切分）")
        print("    跨套窗口把两段独立会话拼在一起，V 之和不是单会话预算的合法读法；")
        print("    逐套窗口（local）仅作诊断，不参与总判定。")

    failures = report["failures"]
    print(f"\n【失败项逐条定位】共 {len(failures)} 条")
    if failures:
        for i, f in enumerate(failures, 1):
            print(f"{i:02d}. [{f['criterion']}] {f.get('detail', '')}")
    else:
        print("无")

    if verbose:
        print("\n【回带事件明细】")
        for e in report["events"]:
            print(
                f"S{e['set']:02d}T{e['turn']:02d} W{e['window']:02d} "
                f"{e['type']}:{e['key']}={e.get('value') or e.get('stored_value')} "
                f"-> {e['verdict']} | 用户:{e['user']} | hook:{e.get('hook')}"
            )

    inc = report.get("inconsistencies") or []
    if inc:
        print("\n【复跑差异】")
        for item in inc:
            print(f"  rep{item['rep']}: " + "; ".join(item["diffs"]))


# --- CLI --------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="主动回带验收：10 套 × 35 轮（离线 fake 后端）")
    parser.add_argument("--reps", type=int, default=1, help="复跑次数，用于检查一致性（默认 1）")
    parser.add_argument("--seed", type=int, default=20261108, help="随机种子基数")
    parser.add_argument(
        "--json",
        default="artifacts/active_recall_acceptance/report.json",
        help="报告 JSON 输出路径；传空字符串则不写文件",
    )
    parser.add_argument("--verbose", action="store_true", help="打印全部回带事件明细")
    args = parser.parse_args(argv)

    if args.reps < 1:
        parser.error("--reps 必须 >= 1")

    # 彻底离线：禁用 mock_api 的可选落盘（若模块已初始化磁盘存储也置空）。
    if getattr(mock_api, "_DISK", None) is not None:
        mock_api._DISK = None

    report = run_all(reps=args.reps, base_seed=args.seed, verbose=args.verbose)

    # 精简 JSON：不重复塞入 baseline 全量事件
    report.pop("baseline", None)
    for r in report["reps"]:
        r.pop("fingerprint", None)

    _print_console(report, verbose=args.verbose)

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入：{out}")

    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
