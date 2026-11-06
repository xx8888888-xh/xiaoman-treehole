# -*- coding: utf-8 -*-
"""test_safety_copy.py · 服务端话术与 docs/SAFETY_COPY.md 逐字一致

唯一措辞来源是 docs/SAFETY_COPY.md；server/risk_guard.py 与 web/js/risk.js
各自持有一份副本，改文档时必须同步改代码，否则这里直接红。
前端逐字断言见 web/tests/safety_copy.test.js。
"""
import re
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import risk_guard  # noqa: E402

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "SAFETY_COPY.md"
ROW_RE = re.compile(r"^\|\s*\d+\s*\|\s*(.+?)\s*\|\s*$")


def copy_rows(section_title):
    """取二级标题下表格的「文案」列（按 # 顺序）"""
    text = DOC_PATH.read_text(encoding="utf-8")
    marker = "## " + section_title
    start = text.index(marker) + len(marker)
    nxt = text.find("\n## ", start)
    block = text[start:nxt] if nxt >= 0 else text[start:]
    rows = []
    for line in block.splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.append(m.group(1).strip())
    return rows


class SafetyCopyTest(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(DOC_PATH.is_file(), "docs/SAFETY_COPY.md 必须是措辞唯一来源")

    def test_medium_matches_doc_verbatim(self):
        rows = copy_rows("中危（medium）")
        self.assertEqual(len(rows), 3, "中危应为 3 段")
        self.assertEqual(risk_guard.SCRIPT_MEDIUM, "||".join(rows))

    def test_high_matches_doc_verbatim(self):
        rows = copy_rows("高危（high）")
        self.assertEqual(len(rows), 4, "高危应为 4 段")
        self.assertEqual(risk_guard.SCRIPT_HIGH, "||".join(rows))

    def test_self_check_rules(self):
        medium = risk_guard.SCRIPT_MEDIUM.split("||")
        self.assertNotIn("12356", medium[0])
        self.assertNotIn("120", medium[0])
        self.assertNotIn("110", medium[0])
        self.assertNotIn("120", risk_guard.SCRIPT_MEDIUM)
        self.assertNotIn("110", risk_guard.SCRIPT_MEDIUM)
        self.assertIn("最难受的是什么", risk_guard.SCRIPT_MEDIUM)
        for promise in ("会好的", "我保证", "我不走"):
            self.assertNotIn(promise, risk_guard.SCRIPT_MEDIUM)


if __name__ == "__main__":
    unittest.main()
