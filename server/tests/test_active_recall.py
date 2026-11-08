# -*- coding: utf-8 -*-
"""W21 主动记忆回带（名字/计划/时间）离线断言。

不依赖真实模型：用 mock_api 的模板语料做确定性替身，
判定只落在回复文本上——记忆槽里有名字不算回带，回复里出现才算。
"""
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import mock_api  # noqa: E402
import memory_slots  # noqa: E402
import prompt_core  # noqa: E402


def _reply(messages, sid):
    out = mock_api.make_reply(messages, sid)
    return out["reply"] + "||" + (out.get("hook") or "")


def _name_recalled(reply_text, name):
    return name in reply_text


def test_prompt_requires_proactive_recall():
    """提示词必须把“名字/计划/时间主动带回”写成硬要求，而不是“相关时再说”。"""
    rules = prompt_core.MEMORY_SLOT_RULES
    assert "名字/计划/时间" in rules
    assert "隔 1-2 轮主动带一次" in rules
    assert "叫名字" in rules


def test_slot_pin_survives_competing_match():
    """别的槽命中时，昵称/大事这类钉子槽不能被检索丢掉。"""
    store = memory_slots.SlotStore()
    sid = "w21-pin-survive"
    for text in ("我叫张三", "我下个月要搬家", "最近在加班赶稿"):
        store.ingest(sid, text)
    hits = [x["key"] for x in store.recall(sid, "今天又加班到十点")]
    assert "在忙" in hits
    assert "昵称" in hits, "用户本轮没提名字，但昵称槽应仍在注入上下文里"


def test_mock_recalls_name_on_next_turn():
    """正例：用户提名字 → 下一轮不再提 → 回复文本里出现该名字。"""
    sid = "w21-name-positive"
    first = _reply([{"role": "user", "content": "叫我阿秋就行"}], sid)
    second = _reply([
        {"role": "user", "content": "叫我阿秋就行"},
        {"role": "assistant", "content": first},
        {"role": "user", "content": "今天上班被骂了"},
    ], sid)
    assert _name_recalled(second, "阿秋"), "回复完全没提名字：%r" % second


def test_name_recall_does_not_consume_hook_budget():
    """称呼不算 hook 回提：不能挤掉大事/宠物等 RECALL_MAX 名额。"""
    sid = "w21-name-budget"
    _reply([{"role": "user", "content": "叫我阿秋就行"}], sid)
    _reply([
        {"role": "user", "content": "叫我阿秋就行"},
        {"role": "user", "content": "今天上班被骂了"},
    ], sid)
    sess = mock_api._STATE.get_session(sid)
    assert sess.get("recall_count", 0) == 0
    assert "昵称" in sess.get("memory_hits", {})


def test_negative_reply_without_name_is_flagged():
    """反例对照：同一判定器必须把“完全没提名字”的回复判红。"""
    assert _name_recalled("今天上班被骂了，真烦。", "阿秋") is False
    assert _name_recalled("阿秋，今天上班被骂了？", "阿秋") is True


def test_one_recall_per_turn_cap():
    """每轮最多 1 条主动回带（prompt_core.MEMORY_RULES / MEMORY_CONTRACT §6.2）：
    hook 已回带一条记忆时，本轮不再叠名字回带；名字顺延到下一轮，不被永久吞掉。"""
    sid = "w21-per-turn-cap"
    mock_api._STATE.update_memories(sid, {"昵称": "阿秋", "老板": "张三"})
    sess = mock_api._STATE.get_session(sid)
    # 第 10 轮末：名字与老板的冷却同时到期（都是第 5 轮用过）
    sess["turns"] = 10
    sess["mem_extracted"] = {"昵称": 1, "老板": 1}
    sess["memory_hits"] = {"昵称": 5, "老板": 5}

    out = mock_api.make_reply([{"role": "user", "content": "今天上班又被客户催了"}], sid)
    assert "张三" in (out.get("hook") or ""), "老板回提应命中：%r" % out
    assert "阿秋" not in out["reply"], "同轮已有 hook 回带，不应再叠名字：%r" % out

    nxt = mock_api.make_reply([{"role": "user", "content": "今天上班又被客户催了"}], sid)
    assert "阿秋" in nxt["reply"], "名字应顺延到下一轮：%r" % nxt
    assert "张三" not in (nxt.get("hook") or ""), "冷却内的老板不应再提：%r" % nxt


def test_sensitive_state_only_when_invited():
    """W22 敏感门控：状态=失眠默认不回带。"考试好难"没点名这一领域 -> 绝不出现；
    用户本轮自己提到累/困等 -> 正常回带（同一会话同一状态槽）。"""
    sid = "w22-sensitive-gate"

    def say(text):
        return mock_api.make_reply([{"role": "user", "content": text}], sid)

    say("最近总是失眠")
    say("今天工作好多")
    say("开了三个会")
    uninvited = say("考试好难")
    blob = (uninvited.get("hook") or "") + uninvited["reply"]
    assert "失眠" not in blob, "未受邀却回带敏感状态：%r" % uninvited

    invited = say("最近总觉得累")
    assert "失眠" in (invited.get("hook") or ""), "受邀后应回带：%r" % invited


def test_non_sensitive_recall_not_gated():
    """W22：门控只针对敏感槽，老板等普通记忆照常回带，不能一刀切。"""
    sid = "w22-nonsensitive"

    def say(text):
        return mock_api.make_reply([{"role": "user", "content": text}], sid)

    say("我们老板叫张三")
    say("今天工作好多")
    say("开了三个会")
    out = say("今天上班被领导怼了")
    assert "张三" in (out.get("hook") or ""), "非敏感记忆不应被门控砍掉：%r" % out
