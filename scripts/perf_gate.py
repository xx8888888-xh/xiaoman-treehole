#!/usr/bin/env python3
"""R6 性能基线门：把云端实测 JSON 与 docs/perf_baseline_r6.json 逐项比对。

三类结论：
  PASS   —— 实测在阈值内
  FAIL   —— 超阈值（进程退出码 1）
  EXEMPT —— 基线豁免清单里的已知环境上限（必须打印理由；guard 不满足时转为 FAIL）
  SKIP   —— 本次 run 未采集该段（--require-sections 指定后视为缺数据 FAIL）

用法：
  python scripts/perf_gate.py --float float_r4.json --power power_r4.json \
         --require-sections float,power
  python scripts/perf_gate.py --perf r6.json --require-sections firstsend
"""

from __future__ import annotations

import argparse
import json
import sys

PASS = "PASS"
FAIL = "FAIL"
EXEMPT = "EXEMPT"
SKIP = "SKIP"

SECTIONS = ("float", "power", "firstsend", "battery")


def _f(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_no(rec, idx):
    return rec.get("round", idx + 1)


def _presets(rec):
    return rec.get("presets") or {}


#define helpers to collect per-round values
def _collect_float(data, baseline):
    """返回 {metric_id: [(round_no, value, detail)]}"""
    out = {}
    rounds = data.get("rounds") or []
    full_ref = baseline.get("reference", {}).get("fullLogicalCanvas") or [0, 0]

    for idx, rec in enumerate(rounds):
        rn = _round_no(rec, idx)
        minimal = _presets(rec).get("minimal") or {}
        default = _presets(rec).get("default") or {}

        canvas = minimal.get("canvas") or {}
        w = _f(canvas.get("width"))
        h = _f(canvas.get("height"))
        area = _f(canvas.get("canvasArea"))
        if area is None and w is not None and h is not None:
            area = w * h
        out.setdefault("float.backing_area_px", []).append(
            (rn, area, f"{int(w) if w else '?'}x{int(h) if h else '?'}" if area is not None else "字段缺失"))

        gm = minimal.get("geometryMatrix") or []
        full_w, full_h = None, None
        if gm and isinstance(gm[0], dict):
            backing = gm[0].get("backing") or {}
            full_w = _f(backing.get("fullW"))
            full_h = _f(backing.get("fullH"))
        if not full_w or not full_h:
            full_w, full_h = _f(full_ref[0]), _f(full_ref[1])
        share = None
        if area is not None and full_w and full_h:
            share = area / (full_w * full_h)
        out.setdefault("float.backing_share_of_window", []).append(
            (rn, share, f"{int(area) if area else '?'}/{int(full_w)}x{int(full_h)}" if share is not None else "字段缺失"))

        ir = minimal.get("invalid_rasterization") or {}
        visible = _f(ir.get("visibleShareOfCanvas"))
        out.setdefault("float.visible_share_of_canvas", []).append(
            (rn, visible, "" if visible is not None else "字段缺失"))

        wins = default.get("windows") or {}
        full = wins.get("full") or {}
        clear_ps = _f(full.get("clearPxPerSec"))
        frames_ps = _f(full.get("framesPerSec"))
        clear_per_frame = clear_ps / frames_ps if clear_ps is not None and frames_ps else None
        out.setdefault("float.clear_px_per_frame", []).append(
            (rn, clear_per_frame, f"{clear_ps}/{frames_ps}" if clear_per_frame is not None else "字段缺失"))

        ir_def = default.get("invalid_rasterization") or {}
        outside = _f(ir_def.get("rasterOutsideRatio"))
        out.setdefault("float.raster_outside_ratio", []).append(
            (rn, outside, "" if outside is not None else "字段缺失"))

        thr = wins.get("throttle") or {}
        throttle_fps = _f(thr.get("framesPerSec"))
        out.setdefault("float.throttle_frames_per_sec", []).append(
            (rn, throttle_fps, "" if throttle_fps is not None else "字段缺失"))

        stp = wins.get("stopped") or {}
        stopped_fps = _f(stp.get("framesPerSec"))
        out.setdefault("float.stopped_frames_per_sec", []).append(
            (rn, stopped_fps, "" if stopped_fps is not None else "字段缺失"))

        gsum = minimal.get("geometryMatrixSummary") or {}
        out.setdefault("float.geometry_matrix_ok", []).append(
            (rn, bool(gsum.get("ok")) if gsum else None,
             f"groups={gsum.get('groupsExecuted')}/{gsum.get('groups')}" if gsum else "字段缺失"))

    return out


def _collect_power(data):
    out = {}
    segs = {s.get("name"): s for s in (data.get("segments") or [])}

    def seg(name):
        return segs.get(name) or {}

    minimal = seg("idle_minimal")
    px = minimal.get("pxPerSecond") or []
    px_tail = [v for v in px[1:]] if len(px) > 1 else []
    repaints = sum(1 for v in px_tail if _f(v) and _f(v) > 0)
    out.setdefault("power.idle_minimal_repaints_60s", []).append(
        (1, float(repaints) if px else None, f"px 非零秒={repaints}"))
    out.setdefault("power.idle_minimal_px_60s", []).append(
        (1, float(sum(_f(v) or 0 for v in px_tail)) if px else None, f"剔除首秒，共 {len(px_tail)}s"))

    probes = minimal.get("stageProbes") or []
    attribution_ok = None
    if px:
        if repaints == 0:
            attribution_ok = True
        else:
            attribution_ok = any("stage-repaint src=emotion" in str(s) for s in probes)
    out.setdefault("power.repaint_attribution_present", []).append(
        (1, attribution_ok, "无重绘，无需打点" if attribution_ok and repaints == 0 else "src=emotion 打点"))

    raf = minimal.get("rafPerSecond") or []
    out.setdefault("power.idle_minimal_raf_60s", []).append(
        (1, float(sum(_f(v) or 0 for v in raf[1:])) if raf else None, "剔除首秒"))
    cpu = _f(minimal.get("cpuPct"))
    out.setdefault("power.idle_minimal_cpu_pct", []).append(
        (1, cpu, "" if cpu is not None else "字段缺失"))

    default = seg("idle_default")
    default_raf = _f(default.get("rafAvgPerSec"))
    out.setdefault("power.idle_default_raf_per_sec", []).append(
        (1, default_raf, "" if default_raf is not None else "字段缺失"))

    bg = seg("background")
    bg_frames = _f(bg.get("framesWhileHidden"))
    out.setdefault("power.background_frames_hidden", []).append(
        (1, bg_frames, "" if bg_frames is not None else "字段缺失"))
    bg_cpu = _f(bg.get("cpuPct"))
    out.setdefault("power.background_cpu_pct", []).append(
        (1, bg_cpu, "" if bg_cpu is not None else "字段缺失"))
    return out


def _collect_firstsend(data):
    out = {}
    load = data.get("load") or {}
    dist = data.get("distributions") or load.get("distributions") or {}
    metric = (dist.get("metrics") or {}).get("firstSendMs") or {}

    for label, prefix in (("1x", "1x"), ("4x", "4x")):
        item = metric.get(label) or {}
        n = _f(item.get("n"))
        median = _f(item.get("median"))
        worst = _f(item.get("max"))
        out.setdefault("firstsend.sample_count", []).append(
            (label, n, f"{label} n={item.get('n')}"))
        out.setdefault(f"firstsend.{prefix}_median_ms", []).append(
            (label, median, f"{label} median={item.get('median')}"))
        out.setdefault(f"firstsend.{prefix}_worst_ms", []).append(
            (label, worst, f"{label} worst={item.get('max')}"))
    return out


def _collect_battery(data):
    """30 分钟电池三段实测：绝对量 + 与同设备纯待机基线的差值。

    主判据是 CPU 时间 / 唤醒次数 / 网络字节；mAh 是 AVD 软件电源模型估算，
    只作同一模型下的回归信号（报告里注明 mahReliable=false）。
    """
    out = {}
    segs = {s.get("name"): s for s in (data.get("segments") or [])}

    def seg(name):
        return segs.get(name) or {}

    def val(name, key):
        return _f(seg(name).get(key))

    standby = seg("standby")
    fg = seg("foreground")
    bg = seg("background")

    def device_mah(name):
        item = seg(name)
        return _f(item.get("deviceMah30")) if item.get("deviceMah30") is not None \
            else _f(item.get("mah30"))

    def delta(name, key, base_key=None):
        a = _f(seg(name).get(key))
        b = _f(standby.get(base_key or key))
        if a is None or b is None:
            return None
        return a - b

    # 纯待机对照（整机模型）
    out.setdefault("battery.standby_mah_30m", []).append(
        (1, _f(standby.get("mah30")), f"standby {standby.get('mahBasis')}"))

    # 前台绝对量
    out.setdefault("battery.foreground_uid_mah_30m", []).append(
        (1, _f(fg.get("mah30")), f"uid {fg.get('mahBasis')}"))
    out.setdefault("battery.foreground_device_mah_over_standby", []).append(
        (1, (round(device_mah("foreground") - _f(standby.get("mah30")), 6)
             if device_mah("foreground") is not None and _f(standby.get("mah30")) is not None
             else None), "device 模型 - standby"))
    out.setdefault("battery.foreground_cpu_ms", []).append(
        (1, val("foreground", "cpuMs"), "App 进程 /proc ticks"))
    out.setdefault("battery.foreground_wakeups", []).append(
        (1, val("foreground", "wakeups"), "alarm + job 次数"))
    out.setdefault("battery.foreground_net_bytes", []).append(
        (1, val("foreground", "netBytes"), "cell+wifi 收发字节"))

    # 后台绝对量 + 相对纯待机的净成本
    out.setdefault("battery.background_uid_mah_30m", []).append(
        (1, _f(bg.get("mah30")), f"uid {bg.get('mahBasis')}"))
    out.setdefault("battery.background_device_mah_over_standby", []).append(
        (1, (round(device_mah("background") - _f(standby.get("mah30")), 6)
             if device_mah("background") is not None and _f(standby.get("mah30")) is not None
             else None), "device 模型 - standby"))
    out.setdefault("battery.background_cpu_ms", []).append(
        (1, val("background", "cpuMs"), "App 进程 /proc ticks"))
    out.setdefault("battery.background_wakeups", []).append(
        (1, val("background", "wakeups"), "alarm + job 次数"))
    out.setdefault("battery.background_wakeups_over_standby", []).append(
        (1, delta("background", "wakeups"), "后台 - 待机"))
    out.setdefault("battery.background_net_bytes_over_standby", []).append(
        (1, delta("background", "netBytes"), "后台 - 待机"))
    frames = ((bg.get("frames") or {}).get("framesWhileHidden"))
    out.setdefault("battery.background_frames_30m", []).append(
        (1, _f(frames), f"source={((bg.get('frames') or {}).get('source'))}"))
    return out


EXTRACTORS = {
    "float": _collect_float,
    "power": _collect_power,
    "firstsend": _collect_firstsend,
    "battery": _collect_battery,
}


def _judge(rule, value):
    kind = (rule or {}).get("type")
    target = _f((rule or {}).get("value"))
    if value is None:
        return False
    if kind == "max":
        return value <= target
    if kind == "min":
        return value >= target
    if kind == "range":
        lo, hi = (rule.get("value") or [None, None])[:2]
        return _f(lo) <= value <= _f(hi)
    if kind == "near":
        tol = _f(rule.get("tolerance")) or 0.0
        return target == 0 or abs(value - target) / abs(target) <= tol
    if kind == "bool":
        return bool(value) is True
    raise ValueError(f"未知 rule.type={kind}")


def _fmt(unit, value):
    if value is None:
        return "缺失"
    if unit == "px" or unit == "count" or unit == "bytes":
        return f"{value:,.0f}"
    if unit == "mAh":
        return f"{value:.6f}"
    if unit == "ratio":
        return f"{value:.4f}"
    if unit == "pct":
        return f"{value:.2f}%"
    if unit == "ms":
        return f"{value:.1f}ms"
    if unit == "per_sec":
        return f"{value:.2f}/s"
    return str(value)


def _threshold_text(metric):
    rule = metric.get("rule") or {}
    kind = rule.get("type")
    if kind == "range":
        return f"{rule['value'][0]}–{rule['value'][1]}"
    if kind == "bool":
        return "必须为 true"
    if kind == "near":
        return f"{rule['value']} ±{rule.get('tolerance') * 100:.0f}%"
    op = "<=" if kind == "max" else ">="
    return f"{op} {rule.get('value')}"


def verify_monotonic(old, new):
    """基线只准收紧：返回错误列表（空 = 合规）。

    - max 只能变小，min 只能变大，range 只能变窄，near 只能收紧容差；
    - bool 规则必须保持 true；
    - 指标不能删除（删掉等于给该项松绑）；
    - 不能新增豁免；已有豁免的 guard 只能变严。
    """
    errors = []
    old_metrics = {m.get("id"): m for m in (old.get("metrics") or [])}
    new_metrics = {m.get("id"): m for m in (new.get("metrics") or [])}

    for mid, om in old_metrics.items():
        nm = new_metrics.get(mid)
        if nm is None:
            errors.append(f"指标被删除（等价放宽）：{mid}")
            continue
        if (om.get("section") or "") != (nm.get("section") or ""):
            errors.append(f"指标换段（不可比）：{mid} {om.get('section')} -> {nm.get('section')}")
            continue
        if (om.get("unit") or "") != (nm.get("unit") or ""):
            errors.append(f"指标单位变化（可能掩盖数值）：{mid} {om.get('unit')} -> {nm.get('unit')}")
            continue
        orule = om.get("rule") or {}
        nrule = nm.get("rule") or {}
        if orule.get("type") != nrule.get("type"):
            errors.append(f"规则类型变化：{mid} {orule.get('type')} -> {nrule.get('type')}")
            continue
        kind = orule.get("type")
        ov = _f(orule.get("value")) if kind in ("max", "min") else None
        nv = _f(nrule.get("value")) if kind in ("max", "min") else None
        if kind == "max":
            if nv is None or ov is None or nv > ov:
                errors.append(f"max 阈值被放宽：{mid} {orule.get('value')} -> {nrule.get('value')}")
        elif kind == "min":
            if nv is None or ov is None or nv < ov:
                errors.append(f"min 阈值被放宽：{mid} {orule.get('value')} -> {nrule.get('value')}")
        elif kind == "range":
            old_lo, old_hi = (orule.get("value") or [None, None])[:2]
            new_lo, new_hi = (nrule.get("value") or [None, None])[:2]
            if _f(new_lo) is None or _f(new_hi) is None \
                    or _f(old_lo) is None or _f(old_hi) is None \
                    or _f(new_lo) < _f(old_lo) or _f(new_hi) > _f(old_hi):
                errors.append(f"range 阈值被放宽：{mid} {orule.get('value')} -> {nrule.get('value')}")
        elif kind == "near":
            if _f(nrule.get("tolerance")) is None or _f(orule.get("tolerance")) is None \
                    or _f(nrule.get("tolerance")) > _f(orule.get("tolerance")):
                errors.append(f"near 容差被放宽：{mid} {orule.get('tolerance')} -> {nrule.get('tolerance')}")
        elif kind == "bool":
            if not bool(nrule.get("value")):
                errors.append(f"bool 规则被关闭：{mid}")
        else:
            errors.append(f"未知规则类型：{mid} {kind}")

    old_ex = {e.get("id"): e for e in (old.get("exemptions") or [])}
    new_ex = {e.get("id"): e for e in (new.get("exemptions") or [])}
    for eid in old_ex:
        if eid not in new_ex:
            errors.append(f"豁免被删除（数据口径变化需复核）：{eid}")
    for eid, ex in new_ex.items():
        if eid not in old_ex:
            errors.append(f"新增豁免（等于放宽）：{eid}")
            continue
        og = old_ex[eid].get("guard") or {}
        ng = ex.get("guard") or {}
        for key, label in (("minRafPerSec", "minRafPerSec"),
                           ("minFramesPerSec", "minFramesPerSec")):
            if key in og:
                if key not in ng or _f(ng.get(key)) is None or _f(ng.get(key)) < _f(og.get(key)):
                    errors.append(f"豁免 guard 被放宽：{eid}.{label} {og.get(key)} -> {ng.get(key)}")
    return errors


def evaluate(baseline, sources):
    """sources: {'float': data|None, 'power': data|None, 'firstsend': data|None, '__required__': set}"""
    required = sources.get("__required__") or set()
    collected = {}
    present = {}

    for section in SECTIONS:
        data = sources.get(section)
        if data is None:
            present[section] = False
            continue
        present[section] = True
        collected[section] = EXTRACTORS[section](data, baseline) if section == "float" else EXTRACTORS[section](data)

    results = []

    for metric in baseline.get("metrics") or []:
        section = metric.get("section")
        mid = metric.get("id")
        unit = metric.get("unit")
        if not present.get(section):
            klass = FAIL if section in required else SKIP
            detail = "本次 run 未采集该段" + ("（--require-sections 要求，判红）" if klass == FAIL else "")
            results.append({
                "metric": mid, "section": section, "class": klass,
                "actual": None, "threshold": _threshold_text(metric),
                "unit": unit, "detail": detail, "rounds": [],
            })
            continue

        rows = collected.get(section, {}).get(mid)
        if not rows:
            results.append({
                "metric": mid, "section": section, "class": FAIL,
                "actual": None, "threshold": _threshold_text(metric),
                "unit": unit, "detail": "JSON 中找不到该指标字段", "rounds": [],
            })
            continue

        bad = [(rn, val, why) for rn, val, why in rows if not _judge(metric.get("rule"), val)]
        worst = max(rows, key=lambda row: (row[1] is not None, row[1] if row[1] is not None else 0))
        results.append({
            "metric": mid,
            "section": section,
            "class": FAIL if bad else PASS,
            "actual": [_fmt(unit, val) for _, val, _ in rows],
            "threshold": _threshold_text(metric),
            "unit": unit,
            "detail": "; ".join(f"r{rn} {why}" for rn, _, why in bad) if bad else "; ".join(
                f"r{rn} {why}" for rn, _, why in rows),
            "rounds": [rn for rn, _, _ in rows],
            "worst": _fmt(unit, worst[1]),
        })

    # 豁免：只认基线清单，且 guard 不满足仍判 FAIL
    for ex in baseline.get("exemptions") or []:
        section = ex.get("section")
        if not present.get(section):
            klass = FAIL if section in required else SKIP
            results.append({
                "metric": f"{ex.get('id')} (exempt)",
                "section": section, "class": klass,
                "actual": None, "threshold": ex.get("criterion"),
                "unit": "", "detail": "本次 run 未采集该段", "rounds": [],
            })
            continue

        # 专项计算：满帧档 rAF / 帧率
        rafs, frames = [], []
        for idx, rec in enumerate(sources["float"].get("rounds") or []):
            rn = _round_no(rec, idx)
            full = ((_presets(rec).get("default") or {}).get("windows") or {}).get("full") or {}
            rafs.append((rn, _f(full.get("rafPerSec"))))
            frames.append((rn, _f(full.get("framesPerSec"))))
        guard = ex.get("guard") or {}
        min_raf = _f(guard.get("minRafPerSec")) or 0.0
        min_frames = _f(guard.get("minFramesPerSec")) or 0.0
        guard_ok = all(
            raf is not None and raf >= min_raf and fr is not None and fr >= min_frames
            for (_, raf), (_, fr) in zip(rafs, frames)
        )
        observed = "、".join(
            f"r{rn} {_fmt('per_sec', raf)}/{_fmt('per_sec', fr)}" for (rn, raf), (_, fr) in zip(rafs, frames))
        results.append({
            "metric": f"{ex.get('id')} (exempt)",
            "section": section,
            "class": EXEMPT if guard_ok else FAIL,
            "actual": observed,
            "threshold": ex.get("criterion"),
            "unit": "",
            "detail": (f"已知环境上限：{ex.get('reason')} [{ex.get('evidence')}]"
                       if guard_ok else
                       f"低于豁免 guard（rAF>={min_raf}, 帧>={min_frames}）：比 R6 环境基线更差，判 FAIL"),
            "rounds": [rn for rn, _ in rafs],
        })

    return results


def _counts(results):
    counts = {PASS: 0, FAIL: 0, EXEMPT: 0, SKIP: 0}
    for row in results:
        counts[row["class"]] += 1
    return counts


def _print_results(results, present, required):
    for section in SECTIONS:
        rows = [r for r in results if r["section"] == section]
        if not rows:
            continue
        state = "采集" if present.get(section) else ("未采集（必需）" if section in required else "未采集（跳过）")
        print(f"\nSECTION {section} [{state}]")
        for row in rows:
            if isinstance(row["actual"], list):
                actual = ", ".join(str(x) for x in row["actual"])
            else:
                actual = str(row["actual"])
            print(f"  [{row['class']:<6}] {row['metric']:<42} {actual:<26} 阈值 {row['threshold']}")
            if row["detail"]:
                print(f"           └ {row['detail']}")
    counts = _counts(results)
    print(f"\nSUMMARY pass={counts[PASS]} fail={counts[FAIL]} exempt={counts[EXEMPT]} skip={counts[SKIP]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="R6 性能基线门")
    parser.add_argument("--baseline", default="docs/perf_baseline_r6.json")
    parser.add_argument("--float", default=None, help="float_r4.json")
    parser.add_argument("--power", default=None, help="power_r4.json")
    parser.add_argument("--battery", default=None, help="battery_r4.json")
    parser.add_argument("--perf", default=None, help="perf r6.json")
    parser.add_argument("--require-sections", default="",
                        help="逗号分隔；这些段缺数据判红。例：float,power,battery")
    parser.add_argument("--verify-monotonic-against", default=None,
                        help="上一版基线 JSON；校验本次基线只收紧、不放宽")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)

    verified = False
    if args.verify_monotonic_against:
        with open(args.verify_monotonic_against, encoding="utf-8") as f:
            prev = json.load(f)
        errors = verify_monotonic(prev, baseline)
        if errors:
            print("BASELINE: FAIL（阈值/豁免被放宽）")
            for err in errors:
                print(f"  - {err}")
            return 1
        print("BASELINE: PASS（只收紧或新增，没有放宽）")
        verified = True

    required = {s.strip() for s in args.require_sections.split(",") if s.strip()}
    unknown = required - set(SECTIONS)
    if unknown:
        print(f"未知 section: {sorted(unknown)}", file=sys.stderr)
        return 2

    sources = {"__required__": required}
    for section, path in (("float", args.float), ("power", args.power),
                          ("battery", args.battery), ("firstsend", args.perf)):
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    sources[section] = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"读取 {section} 失败: {path}: {exc}", file=sys.stderr)
                return 2
        else:
            sources[section] = None

    if not any(sources.get(s) for s in SECTIONS):
        if verified:
            return 0
        print("没有任何实测数据输入", file=sys.stderr)
        return 2

    results = evaluate(baseline, sources)
    present = {s: sources.get(s) is not None for s in SECTIONS}
    _print_results(results, present, required)

    counts = _counts(results)
    ok = counts[FAIL] == 0
    print(f"GATE: {'PASS' if ok else 'FAIL'} (fail={counts[FAIL]})")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({
                "ok": ok,
                "counts": counts,
                "present": present,
                "required": sorted(required),
                "results": results,
            }, f, ensure_ascii=False, indent=2)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
