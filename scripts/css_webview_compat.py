#!/usr/bin/env python3
"""旧版 Android WebView 的 CSS 语法扫描（轻量防回归）。

背景：本项目的 Android 壳固定使用系统 WebView（目标机型实测为 Chrome 83）。
Chrome 83 会**整条丢弃**它不认识的声明/选择器，典型后果就是 position:fixed
拿不到偏移 → shrink-to-fit 0x0 → 整页/浮窗塌陷（run #121-#126 实锤）。
docs/android-audio/ANDROID_AUDIO_VERIFIABILITY.md §3.7 记录了这次事故与修复。

本脚本只做静态扫描，不启动浏览器、不跑模拟器，标准库实现，可直接进快闸门。
按「必须修」与「已知可接受」两类输出清单：

  MUST-FIX  会改变布局/命中区，旧 WebView 会整条丢弃 → 应当修
  KNOWN-OK  只影响装饰/焦点样式，项目已登记接受 → 只列清单，不判红

默认永远 exit 0（输出清单，交给评审判断）；加 --strict 时，
只要存在 MUST-FIX 项就 exit 1，用于「已清零后不许再引入」的阶段。

用法：
  python3 scripts/css_webview_compat.py            # 扫描 web/，打印清单
  python3 scripts/css_webview_compat.py --strict   # MUST-FIX>0 时 exit 1
  python3 scripts/css_webview_compat.py --root web --strict
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCAN_EXTENSIONS = (".css", ".js", ".html")
# 第三方/生成物与测试断言字符串不算产品代码，直接跳过
SKIP_DIRS = {"node_modules", "tests", "assets", "build", "dist", ".git"}
# 扫描源是 web/（单一真源）；android assets/www 由 scripts/build_assets.sh 生成，不重复扫

MUST_FIX = "MUST-FIX"
KNOWN_OK = "KNOWN-OK"

# 动态视口单位（dvh/svh/lvh 等，Chrome 108 起）
DYN_VIEWPORT = re.compile(
    r"\b(?:\d+(?:\.\d+)?|calc\([^;{}]*\))(?:dvh|dvw|dvi|dvb|dvmin|dvmax"
    r"|svh|svw|svi|svb|svmin|svmax|lvh|lvw|lvi|lvb|lvmin|lvmax)\b"
)


@dataclass(frozen=True)
class Rule:
    name: str
    kind: str
    pattern: re.Pattern
    hint: str
    extensions: tuple = SCAN_EXTENSIONS


RULES = [
    Rule(
        "inset-shorthand",
        MUST_FIX,
        re.compile(r"(?<![\w-])inset\s*:"),
        "WebView 83 不解析 inset 简写（Chrome 87 起）；拆成 top/right/bottom/left",
    ),
    Rule(
        "dynamic-viewport-unit",
        MUST_FIX,
        DYN_VIEWPORT,
        "dvh/svh/lvh 需 Chrome 108；同一规则内先补 vh/% 兜底声明",
    ),
    Rule(
        "css-nesting",
        MUST_FIX,
        re.compile(r"(?<![\w&])&[^;{}]*{"),
        "CSS 嵌套需 Chrome 120；WebView 83 会丢弃整条规则，改写为完整选择器",
        extensions=(".css",),
    ),
    Rule(
        "has-selector",
        KNOWN_OK,
        re.compile(r":has\("),
        "需 Chrome 105；本仓库仅装饰留白，且有相邻兄弟选择器等兜底",
    ),
    Rule(
        "flex-gap",
        KNOWN_OK,
        re.compile(r"(?<![\w-])gap\s*:"),
        "flex 布局的 gap 需 Chrome 84（grid gap 不受影响）；本仓库为装饰间距",
    ),
    Rule(
        "focus-visible",
        KNOWN_OK,
        re.compile(r":focus-visible"),
        "需 Chrome 86；只影响键盘焦点环，触摸端有默认外观",
    ),
    Rule(
        "is-where-selector",
        KNOWN_OK,
        re.compile(r":(?:is|where)\("),
        "需 Chrome 88；只做选择器简写，失效等于该分支样式不生效",
    ),
    Rule(
        "aspect-ratio",
        KNOWN_OK,
        re.compile(r"(?<![\w-])aspect-ratio\s*:"),
        "需 Chrome 88；本仓库仅用于图片/占位比例",
    ),
    Rule(
        "color-mix",
        KNOWN_OK,
        re.compile(r"color-mix\("),
        "需 Chrome 111；只影响颜色，不影响布局",
    ),
]


@dataclass
class Finding:
    path: str
    line: int
    column: int
    rule: str
    kind: str
    snippet: str
    hint: str
    fallback_ok: bool = False


def iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(SCAN_EXTENSIONS):
                yield os.path.join(dirpath, filename)


def _prop_of(prefix: str) -> str:
    """从「当前行匹配点之前的文本」取所在声明的属性名（如 33dvh ← height）。"""
    segment = prefix.rsplit(";", 1)[-1]
    if ":" not in segment:
        return ""
    head = segment.rsplit(":", 1)[0]
    parts = head.strip().split()
    return parts[-1].lower() if parts else ""


def _same_property_fallback(lines, idx: int, line: str, col: int, prop: str) -> bool:
    """判断同一规则块内、当前声明之前，是否有同属性的非动态单位兜底。"""
    prop_re = re.compile(r"(?<![\w-])" + re.escape(prop) + r"\s*:")

    # 同一行（压缩后的单行规则，如 `height:100%;height:100dvh;`）
    # 最后一个分号后的片段是「当前声明」自身，必须排除，否则会把自己当兜底
    scope = line[:col].rsplit("{", 1)[-1]
    for segment in scope.split(";")[:-1]:
        if prop_re.search(segment) and not DYN_VIEWPORT.search(segment.split(":", 1)[-1]):
            return True

    # 向上回溯，遇到块边界即停
    j = idx - 1
    while j >= 0:
        prev = lines[j]
        boundary = "{" in prev or "}" in prev
        scope = prev.rsplit("{", 1)[-1] if boundary else prev
        for segment in scope.split(";"):
            if prop_re.search(segment) and not DYN_VIEWPORT.search(segment.split(":", 1)[-1]):
                return True
        if boundary:
            break
        j -= 1
    return False


def scan_file(path: str, root: str) -> list[Finding]:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    findings: list[Finding] = []
    for idx, line in enumerate(lines):
        for rule in RULES:
            if not path.endswith(rule.extensions):
                continue
            for match in rule.pattern.finditer(line):
                start = match.start()
                snippet = line.strip()
                if len(snippet) > 110:
                    snippet = snippet[:107] + "..."
                entry = Finding(
                    path=rel,
                    line=idx + 1,
                    column=start + 1,
                    rule=rule.name,
                    kind=rule.kind,
                    snippet=snippet,
                    hint=rule.hint,
                )
                if rule.name == "dynamic-viewport-unit":
                    prop = _prop_of(line[:start])
                    entry.fallback_ok = _same_property_fallback(lines, idx, line, start, prop)
                findings.append(entry)
    return findings


def emit(findings: list[Finding], file_count: int) -> tuple[int, int]:
    must = [f for f in findings if f.kind == MUST_FIX and not f.fallback_ok]
    known = [f for f in findings if f.kind == KNOWN_OK or (f.kind == MUST_FIX and f.fallback_ok)]

    print(
        "[CSS-COMPAT] scan root=web files=%d must-fix=%d known-ok=%d"
        % (file_count, len(must), len(known)),
        flush=True,
    )

    for f in must:
        note = f.hint
        if f.rule == "dynamic-viewport-unit":
            note = "缺少同属性 vh/% 兜底：" + note
        print(f"[CSS-COMPAT][{MUST_FIX}] {f.path}:{f.line}:{f.column} {f.rule} | {f.snippet} | {note}", flush=True)
        # GitHub Actions 注解（不判红，只提示）
        print(f"::warning file={f.path},line={f.line}::[{f.rule}] {note}", flush=True)

    for f in known:
        if f.rule == "dynamic-viewport-unit":
            note = "已有同属性兜底，旧 WebView 会忽略本行：" + f.hint
        else:
            note = f.hint
        print(f"[CSS-COMPAT][{KNOWN_OK}] {f.path}:{f.line}:{f.column} {f.rule} | {f.snippet} | {note}", flush=True)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### css-webview-compat\n\n")
            fh.write(f"- 扫描文件：{file_count} 个（web/ 下的 .css/.js/.html）\n")
            fh.write(f"- MUST-FIX：{len(must)}；KNOWN-OK：{len(known)}\n")
            for f in must:
                fh.write(f"- **MUST-FIX** `{f.path}:{f.line}` `{f.rule}` — {f.hint}\n")
            fh.write("\n<details><summary>KNOWN-OK 清单</summary>\n\n")
            for f in known:
                fh.write(f"- `{f.path}:{f.line}` `{f.rule}`\n")
            fh.write("\n</details>\n")

    return len(must), len(known)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="旧版 WebView CSS 语法扫描（WebView 83 基线）")
    parser.add_argument("--root", default=os.path.join(REPO_ROOT, "web"), help="扫描根目录（默认 web/）")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在 MUST-FIX 时 exit 1；默认只输出清单、exit 0",
    )
    args = parser.parse_args(argv)

    root = args.root
    if not os.path.isdir(root):
        print(f"[CSS-COMPAT] ERROR: root not found: {root}", flush=True)
        return 2

    files = list(iter_files(root))
    findings: list[Finding] = []
    for path in files:
        findings.extend(scan_file(path, root))

    must, known = emit(findings, len(files))
    if must and args.strict:
        print(f"[CSS-COMPAT] strict: {must} must-fix item(s) -> exit 1", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
