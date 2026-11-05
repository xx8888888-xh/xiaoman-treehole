#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_eval_rotation.py · 多免费后端轮换（W20）

目标（本轮用户指定）：
  - 429/配额耗尽自动切下一家，并把“哪家用了多少次、哪次开始 429”记进报告；
  - 全部后端判死时中止评测，不把后端失败折算成质量分；
  - key 只从环境变量 / 本地被忽略的配置读，示例文件永不含 key。

这里用假后端验证机制，不会消耗真实配额。
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import eval_human_feel as ev  # noqa: E402


class FakeBackend:
    name = "fake"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0
        self.errors = 0
        self.throttled = 0
        self.case = None

    def reset_case(self, case):
        self.case = case

    def chat(self, history, user, case):
        self.calls += 1
        item = self.behaviour[min(self.calls - 1, len(self.behaviour) - 1)]
        if isinstance(item, Exception):
            raise item
        if item.get("_backend_error"):
            self.errors += 1
        if "429" in item.get("_backend_error", ""):
            self.throttled += 1
        return item

    def snapshot(self):
        return {}

    def cleanup(self):
        pass


def make_factory(providers_behaviour):
    def factory(provider):
        return FakeBackend(providers_behaviour[provider["name"]])
    return factory


PROVIDERS = [
    {"name": "a", "base": "https://a.example", "model": "m1", "key": "k",
     "temperature": None, "max_tokens": None, "retries": None},
    {"name": "b", "base": "https://b.example", "model": "m2", "key": "",
     "temperature": None, "max_tokens": None, "retries": None},
]


class RotationTest(unittest.TestCase):
    def test_switch_on_quota_and_record_stats(self):
        rb = ev.RotatingBackend(
            PROVIDERS, factory=make_factory({
                "a": [ev.QuotaExhausted("a", "HTTP 429 free-models-per-day")],
                "b": [{"reply": "行，不问了。"}],
            }))
        out = rb.chat([], "不想说", {"id": "x"})
        self.assertEqual(out["reply"], "行，不问了。")
        report = rb.report()
        self.assertEqual(report["rotations"], 1)
        a, b = report["providers"]
        self.assertEqual(a["status"], "配额/鉴权失效")
        self.assertTrue(a["first_429_at"], "第一次 429 的时间没记录")
        self.assertIn("429", a["reason"])
        self.assertEqual(b["status"], "在用")
        self.assertEqual(b["calls"], 1)
        self.assertEqual(rb.current.calls, 1)

    def test_all_exhausted_raises(self):
        rb = ev.RotatingBackend(
            PROVIDERS, factory=make_factory({
                "a": [ev.QuotaExhausted("a", "HTTP 429")],
                "b": [ev.QuotaExhausted("b", "HTTP 402")],
            }))
        with self.assertRaises(ev.AllProvidersExhausted) as cm:
            rb.chat([], "在吗", {"id": "y"})
        self.assertEqual(len(cm.exception.stats), 2)
        self.assertEqual(cm.exception.stats[1]["status"], "配额/鉴权失效")
        self.assertEqual(rb.rotations, 1)

    def test_switch_after_two_empty_responses(self):
        rb = ev.RotatingBackend(
            PROVIDERS, factory=make_factory({
                "a": [{"reply": "", "hook": None, "_backend_error": "超时"}],
                "b": [{"reply": "嗯，我在。"}],
            }))
        rb.consecutive_fail = 0
        first = rb.chat([], "嗯", {"id": "z"})
        self.assertIn("_backend_error", first)
        self.assertEqual(rb.rotations, 0)
        second = rb.chat([], "嗯", {"id": "z"})
        self.assertEqual(second["reply"], "嗯，我在。")
        self.assertEqual(rb.rotations, 1)

    def test_evaluate_aborts_without_scoring(self):
        rb = ev.RotatingBackend(
            PROVIDERS, factory=make_factory({"a": [ev.QuotaExhausted("a", "HTTP 429")],
                                             "b": [ev.QuotaExhausted("b", "HTTP 429")]}))
        data = ev.evaluate(ev.CASES_FILE, rb, limit=1, on_case=lambda res: None)
        self.assertEqual(data["results"], [])
        self.assertIn("aborted", data)
        self.assertEqual(data["aborted"]["after_cases"], 0)
        self.assertEqual(data["metrics"]["turns"], 0)


class ConfigTest(unittest.TestCase):
    def test_env_json_resolves_key_and_skips_missing(self):
        cfg = json.dumps([
            {"name": "with-env", "base": "https://x.example", "model": "m",
             "key_env": "W20_TEST_KEY"},
            {"name": "no-key", "base": "https://y.example", "model": "m2"},
            {"name": "keyless", "base": "https://z.example", "model": "m3",
             "requires_key": False},
        ])
        with mock.patch.dict(os.environ, {"HUMAN_FEEL_BACKENDS": cfg,
                                          "W20_TEST_KEY": "test-key"}):
            providers, skipped = ev.load_providers()
        self.assertEqual([p["name"] for p in providers], ["with-env", "keyless"])
        self.assertEqual(providers[0]["key"], "test-key")
        self.assertEqual(skipped[0]["name"], "no-key")

    def test_example_config_has_no_secret(self):
        text = ev.EXAMPLE_BACKENDS_FILE.read_text(encoding="utf-8")
        self.assertNotIn("sk-", text, "示例配置里不允许出现真实 key")
        data = json.loads(text)
        for entry in data["backends"]:
            self.assertFalse(entry.get("key"), "示例配置只能用 key_env")

    def test_local_config_is_gitignored(self):
        import subprocess
        path = ev.DEFAULT_BACKENDS_FILE
        if not path.exists():
            self.skipTest("本地轮换配置不存在（按需创建）")
        code = subprocess.run(["git", "check-ignore", "-q", str(path)],
                              cwd=str(ROOT)).returncode
        self.assertEqual(code, 0, "backends.local.json 必须被 gitignore")


if __name__ == "__main__":
    unittest.main()
