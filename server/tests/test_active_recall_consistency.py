# -*- coding: utf-8 -*-
"""复跑一致性验证脚本（scripts/active_recall_consistency.py）的单测。

只测比对与结论文案，不跑 10 套、不请求后端：
输入是手工构造的「三次运行视图」，验证窗口级比对、差异定位、
以及 reps=1 必须判“未完成验证”而不是“一致”。
"""
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"
for p in (SERVER_DIR, REPO_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_spec = importlib.util.spec_from_file_location(
    "active_recall_consistency", REPO_ROOT / "scripts" / "active_recall_consistency.py"
)
arc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = arc
_spec.loader.exec_module(arc)


def _windows(v_hook_override=None, v_name_override=None):
    """40 个局部窗（每套 4 个），默认每窗 V_hook=1、V_name=0、E=0。"""
    v_hook_override = v_hook_override or {}
    v_name_override = v_name_override or {}
    out = []
    for s in range(1, arc.ara.SETS + 1):
        for start in range(1, arc.ara.TURNS_PER_SET + 1, arc.ara.WINDOW_SIZE):
            end = min(start + arc.ara.WINDOW_SIZE - 1, arc.ara.TURNS_PER_SET)
            key = (s, start)
            vh = v_hook_override.get(key, 1)
            vn = v_name_override.get(key, 0)
            out.append(
                {
                    "set": s,
                    "start": start,
                    "end": end,
                    "label": f"S{s:02d} T{start:02d}-{end:02d}",
                    "partial": (end - start + 1) < arc.ara.WINDOW_SIZE,
                    "V_hook": vh,
                    "V_name": vn,
                    "V": vh + vn,
                    "E": 0,
                    "event_ids": [f"S{s:02d}T{start:02d}:hook:宠物=V"],
                }
            )
    return out


def _view(rep, *, v_hook_override=None, v_name_override=None, **extra):
    windows = _windows(v_hook_override, v_name_override)
    view = {
        "rep": rep,
        "failed": False,
        "fail_reason": None,
        "windows": windows,
        "V": sum(w["V"] for w in windows),
        "V_hook": sum(w["V_hook"] for w in windows),
        "V_name": sum(w["V_name"] for w in windows),
        "E": 0,
        "precision": 1.0,
    }
    view.update(extra)
    return view


def test_single_rep_is_incomplete_not_consistent(monkeypatch, capsys):
    """reps=1：必须判“未完成验证（只有 1 次运行）”，不许判一致。"""
    monkeypatch.setattr(arc, "run_one_rep", lambda rep, seed: _view(rep))
    rc = arc.main(["--reps", "1", "--json", ""])
    out = capsys.readouterr().out
    assert "未完成验证" in out
    assert "只有 1 次运行" in out
    assert "结论：一致" not in out
    assert rc == 1

    res = arc.verify(reps=1, seed=1, runner=lambda rep, seed: _view(rep))
    assert res["verdict"] == arc.VERDICT_INCOMPLETE
    assert "只有 1 次运行" in arc.conclusion_line(res)


def test_three_identical_views_are_consistent():
    views = [_view(r) for r in (1, 2, 3)]
    res = arc.compare_views(views, requested_reps=3)
    assert res["verdict"] == arc.VERDICT_CONSISTENT
    assert res["window_diffs"] == [] and res["global_diffs"] == []
    assert arc.conclusion_line(res).startswith("结论：一致")


def test_window_level_diff_is_located_with_values_and_event_ids():
    views = [
        _view(1),
        _view(2, v_hook_override={(1, 1): 4}),
        _view(3),
    ]
    res = arc.compare_views(views, requested_reps=3)
    assert res["verdict"] == arc.VERDICT_INCONSISTENT
    diffs = res["window_diffs"]
    assert len(diffs) == 1 and diffs[0]["window"] == "S01 T01-10"
    assert diffs[0]["values"]["rep1"] == {"V_hook": 1, "V_name": 0, "E": 0, "V": 1}
    assert diffs[0]["values"]["rep2"] == {"V_hook": 4, "V_name": 0, "E": 0, "V": 4}
    assert diffs[0]["values"]["rep3"] == {"V_hook": 1, "V_name": 0, "E": 0, "V": 1}
    assert diffs[0]["event_ids"]["rep2"]
    line = arc.conclusion_line(res)
    assert "不一致" in line and "S01 T01-10" in line


def test_globals_equal_but_windows_differ_still_inconsistent():
    """全局三项相等不算一致：窗口级差异照样判不一致。"""
    a = _view(1)
    b = _view(2, v_hook_override={(1, 1): 2, (1, 11): 0})
    b["V"] = a["V"]  # 冒充全局相等
    b["precision"] = a["precision"]
    b["E"] = a["E"]
    c = _view(3)
    res = arc.compare_views([a, b, c], requested_reps=3)
    assert res["verdict"] == arc.VERDICT_INCONSISTENT
    assert {d["window"] for d in res["window_diffs"]} == {"S01 T01-10", "S01 T11-20"}


def test_failed_rep_or_unverified_is_incomplete():
    views = [_view(1), arc.failed_view(2, "RuntimeError: boom"), _view(3)]
    res = arc.compare_views(views, requested_reps=3)
    assert res["verdict"] == arc.VERDICT_INCOMPLETE
    assert any("第 2 次运行失败" in r for r in res["reasons"])

    views2 = [_view(1), _view(2, consistency_unverified=True), _view(3)]
    res2 = arc.compare_views(views2, requested_reps=3)
    assert res2["verdict"] == arc.VERDICT_INCOMPLETE
    assert any("consistency_unverified" in r for r in res2["reasons"])


def test_window_set_mismatch_is_incomplete():
    a = _view(1)
    b = _view(2)
    b["windows"] = b["windows"][:-1]  # 少一个窗
    res = arc.compare_views([a, b], requested_reps=2)
    assert res["verdict"] == arc.VERDICT_INCOMPLETE
    assert any("窗口集合" in r for r in res["reasons"])
