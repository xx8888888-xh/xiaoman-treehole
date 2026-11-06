#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emulator_ime_check 离线单测（不连真机、不装 requests/websockets）。

覆盖三处此前会静默误判的路径：
  1) DOM 就绪判据必须是 readyState=complete 且能取到已知元素；
  2) eval 连续失败（旋转后 target 失效）要重新 attach，而不是把异常吞掉；
  3) 失败现场要带 probe 原文 / readyState / URL / WebView 版本，超时默认 60s 且可配。

运行：python3 scripts/test_emulator_ime_check.py
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# android_cdp 顶层 import requests/websockets；单测只需要它的符号，先注入桩。
_stub = types.ModuleType("android_cdp")
_stub.PKG = "com.xiaoman.treehole"
_stub.Cdp = object
_stub.sh = lambda cmd, timeout=90: (0, "")
sys.modules.setdefault("android_cdp", _stub)

import emulator_ime_check as ime  # noqa: E402


class FakeCdp:
    """按脚本顺序返回预设结果；Exception 实例表示该次 eval 抛错。"""

    def __init__(self, results, attach_errors=()):
        self.results = list(results)
        self.url_filter = "index.html"
        self.port = 9226
        self.attach_calls = 0
        self.attach_errors = list(attach_errors)

    def eval(self, js, timeout=20):
        if not self.results:
            raise AssertionError("FakeCdp 结果用尽，调用次数超出预期")
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def attach(self, *args, **kwargs):
        self.attach_calls += 1
        if self.attach_errors:
            raise self.attach_errors.pop(0)
        return None


class TimeoutConfigTest(unittest.TestCase):
    def test_default_is_60(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ime.DOM_TIMEOUT_ENV, None)
            self.assertEqual(ime.dom_timeout([]), 60.0)

    def test_env_override(self):
        with mock.patch.dict(os.environ, {ime.DOM_TIMEOUT_ENV: "15"}):
            self.assertEqual(ime.dom_timeout([]), 15.0)

    def test_cli_beats_env(self):
        with mock.patch.dict(os.environ, {ime.DOM_TIMEOUT_ENV: "15"}):
            self.assertEqual(ime.dom_timeout(["--dom-timeout=7"]), 7.0)
            self.assertEqual(ime.dom_timeout(["--dom-timeout", "9"]), 9.0)

    def test_bad_values_fall_back(self):
        with mock.patch.dict(os.environ, {ime.DOM_TIMEOUT_ENV: "abc"}):
            self.assertEqual(ime.dom_timeout([]), 60.0)
        with mock.patch.dict(os.environ, {ime.DOM_TIMEOUT_ENV: "0"}):
            self.assertEqual(ime.dom_timeout([]), 60.0)


class WaitDomReadyTest(unittest.TestCase):
    def test_waits_until_complete_and_element_present(self):
        cdp = FakeCdp([
            {"ready": "loading", "hasInput": False, "href": "about:blank"},
            {"ready": "interactive", "hasInput": True, "href": "file:///x/index.html"},
            {"ready": "complete", "hasInput": True, "href": "file:///x/index.html"},
        ])
        diag, err, attempts = ime.wait_dom_ready(cdp, timeout=5, poll=0, log=lambda *_: None)
        self.assertIsNone(err)
        self.assertEqual(diag["href"], "file:///x/index.html")
        self.assertEqual(attempts, 3)

    def test_complete_but_missing_element_is_not_ready(self):
        cdp = FakeCdp([{"ready": "complete", "hasInput": False,
                        "href": "file:///x/index.html"}] * 50)
        diag, err, attempts = ime.wait_dom_ready(cdp, timeout=0.05, poll=0.01, log=lambda *_: None)
        self.assertIsNotNone(diag)
        self.assertFalse(diag["hasInput"])
        self.assertGreaterEqual(attempts, 1)

    def test_timeout_returns_last_diag(self):
        cdp = FakeCdp([{"ready": "loading", "hasInput": False, "href": "about:blank"}] * 50)
        diag, err, attempts = ime.wait_dom_ready(cdp, timeout=0.05, poll=0.01, log=lambda *_: None)
        self.assertEqual(diag["ready"], "loading")
        self.assertGreaterEqual(attempts, 1)

    def test_reattaches_after_three_eval_failures(self):
        cdp = FakeCdp([
            RuntimeError("ws closed"),
            RuntimeError("ws closed"),
            RuntimeError("ws closed"),
            {"ready": "complete", "hasInput": True, "href": "file:///x/index.html"},
        ])
        diag, err, attempts = ime.wait_dom_ready(cdp, timeout=5, poll=0, log=lambda *_: None)
        self.assertEqual(cdp.attach_calls, 1)
        self.assertEqual(diag["href"], "file:///x/index.html")
        self.assertEqual(attempts, 4)

    def test_returns_last_error_when_never_ready(self):
        cdp = FakeCdp([RuntimeError("target gone")] * 50)
        diag, err, attempts = ime.wait_dom_ready(cdp, timeout=0.05, poll=0.01, log=lambda *_: None)
        self.assertTrue(attempts >= 3)
        self.assertIsInstance(err, RuntimeError)


class FailureDiagTest(unittest.TestCase):
    def test_collects_probe_ready_state_url_and_webview_version(self):
        cdp = FakeCdp([
            "ready=loading url=file:///x/index.html body=html input=false",
            "loading",
            "file:///x/index.html",
        ])
        out = (0, "Current WebView package (name, version): "
               "(com.google.android.webview, 130.0.6723.58)\nMinimum WebView version code: 1\n")
        with mock.patch.object(ime, "sh", return_value=out):
            diag = ime.collect_diag(cdp, {"ready": "loading"}, RuntimeError("boom"))
        self.assertIn("ready=loading", diag["probe"])
        self.assertEqual(diag["readyState"], "loading")
        self.assertEqual(diag["url"], "file:///x/index.html")
        self.assertEqual(diag["webviewVersion"], "com.google.android.webview, 130.0.6723.58")
        self.assertIn("boom", diag["lastWaitError"])

    def test_probe_error_is_reported_not_swallowed(self):
        cdp = FakeCdp([RuntimeError("Runtime.evaluate 返回空值")])

        def fake_sh(cmd, timeout=90):
            if "webviewupdate" in cmd:
                return 0, ""
            return 0, ""

        with mock.patch.object(ime, "sh", side_effect=fake_sh):
            diag = ime.collect_diag(cdp)
        self.assertIn("evalError", diag)
        self.assertIn("Runtime.evaluate", diag["evalError"])
        self.assertEqual(diag["webviewVersion"], "unknown")


class AttachRecoveryTest(unittest.TestCase):
    def test_first_attach_success_needs_no_restart(self):
        cdp = FakeCdp([])
        attached, err = ime.attach_with_recovery(cdp, log=lambda *_: None)
        self.assertTrue(attached)
        self.assertIsNone(err)
        self.assertEqual(cdp.attach_calls, 1)

    def test_failure_restarts_app_then_attaches(self):
        cdp = FakeCdp([], attach_errors=[RuntimeError("120s 内没有匹配 index.html 的 WebView target，"
                                                    "最近一次 url 列表=['']")])
        cmds = []
        with mock.patch.object(ime, "sh", side_effect=lambda c, timeout=90: (cmds.append(c), (0, ""))[1]), \
                mock.patch.object(ime, "attach_diag", return_value={"targets": [{"url": ""}]}), \
                mock.patch.object(ime.time, "sleep", return_value=None):
            attached, err = ime.attach_with_recovery(cdp, log=lambda *_: None)
        self.assertTrue(attached)
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(cdp.attach_calls, 2)
        self.assertTrue(any("am force-stop" in c for c in cmds))
        self.assertTrue(any("am start" in c for c in cmds))

    def test_still_failing_after_restart_returns_none(self):
        cdp = FakeCdp([], attach_errors=[RuntimeError("a"), RuntimeError("b")])
        with mock.patch.object(ime, "sh", return_value=(0, "")), \
                mock.patch.object(ime, "attach_diag", return_value={}), \
                mock.patch.object(ime.time, "sleep", return_value=None):
            attached, err = ime.attach_with_recovery(cdp, log=lambda *_: None)
        self.assertFalse(attached)
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(str(err), "b")

    def test_attach_diag_carries_targets_sockets_pid_activity(self):
        def fake_sh(cmd, timeout=90):
            if "/proc/net/unix" in cmd:
                return 0, "@webview_devtools_remote_2373\n@webview_devtools_remote_2410\n"
            if "pidof" in cmd:
                return 0, "2373\n"
            return 0, "mResumedActivity: ActivityRecord{x u0 com.xiaoman.treehole/.MainActivity t9}\n"

        with mock.patch.object(ime, "sh", side_effect=fake_sh), \
                mock.patch.object(ime, "cdp_targets", return_value=[{"url": ""}]):
            diag = ime.attach_diag()
        self.assertEqual(diag["targets"], [{"url": ""}])
        self.assertEqual(diag["sockets"], ["2373", "2410"])
        self.assertEqual(diag["pid"], "2373")
        self.assertIn("MainActivity", diag["resumedActivity"][0])


class ImeFrameTest(unittest.TestCase):
    def test_keyboard_height_is_frame_minus_visible_top(self):
        dump = ("\n  Window #5 Window{b95619f u0 InputMethod}:\n"
                "    mFrame=[0,83][2148,1080]\n"
                "    mGivenContentInsets=[0,409][0,0] mGivenVisibleInsets=[0,409][0,0]\n")
        with mock.patch.object(ime, "sh", return_value=(0, dump)):
            fr = ime.ime_frame()
        self.assertEqual(fr["frame"], (0, 83, 2148, 1080))
        self.assertEqual(fr["visibleTop"], 409)
        self.assertEqual((fr["frame"][3] - fr["frame"][1]) - fr["visibleTop"], 588)

    def test_no_ime_window_returns_none(self):
        with mock.patch.object(ime, "sh", return_value=(0, "\n  Window #1 Window{a u0 MainActivity}:")):
            self.assertIsNone(ime.ime_frame())


if __name__ == "__main__":
    unittest.main(verbosity=2)
