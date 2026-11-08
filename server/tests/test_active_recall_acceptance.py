# -*- coding: utf-8 -*-
"""主动回带验收脚本（scripts/active_recall_acceptance.py）的判据单测。

只测判据与聚合逻辑（模板匹配、事件检测、分类、墓碑扫描、窗口汇总、复跑一致性），
不跑 10 套全量；需要全量一致性验证时用脚本的 --reps >= 2。
"""
import copy
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

_spec = importlib.util.spec_from_file_location(
    "active_recall_acceptance", REPO_ROOT / "scripts" / "active_recall_acceptance.py"
)
ara = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ara
_spec.loader.exec_module(ara)


def _ev(key, value, text=None, kind="value", type_="hook"):
    return {
        "type": type_,
        "key": key,
        "kind": kind,
        "value": value,
        "text": text if text is not None else f"{value}",
    }


def _ctx(**override):
    base = {
        "user_text": "今天过得还行",
        "memory_before": {},
        "memory_after": {},
        "mem_updates": {},
        "extracted_before": {},
        "tombstones": [],
        "turn": 10,
        "last_turn": {},
        "seen_values": set(),
        "event_index": 0,
    }
    base.update(override)
    return base


# --- 模板匹配 ---------------------------------------------------------------
def test_match_hook_value_and_long_templates():
    hit = ara.match_hook("你家年糕呢？今天乖不乖")
    assert hit["key"] == "宠物" and hit["kind"] == "value" and hit["value"] == "年糕"

    long_hit = ara.match_hook("你上次说的那件事——后来怎么样了？")
    assert long_hit["key"] == "大事" and long_hit["kind"] == "long"

    assert ara.match_hook("今天天气不错") is None
    assert ara.match_hook(None) is None


def test_pet_generic_rejects_phrase_values():
    assert ara._pet_generic("养只狗") is True
    assert ara._pet_generic("想养只狗") is True
    assert ara._pet_generic("猫") is True
    assert ara._pet_generic("年糕") is False
    assert ara._pet_generic("橘猫") is False


# --- 事件检测 ---------------------------------------------------------------
def test_detect_events_name_and_hook():
    evs = ara.detect_events(
        user_text="今天上班被骂了",
        reply="阿秋，怎么啦",
        hook="你家年糕呢？今天乖不乖",
        memory_before={"昵称": "阿秋", "宠物": "年糕"},
        memory_after={"昵称": "阿秋", "宠物": "年糕"},
    )
    assert [e["key"] for e in evs] == ["昵称", "宠物"]
    assert evs[0]["type"] == "name" and evs[1]["type"] == "hook"


def test_detect_events_skips_name_when_user_mentions_it():
    evs = ara.detect_events(
        user_text="阿秋今天好累",
        reply="阿秋，那就先歇会儿",
        hook=None,
        memory_before={"昵称": "阿秋"},
        memory_after={"昵称": "阿秋"},
    )
    assert evs == []


# --- 分类：有效 / 错误 / 无效 -------------------------------------------------
def test_classify_valid_recall():
    ev = _ev("宠物", "年糕", "你家年糕呢？今天乖不乖")
    ara.classify_event(ev, _ctx(memory_after={"宠物": "年糕"}, turn=10))
    assert ev["verdict"] == "V"


def test_classify_deleted_value_is_tombstone():
    ev = _ev("宠物", "年糕", "你家年糕呢？今天乖不乖")
    ara.classify_event(
        ev,
        _ctx(
            memory_after={},
            tombstones=[{"kind": "delete", "key": "宠物", "value": "年糕", "turn": 6}],
            turn=10,
        ),
    )
    assert ev["verdict"] == "E_tombstone"


def test_classify_old_value_after_override():
    ev = _ev("宠物", "团子", "你家团子呢？今天乖不乖")
    ara.classify_event(
        ev,
        _ctx(
            memory_after={"宠物": "年糕"},
            tombstones=[{"kind": "override", "key": "宠物", "value": "团子", "turn": 8}],
            turn=10,
        ),
    )
    assert ev["verdict"] == "E_override"


def test_classify_cross_entity_is_misbind():
    ev = _ev("老板", "王芳", "王芳今天没又折腾你吧")
    ara.classify_event(
        ev,
        _ctx(memory_after={"老板": "张三", "家人": "王芳"}, turn=10),
    )
    assert ev["verdict"] == "E_misbind"


def test_classify_unknown_attribute_is_fabricated():
    ev = _ev("宠物", "年糕", "你家年糕3岁了呢")
    ara.classify_event(ev, _ctx(memory_after={"宠物": "年糕"}, turn=10))
    assert ev["verdict"] == "E_fabricated"


def test_classify_sensitive_without_invite():
    ev = _ev("状态", "失眠", "你上次说失眠——这两天好些了吗？")
    ara.classify_event(
        ev,
        _ctx(memory_after={"状态": "失眠"}, user_text="考试好难", turn=10),
    )
    assert ev["verdict"] == "E_sensitive"


def test_classify_sensitive_with_invite_is_valid():
    ev = _ev("状态", "失眠", "你上次说失眠——这两天好些了吗？")
    ara.classify_event(
        ev,
        _ctx(memory_after={"状态": "失眠"}, user_text="最近总觉得累", turn=10),
    )
    assert ev["verdict"] == "V"


def test_classify_cooldown_and_per_turn_cap():
    ev = _ev("宠物", "年糕", "你家年糕呢？今天乖不乖")
    ara.classify_event(
        ev,
        _ctx(memory_after={"宠物": "年糕"}, last_turn={"宠物": 7}, turn=10),
    )
    assert ev["verdict"] == "E_cooldown"

    ev2 = _ev("宠物", "年糕", "你家年糕呢？今天乖不乖")
    ara.classify_event(
        ev2,
        _ctx(memory_after={"宠物": "年糕"}, event_index=1, turn=10),
    )
    assert ev2["verdict"] == "E_multi"


def test_classify_soft_invalid_cases():
    same = _ev("宠物", "年糕", "你家年糕呢？今天乖不乖")
    ara.classify_event(
        same,
        _ctx(memory_after={"宠物": "年糕"}, user_text="我家年糕今天拆家了"),
    )
    assert same["verdict"] == "无效_same_turn"

    premature = _ev("宠物", "年糕", "你家年糕呢？今天乖不乖")
    ara.classify_event(
        premature,
        _ctx(memory_after={"宠物": "年糕"}, extracted_before={"宠物": 9}, turn=10),
    )
    assert premature["verdict"] == "无效_premature"

    generic = _ev("宠物", "养只狗", "你家养只狗呢？今天乖不乖")
    ara.classify_event(generic, _ctx(memory_after={"宠物": "养只狗"}))
    assert generic["verdict"] == "无效_generic"

    stale = _ev("宠物", "团子", "你家团子呢？今天乖不乖")
    ara.classify_event(
        stale,
        _ctx(
            memory_after={"宠物": "年糕"},
            user_text="是我记错了",
            seen_values={"团子"},
            tombstones=[{"kind": "override", "key": "宠物", "value": "团子", "turn": 10}],
            turn=10,
        ),
    )
    assert stale["verdict"] == "无效_stale"


# --- 墓碑/改口后提及扫描 ------------------------------------------------------
def test_scan_mentions_only_after_tombstone_turn():
    tombstones = [{"kind": "delete", "key": "宠物", "value": "年糕", "turn": 18}]
    assert ara.scan_mentions(turn=17, assistant_text="年糕", tombstones=tombstones) == []
    assert ara.scan_mentions(turn=18, assistant_text="年糕", tombstones=tombstones) == []
    hits = ara.scan_mentions(turn=19, assistant_text="年糕又闹了", tombstones=tombstones)
    assert len(hits) == 1 and hits[0]["value"] == "年糕" and hits[0]["kind"] == "delete"


def test_update_facts_records_delete_and_override():
    facts, tbs = [], []
    ara._update_facts(before={}, after={"宠物": "团子"}, turn=1, facts=facts, tombstones=tbs)
    assert facts[-1]["kind"] == "introduce"

    ara._update_facts(before={"宠物": "团子"}, after={"宠物": "年糕"}, turn=16, facts=facts, tombstones=tbs)
    assert facts[-1]["kind"] == "override" and tbs[-1]["value"] == "团子"

    ara._update_facts(before={"宠物": "年糕"}, after={}, turn=18, facts=facts, tombstones=tbs)
    assert facts[-1]["kind"] == "delete" and tbs[-1]["kind"] == "delete"


# --- 窗口聚合 ---------------------------------------------------------------
def _synth_event(set_index, turn, verdict="V", type_="hook"):
    g = (set_index - 1) * ara.TURNS_PER_SET + turn
    return {
        "type": type_,
        "key": "宠物",
        "kind": "value",
        "value": "年糕",
        "stored_value": "年糕",
        "text": "你家年糕呢？今天乖不乖",
        "user": "今天过得还行",
        "reply": "嗯",
        "hook": "你家年糕呢？今天乖不乖",
        "verdict": verdict,
        "reason": "",
        "set": set_index,
        "set_id": f"S{set_index:02d}",
        "turn": turn,
        "global_turn": g,
        "window": (g - 1) // ara.WINDOW_SIZE + 1,
    }


def _synth_sets(events_by_set, mentions_by_set=None):
    mentions_by_set = mentions_by_set or {}
    out = []
    for idx in range(1, ara.SETS + 1):
        out.append(
            {
                "summary": {"turns": ara.TURNS_PER_SET},
                "events": events_by_set.get(idx, []),
                "mentions": list(mentions_by_set.get(idx, [])),
            }
        )
    return out


def test_evaluate_windows_errors_and_failures():
    events = {}
    for s in range(1, ara.SETS + 1):
        events[s] = [_synth_event(s, t) for t in (1, 11, 21, 31)]
    events[1].extend(
        [
            _synth_event(1, 2),
            _synth_event(1, 12),
            _synth_event(1, 21),  # 与 T21 同轮 -> per_turn_cap
            _synth_event(1, 22),
            _synth_event(1, 23),
        ]
    )
    events[3].append(_synth_event(3, 10, verdict="E_sensitive"))
    mention = {
        "kind": "delete",
        "key": "宠物",
        "value": "年糕",
        "set": 4,
        "set_id": "S04",
        "turn": 5,
        "global_turn": 110,
        "window": 11,
        "tombstone_turn": 3,
        "text": "年糕",
    }
    res = ara.evaluate(_synth_sets(events, {4: [mention]}))

    assert len(res["windows_local"]) == ara.SETS * 4
    assert [(w["start"], w["end"], w["V"], w["pass"]) for w in res["windows_local"][:4]] == [
        (1, 10, 2, True),
        (11, 20, 2, True),
        (21, 30, 4, False),
        (31, 35, 1, True),
    ]
    assert res["windows_local"][3]["partial"] is True
    assert res["V"] == 45 and res["E"] == 2 and res["invalid"] == 0
    assert res["V_hook"] == 45 and res["V_name"] == 0
    assert res["summary"] == {"V_hook": 45, "V_name": 0, "E": 2, "precision": res["precision"]}
    assert res["checks"]["name_budget"]["pass"] is True
    assert res["checks"]["window_budget"]["violations"] == 1
    assert res["checks"]["window_budget"]["windows"] == 40
    assert res["checks"]["errors"]["actual"] == 2
    assert res["checks"]["tombstone_zero"]["mentions"] == 1
    assert abs(res["precision"] - 45 / 47) < 1e-9

    assert len(res["windows"]) == ara.TOTAL_WINDOWS
    diag = res["diagnostics"]
    assert diag["global_windows"]["count"] == 35
    assert diag["global_windows"]["participates_in_verdict"] is False
    assert diag["still_red"] == [3]
    assert diag["green_to_red"] == []
    assert diag["coverage_complete"] is True and diag["coverage_gaps"] == []
    assert "绿转红 0 条" in diag["green_to_red_conclusion"]
    assert "_covering_global()" in diag["green_to_red_computed_by"]
    assert res["checks"]["window_scheme_audit"]["pass"] is True
    assert res["checks"]["window_budget"]["partial_windows"] == ara.SETS
    assert diag["offset_slice_windows"]

    assert any(
        f["criterion"] == "window_budget" and f["set"] == 1 and f["local_turns"] == [21, 30]
        for f in res["failures"]
    )
    cap = [f for f in res["failures"] if f["criterion"] == "per_turn_cap"]
    assert cap and cap[0]["set"] == 1 and cap[0]["turn"] == 21
    assert any(f["criterion"] == "tombstone_zero" for f in res["failures"])


def test_local_windows_partial_tail_is_upper_bound_only():
    """尾窗只有 5 轮样本：V=0 不判“回带过少”，但 V>2 仍越上限。"""
    empty = ara._local_windows(1, "S01", [], [])
    assert [(w["start"], w["end"], w["rule"], w["pass"]) for w in empty] == [
        (1, 10, "range", False),
        (11, 20, "range", False),
        (21, 30, "range", False),
        (31, 35, "upper_only", True),
    ]
    assert empty[3]["partial"] is True and empty[3]["actual_turns"] == 5

    tail = ara._local_windows(
        1, "S01", [_synth_event(1, t) for t in (31, 32, 33)], []
    )[3]
    assert tail["V"] == 3 and tail["pass"] is False


def test_name_recall_split_and_own_budget():
    """名字与 hook 分开量：合计 V=3 不触发 hook 窗口上限；名字过密走自己的判据。"""
    events = {
        s: [_synth_event(s, 1), _synth_event(s, 6), _synth_event(s, 3, type_="name")]
        for s in range(1, ara.SETS + 1)
    }
    res = ara.evaluate(_synth_sets(events))
    w = res["windows_local"][0]
    assert (w["V"], w["V_hook"], w["V_name"]) == (3, 2, 1)
    assert w["pass"] is True and w["name_pass"] is True
    assert res["summary"]["V_hook"] == 20 and res["summary"]["V_name"] == 10
    assert res["checks"]["name_budget"]["distribution"] == {"0": 30, "1": ara.SETS}
    assert res["checks"]["window_budget"]["violations"] == 20  # 每套后两个整窗无 hook（尾窗不判下限）

    dense = {
        s: [_synth_event(s, t, type_="name") for t in range(1, 8)] + [_synth_event(s, 2)]
        for s in range(1, ara.SETS + 1)
    }
    res2 = ara.evaluate(_synth_sets(dense))
    w2 = res2["windows_local"][0]
    assert w2["name_pass"] is False and w2["pass"] is True
    assert res2["checks"]["name_budget"]["violations"] == ara.SETS
    assert any(f["criterion"] == "name_budget" for f in res2["failures"])


def test_consistency_requires_at_least_two_reps():
    fp = {
        "V": 1, "E": 0, "invalid": 0,
        "windows_local": [(1, 1, 10, 1, 0, 0, True)],
        "diagnostic_global": [(1, 1, 0, 0, True)],
        "events": [(1, 1, "hook", "宠物", "V")],
        "mentions": [],
        "failures": [],
    }
    reps = [{"rep": 1, "fingerprint": copy.deepcopy(fp)}, {"rep": 2, "fingerprint": copy.deepcopy(fp)}]

    check, inc = ara._check_consistency(reps, 2)
    assert check["pass"] is True and check["verified"] is True and inc == []

    single, _ = ara._check_consistency(reps[:1], 1)
    assert single["pass"] is False and single["verified"] is False

    reps[1]["fingerprint"]["V"] = 2
    check2, inc2 = ara._check_consistency(reps, 2)
    assert check2["pass"] is False and inc2 and "V: 1 -> 2" in inc2[0]["diffs"]


def test_scenario_shape_and_turn_builder():
    assert len(ara.SET_SPECS) == ara.SETS
    ids = {s["id"] for s in ara.SET_SPECS}
    assert len(ids) == ara.SETS
    for spec in ara.SET_SPECS:
        turns = ara.build_set_turns(spec, filler_offset=0)
        assert len(turns) == ara.TURNS_PER_SET
        for t in spec.get("special", {}):
            assert 1 <= int(t) <= ara.TURNS_PER_SET


def test_run_set_single_set_is_deterministic():
    spec = ara.SET_SPECS[8]  # S09 只有昵称，35 轮，单集重跑成本低
    first = ara.run_set(rep=1, set_index=9, spec=spec, base_seed=20261108)
    second = ara.run_set(rep=1, set_index=9, spec=spec, base_seed=20261108)
    sig1 = [(e["turn"], e["type"], e["key"], e["verdict"]) for e in first["events"]]
    sig2 = [(e["turn"], e["type"], e["key"], e["verdict"]) for e in second["events"]]
    assert sig1 == sig2
    assert first["summary"]["turns"] == ara.TURNS_PER_SET
