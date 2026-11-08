#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sync_assets.py · Gradle 发布物同步脚本（scripts/sync_assets.py）单测

覆盖：排除口径一致、清单字段、旧文件清理、自检通过、git HEAD 不可解析时硬失败。
不启动任何服务、不写仓库目录（全部在临时目录完成）。
运行：python -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "sync_assets.py"

spec = importlib.util.spec_from_file_location("sync_assets", SCRIPT)
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)


def write(root, rel, data="x"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return path


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "web")
        self.dst = os.path.join(self.tmp.name, "assets", "www")
        os.makedirs(self.src)
        os.makedirs(self.dst)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sa.main(["--src", self.src, "--dst", self.dst])
        return code, out.getvalue() + err.getvalue()

    def test_sync_copies_runtime_files_and_skips_excluded(self):
        for rel in ("index.html", "js/app.js", "assets/models/a.json"):
            write(self.src, rel, rel)
        for rel in ("node_modules/x.js", "tests/a.js", "docs/shots/a.png",
                    "package.json", "assets/audio/selftest.mp3", "m.json.orig"):
            write(self.src, rel)
        code, text = self._run()
        self.assertEqual(code, 0, text)
        self.assertIn("ASSET_SYNC PASS", text)
        copied = sorted(
            os.path.relpath(os.path.join(base, n), self.dst).replace(os.sep, "/")
            for base, _dirs, names in os.walk(self.dst) for n in names
        )
        self.assertEqual(
            copied,
            ["ASSET_MANIFEST.json", "assets/models/a.json", "index.html", "js/app.js"],
        )

    def test_manifest_fields_and_excludes_itself(self):
        write(self.src, "index.html", "hello")
        code, text = self._run()
        self.assertEqual(code, 0, text)
        with open(os.path.join(self.dst, "ASSET_MANIFEST.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["source_dir"], "web")
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual(manifest["total_bytes"], 5)
        self.assertNotIn("ASSET_MANIFEST.json", [e["path"] for e in manifest["files"]])
        # 且清单本身不计入与工作树的比对（否则永远 extra=1）
        missing, extra, mismatch = sa.verify_assets.compare(self.src, self.dst)
        self.assertEqual((missing, extra, mismatch), ([], [], []))

    def test_stale_files_removed_but_directory_kept(self):
        write(self.dst, "stale.js", "old")
        write(self.src, "index.html", "new")
        code, text = self._run()
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.exists(os.path.join(self.dst, "stale.js")))
        self.assertTrue(os.path.isdir(self.dst))  # 目录保留，compose bind mount 不失效

    def test_missing_src_returns_2(self):
        import shutil
        shutil.rmtree(self.src)
        code, text = self._run()
        self.assertEqual(code, 2)
        self.assertIn("ASSET_SYNC FAIL", text)

    def test_git_head_unavailable_hard_fails(self):
        write(self.src, "index.html", "x")
        with mock.patch.object(sa, "REPO_ROOT", os.path.join(self.tmp.name, "not-a-repo")):
            code, text = self._run()
        self.assertEqual(code, 1)
        self.assertIn("无法解析 git HEAD", text)
        self.assertFalse(os.path.exists(os.path.join(self.dst, "ASSET_MANIFEST.json")))


if __name__ == "__main__":
    unittest.main()
