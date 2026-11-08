#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主动回带复跑一致性验证：一条命令跑 N 次（默认 3），自动出结论。

用途：
  python scripts/active_recall_consistency.py            # = --reps 3
  python scripts/active_recall_consistency.py --reps 3 --json artifacts/active_recall_consistency/report.json

判定口径（不靠人肉看三份报告）：
  1. 每次运行各自输出 40 个「按会话切窗」局部窗的 V/E，脚本逐窗比对；
     全部 40 窗的 (V, E) 都相等，才可判「一致」。
  2. 全局 V 总数 / E 总数 / 精确率只作附带对照；这三项相等不算一致，
     窗口级相等才是证据。
  3. 任一次运行失败、或窗口集合对不齐、或某次仍带 consistency_unverified，
     一律判「未完成验证」，不允许降级成「一致」。
  4. 差异窗逐条列出：窗口编号、每次运行的 V/E、该窗口内引用的事件 id。

离线约束：只用 server/mock_api.py 的 fake 后端，不联网、不调用真实模型。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import active_recall_acceptance as ara  # noqa: E402  (其内部先设离线环境变量再 import mock_api)

REPO_ROOT = Path(__file__).resolve().parents[1]

VERDICT_CONSISTENT = "一致"
VERDICT_INCONSISTENT = "不一致"
VERDICT_INCOMPLETE = "未完成验证"


def expected_window_count() -> int:
    return ara.SETS * ((ara.TURNS_PER_SET + ara.WINDOW_SIZE - 1) // ara.WINDOW_SIZE)


def _event_ids(
    events: Sequence[Dict[str, Any]], set_index: int, start: int, end: int
) -> List[str]:
    """窗口内事件的可引用 id：套T轮:类型:键=值:判定。"""
    ids: List[str] = []
    for e in events:
        if e.get("set") != set_index:
            continue
        if not (start <= int(e.get("turn") or 0) <= end):
            continue
        ids.append(
            f"S{int(e['set']):02d}T{int(e['turn']):02d}:{e['type']}:{e['key']}"
            f"={e.get('value') or e.get('stored_value')}:{e['verdict']}"
        )
    return ids


def view_from_result(res: Dict[str, Any]) -> Dict[str, Any]:
    """把某一轮 evaluate() 的结果转成比对用的视图。"""
    return {
        "rep": res.get("rep"),
        "failed": False,
        "fail_reason": None,
        "windows": [
            {
                "set": w["set"],
                "start": w["start"],
                "end": w["end"],
                "label": w["label"],
                "partial": w["partial"],
                "V": w["V"],
                "E": w["E"],
                "event_ids": _event_ids(res["events"], w["set"], w["start"], w["end"]),
            }
            for w in res["windows_local"]
        ],
        "V": res["V"],
        "E": res["E"],
        "precision": res["precision"],
    }


def failed_view(rep: int, reason: str) -> Dict[str, Any]:
    return {
        "rep": rep,
        "failed": True,
        "fail_reason": reason,
        "windows": [],
        "V": None,
        "E": None,
        "precision": None,
    }


def _verdict(kind: str, **extra: Any) -> Dict[str, Any]:
    out = {"verdict": kind, "reasons": [], "window_diffs": [], "global_diffs": []}
    out.update(extra)
    return out


def compare_views(views: Sequence[Dict[str, Any]], requested_reps: int) -> Dict[str, Any]:
    """逐窗比对 N 次运行；窗口级相等才算一致。"""
    failed = [v for v in views if v.get("failed")]
    if failed:
        return _verdict(
            VERDICT_INCOMPLETE,
            reasons=[f"第 {v['rep']} 次运行失败：{v['fail_reason']}" for v in failed],
        )
    if len(views) < 2:
        return _verdict(
            VERDICT_INCOMPLETE,
            reasons=[f"只有 {len(views)} 次运行，至少需要 2 次才能谈一致性"],
        )
    if len(views) < requested_reps:
        return _verdict(
            VERDICT_INCOMPLETE,
            reasons=[f"计划 {requested_reps} 次，实际完成 {len(views)} 次"],
        )
    unverified = [v["rep"] for v in views if v.get("consistency_unverified")]
    if unverified:
        return _verdict(
            VERDICT_INCOMPLETE,
            reasons=[f"第 {r} 次仍带 consistency_unverified=FAIL" for r in unverified],
        )

    keys0: List[Tuple[int, int, int]] = [
        (w["set"], w["start"], w["end"]) for w in views[0]["windows"]
    ]
    for v in views[1:]:
        keys = [(w["set"], w["start"], w["end"]) for w in v["windows"]]
        if keys != keys0:
            return _verdict(
                VERDICT_INCOMPLETE,
                reasons=[
                    f"第 {v['rep']} 次的局部窗口集合与第 {views[0]['rep']} 次不一致，"
                    "无法逐窗比对（视为未完成验证，不降级为一致）"
                ],
            )
    if len(keys0) != expected_window_count():
        return _verdict(
            VERDICT_INCOMPLETE,
            reasons=[f"局部窗口数 {len(keys0)}，期望 {expected_window_count()}"],
        )

    window_diffs: List[Dict[str, Any]] = []
    for i, key in enumerate(keys0):
        vals = [(v["windows"][i]["V"], v["windows"][i]["E"]) for v in views]
        if len(set(vals)) <= 1:
            continue
        window_diffs.append(
            {
                "window": f"S{key[0]:02d} T{key[1]:02d}-{key[2]:02d}",
                "set": key[0],
                "start": key[1],
                "end": key[2],
                "values": {f"rep{v['rep']}": {"V": V, "E": E} for v, (V, E) in zip(views, vals)},
                "event_ids": {f"rep{v['rep']}": v["windows"][i]["event_ids"] for v in views},
            }
        )

    global_diffs: List[Dict[str, Any]] = []
    for name in ("V", "E", "precision"):
        got = [v.get(name) for v in views]
        if name == "precision":
            equal = all(
                g is not None and got[0] is not None and abs(g - got[0]) < 1e-12 for g in got
            )
        else:
            equal = len(set(got)) <= 1
        if not equal:
            global_diffs.append({"metric": name, "values": {f"rep{v['rep']}": v.get(name) for v in views}})

    if window_diffs or global_diffs:
        return _verdict(
            VERDICT_INCONSISTENT, window_diffs=window_diffs, global_diffs=global_diffs
        )
    return _verdict(VERDICT_CONSISTENT)


def run_one_rep(rep: int, seed: int) -> Dict[str, Any]:
    sets = [
        ara.run_set(rep=rep, set_index=idx, spec=spec, base_seed=seed)
        for idx, spec in enumerate(ara.SET_SPECS, 1)
    ]
    res = ara.evaluate(sets)
    res["rep"] = rep
    return view_from_result(res)


def collect_views(
    reps: int, seed: int, runner: Optional[Callable[[int, int], Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    runner = runner or run_one_rep
    views: List[Dict[str, Any]] = []
    for rep in range(1, reps + 1):
        try:
            views.append(runner(rep, seed))
        except Exception as exc:  # 任一次运行失败 -> 未完成验证，不降级
            views.append(failed_view(rep, f"{type(exc).__name__}: {exc}"))
    return views


def verify(
    *, reps: int, seed: int, runner: Optional[Callable[[int, int], Dict[str, Any]]] = None
) -> Dict[str, Any]:
    views = collect_views(reps=reps, seed=seed, runner=runner)
    result = compare_views(views, requested_reps=reps)
    result.update({"reps_planned": reps, "reps_completed": sum(1 for v in views if not v.get("failed"))})
    return result


# --- 输出 -------------------------------------------------------------------
def _fmt_ve(V: Any, E: Any) -> str:
    if V is None and E is None:
        return "-"
    return f"{V}/{E}"


def conclusion_line(result: Dict[str, Any]) -> str:
    kind = result["verdict"]
    if kind == VERDICT_CONSISTENT:
        return f"结论：{kind}（{result.get('reps_completed', 0)} 次运行，40 个局部窗逐窗 V/E 全等）"
    if kind == VERDICT_INCONSISTENT:
        labels = "、".join(d["window"] for d in result["window_diffs"]) or "无窗口差异"
        extra = "；全局量差异：" + "、".join(g["metric"] for g in result["global_diffs"]) if result["global_diffs"] else ""
        return f"结论：{kind}（差异窗：{labels}{extra}）"
    reasons = "；".join(result["reasons"]) or "原因未记录"
    return f"结论：{kind}（{reasons}）"


def print_report(result: Dict[str, Any], views: Sequence[Dict[str, Any]]) -> None:
    print("=" * 78)
    print("主动回带复跑一致性验证 · 窗口级比对（离线 fake 后端）")
    print("=" * 78)

    if views and views[0].get("windows"):
        reps = [v["rep"] for v in views]
        header = f"{'窗口':<18}" + "".join(f"{'rep' + str(r) + ' V/E':>12}" for r in reps) + "  结果"
        print("\n【逐窗表】40 个局部窗（V/E）")
        print(header)
        print("-" * 78)
        for i, w0 in enumerate(views[0]["windows"]):
            cells = "".join(
                f"{_fmt_ve(v['windows'][i]['V'], v['windows'][i]['E']):>12}"
                for v in views
                if v.get("windows")
            )
            same = all(
                (v["windows"][i]["V"], v["windows"][i]["E"])
                == (w0["V"], w0["E"])
                for v in views
            )
            print(f"{w0['label']:<18}{cells}  {'一致' if same else '差异'}")
    else:
        print("\n【逐窗表】无可比窗口（存在运行失败）")

    print("\n【全局量对照（仅附带，不作为一致性证据）】")
    for name, label in (("V", "V 总数"), ("E", "E 总数"), ("precision", "精确率")):
        vals = " / ".join(
            f"rep{v['rep']}={v.get(name)}" for v in views
        )
        same = len({v.get(name) for v in views}) <= 1
        print(f"  {label:<8}{vals:<44}{'相等' if same else '不等'}")

    if result["window_diffs"]:
        print("\n【差异窗明细】")
        for d in result["window_diffs"]:
            vals = " ".join(f"{k}={x['V']}/{x['E']}" for k, x in d["values"].items())
            print(f"  {d['window']}: {vals}")
            for k, ids in d["event_ids"].items():
                print(f"    {k} 事件：{('; '.join(ids)) if ids else '无事件'}")

    print("\n" + conclusion_line(result))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="主动回带复跑一致性验证（默认 3 次）")
    parser.add_argument("--reps", type=int, default=3, help="复跑次数（默认 3，至少 2 才算验证）")
    parser.add_argument("--seed", type=int, default=20261108, help="随机种子基数")
    parser.add_argument(
        "--json",
        default="artifacts/active_recall_consistency/report.json",
        help="结论 JSON 输出路径；传空字符串则不写文件",
    )
    args = parser.parse_args(argv)
    if args.reps < 1:
        parser.error("--reps 必须 >= 1")

    # 彻底离线：禁用 mock_api 的可选落盘。
    if getattr(ara.mock_api, "_DISK", None) is not None:
        ara.mock_api._DISK = None

    views = collect_views(reps=args.reps, seed=args.seed)
    result = compare_views(views, requested_reps=args.reps)

    print_report(result, views)

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {
                "reps_planned": args.reps,
                "seed": args.seed,
                "backend": "server/mock_api.py::make_reply（离线 fake 后端）",
                "window_primary": expected_window_count(),
            },
            "verdict": result["verdict"],
            "conclusion": conclusion_line(result),
            "reasons": result["reasons"],
            "window_diffs": result["window_diffs"],
            "global_diffs": result["global_diffs"],
            "reps": [
                {
                    "rep": v["rep"],
                    "failed": v.get("failed", False),
                    "fail_reason": v.get("fail_reason"),
                    "V": v.get("V"),
                    "E": v.get("E"),
                    "precision": v.get("precision"),
                    "windows": [
                        {"label": w["label"], "V": w["V"], "E": w["E"], "event_ids": w["event_ids"]}
                        for w in v.get("windows", [])
                    ],
                }
                for v in views
            ],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结论已写入：{out}")

    return 0 if result["verdict"] == VERDICT_CONSISTENT else 1


if __name__ == "__main__":
    raise SystemExit(main())
