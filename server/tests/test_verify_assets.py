#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_verify_assets.py · 发布物一致性自检脚本单测（审计 P1-1 / w4-1）

覆盖：一致通过、缺失、多余、内容不一致、排除规则与 asset_excludes.sh 对齐。
不启动任何服务、不写仓库目录（全部在临时目录完成）。
运行：python -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_assets.py"

spec = importlib.util.spec_from_file_location("verify_assets", SCRIPT)
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)


def write(root, rel, data="x"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return path


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "web")
        self.dst = os.path.join(self.tmp.name, "assets")
        os.makedirs(self.src)
        os.makedirs(self.dst)

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_passes(self):
        for rel in ("index.html", "js/app.js", "assets/models/a.json"):
            write(self.src, rel, rel)
            write(self.dst, rel, rel)
        self.assertEqual(va.compare(self.src, self.dst), ([], [], []))

    def test_missing_file_detected(self):
        write(self.src, "index.html", "new")
        missing, extra, mismatch = va.compare(self.src, self.dst)
        self.assertEqual(missing, ["index.html"])
        self.assertEqual(extra, [])
        self.assertEqual(mismatch, [])

    def test_extra_file_detected(self):
        write(self.src, "index.html", "same")
        write(self.dst, "index.html", "same")
        write(self.dst, "js/stale.js", "old")
        missing, extra, mismatch = va.compare(self.src, self.dst)
        self.assertEqual(extra, ["js/stale.js"])
        self.assertEqual(missing, [])

    def test_hash_mismatch_detected(self):
        write(self.src, "index.html", "v2")
        write(self.dst, "index.html", "v1")
        missing, extra, mismatch = va.compare(self.src, self.dst)
        self.assertEqual(len(mismatch), 1)
        self.assertIn("index.html", mismatch[0])
        self.assertIn("sha256", mismatch[0])

    def test_same_size_different_content_detected(self):
        write(self.src, "app.js", "aaaa")
        write(self.dst, "app.js", "bbbb")
        _m, _e, mismatch = va.compare(self.src, self.dst)
        self.assertEqual(len(mismatch), 1)

    def test_exclusions_match_build_assets(self):
        for rel in ("node_modules/x.js", "tests/a.js", "docs/shots/a.png",
                    "package.json", "package-lock.json", "biome.json",
                    "assets/audio/selftest.mp3", "model.json.orig",
                    "ASSET_MANIFEST.json"):
            write(self.src, rel)
        files = va.collect_files(self.src)
        self.assertEqual(files, {})

    def test_runtime_files_included(self):
        for rel in ("index.html", "js/app.js", "css/style.css", "assets/models/a.json"):
            write(self.src, rel)
        files = va.collect_files(self.src)
        self.assertEqual(set(files), {"index.html", "js/app.js", "css/style.css", "assets/models/a.json"})


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "web")
        self.dst = os.path.join(self.tmp.name, "assets")
        os.makedirs(self.src)
        os.makedirs(self.dst)

    def tearDown(self):
        self.tmp.cleanup()

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = va.main(["--src", self.src, "--dst", self.dst] + list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_cli_pass(self):
        write(self.src, "index.html", "same")
        write(self.dst, "index.html", "same")
        code, text = self._main()
        self.assertEqual(code, 0)
        self.assertIn("ASSET_VERIFY PASS", text)

    def test_cli_fail_prints_actionable_error(self):
        write(self.src, "index.html", "v2")
        write(self.dst, "index.html", "v1")
        code, text = self._main()
        self.assertEqual(code, 1)
        self.assertIn("ASSET_VERIFY FAIL", text)
        self.assertIn("build_assets.sh", text)

    def test_missing_dst_returns_2(self):
        import shutil
        shutil.rmtree(self.dst)
        code, text = self._main()
        self.assertEqual(code, 2)
        self.assertIn("build_assets.sh", text)


if __name__ == "__main__":
    unittest.main()
