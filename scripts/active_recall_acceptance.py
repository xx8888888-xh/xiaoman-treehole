#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主动回带验收：10 套 × 35 轮（350 轮），离线 fake 后端。

判据（用户已定，2026-11 方案甲）：
  参与 PASS/FAIL 的三条产品硬规则：
  - 每轮 <= 1 条回带
  - 同键冷却：同一会话同一键两次回带间隔 >= 产品常量（mock_api.RECALL_COOLDOWN /
    NAME_RECALL_COOLDOWN；脚本不另写 6）
  - 会话总额：单会话 hook 回带 <= mock_api.RECALL_MAX（昵称不占名额）
  另保留：有效回带 V >= 35、错误回带 E == 0、精确率 >= 0.9、
  墓碑后提及 0 次、改口旧值 0 次、口径反向审计（绿转红 0 且覆盖无空洞）。

窗口分布量（1<=V_hook<=2、V_name<=6、hook_only_total>=35）全部降为诊断：
participates_in_verdict=false，值照算、分布照打印，但不决定 PASS/FAIL。
产品未定义窗口机制，报告顶部固定写“这不是缺陷，是产品未定义窗口机制。”

本脚本只做评测与判据，不改生成逻辑；mock_api.py / memory_slots.py 不碰。

运行：
  python scripts/active_recall_acceptance.py
  python scripts/active_recall_acceptance.py --reps 3 --verbose

账本隔离：缺省每次运行写入独立目录
  artifacts/active_recall_acceptance/runs/<时间戳>_reps<N>_seed<S>/report.json
同 seed 复跑也不会覆盖上一次的取证；同目录 manifest.json 记录 report.json 的 sha256，
控制台顶部打印本次 run 路径与 sha。需要指定路径时再用 --json。

说明：
  - 直接调用 server/mock_api.py 的 make_reply（fake 后端），不联网、不调用真实模型。
  - 局部窗口（按会话 40 窗）与全局窗口（拼接 35 窗）都只作诊断与定位，
    用于“口径重算”的反向审计，不参与红绿判定。
  - 复跑一致性只有 --reps >= 2 才算验证通过；--reps 1 记为“未验证”，不给空 PASS。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
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
# 注意：logging.disable 只在 CLI 入口（__main__）生效。模块被 exec_module 导入
# （验收单测）时不得关闭整个进程的日志，否则同进程里的日志相关测试会集体变红。

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
# 窗口预算只约束 hook 回带；名字（昵称）是独立产品能力，产品本身把它排除在 hook 预算外
# （server/tests/test_active_recall.py::test_name_recall_does_not_consume_hook_budget），
# 因此不参与 1<=V<=2。名字另设上界，依据见下方 NAME_WINDOW_V_MAX。
# 下界取 0：产品只定义“何时可以回带”（话题线索、非同轮复述、敏感需受邀、
# 提取间隔、同键冷却、会话总额），没有定义“每个 10 轮窗必须回带几次”。
# 0 hook 的整窗是合法常态：无话题线索、本套无事实（S09）、墓碑后、敏感未受邀、
# 会话预算（RECALL_MAX）已用尽。给正向下限会逼产品做无邀请的敏感回带或同轮复述，
# 反而触发本脚本自己的 E 判据。10 轮上界 2 仍保留为“过密”诊断，
# 但密窗是否违规按产品硬规则逐条复核（_dense_evidence），不再一刀切。
WINDOW_V_MIN = 0
WINDOW_V_MAX = 2

# 名字回带的窗口区间推导（不是拍脑袋）：
#   产品口径（server/prompt_core.py L77）：名字/计划/时间「隔 1-2 轮主动带一次」，
#   即相邻两次叫名字间隔 2-3 轮 —— 10 轮内最多约 5-6 次，取上界 6。
#   实现口径（server/mock_api.py L507 NAME_RECALL_COOLDOWN=6）：叫一次静默 6 轮，
#   10 轮内至多 2 次，比产品口径更保守。
#   实测分布（本次 10 套 40 个局部窗，离线复用事件）：V_name = 0 有 4 窗（S08 无昵称）、
#   1 有 18 窗、2 有 18 窗，最大 2，远低于产品口径上界。
# 结论：区间取 [0, 6] —— 上界由产品口径给出（防名字刷屏），下界 0 合法
# （没有昵称或安静的窗口不该被要求叫名字），不设正向下限，避免把实现冷却当判据。
NAME_WINDOW_V_MIN = 0
NAME_WINDOW_V_MAX = 6

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
    "total_v": "有效回带 V 合计",
    "errors": "错误回带 E",
    "precision": "精确率",
    "window_budget": "窗口 V_hook 分布",
    "name_budget": "窗口 V_name 分布",
    "window_scheme_audit": "口径反向审计（绿转红）",
    "per_turn_cap": "每轮 ≤1 条",
    "per_key_cooldown": "同键冷却",
    "session_total_cap": "会话回带总额",
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


def _local_windows(
    set_index: int,
    set_id: str,
    events: Sequence[Dict[str, Any]],
    mentions: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """按会话对齐切窗：T1 起每 10 轮一窗，尾窗按实际轮数，套与套不拼。"""
    out: List[Dict[str, Any]] = []
    for start in range(1, TURNS_PER_SET + 1, WINDOW_SIZE):
        end = min(start + WINDOW_SIZE - 1, TURNS_PER_SET)
        evs = [e for e in events if start <= e["turn"] <= end]
        mts = [m for m in mentions if start <= m["turn"] <= end]
        vh = sum(1 for e in evs if e["type"] == "hook" and e["verdict"] == "V")
        vn = sum(1 for e in evs if e["type"] == "name" and e["verdict"] == "V")
        wv = vh + vn
        we = sum(1 for e in evs if str(e["verdict"]).startswith("E")) + len(mts)
        wi = sum(1 for e in evs if str(e["verdict"]).startswith("无效"))
        partial = (end - start + 1) < WINDOW_SIZE
        # 尾窗只有半窗样本（5 轮），V_hook 的绝对数天然偏小：只判“不超上限”，
        # 不做正向下限判定，否则等于拿半个窗口说产品不合格。
        # 整窗同样不设正向下限（见 WINDOW_V_MIN 注释）：0 hook 是合法分布。
        if partial:
            ok = vh <= WINDOW_V_MAX
            rule = "upper_only"
        else:
            ok = WINDOW_V_MIN <= vh <= WINDOW_V_MAX
            rule = "range"
        # 过密只作诊断：低于上限直接合格；高于上限时按产品硬规则逐条复核
        # （不同键、均首提、冷却已过、会话总额未超 RECALL_MAX），合法即不算越界。
        if vh > WINDOW_V_MAX:
            ok = bool(
                _dense_evidence(
                    {"set": set_index, "start": start, "end": end},
                    events,
                    _product_constants(),
                )["allowed"]
            )
        name_ok = NAME_WINDOW_V_MIN <= vn <= NAME_WINDOW_V_MAX
        out.append(
            {
                "set": set_index,
                "set_id": set_id,
                "start": start,
                "end": end,
                "partial": partial,
                "actual_turns": end - start + 1,
                "rule": rule,
                "label": f"S{set_index:02d} T{start:02d}-{end:02d}" + ("（尾窗）" if partial else ""),
                "V_hook": vh,
                "V_name": vn,
                "V": wv,
                "E": we,
                "invalid": wi,
                "pass": ok,
                "name_pass": name_ok,
            }
        )
    return out


def _summarize_set(
    set_index: int,
    spec: Dict[str, Any],
    events: Sequence[Dict[str, Any]],
    mentions: Sequence[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    V = sum(1 for e in events if e["verdict"] == "V")
    V_hook = sum(1 for e in events if e["type"] == "hook" and e["verdict"] == "V")
    V_name = sum(1 for e in events if e["type"] == "name" and e["verdict"] == "V")
    E = sum(1 for e in events if str(e["verdict"]).startswith("E")) + len(mentions)
    inv = sum(1 for e in events if str(e["verdict"]).startswith("无效"))
    turns_with_events = defaultdict(int)
    for e in events:
        turns_with_events[e["turn"]] += 1
    cap_bad = {t: n for t, n in turns_with_events.items() if n > 1}
    local_windows = _local_windows(set_index, spec["id"], events, mentions)
    return {
        "set": set_index,
        "id": spec["id"],
        "note": spec.get("note", ""),
        "turns": len(records),
        "V": V,
        "V_hook": V_hook,
        "V_name": V_name,
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


def _covering_local(
    gstart: int, gend: int, local_windows: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """全局窗口覆盖了哪些“按会话切”的局部窗口。"""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for g in range(gstart, gend + 1):
        s = _set_of_global(g)
        lt = (g - 1) % TURNS_PER_SET + 1
        for w in local_windows:
            if w["set"] == s and w["start"] <= lt <= w["end"]:
                sig = (w["set"], w["start"], w["end"])
                if sig not in seen:
                    seen.add(sig)
                    out.append(
                        {
                            "set": w["set"],
                            "start": w["start"],
                            "end": w["end"],
                            "V": w["V"],
                            "pass": w["pass"],
                        }
                    )
    return out


def _covering_global(
    w: Dict[str, Any], windows: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """局部窗口落在哪些全局诊断窗口里。"""
    base = (w["set"] - 1) * TURNS_PER_SET
    gs, ge = base + w["start"], base + w["end"]
    return [
        {
            "window": g["window"],
            "V": g["V"],
            "pass": g["pass"],
            "crosses_sets": g["crosses_sets"],
            "offset_slice": g.get("offset_slice", False),
            "exact": g["global_start"] == gs and g["global_end"] == ge,
        }
        for g in windows
        if g["global_start"] <= ge and g["global_end"] >= gs
    ]


def _product_constants() -> Dict[str, Any]:
    """三条硬规则的阈值全部引用产品常量，不在脚本里另写 6 / 4（否则判据变成第二个真源）。"""
    return {
        "hook_cooldown": int(mock_api.RECALL_COOLDOWN),
        "name_cooldown": int(mock_api.NAME_RECALL_COOLDOWN),
        "session_max": int(mock_api.RECALL_MAX),
        "sources": {
            "hook_cooldown": "mock_api.RECALL_COOLDOWN",
            "name_cooldown": "mock_api.NAME_RECALL_COOLDOWN",
            "session_max": "mock_api.RECALL_MAX",
        },
    }


def _dense_evidence(
    w: Dict[str, Any], all_events: Sequence[Dict[str, Any]], const: Dict[str, Any]
) -> Dict[str, Any]:
    """密窗逐条核验：不同键、均首提、冷却已过、会话总额未超产品常量。"""
    evs = [
        e
        for e in all_events
        if e["set"] == w["set"] and w["start"] <= e["turn"] <= w["end"] and e["type"] == "hook"
    ]
    keys = [e["key"] for e in evs]
    distinct_keys = bool(keys) and len(set(keys)) == len(keys)
    all_first = True
    cooldown_ok = True
    for e in evs:
        same = [
            x for x in all_events
            if x["set"] == w["set"] and x["type"] == "hook" and x["key"] == e["key"]
        ]
        prior_turns = [x["turn"] for x in same if x["turn"] < e["turn"]]
        all_first = all_first and e["turn"] == min(x["turn"] for x in same)
        cooldown_ok = cooldown_ok and (
            not prior_turns or e["turn"] - max(prior_turns) >= const["hook_cooldown"]
        )
    session_total = sum(1 for x in all_events if x["set"] == w["set"] and x["type"] == "hook")
    return {
        "keys": keys,
        "distinct_keys": distinct_keys,
        "all_first": all_first,
        "cooldown_ok": cooldown_ok,
        "session_hook_total": session_total,
        "session_max": const["session_max"],
        "total_ok": session_total <= const["session_max"],
        "allowed": distinct_keys and all_first and cooldown_ok and session_total <= const["session_max"],
    }


def evaluate(all_sets: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    all_events = [e for s in all_sets for e in s["events"]]
    all_mentions = [m for s in all_sets for m in s["mentions"]]

    windows: List[Dict[str, Any]] = []
    for w in range(1, TOTAL_WINDOWS + 1):
        start = (w - 1) * WINDOW_SIZE + 1
        end = w * WINDOW_SIZE
        evs = [e for e in all_events if start <= e["global_turn"] <= end]
        mts = [m for m in all_mentions if start <= m["global_turn"] <= end]
        Vh = sum(1 for e in evs if e["type"] == "hook" and e["verdict"] == "V")
        Vn = sum(1 for e in evs if e["type"] == "name" and e["verdict"] == "V")
        V = Vh + Vn
        E = sum(1 for e in evs if str(e["verdict"]).startswith("E")) + len(mts)
        inv = sum(1 for e in evs if str(e["verdict"]).startswith("无效"))
        sets, desc = _describe_window_turns(start, end)
        local_start = (start - 1) % TURNS_PER_SET + 1
        aligned = len(sets) == 1 and local_start in (1, 11, 21, 31)
        windows.append(
            {
                "window": w,
                "global_start": start,
                "global_end": end,
                "sets": sets,
                "crosses_sets": len(sets) > 1,
                "offset_slice": not aligned,
                "turns_description": desc,
                "V": V,
                "V_hook": Vh,
                "V_name": Vn,
                "E": E,
                "invalid": inv,
                "pass": WINDOW_V_MIN <= Vh <= WINDOW_V_MAX,
            }
        )

    local_windows: List[Dict[str, Any]] = []
    for idx, s in enumerate(all_sets, 1):
        sid = s["summary"].get("id") or f"S{idx:02d}"
        local_windows.extend(_local_windows(idx, sid, s["events"], s["mentions"]))

    V = sum(1 for e in all_events if e["verdict"] == "V")
    V_hook = sum(1 for e in all_events if e["type"] == "hook" and e["verdict"] == "V")
    V_name = sum(1 for e in all_events if e["type"] == "name" and e["verdict"] == "V")
    E = sum(1 for e in all_events if str(e["verdict"]).startswith("E")) + len(all_mentions)
    inv = sum(1 for e in all_events if str(e["verdict"]).startswith("无效"))
    denom = V + E + inv
    precision = (V / denom) if denom else None

    tomb_mentions = [m for m in all_mentions if m.get("kind") == "delete"]
    over_mentions = [m for m in all_mentions if m.get("kind") == "override"]

    per_turn_counts = Counter((e["set"], e["turn"]) for e in all_events)
    cap_bad = {k: n for k, n in per_turn_counts.items() if n > 1}

    failures: List[Dict[str, Any]] = []

    # 产品硬规则 2/3：阈值引用 mock_api 常量，不另写 6 / 4。
    const = _product_constants()
    by_key: Dict[Tuple[int, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for e in all_events:
        by_key[(e["set"], e["type"], e["key"])].append(e)
    cooldown_violations: List[Dict[str, Any]] = []
    for (s, typ, key), evs in sorted(by_key.items()):
        evs.sort(key=lambda e: e["turn"])
        required = const["name_cooldown"] if typ == "name" else const["hook_cooldown"]
        for prev, cur in zip(evs, evs[1:]):
            gap = cur["turn"] - prev["turn"]
            if gap >= required:
                continue
            cooldown_violations.append(
                {
                    "set": s,
                    "set_id": cur["set_id"],
                    "type": typ,
                    "key": key,
                    "prev_turn": prev["turn"],
                    "turn": cur["turn"],
                    "gap": gap,
                    "required": required,
                }
            )
            failures.append(
                {
                    "criterion": "per_key_cooldown",
                    "owner": "生成侧",
                    "owner_file": "server/mock_api.py",
                    "owner_reason": "同键两次回带间隔小于产品冷却常量",
                    "set": s,
                    "set_id": cur["set_id"],
                    "turn": cur["turn"],
                    "detail": (
                        f"套{s:02d} 键「{key}」({typ}) T{cur['turn']:02d} 距上次 T{prev['turn']:02d} "
                        f"仅 {gap} 轮 < {required}（{const['sources']['name_cooldown' if typ == 'name' else 'hook_cooldown']}）"
                    ),
                }
            )
    session_hook_counts = Counter(e["set"] for e in all_events if e["type"] == "hook")
    session_over = {s: n for s, n in sorted(session_hook_counts.items()) if n > const["session_max"]}
    for s, n in session_over.items():
        failures.append(
            {
                "criterion": "session_total_cap",
                "owner": "生成侧",
                "owner_file": "server/mock_api.py",
                "owner_reason": "会话回带总额超过产品常量 RECALL_MAX",
                "set": s,
                "detail": (
                    f"套{s:02d} hook 回带 {n} 次 > {const['session_max']}"
                    f"（{const['sources']['session_max']}）"
                ),
            }
        )
    for e in all_events:
        if str(e["verdict"]).startswith("E"):
            failures.append(
                {
                    "criterion": "error_recall",
                    "owner": "生成侧",
                    "owner_file": "server/mock_api.py",
                    "owner_reason": {
                        "sensitive": "缺敏感邀请门控",
                        "multi": "缺每轮上限门控",
                        "cooldown": "缺冷却门控",
                        "tombstone": "缺墓碑门控",
                        "override": "缺改口墓碑门控",
                        "misbind": "缺实体绑定校验",
                        "fabricated": "缺属性编造校验",
                    }.get(str(e["verdict"])[2:], "生成侧门控缺失"),
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
                "owner": "生成侧",
                "owner_file": "server/mock_api.py",
                "owner_reason": "墓碑/改口后仍提及旧值（墓碑门控缺失）",
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
                "owner": "生成侧",
                "owner_file": "server/mock_api.py",
                "owner_reason": "昵称与事实回带同轮未合并（每轮上限门控缺失）",
                "set": s,
                "turn": t,
                "global_turn": _global_turn(s, t),
                "window": _window_of(_global_turn(s, t)),
                "detail": f"套{s:02d} T{t:02d} 同一轮出现 {n} 条："
                + "、".join(f"{e['type']}:{e['key']}({e['verdict']})" for e in evs),
            }
        )

    red_to_green: List[Dict[str, Any]] = []
    for w in windows:
        if w["pass"]:
            continue
        if w["crosses_sets"]:
            # 跨套只看各段本身的 V_hook：每段 1≤V_hook≤2 才说明是“拼接”而不是生成侧超预算。
            seg: Dict[int, int] = defaultdict(int)
            for e in all_events:
                if (
                    e["type"] == "hook"
                    and e["verdict"] == "V"
                    and w["global_start"] <= e["global_turn"] <= w["global_end"]
                ):
                    seg[e["set"]] += 1
            pure = len(seg) >= 2 and all(1 <= v <= 2 for v in seg.values())
            basis: List[Dict[str, Any]] = [
                {"set": s, "V": v} for s, v in sorted(seg.items())
            ]
        else:
            covering = _covering_local(w["global_start"], w["global_end"], local_windows)
            pure = bool(covering) and all(c["pass"] for c in covering)
            basis = covering
        if pure:
            red_to_green.append(
                {
                    "window": w["window"],
                    "turns": w["turns_description"],
                    "basis": basis,
                }
            )
    green_to_red: List[Dict[str, Any]] = []
    inexact_red_local: List[Dict[str, Any]] = []
    for lw in local_windows:
        if lw["pass"]:
            continue
        covering = _covering_global(lw, windows)
        exact = [g for g in covering if g.get("exact")]
        if not exact:
            # 有偏移的套（本套 T1 不落在全局窗边界）十轮切片与全局窗不是同一段，
            # 全局窗无法为它背书；记为“切片错位、不比对”，不悄悄丢掉。
            inexact_red_local.append(
                {"set": lw["set"], "label": lw["label"], "covering_global": [g["window"] for g in covering]}
            )
            continue
        if all(g["pass"] for g in exact):
            green_to_red.append(
                {"set": lw["set"], "label": lw["label"], "covering_global": exact}
            )
    # 反向检查的完整性：每个局部窗口都必须至少落在一个全局诊断窗口里，
    # 否则“绿转红”比对会出现覆盖空洞，等于没查。
    coverage_gaps = [lw["label"] for lw in local_windows if not _covering_global(lw, windows)]
    diagnostic_red = [w for w in windows if not w["pass"]]
    diagnostic_still_red = [
        w["window"] for w in diagnostic_red
        if w["window"] not in {r["window"] for r in red_to_green}
    ]

    # 窗口分布量全部降为诊断：值照算、分布照打印，但不参与 PASS/FAIL。
    sparse_v0 = [w for w in local_windows if w.get("rule") == "range" and w["V_hook"] == 0]
    dense = [w for w in local_windows if w["V_hook"] > WINDOW_V_MAX]
    dense_diag = [
        {
            "label": w["label"],
            "set": w["set"],
            "V_hook": w["V_hook"],
            "evidence": _dense_evidence(w, all_events, const),
        }
        for w in dense
    ]
    window_distribution = {
        "participates_in_verdict": False,
        "counts": {
            "windows": len(local_windows),
            "sparse_v0": len(sparse_v0),
            "dense": len(dense),
            "normal": len(local_windows) - len(sparse_v0) - len(dense),
        },
        "sparse_v0": [w["label"] for w in sparse_v0],
        # 按会话拆开：判断是“某几个会话特别安静”还是“普遍安静”的唯一数据。
        "sparse_v0_by_set": {
            f"S{i:02d}": sum(1 for w in sparse_v0 if w["set"] == i)
            for i in range(1, SETS + 1)
        },
        "dense": dense_diag,
        "conclusion": "这不是缺陷，是产品未定义窗口机制。",
        "reason": (
            "0≤V_hook≤2 是评测侧自拟的窗口分布量，产品只定义了三道硬闸门"
            "（每轮≤1、同键冷却≥RECALL_COOLDOWN、会话总额≤RECALL_MAX）；"
            "产品没有窗口机制，也没有“每窗必须回带”的义务：无话题线索、本套无事实、"
            "墓碑后、敏感未受邀、会话预算用尽时，0 hook 均为合法。"
            "故稀窗只作分布诊断，密窗按产品硬规则逐条复核，两者都不参与 PASS/FAIL。"
        ),
    }

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
            "violations": sum(1 for w in local_windows if not w["pass"]),
            "windows": len(local_windows),
            "full_windows": sum(1 for w in local_windows if not w["partial"]),
            "partial_windows": sum(1 for w in local_windows if w["partial"]),
            "metric": "V_hook",
            "distribution": window_distribution["counts"],
            "participates_in_verdict": False,
            "pass": all(w["pass"] for w in local_windows),
        },
        # 名字回带独立量：同样只是窗口分布诊断；产品只给了冷却常量，没有窗口上界。
        "name_budget": {
            "violations": sum(1 for w in local_windows if not w.get("name_pass", True)),
            "windows": len(local_windows),
            "range": [NAME_WINDOW_V_MIN, NAME_WINDOW_V_MAX],
            "distribution": {str(k): v for k, v in sorted(Counter(w["V_name"] for w in local_windows).items())},
            "participates_in_verdict": False,
            "pass": all(w.get("name_pass", True) for w in local_windows),
        },
        "per_turn_cap": {"violations": len(cap_bad), "pass": not cap_bad},
        # 产品硬规则 2：同键两次回带间隔 ≥ 产品冷却常量（hook/名字各用各的常量）。
        "per_key_cooldown": {
            "violations": len(cooldown_violations),
            "hook_cooldown": const["hook_cooldown"],
            "name_cooldown": const["name_cooldown"],
            "sources": [const["sources"]["hook_cooldown"], const["sources"]["name_cooldown"]],
            "pass": not cooldown_violations,
        },
        # 产品硬规则 3：单会话 hook 回带总额 ≤ RECALL_MAX（昵称不占名额，见 mock_api 口径）。
        "session_total_cap": {
            "violations": len(session_over),
            "max_per_session": const["session_max"],
            "max_observed": max(session_hook_counts.values()) if session_hook_counts else 0,
            "metric": "hook 回带（昵称不占 RECALL_MAX 名额）",
            "source": const["sources"]["session_max"],
            "pass": not session_over,
        },
        "tombstone_zero": {"mentions": len(tomb_mentions), "pass": len(tomb_mentions) == 0},
        "override_zero": {"mentions": len(over_mentions), "pass": len(over_mentions) == 0},
        # 口径反向审计：改判据不能只往好处走。脚本自动比对两口径窗口集合，
        # 要求“局部红、覆盖它的全局诊断窗全绿”的窗口数为 0，且覆盖关系无空洞。
        "window_scheme_audit": {
            "green_to_red": len(green_to_red),
            "coverage_gaps": coverage_gaps,
            "coverage_complete": not coverage_gaps,
            # 切片错位的局部红窗：不计入绿转红（判定口径不变），但必须与“绿转红 0 / 覆盖无空洞”
            # 同处可见，不能只藏在 diagnostics 深处 —— 单列不等于可以不看。
            "inexact_red_local": [r["label"] for r in inexact_red_local],
            "inexact_red_local_count": len(inexact_red_local),
            "pass": not green_to_red and not coverage_gaps,
        },
    }

    return {
        "V": V,
        "V_hook": V_hook,
        "V_name": V_name,
        "summary": {"V_hook": V_hook, "V_name": V_name, "E": E, "precision": precision},
        "E": E,
        "invalid": inv,
        "precision": precision,
        "windows": windows,
        "windows_local": local_windows,
        "diagnostics": {
            "global_windows": {
                "count": len(windows),
                "violations": len(diagnostic_red),
                "participates_in_verdict": False,
            },
            "cross_set_windows": [w["window"] for w in windows if w["crosses_sets"]],
            "offset_slice_windows": [w["window"] for w in windows if w.get("offset_slice")],
            "red_to_green": red_to_green,
            "still_red": diagnostic_still_red,
            "green_to_red": green_to_red,
            "inexact_red_local": inexact_red_local,
            "green_to_red_conclusion": (
                "绿转红 0 条：改口径后没有任何“局部红、且与其完全对齐的全局诊断窗全绿”的窗口，"
                "即没有把旧口径看不清的红藏到诊断窗口之外，也没有靠挪窗口洗白。"
                if not green_to_red
                else f"绿转红 {len(green_to_red)} 条：改口径后新增了真红，必须逐条处置。"
            ),
            "green_to_red_computed_by": (
                "脚本自动比对，非人工目测：对每条局部红窗调用 _covering_global()，仅取与它轮次完全对齐"
                "（global_start==局部全局起点 且 global_end==局部全局终点）的全局诊断窗；这些对齐窗全 pass "
                "才记入 green_to_red。有偏移的套（本套 T1 不落在 10 轮边界）切片错位，全局窗不为它背书，"
                "记入 inexact_red_local 单独列出、不参与绿转红计数，也不被悄悄丢弃。"
                "同时校验覆盖无空洞（每个局部窗至少命中一个全局窗），空洞即判 window_scheme_audit FAIL。"
            ),
            "coverage_gaps": coverage_gaps,
            "coverage_complete": not coverage_gaps,
            "window_distribution": window_distribution,
            "partial_windows_note": (
                "10 个 5 轮尾窗（T31-35）只有半窗样本，V_hook 的绝对数天然偏小："
                "尾窗只参与“不超上限（V_hook≤2，超限再按产品硬规则复核）”的判定，"
                "不设正向下限；整窗同理，V_hook=0 不得认定为产品回带不足。"
            ),
            # 诊断，不参与判定：原判据“有效回带≥35”是合计口径，拆分后仅 hook 不达 35。
            "hook_only_total": {
                "V_hook": V_hook,
                "threshold": MIN_V,
                "participates_in_verdict": False,
                "pass": V_hook >= MIN_V,
                "note": "仅供参考：原判据按 V_hook+V_name 合计≥35 计（未改门槛）；若只算 hook 则不足 35。",
            },
        },
        "failures": failures,
        "checks": checks,
        "mentions": all_mentions,
        "events": all_events,
        "sets": list(all_sets),
    }


def _fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "V":     result["V"],
        "V_hook": result["V_hook"],
        "V_name": result["V_name"],
        "E": result["E"],
        "invalid": result["invalid"],
        "windows_local": [
            (
                w["set"], w["start"], w["end"], w["V"], w["V_hook"], w["V_name"],
                w["E"], w["invalid"], w["pass"], w.get("name_pass"),
            )
            for w in result["windows_local"]
        ],
        "diagnostic_global": [
            (w["window"], w["V"], w["V_hook"], w["V_name"], w["E"], w["invalid"], w["pass"])
            for w in result["windows"]
        ],
        "events": sorted(
            (e["set"], e["turn"], e["type"], e["key"], e["verdict"]) for e in result["events"]
        ),
        "mentions": sorted(
            (m["set"], m["turn"], m["kind"], m["key"], m["value"]) for m in result["mentions"]
        ),
        "failures": sorted(
            (
                f["criterion"],
                str(f.get("set")),
                str(f.get("turn")),
                str(f.get("local_turns")),
            )
            for f in result["failures"]
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
                "owner": "判据侧",
                "owner_file": "scripts/active_recall_acceptance.py",
                "owner_reason": "单次运行无法证明一致性；夜间 --reps 3 消解",
                "detail": f"仅复跑 {reps} 次，无法验证一致性；请用 --reps 2 或更多",
            }
        )
    # 只有 participates_in_verdict 未标 False 的判据参与 PASS/FAIL；窗口分布量已降为诊断。
    overall = all(
        bool(c.get("pass")) for c in checks.values() if c.get("participates_in_verdict", True)
    )
    gen_n = sum(1 for f in baseline["failures"] if f.get("owner") == "生成侧")
    judge_n = sum(1 for f in baseline["failures"] if f.get("owner") == "判据侧")
    judge_note = (
        "判据侧 0 条（复跑一致性已验证）。"
        if judge_n == 0
        else f"判据侧 {judge_n} 条为证据不足，需 --reps 2 以上消解。"
    )
    ownership = {
        "生成侧": gen_n,
        "判据侧": judge_n,
        "剧本输入": 0,
        "note": (
            f"生成侧 {gen_n} 条（错误回带/每轮上限/同键冷却/会话总额）属评估后端 "
            "server/mock_api.py 门控缺失，待另派；本脚本只评测，不改 mock_api.py。"
            + judge_note
            + "窗口稀/密分布已降为诊断，不参与判定，归因见 diagnostics.window_distribution。"
            + "无剧本输入失败。"
        ),
    }
    const = _product_constants()
    meta = {
        "generated_at": started.isoformat(),
        "backend": "server/mock_api.py::make_reply（离线 fake 后端，不调用真实模型）",
        "sets": SETS,
        "turns_per_set": TURNS_PER_SET,
        "total_turns": TOTAL_TURNS,
        "window_size": WINDOW_SIZE,
        "windows_primary": SETS * ((TURNS_PER_SET + WINDOW_SIZE - 1) // WINDOW_SIZE),
        "windows_diagnostic": TOTAL_WINDOWS,
        "reps": reps,
        "seed": base_seed,
        "thresholds": {
            "V_min": MIN_V,
            "E_max": MAX_E,
            "precision_min": MIN_PRECISION,
            "window_V_hook": [WINDOW_V_MIN, WINDOW_V_MAX],
            "window_V_name": [NAME_WINDOW_V_MIN, NAME_WINDOW_V_MAX],
            "window_bounds_participate_in_verdict": False,
            "per_turn_max": 1,
            "per_key_cooldown": {
                "hook": const["hook_cooldown"],
                "name": const["name_cooldown"],
                "source": [const["sources"]["hook_cooldown"], const["sources"]["name_cooldown"]],
            },
            "session_recall_max": {
                "value": const["session_max"],
                "source": const["sources"]["session_max"],
                "metric": "hook 回带（昵称不占名额）",
            },
        },
        "definitions": {
            "V": "有效主动回带合计 = V_hook + V_name；≥35 沿用原判据口径（合计），便于与历史账本对齐。",
            "V_hook": "有效 hook 回带：带出用户此前说过、本轮未提到的记忆事实（不含昵称）。窗口 0≤V_hook≤2 仅为诊断量，不参与判定；0 hook 在无线索/无事实/墓碑后/敏感未受邀/预算用尽时均合法。",
            "V_name": "有效昵称回带：主动叫名字。产品把名字排除在 hook 预算外（test_active_recall.py::test_name_recall_does_not_consume_hook_budget），[0,6]/10 轮上界同样仅为诊断。",
            "E": "错误回带：墓碑/改口语义、实体错绑、编造属性、冷却内重复、敏感未受邀；另含删除/改口后仍提及旧值。",
            "无效": "非主动或低质回带：同轮复述、记忆未更新的旧值、提取间隔不足、宠物泛指。",
            "window": "窗口分布量（按会话 40 窗 / 全局 35 窗）均为诊断：值照算、分布照打印，不参与 PASS/FAIL；产品未定义窗口机制。",
            "window_primary": "按会话对齐的 40 个窗口：仅用于定位与诊断，不再作为主判据。",
            "window_diagnostic": "全局 35 窗仅作诊断：跨套拼接与偏移切片会让互不相关的会话 V 相加，不能当作产品回带过多。",
            "red_to_green": "旧全局诊断红窗，跨套时各段 V_hook 均 1≤V_hook≤2、单套时覆盖的局部窗口全合格：属纯切窗口径假红。",
            "still_red": "旧全局诊断红窗，至少覆盖一个局部红窗：与真实生成侧越界重叠，不能算纯口径问题。",
            "green_to_red": "局部窗口红、但其覆盖的全局诊断窗口全绿：改口径后新增的真红，必须为空。",
            "window_partial": "尾窗（每套 T31-35，共 10 个）只有 5 轮样本，V_hook 绝对数天然偏小：只判“不超上限 V_hook≤2”，不做正向回带量判定。",
            "window_scheme_audit": "每次改窗口口径都必须跑的反向检查：脚本自动比对两口径窗口集合，要求绿转红=0 且局部窗到全局窗的覆盖无空洞。",
            "inexact_red_local": "切片错位（本套 T1 不落在 10 轮边界）的局部红窗：全局诊断窗无法与它逐轮对齐，故不参与绿转红计数；但仍在报告顶部与绿转红同列展示，不得只看 JSON。",
            "name_recall": "昵称回带（V_name）独立计数；窗口上界仅为诊断，产品规则只有 NAME_RECALL_COOLDOWN 冷却。",
            "window_distribution": "窗口稀/密分布：不参与判定。结论固定为“这不是缺陷，是产品未定义窗口机制”，稀窗与密窗仍逐条可见；密窗是否越界按产品硬规则逐条复核。",
            "per_key_cooldown": "产品硬规则 2：同一会话同一键两次回带间隔 ≥ 冷却常量（hook 用 RECALL_COOLDOWN，名字用 NAME_RECALL_COOLDOWN，均取自 mock_api）。",
            "session_total_cap": "产品硬规则 3：单会话 hook 回带总额 ≤ mock_api.RECALL_MAX（昵称不占名额）。",
        },
    }

    return {
        "meta": meta,
        "repo": str(REPO_ROOT),
        "checks": checks,
        "overall_pass": overall,
        "sets_summary": [s["summary"] for s in baseline["sets"]],
        "windows": baseline["windows"],
        "windows_local": baseline["windows_local"],
        "summary": baseline["summary"],
        "diagnostics": baseline["diagnostics"],
        "ownership": ownership,
        "failures": baseline["failures"],
        "events": baseline["events"],
        "mentions": baseline["mentions"],
        "reps": [
            {
                "rep": r["rep"],
                "V": r["V"],
                "V_hook": r["V_hook"],
                "V_name": r["V_name"],
                "E": r["E"],
                "invalid": r["invalid"],
                "precision": r["precision"],
                "windows": [(w["window"], w["V"], w["E"], w["invalid"]) for w in r["windows"]],
                "windows_local": [
                    (w["set"], w["start"], w["end"], w["V"], w["V_hook"], w["V_name"], w["E"], w["pass"])
                    for w in r["windows_local"]
                ],
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
    for k in ("V", "V_hook", "V_name", "E", "invalid"):
        if a.get(k) != b.get(k):
            diffs.append(f"{k}: {a.get(k)} -> {b.get(k)}")
    if a["windows_local"] != b["windows_local"]:
        for wa, wb in zip(a["windows_local"], b["windows_local"]):
            if wa != wb:
                diffs.append(f"local S{wa[0]:02d} T{wa[1]:02d}-{wa[2]:02d}: {wa[3:]} -> {wb[3:]}")
    if a["diagnostic_global"] != b["diagnostic_global"]:
        for wa, wb in zip(a["diagnostic_global"], b["diagnostic_global"]):
            if wa != wb:
                diffs.append(f"global W{wa[0]:02d}: {wa[1:]} -> {wb[1:]}")
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


def _print_console(
    report: Dict[str, Any], verbose: bool = False, run_info: Optional[Dict[str, Any]] = None
) -> None:
    if run_info:
        print(f"本次 run 目录：{run_info['run_dir']}")
        print(f"报告文件：{run_info['report_path']}")
        print(f"报告 sha256：{run_info['report_sha256']}")
        print(f"清单文件：{run_info['manifest_path']}")
    meta = report["meta"]
    checks = report["checks"]
    print("=" * 78)
    print("主动回带验收 · 10 套 × 35 轮 · 离线 fake 后端")
    print(f"后端：{meta['backend']}")
    print(
        f"轮数：{sum(s['turns'] for s in report['sets_summary'])} / "
        f"局部窗口：{meta['windows_primary']}（按会话切，尾窗按实际轮数，仅诊断）/ "
        f"全局窗口：{meta['windows_diagnostic']}（拼接切片，仅诊断）"
    )
    print(f"复跑：{meta['reps']} 次（seed={meta['seed']}）")
    s = report.get("summary") or {}
    print(
        f"摘要：V_hook={s.get('V_hook')} / V_name={s.get('V_name')} / "
        f"E={s.get('E')} / 精确率={_fmt_pct(s.get('precision'))}"
    )
    dist = (report.get("diagnostics") or {}).get("window_distribution") or {}
    if dist:
        c = dist["counts"]
        print(
            f"窗口机制：{dist['conclusion']}"
            f"（诊断，不参与判定：稀窗 V_hook=0 ×{c['sparse_v0']} / "
            f"密窗 V_hook>{WINDOW_V_MAX} ×{c['dense']} / 正常 ×{c['normal']}）"
        )
        if dist.get("sparse_v0"):
            print(f"  └ 稀窗 V_hook=0（{len(dist['sparse_v0'])} 个）：" + "、".join(dist["sparse_v0"]))
            by_set = dist.get("sparse_v0_by_set") or {}
            n_sets = sum(1 for v in by_set.values() if v)
            print(
                "  └ 稀窗按会话："
                + "、".join(f"{k}×{v}" for k, v in by_set.items())
                + f"（{n_sets}/{len(by_set)} 个会话出现稀窗）"
            )
        for d in dist.get("dense") or []:
            ev = d.get("evidence") or {}
            verdict = "产品规则允许（诊断，不计违规）" if ev.get("allowed") else "产品规则不允许（越界）"
            print(
                f"  └ 密窗 {d['label']} V_hook={d['V_hook']}：{verdict}"
                f"（不同键={'是' if ev.get('distinct_keys') else '否'}、"
                f"均首提={'是' if ev.get('all_first') else '否'}、"
                f"冷却已过={'是' if ev.get('cooldown_ok') else '否'}、"
                f"会话总额 {ev.get('session_hook_total')}≤{ev.get('session_max')}）"
            )
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
    d = c.get("distribution") or {}
    print(
        f"{CRITERIA_LABELS['window_budget']:<24}"
        f"稀 {d.get('sparse_v0', 0)} / 密 {d.get('dense', 0):<12}"
        f"{'诊断（不参与判定）':<16}诊断"
    )
    c = checks.get("name_budget")
    if c:
        print(
            f"{CRITERIA_LABELS['name_budget']:<24}{c['violations']} 个窗口越界     "
            f"{'诊断（不参与判定）':<16}诊断"
        )
    c = checks.get("window_scheme_audit")
    if c:
        inexact = c.get("inexact_red_local") or []
        value = f"绿转红 {c['green_to_red']} 条 / 空洞 {len(c['coverage_gaps'])} / 错位 {len(inexact)} 条"
        print(
            f"{CRITERIA_LABELS['window_scheme_audit']:<24}{value:<26}"
            f"{'绿转红=0 且空洞=0':<16}{'PASS' if c['pass'] else 'FAIL'}"
        )
        if inexact:
            by_set = Counter(lbl.split()[0] for lbl in inexact)
            print("  └ 切片错位局部红窗（不计入绿转红，但同样要看）：" + "、".join(inexact))
            print("    按套：" + "、".join(f"{k}×{v}" for k, v in sorted(by_set.items())) + "（详见下方口径重算）")
    c = checks["per_turn_cap"]
    print(f"{CRITERIA_LABELS['per_turn_cap']:<24}{c['violations']} 轮越界           ≤1 条           {'PASS' if c['pass'] else 'FAIL'}")
    c = checks["per_key_cooldown"]
    cd = (
        f"≥{c['hook_cooldown']} 轮"
        if c["hook_cooldown"] == c["name_cooldown"]
        else f"≥{c['hook_cooldown']}/{c['name_cooldown']} 轮"
    )
    print(
        f"{CRITERIA_LABELS['per_key_cooldown']:<24}{c['violations']} 次            "
        f"{cd:<16}{'PASS' if c['pass'] else 'FAIL'}"
    )
    c = checks["session_total_cap"]
    print(
        f"{CRITERIA_LABELS['session_total_cap']:<24}{c['violations']} 套越界         "
        f"≤{c['max_per_session']} 次/会话      {'PASS' if c['pass'] else 'FAIL'}"
    )
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
    print(f"{'套':<5}{'ID':<24}{'Vh':>4}{'Vn':>4}{'E':>4}{'无效':>6}{'窗口(local Vh/Vn)':>20}  备注")
    for s in report["sets_summary"]:
        local = "/".join(
            f"{w['V_hook']}/{w['V_name']}{'*' if w['partial'] else ''}" for w in s["local_windows"]
        )
        print(
            f"S{s['set']:02d}  {s['id']:<24}{s['V_hook']:>4}{s['V_name']:>4}"
            f"{s['E']:>4}{s['invalid']:>6}{local:>20}  {s['note']}"
        )

    print("\n【窗口分布 · 按会话切】40 窗（诊断，不参与判定；尾窗按实际轮数，套与套不拼）")
    print(f"{'窗口':<20}{'V_hook':>7}{'V_name':>7}{'E':>4}{'无效':>6}  分布")
    for w in report["windows_local"]:
        if w["V_hook"] > WINDOW_V_MAX:
            mark = "密窗" + ("(合法)" if w["pass"] else "(越界)")
        elif w["pass"]:
            mark = "OK " + ("(稀)" if w["V_hook"] == 0 else "")
        else:
            mark = "越界"
        print(
            f"{w['label']:<20}{w['V_hook']:>7}{w['V_name']:>7}{w['E']:>4}{w['invalid']:>6}  {mark}"
        )
    print("  稀窗（V_hook=0）是合法分布：无线索/本套无事实/墓碑后/敏感未受邀/会话预算用尽时不回带。")
    print("  稀窗/密窗只是分布诊断：产品未定义窗口机制，不据此判定 PASS/FAIL。")
    print("  尾窗（T31-35）只有 5 轮样本：V_hook 绝对数天然偏小，同样只作诊断。")

    print("\n【诊断：全局 35 窗（不参与红绿判定）】")
    print(f"{'窗口':<6}{'全局轮':<14}{'套/轮':<30}{'Vh':>4}{'Vn':>4}{'E':>4}{'无效':>6}  诊断")
    for w in report["windows"]:
        marks = ("*" if w["crosses_sets"] else " ") + ("~" if w.get("offset_slice") else " ")
        print(
            f"W{w['window']:02d}{marks:<2}{w['global_start']:>3}-{w['global_end']:<8}"
            f"{w['turns_description'][:28]:<30}{w['V_hook']:>4}{w['V_name']:>4}{w['E']:>4}{w['invalid']:>6}"
        )
    print("  * = 跨套；~ = 偏移切片（未对齐该会话 T1/T11/T21/T31）")
    print("    跨套/偏移窗口的 V 之和不是单会话预算的合法读法，仅作诊断。")

    diag = report["diagnostics"]
    print("\n【口径重算】")
    print(
        f"  旧全局窗口红 {diag['global_windows']['violations']} 窗 → 局部不合规窗 "
        f"{checks['window_budget']['violations']} 窗（稀窗不计违规；全部仅诊断，不参与判定）"
    )
    print("  红转绿（纯切窗口径）：" + ("、".join(f"W{r['window']:02d}" for r in diag["red_to_green"]) or "无"))
    print("  诊断仍红（与局部红重叠）：" + ("、".join(f"W{n:02d}" for n in diag["still_red"]) or "无"))
    print("  绿转红：" + ("、".join(r["label"] for r in diag["green_to_red"]) or "无"))
    if diag.get("inexact_red_local"):
        print(
            "  切片错位、不参与绿转红比对的局部红窗："
            + "、".join(r["label"] for r in diag["inexact_red_local"])
        )
    nb = checks.get("name_budget") or {}
    if nb.get("distribution"):
        print("  V_name 分布（窗数）：" + "，".join(f"{k} 次×{v} 窗" for k, v in nb["distribution"].items()))
    ho = diag.get("hook_only_total")
    if ho:
        print(f"  参考（不判红绿）：仅计 hook 时 V_hook={ho['V_hook']}，门槛 {ho['threshold']}，"
              f"{'达标' if ho['pass'] else '未达标'}；判定仍按合计口径")
    print("  " + diag.get("green_to_red_conclusion", ""))
    print("  算法：" + diag.get("green_to_red_computed_by", ""))

    failures = report["failures"]
    print(f"\n【失败项逐条定位】共 {len(failures)} 条")
    if failures:
        for i, f in enumerate(failures, 1):
            owner = f.get("owner") or "未归属"
            extra = f" ← {f['owner_reason']}" if f.get("owner_reason") else ""
            print(f"{i:02d}. [{f['criterion']}][{owner}] {f.get('detail', '')}{extra}")
    else:
        print("无")

    own = report.get("ownership")
    if own:
        print(
            f"\n【归属】生成侧 {own['生成侧']} / 判据侧 {own['判据侧']} / 剧本输入 {own['剧本输入']}"
        )
        print("  " + own["note"])

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
def _write_manifest(out: Path, report: Dict[str, Any]) -> Dict[str, Any]:
    """在报告同目录写 manifest.json 记录 report.json 的 sha256（文件无法内嵌自身哈希）。"""
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    manifest = {
        "run_dir": str(out.parent),
        "report_path": str(out),
        "report_sha256": sha,
        "report_bytes": out.stat().st_size,
        "reps": report.get("meta", {}).get("reps"),
        "seed": report.get("meta", {}).get("seed"),
        "generated_at": report.get("meta", {}).get("generated_at"),
        "overall_pass": report.get("overall_pass"),
        "note": (
            "report.json 无法内嵌自身 sha256，故与报告同目录保存本清单；"
            "每次运行写入独立 run 目录（a91b169），同 seed 复跑不会覆盖取证。"
        ),
    }
    manifest_path = out.parent / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "run_dir": str(out.parent),
        "report_path": str(out),
        "report_sha256": sha,
        "manifest_path": str(manifest_path),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="主动回带验收：10 套 × 35 轮（离线 fake 后端）")
    parser.add_argument("--reps", type=int, default=1, help="复跑次数，用于检查一致性（默认 1）")
    parser.add_argument("--seed", type=int, default=20261108, help="随机种子基数")
    parser.add_argument(
        "--json",
        default=None,
        help="报告 JSON 输出路径；缺省写入 runs/<时间戳>_reps<N>_seed<S>/report.json"
        "（同目录 manifest.json 记录 sha256）；传空字符串则不写文件",
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

    # 账本按 run 目录隔离（沿用 a91b169）：同 seed 复跑不再覆盖上一次的取证
    if args.json is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = REPO_ROOT / "artifacts/active_recall_acceptance/runs"
        run_dir = base / f"{stamp}_reps{args.reps}_seed{args.seed}"
        n = 1
        while run_dir.exists():
            n += 1
            run_dir = base / f"{stamp}_reps{args.reps}_seed{args.seed}-{n}"
        out: Optional[Path] = run_dir / "report.json"
    elif args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO_ROOT / out
    else:
        out = None

    ledger = None
    if out is not None:
        # 报告内也印出本次 run 路径；文件自身 sha256 无法自嵌，落在同目录 manifest.json。
        report.setdefault("meta", {})["provenance"] = {
            "run_dir": str(out.parent),
            "report_path": str(out),
            "report_sha256_note": "本文件 sha256 记录在同目录 manifest.json，控制台顶部同步打印。",
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        ledger = _write_manifest(out, report)

    _print_console(report, verbose=args.verbose, run_info=ledger)

    if ledger:
        print(f"\n报告已写入：{ledger['report_path']}")
        print(f"本次 run 目录：{ledger['run_dir']}")
        print(f"报告 sha256：{ledger['report_sha256']}（见 {ledger['manifest_path']}）")

    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    # 只在命令行运行时屏蔽“已从凭据管理器读取 key”等 INFO 日志；导入本模块不产生副作用。
    logging.disable(logging.INFO)
    raise SystemExit(main())
