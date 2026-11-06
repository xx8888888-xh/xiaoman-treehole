#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_eval_rotation.py · 多免费后端轮换（W20）

目标（本轮用户指定）：
  - 429/配额耗尽自动切下一家，并把“哪家用了多少次、哪次开始 429”记进报告；
  - 全部后端判死时中止评测，不把后端失败折算成质量分；
  - key 只从环境变量 / 本地被忽略的配置读，示例文件永不含 key。

这里用假后端验证机制，不会消耗真实配额。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
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
    {"name": "a", "base": "https://a.example", "model": "vendor/m1:free", "key": "k",
     "temperature": None, "max_tokens": None, "retries": None},
    {"name": "b", "base": "https://b.example", "model": "vendor/m2:free", "key": "",
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


class FreeWhitelistTest(unittest.TestCase):
    """只准免费额度：白名单外的模型（尤其付费）必须在发请求前被拒绝。"""

    def test_is_free_model(self):
        for model in ("meta-llama/llama-3.1-8b-instruct:free", "gpt:free",
                      "openai", ""):
            self.assertTrue(ev.is_free_model(model), model)
        for model in ("qwen/qwen-2.5-72b-instruct", "openai/gpt-4o",
                      "openai/gpt-4o:nitro", "x:online", "x:pay"):
            self.assertFalse(ev.is_free_model(model), model)

    def test_load_providers_rejects_paid_model(self):
        cfg = json.dumps([
            {"name": "paid", "base": "https://x.example",
             "model": "qwen/qwen-2.5-72b-instruct", "requires_key": False},
            {"name": "free", "base": "https://y.example",
             "model": "vendor/model:free", "requires_key": False},
            {"name": "anon", "base": "https://z.example", "model": "",
             "requires_key": False},
        ])
        with mock.patch.dict(os.environ, {"HUMAN_FEEL_BACKENDS": cfg}):
            providers, skipped = ev.load_providers()
        self.assertEqual([p["name"] for p in providers], ["free", "anon"])
        self.assertEqual(skipped[0]["name"], "paid")
        self.assertIn("白名单", skipped[0]["reason"])

    def test_rotating_backend_refuses_paid_provider(self):
        with self.assertRaises(ValueError):
            ev.RotatingBackend([dict(PROVIDERS[0],
                                     model="qwen/qwen-2.5-72b-instruct")],
                               factory=lambda p: FakeBackend([{"reply": "嗯。"}]))

    def test_request_body_has_no_paid_options(self):
        backend = ev.ApiBackend("https://x.example", "vendor/m:free", "k",
                                retries=1, max_requests=5, delay=0)
        backend.reset_case({"id": "body"})
        seen = {}

        def fake(self, payload):
            seen.update(payload)
            return {"choices": [{"message": {"content": '{"reply":"嗯。"}'}}]}

        with mock.patch.object(ev.ApiBackend, "_request", fake):
            backend.chat([], "在吗", {"id": "body"})
        self.assertEqual(set(seen), {"model", "temperature", "max_tokens", "messages"})
        self.assertLessEqual(seen["max_tokens"], ev.MAX_TOKENS_CAP)

    def test_billing_402_stops_without_retry_upgrade(self):
        import io
        import urllib.error
        backend = ev.ApiBackend("https://x.example", "vendor/m:free", "k",
                                retries=3, raise_on_quota=True, delay=0, max_requests=5)
        backend.reset_case({"id": "bill"})

        def fail(self, payload):
            err = urllib.error.HTTPError(self._url(), 402, "Payment Required", {},
                                         io.BytesIO(b'insufficient balance'))
            raise err

        with mock.patch.object(ev.ApiBackend, "_request", fail):
            with self.assertRaises(ev.QuotaExhausted):
                backend.chat([], "在吗", {"id": "bill"})
        self.assertTrue(backend.billing_stop)
        self.assertEqual(backend.calls, 1, "402/余额不足必须立刻停，不许重试")
        self.assertEqual(backend.model, "vendor/m:free", "不许升级到别的模型")

    def test_billing_stop_marks_provider_disabled(self):
        first = FakeBackend([ev.QuotaExhausted("a", "HTTP 402 insufficient balance")])
        first.billing_stop = True
        rb = ev.RotatingBackend(
            PROVIDERS, factory=lambda p: first if p["name"] == "a"
            else FakeBackend([{"reply": "嗯，我在。"}]))
        out = rb.chat([], "嗯", {"id": "billing"})
        self.assertEqual(out["reply"], "嗯，我在。")
        self.assertEqual(rb.stats[0]["status"], "配额或计费受限，已停用")
        self.assertEqual(rb.rotations, 1)


class MaxRequestsTest(unittest.TestCase):
    def test_max_requests_switches_to_next_provider(self):
        rb = ev.RotatingBackend(PROVIDERS, max_requests=2,
                                factory=make_factory({"a": [{"reply": "嗯。"}],
                                                      "b": [{"reply": "在。"}]}))
        for _ in range(2):
            rb.chat([], "嗯", {"id": "limit"})
        out = rb.chat([], "嗯", {"id": "limit"})
        self.assertEqual(out["reply"], "在。")
        report = rb.report()
        self.assertEqual(report["providers"][0]["calls"], 2)
        self.assertEqual(report["providers"][0]["status"], "达到单次上限")
        self.assertEqual(report["rotations"], 1)

    def test_max_requests_all_used_raises_limit(self):
        rb = ev.RotatingBackend(PROVIDERS, max_requests=1,
                                factory=make_factory({"a": [{"reply": "嗯。"}],
                                                      "b": [{"reply": "在。"}]}))
        rb.chat([], "嗯", {"id": "limit2"})
        rb.chat([], "嗯", {"id": "limit2"})
        with self.assertRaises(ev.RequestLimitReached):
            rb.chat([], "嗯", {"id": "limit2"})

    def test_limit_skipped_case_not_counted_in_rate(self):
        class OneTurn:
            name = "rotate"

            def __init__(self):
                self.calls = 0

            def reset_case(self, case):
                pass

            def chat(self, history, user, case):
                self.calls += 1
                raise ev.RequestLimitReached("x", 1)

            def snapshot(self):
                return {}

        data = ev.evaluate(ev.CASES_FILE, OneTurn(), limit=3,
                           on_case=lambda res: None)
        self.assertEqual(data["results"], [])
        self.assertEqual(data["limit_skips"], 3)
        self.assertEqual(data["metrics"]["turns"], 0)


class ConfigTest(unittest.TestCase):
    def test_example_config_only_free_models(self):
        data = json.loads(ev.EXAMPLE_BACKENDS_FILE.read_text(encoding="utf-8"))
        for entry in data["backends"]:
            self.assertTrue(ev.is_free_model(entry.get("model", "")),
                            "示例配置只允许免费模型：%s" % entry.get("name"))
            self.assertLessEqual(int(entry.get("max_tokens") or 0), ev.MAX_TOKENS_CAP)

    def test_local_config_only_free_models(self):
        path = ev.DEFAULT_BACKENDS_FILE
        if not path.exists():
            self.skipTest("本地轮换配置不存在（按需创建）")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for entry in data["backends"]:
            self.assertTrue(ev.is_free_model(entry.get("model", "")),
                            "本地配置只允许免费模型：%s" % entry.get("name"))

    def test_provider_delay_is_parsed(self):
        """免费端点按 IP 限速时，配置里可以给每家单独的 delay。"""
        cfg = json.dumps([{"name": "slow", "base": "https://x.example", "model": "",
                           "requires_key": False, "delay": 46}])
        with mock.patch.dict(os.environ, {"HUMAN_FEEL_BACKENDS": cfg}):
            providers, skipped = ev.load_providers()
        self.assertEqual(providers[0]["delay"], 46)
        self.assertEqual(skipped, [])

    def test_env_json_resolves_key_and_skips_missing(self):
        cfg = json.dumps([
            {"name": "with-env", "base": "https://x.example", "model": "vendor/m:free",
             "key_env": "W20_TEST_KEY"},
            {"name": "no-key", "base": "https://y.example", "model": "vendor/m2:free"},
            {"name": "keyless", "base": "https://z.example", "model": "",
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

    def test_bom_config_is_readable(self):
        """Windows PowerShell 写出的 UTF-8 BOM 不能让轮换启动失败。"""
        import tempfile
        body = json.dumps([{"name": "bom", "base": "https://x.example",
                            "model": "vendor/m:free", "requires_key": False}])
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8-sig",
                                         delete=False) as f:
            f.write(body)
            path = f.name
        try:
            providers, skipped = ev.load_providers(path)
            self.assertEqual(providers[0]["name"], "bom")
            self.assertEqual(skipped, [])
        finally:
            os.unlink(path)


class ResumeTest(unittest.TestCase):
    """断点续跑：跳过已通过用例，合并历史结果，覆盖率如实算。"""

    def _run(self, args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ev.main(args)
        return rc, buf.getvalue()

    def test_metrics_from_stats_matches_compute_metrics(self):
        data = ev.evaluate(ev.CASES_FILE, ev.MockBackend(ev.CASES_FILE), limit=6)
        stats = [ev.case_stats(r) for r in data["results"]]
        self.assertEqual(ev.metrics_from_stats(stats), data["metrics"])

    def test_resume_skips_passed_and_keeps_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "run.json"
            rc1, _ = self._run(["--backend", "mock", "--limit", "4",
                                "--json", str(out)])
            self.assertEqual(rc1, 0)
            first = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(first["case_stats"]), 4)
            self.assertEqual(len(first["passed_ids"]), 4)
            # 首次整批运行也要如实写覆盖率，不能只给场景数
            self.assertEqual(first["coverage"]["expected"], 112)
            self.assertEqual(first["coverage"]["completed"], 4)
            self.assertEqual(first["coverage"]["resumed"], 0)
            self.assertEqual(first["coverage"]["new"], 4)
            self.assertEqual(first["coverage"]["retried"], 0)
            self.assertIn("覆盖率", first["notes"])

            rc2, _ = self._run(["--backend", "mock", "--limit", "4",
                               "--json", str(out), "--resume"])
            second = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(rc2, 0)
            self.assertEqual(second["coverage"]["completed"], 4)
            self.assertEqual(second["coverage"]["expected"], 112)
            self.assertEqual(second["coverage"]["resumed"], 4)
            self.assertEqual(second["coverage"]["new"], 0)
            self.assertEqual(second["cases"], 4, "续跑不能把总数改大")
            self.assertIn("断点续跑", second["notes"])

    def test_resume_adds_new_cases_without_double_count(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "run.json"
            self._run(["--backend", "mock", "--limit", "2", "--json", str(out)])
            self._run(["--backend", "mock", "--limit", "5", "--json", str(out),
                       "--resume"])
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["coverage"]["completed"], 5)
            self.assertEqual(data["coverage"]["resumed"], 2)
            self.assertEqual(data["coverage"]["new"], 3)
            self.assertEqual(data["cases"], 5, "合并后按用例 id 去重")
            self.assertEqual(len(data["passed_ids"]) + len(data["failed_ids"]), 5)

    def test_resume_retries_failed_case_and_counts_it(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "run.json"
            self._run(["--backend", "mock", "--limit", "2", "--json", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            data["case_stats"][1]["passed"] = False  # 伪造历史失败
            data["passed_ids"] = [s["id"] for s in data["case_stats"] if s["passed"]]
            data["failed_ids"] = [s["id"] for s in data["case_stats"] if not s["passed"]]
            out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self._run(["--backend", "mock", "--limit", "2", "--json", str(out),
                       "--resume"])
            again = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(again["coverage"]["resumed"], 1, "已通过的仍跳过")
            self.assertEqual(again["coverage"]["new"], 1)
            self.assertEqual(again["coverage"]["retried"], 1, "重试历史失败要能看出来")
            self.assertIn("重试历史失败", again["notes"])

    def test_resume_without_json_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "none.json"
            rc, _ = self._run(["--backend", "mock", "--limit", "1",
                               "--resume", "--json", str(missing)])
            self.assertEqual(rc, 2)

    def test_resume_legacy_json_without_stats_starts_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "legacy.json"
            out.write_text(json.dumps({"cases": 9, "passed": 8}, ensure_ascii=False),
                           encoding="utf-8")
            rc, log = self._run(["--backend", "mock", "--limit", "2",
                                 "--json", str(out), "--resume"])
            self.assertEqual(rc, 0)
            self.assertIn("没有逐条统计", log)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["cases"], 2)


if __name__ == "__main__":
    unittest.main()
