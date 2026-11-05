#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_persona_rules.py · 提示词单一源生成器（W20）

真源只有一处：server/prompt_core.py 的常量。
本脚本把它派生成 web/js/persona_rules.js（浏览器/Android WebView/Node 共用），
api.js 通过 require / document.write 注入后引用，不再手写副本。

用法：
  python scripts/gen_persona_rules.py          # 重新生成
  python scripts/gen_persona_rules.py --check  # 只校验，不一致退出 1（测试用）

Android 侧不单独维护副本：scripts/build_assets.sh 把 web/ 整目录同步到
android/app/src/main/assets/www，WebView 加载的就是这份生成物。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import prompt_core as pc  # noqa: E402

OUT = ROOT / "web" / "js" / "persona_rules.js"

# 顺序即拼接顺序，必须与 prompt_core.static_core("full") 一致
CONSTANTS = [
    "SAFETY_RULES",
    "PERSONA_RULES",
    "REALISM_RULES",
    "HUMAN_FEEL_RULES",
    "MEMORY_RULES",
    "MEMORY_SLOT_RULES",
    "BASELINE",
    "OUTPUT_PROTOCOL",
]

HEADER = """/* ============================================================
 * persona_rules.js · 生成物，请勿手改
 * ------------------------------------------------------------
 * 真源：server/prompt_core.py
 * 生成：python scripts/gen_persona_rules.py
 * 校验：python scripts/gen_persona_rules.py --check
 * 消费方：web/js/api.js（浏览器内由 document.write 同步注入；
 *         Node 单测用 require）。Android 走 scripts/build_assets.sh 同步 web/。
 * ============================================================ */
"""


def _js_template(text):
    """把 Python 常量转成 JS 模板字符串字面量（转义反引号/${/反斜杠）。"""
    escaped = (text.replace("\\", "\\\\")
                   .replace("`", "\\`")
                   .replace("${", "\\${"))
    return "`%s`" % escaped


def render():
    lines = [HEADER, "(function (root, factory) {",
             '  if (typeof module === "object" && module.exports) {',
             "    module.exports = factory();",
             "    return;",
             "  }",
             "  root.XMPersonaRules = factory();",
             '}(typeof globalThis !== "undefined" ? globalThis : this, function () {']
    for name in CONSTANTS:
        lines.append("  const %s = %s;" % (name, _js_template(getattr(pc, name))))
    lines += ["",
              "  const SECTIONS = [%s];" % ", ".join(CONSTANTS),
              "",
              "  return {",
              ]
    for name in CONSTANTS:
        lines.append("    %s: %s," % (name, name))
    lines += ["    SECTIONS: SECTIONS,",
              '    SYSTEM_PROMPT: SECTIONS.join("\\n\\n"),',
              '    version: "w20",',
              "  };",
              "}));",
              ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成客户端提示词模块")
    ap.add_argument("--check", action="store_true", help="只校验是否与真源一致")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current.replace("\r\n", "\n") != text.replace("\r\n", "\n"):
            print("persona_rules.js 与 server/prompt_core.py 不一致；"
                  "请运行 python scripts/gen_persona_rules.py", file=sys.stderr)
            return 1
        print("persona_rules.js 与真源一致（%d 字节）" % len(text.encode("utf-8")))
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("已生成 %s（%d 字节）" % (OUT, len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
