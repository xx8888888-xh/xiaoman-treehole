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


def _sources(float_data=None, power_data=None, perf_data=None, required=None):
    src = {"float": float_data, "power": power_data, "firstsend": perf_data,
           "__required__": set(required or [])}
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
