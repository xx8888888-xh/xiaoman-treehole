#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_persona_single_source.py · 三端提示词单一源（W20）

要求（本轮用户指定）：
  - 同一份规则只有一个源：server/prompt_core.py；
  - web 由生成物 web/js/persona_rules.js 接入，api.js 不再手写副本；
  - Android 走 build_assets.sh 同步 web/，不单独维护提示词；
  - 断言“服务端拼出的 system prompt”与“客户端下发的 system prompt”
    包含同样几条硬约束关键词（grep/测试锁住，不靠人工核对）。

运行：python -m pytest server/tests -q
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
for p in (SERVER_DIR, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import prompt_core  # noqa: E402
import gen_persona_rules as gen  # noqa: E402

PERSONA_JS = ROOT / "web" / "js" / "persona_rules.js"
API_JS = ROOT / "web" / "js" / "api.js"
ANDROID_WWW = ROOT / "android" / "app" / "src" / "main" / "assets" / "www"

# 三端必须同时出现的硬约束关键词（安全 + 本轮新增）
KEYWORDS = [
    "【不可覆盖规则",
    "12356",
    "长度硬约束",
    "跑题硬约束",
    "【真人感硬约束",
    "默认不给建议",
    "允许说不知道",
    "允许不好笑",
    "拒绝要落地",
    "记忆像人回忆",
    "【长期记忆槽的用法】",
    "忘了/别记/删掉这个",
]


def _norm(text):
    return (text or "").replace("\r\n", "\n")


def _client_sections():
    """从生成物里取回 8 段常量（模拟客户端拼装）。"""
    text = _norm(PERSONA_JS.read_text(encoding="utf-8"))
    out = []
    for name in gen.CONSTANTS:
        m = re.search(r"const %s = `([^`]*)`;" % re.escape(name), text, re.S)
        if not m:
            raise AssertionError("生成物缺少常量 %s" % name)
        out.append(m.group(1))
    return out


class SingleSourceTest(unittest.TestCase):
    def test_generated_file_matches_source(self):
        self.assertEqual(_norm(PERSONA_JS.read_text(encoding="utf-8")), _norm(gen.render()),
                         "web/js/persona_rules.js 与 prompt_core.py 漂移，请重新生成")

    def test_api_js_no_handwritten_copy(self):
        js = API_JS.read_text(encoding="utf-8")
        self.assertNotIn("【真人感硬约束", js, "api.js 又出现了手写提示词副本")
        self.assertNotRegex(js, r"const SAFETY_RULES = `", "api.js 不应再声明提示词常量")
        self.assertIn("persona_rules.js", js, "api.js 必须接入生成物")
        self.assertIn("XMPersonaRules", js, "浏览器侧必须读取单一源")

    def test_server_and_client_prompt_share_keywords(self):
        server = prompt_core.SERVER_SYSTEM_PROMPT
        client = "\n\n".join(_client_sections())
        for kw in KEYWORDS:
            self.assertIn(kw, server, "服务端 system prompt 缺少硬约束：%s" % kw)
            self.assertIn(kw, client, "客户端 system prompt 缺少硬约束：%s" % kw)

    def test_android_assets_copy_if_built(self):
        """Android 不单独写提示词；assets/www 若已构建，副本必须与 web 一致。"""
        copies = [(PERSONA_JS, ANDROID_WWW / "js" / "persona_rules.js"),
                  (API_JS, ANDROID_WWW / "js" / "api.js")]
        if not ANDROID_WWW.exists():
            self.skipTest("android assets/www 未构建（build_assets.sh 生成物，不入库）")
        for src, dst in copies:
            if not dst.exists():
                self.fail("Android 副本缺失：%s（build_assets.sh 同步不完整）" % dst)
            self.assertEqual(_norm(src.read_text(encoding="utf-8")),
                             _norm(dst.read_text(encoding="utf-8")),
                             "%s 与 web 源不一致" % dst)


if __name__ == "__main__":
    unittest.main()
