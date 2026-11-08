#!/usr/bin/env python3
"""时间炸弹守卫：集成/E2E 脚本里不许写死绝对日期/时刻。

背景：test_roundtrip.py 曾把提醒时间写死成 "2026-11-07T08:00"，过了那个时刻后
提醒必然被判成"过去时间"，CI 从绿变红（时间炸弹）。凡是拿真实时钟跑的测试，
时间一律用相对表达（"明天 08:00"、"当前时刻 +N 分钟"）。

扫描范围（读真实时钟的那一层）：
  - scripts/test_*.py
  - scripts/emulator_*.py
  - scripts/ui_tour.py / scripts/ux_walkthrough.py / scripts/shot.py

不在范围内（纯单元测试，时间全部显式注入，不读真实时钟）：
  - scripts/test_memory_recall.py（node 夹具注入 Date）
  - web/tests/**（单测夹具注入 new Date(...)）

注释行（#、//、*）里的日期只是说明，不算炸弹。
"""
import pathlib
import re
import sys

DATE_RE = re.compile(r"20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])")
COMMENT_RE = re.compile(r"^(#|//|\*|--|\*/)")
EXEMPT = {"test_memory_recall.py"}


def targets(root: pathlib.Path):
    scripts = root / "scripts"
    pats = ["test_*.py", "emulator_*.py"]
    found = []
    for pat in pats:
        found.extend(scripts.glob(pat))
    for name in ("ui_tour.py", "ux_walkthrough.py", "shot.py"):
        p = scripts / name
        if p.is_file():
            found.append(p)
    return sorted(p for p in set(found) if p.name not in EXEMPT)


def scan(path: pathlib.Path):
    bad = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if COMMENT_RE.match(line.strip()):
            continue
        m = DATE_RE.search(line)
        if m:
            bad.append((i, m.group(0), line.strip()[:120]))
    return bad


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    problems = []
    for p in targets(root):
        for line, hit, snippet in scan(p):
            problems.append("%s:%d 写死绝对日期 %s | %s" % (p.relative_to(root), line, hit, snippet))
    if problems:
        print("[TIME-BOMB] 测试里出现写死的绝对日期/时刻（会随时间过期），请改成相对时间：")
        for p in problems:
            print("  - " + p)
        print("例外：纯单元测试请把时间显式注入（new Date('...')/Date.parse('...')），或登记到 EXEMPT。")
        return 1
    print("[TIME-BOMB] OK：%d 个集成/E2E 脚本未发现写死的绝对日期" % len(targets(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
