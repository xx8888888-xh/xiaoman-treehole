#!/usr/bin/env python3
"""perf_gate.py 单元测试：R6 基线绿、各类人为退化必红、豁免 guard、缺段处理。"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_gate  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent.parent / "docs" / "perf_baseline_r6.json"


def _float_round(round_no=1, area=98610, width=285, height=346, visible=0.9802,
                 clear_per_sec=2530864.5, frames_per_sec=25.67, full_raf=22.83,
                 full_frames=25.67, outside=0.0031, throttle=10.5, stopped=0.04, geom_ok=True):
    return {
        "round": round_no,
        "presets": {
            "minimal": {
                "canvas": {"width": width, "height": height, "canvasArea": area},
                "invalid_rasterization": {"visibleShareOfCanvas": visible},
                "geometryMatrix": [{"backing": {"fullW": 469, "fullH": 1080}}],
                "geometryMatrixSummary": {"enabled": True, "ok": geom_ok,
                                          "groups": 9, "groupsExecuted": 9},
            },
            "default": {
                "windows": {
                    "full": {"rafPerSec": full_raf, "framesPerSec": full_frames,
                             "clearPxPerSec": clear_per_sec},
                    "throttle": {"framesPerSec": throttle},
                    "stopped": {"framesPerSec": stopped},
                },
                "invalid_rasterization": {"rasterOutsideRatio": outside},
            },
        },
    }


def _float_data(rounds=None):
    return {"rounds": rounds or [_float_round(1), _float_round(2)]}


def _power_data(px_tail=None, probes=None, minimal_cpu=0.67, default_raf=5.19,
                bg_frames=0, bg_cpu=0.01):
    px = [0] + list(px_tail if px_tail is not None else [0] * 44 + [151655] + [0] * 14)
    return {"segments": [
        {"name": "idle_minimal", "pxPerSecond": px, "rafPerSecond": [0] * 60,
         "cpuPct": minimal_cpu,
         "stageProbes": probes if probes is not None else ["[probe] stage-repaint src=emotion"]},
        {"name": "idle_default", "rafAvgPerSec": default_raf},
        {"name": "background", "framesWhileHidden": bg_frames, "cpuPct": bg_cpu},
    ]}


def _perf_data(n1=5, med1=152, max1=184, n4=5, med4=403, max4=638):
    return {"distributions": {"metrics": {"firstSendMs": {
        "1x": {"n": n1, "median": med1, "max": max1},
        "4x": {"n": n4, "median": med4, "max": max4},
    }}}}


def _battery_seg(name, mah30, device_mah30, cpu_ms, wakeups, net_bytes,
                 frames=None, wakeup_alarms=0, jobs=0):
    seg = {
        "name": name,
        "mah30": mah30,
        "deviceMah30": device_mah30,
        "cpuMs": cpu_ms,
        "wakeups": wakeups,
        "wakeupAlarms": wakeup_alarms,
        "jobs": jobs,
        "netBytes": net_bytes,
        "mahMeasured": mah30,
        "mahBasis": "uid-power-model" if name != "standby" else "device-power-model",
    }
    if frames is not None:
        seg["frames"] = {"framesWhileHidden": frames, "source": "pet-visibilitychange"}
    return seg


def _battery_data(bg_frames=0, bg_cpu=200, bg_net=2000, bg_mah=0.04,
                  fg_cpu=400000, fg_wakeups=0, bg_wakeups=0, standby_mah=0.03,
                  standby_net=500):
    return {"segments": [
        _battery_seg("standby", standby_mah, standby_mah, 0, 0, standby_net),
        _battery_seg("foreground", 0.01, 1.5, fg_cpu, fg_wakeups, 100),
        _battery_seg("background", 0.0005, bg_mah, bg_cpu, bg_wakeups, bg_net,
                     frames=bg_frames),
    ]}


def _sources(float_data=None, power_data=None, perf_data=None, battery_data=None,
             required=None):
    src = {"float": float_data, "power": power_data, "firstsend": perf_data,
           "battery": battery_data, "__required__": set(required or [])}
    return src


def _find(results, metric_id):
    for row in results:
        if row["metric"] == metric_id:
            return row
    raise AssertionError(f"metric not found: {metric_id}; have {[r['metric'] for r in results]}")


class PerfGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    def test_r6_fixture_all_green_with_expected_exemption(self):
        results = perf_gate.evaluate(self.baseline,
                                     _sources(_float_data(), _power_data(), _perf_data()))
        fails = [r for r in results if r["class"] == perf_gate.FAIL]
        self.assertEqual(fails, [], f"R6 基线不应有红项: {fails}")
        self.assertEqual(_find(results, "float.full_tier_raf (exempt)")["class"], perf_gate.EXEMPT)
        self.assertEqual(_find(results, "float.backing_area_px")["class"], perf_gate.PASS)
        self.assertEqual(_find(results, "firstsend.4x_median_ms")["class"], perf_gate.PASS)

    def test_double_backing_turns_red(self):
        """人为退化：后备放大 2 倍 -> 门必须红。"""
        rounds = [_float_round(area=197220, width=570, height=692)]
        results = perf_gate.evaluate(self.baseline, _sources(_float_data(rounds)))
        self.assertEqual(_find(results, "float.backing_area_px")["class"], perf_gate.FAIL)
        self.assertEqual(_find(results, "float.backing_share_of_window")["class"], perf_gate.FAIL)

    def test_clear_raster_double_turns_red(self):
        rounds = [_float_round(clear_per_sec=2530864.5 * 2)]
        results = perf_gate.evaluate(self.baseline, _sources(_float_data(rounds)))
        self.assertEqual(_find(results, "float.clear_px_per_frame")["class"], perf_gate.FAIL)

    def test_raster_outside_over_one_percent_turns_red(self):
        rounds = [_float_round(outside=0.02)]
        results = perf_gate.evaluate(self.baseline, _sources(_float_data(rounds)))
        self.assertEqual(_find(results, "float.raster_outside_ratio")["class"], perf_gate.FAIL)

    def test_full_tier_is_exempt_not_red(self):
        rounds = [_float_round(full_raf=24.5, full_frames=25.0)]
        results = perf_gate.evaluate(self.baseline, _sources(_float_data(rounds)))
        self.assertEqual(_find(results, "float.full_tier_raf (exempt)")["class"], perf_gate.EXEMPT)

    def test_full_tier_below_guard_turns_red(self):
        rounds = [_float_round(full_raf=8.0, full_frames=8.0)]
        results = perf_gate.evaluate(self.baseline, _sources(_float_data(rounds)))
        self.assertEqual(_find(results, "float.full_tier_raf (exempt)")["class"], perf_gate.FAIL)

    def test_repaint_regression_turns_red(self):
        power = _power_data(px_tail=[0] * 10 + [160000, 0, 160000] + [0] * 47)
        results = perf_gate.evaluate(self.baseline, _sources(power_data=power))
        self.assertEqual(_find(results, "power.idle_minimal_repaints_60s")["class"], perf_gate.FAIL)
        self.assertEqual(_find(results, "power.idle_minimal_px_60s")["class"], perf_gate.FAIL)

    def test_repaint_without_emotion_probe_turns_red(self):
        power = _power_data(px_tail=[0] * 45 + [151655] + [0] * 13, probes=[])
        results = perf_gate.evaluate(self.baseline, _sources(power_data=power))
        self.assertEqual(_find(results, "power.repaint_attribution_present")["class"], perf_gate.FAIL)

    def test_firstsend_median_regression_turns_red(self):
        perf = _perf_data(med4=500, max4=690)
        results = perf_gate.evaluate(self.baseline, _sources(perf_data=perf))
        self.assertEqual(_find(results, "firstsend.4x_median_ms")["class"], perf_gate.FAIL)
        self.assertEqual(_find(results, "firstsend.4x_worst_ms")["class"], perf_gate.PASS)

    def test_firstsend_single_point_is_not_a_distribution(self):
        perf = _perf_data(n4=1)
        results = perf_gate.evaluate(self.baseline, _sources(perf_data=perf))
        self.assertEqual(_find(results, "firstsend.sample_count")["class"], perf_gate.FAIL)

    def test_missing_required_section_turns_red(self):
        results = perf_gate.evaluate(self.baseline, _sources(_float_data(), required={"power"}))
        rows = [r for r in results if r["section"] == "power"]
        self.assertTrue(all(r["class"] == perf_gate.FAIL for r in rows))

    def test_missing_optional_section_is_skip(self):
        results = perf_gate.evaluate(self.baseline, _sources(_float_data()))
        rows = [r for r in results if r["section"] == "power"]
        self.assertTrue(all(r["class"] == perf_gate.SKIP for r in rows))
        self.assertFalse([r for r in results if r["class"] == perf_gate.FAIL])

    def test_battery_three_segments_all_green(self):
        results = perf_gate.evaluate(self.baseline, _sources(battery_data=_battery_data()))
        fails = [r for r in results if r["section"] == "battery" and r["class"] == perf_gate.FAIL]
        self.assertEqual(fails, [], f"电池基线不应有红项: {fails}")
        self.assertEqual(_find(results, "battery.background_frames_30m")["class"], perf_gate.PASS)
        self.assertEqual(
            _find(results, "battery.background_device_mah_over_standby")["class"], perf_gate.PASS)

    def test_battery_background_frames_30m_turns_red(self):
        """把 5 分钟 0 帧结论扩到 30 分钟：隐藏期 20 帧必须判红。"""
        results = perf_gate.evaluate(self.baseline,
                                     _sources(battery_data=_battery_data(bg_frames=20)))
        self.assertEqual(_find(results, "battery.background_frames_30m")["class"], perf_gate.FAIL)

    def test_battery_background_cpu_turns_red(self):
        results = perf_gate.evaluate(self.baseline,
                                     _sources(battery_data=_battery_data(bg_cpu=8000)))
        self.assertEqual(_find(results, "battery.background_cpu_ms")["class"], perf_gate.FAIL)

    def test_battery_background_net_over_standby_turns_red(self):
        results = perf_gate.evaluate(self.baseline,
                                     _sources(battery_data=_battery_data(bg_net=200000)))
        row = _find(results, "battery.background_net_bytes_over_standby")
        self.assertEqual(row["class"], perf_gate.FAIL)

    def test_battery_background_mah_over_standby_turns_red(self):
        results = perf_gate.evaluate(self.baseline,
                                     _sources(battery_data=_battery_data(bg_mah=0.5)))
        row = _find(results, "battery.background_device_mah_over_standby")
        self.assertEqual(row["class"], perf_gate.FAIL)

    def test_battery_foreground_wakeups_turns_red(self):
        results = perf_gate.evaluate(self.baseline,
                                     _sources(battery_data=_battery_data(fg_wakeups=40)))
        self.assertEqual(_find(results, "battery.foreground_wakeups")["class"], perf_gate.FAIL)

    def test_battery_missing_required_turns_red(self):
        results = perf_gate.evaluate(self.baseline,
                                     _sources(_float_data(), required={"battery"}))
        rows = [r for r in results if r["section"] == "battery"]
        self.assertTrue(rows)
        self.assertTrue(all(r["class"] == perf_gate.FAIL for r in rows))

    def test_baseline_may_only_tighten(self):
        old = {"metrics": [{
            "id": "battery.background_device_mah_over_standby", "section": "battery",
            "unit": "mAh", "rule": {"type": "max", "value": 0.2},
        }]}
        tighter = copy.deepcopy(old)
        tighter["metrics"][0]["rule"]["value"] = 0.1
        self.assertEqual(perf_gate.verify_monotonic(old, tighter), [])

        looser = copy.deepcopy(old)
        looser["metrics"][0]["rule"]["value"] = 0.3
        errors = perf_gate.verify_monotonic(old, looser)
        self.assertTrue(errors)
        self.assertIn("放宽", errors[0])

    def test_baseline_removed_metric_or_new_exemption_is_loosening(self):
        old = {"metrics": [{
            "id": "battery.background_cpu_ms", "section": "battery",
            "unit": "ms", "rule": {"type": "max", "value": 5000.0},
        }]}
        removed = {"metrics": []}
        self.assertTrue(perf_gate.verify_monotonic(old, removed))

        old_ex = {"metrics": [], "exemptions": [{
            "id": "float.full_tier_raf", "section": "float",
            "guard": {"minRafPerSec": 12.0},
        }]}
        new_ex = copy.deepcopy(old_ex)
        new_ex["exemptions"].append({"id": "battery.free_pass", "section": "battery"})
        errors = perf_gate.verify_monotonic(old_ex, new_ex)
        self.assertTrue(any("新增豁免" in e for e in errors), errors)

        weak_guard = copy.deepcopy(old_ex)
        weak_guard["exemptions"][0]["guard"]["minRafPerSec"] = 8.0
        errors = perf_gate.verify_monotonic(old_ex, weak_guard)
        self.assertTrue(any("guard 被放宽" in e for e in errors), errors)

    def test_cli_monotonic_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            old = {"metrics": [{
                "id": "battery.background_cpu_ms", "section": "battery",
                "unit": "ms", "rule": {"type": "max", "value": 5000.0},
            }]}
            new = {"metrics": [{
                "id": "battery.background_cpu_ms", "section": "battery",
                "unit": "ms", "rule": {"type": "max", "value": 0.3},
            }]}
            (tmp / "old.json").write_text(json.dumps(old), encoding="utf-8")
            (tmp / "new.json").write_text(json.dumps(new), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = perf_gate.main(["--baseline", str(tmp / "new.json"),
                                       "--verify-monotonic-against", str(tmp / "old.json")])
            self.assertEqual(code, 0, buf.getvalue())
            self.assertIn("BASELINE: PASS", buf.getvalue())

    def test_cli_green_and_red_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            baseline = copy.deepcopy(self.baseline)
            (tmp / "baseline.json").write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
            (tmp / "float.json").write_text(json.dumps(_float_data(), ensure_ascii=False), encoding="utf-8")
            (tmp / "power.json").write_text(json.dumps(_power_data(), ensure_ascii=False), encoding="utf-8")
            (tmp / "perf.json").write_text(json.dumps(_perf_data(), ensure_ascii=False), encoding="utf-8")
            out = tmp / "gate.json"

            argv = ["--baseline", str(tmp / "baseline.json"),
                    "--float", str(tmp / "float.json"),
                    "--power", str(tmp / "power.json"),
                    "--perf", str(tmp / "perf.json"),
                    "--json-out", str(out)]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = perf_gate.main(argv)
            self.assertEqual(code, 0, buf.getvalue())
            self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["ok"])

            bad = _float_data([_float_round(area=197220, width=570, height=692)])
            (tmp / "float_bad.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            argv_bad = ["--baseline", str(tmp / "baseline.json"),
                        "--float", str(tmp / "float_bad.json"),
                        "--json-out", str(out)]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = perf_gate.main(argv_bad)
            self.assertEqual(code, 1, buf.getvalue())
            self.assertIn("GATE: FAIL", buf.getvalue())
            self.assertFalse(json.loads(out.read_text(encoding="utf-8"))["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
