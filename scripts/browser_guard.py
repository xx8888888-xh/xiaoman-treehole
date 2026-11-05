#!/usr/bin/env python3
"""浏览器类验证的本地硬闸门（2026-11-05）。

策略：E2E / 截图 / rAF 采样 / 视觉对比一律走云端 `.github/workflows/e2e.yml`
（ubuntu runner + Playwright Chromium + `--mute-audio`）。本地默认禁止拉起
Playwright / chrome-headless-shell，避免残留进程把 CPU 打满。

放行条件（任一）：
- `GITHUB_ACTIONS=true` → 云端 runner 自动放行（注意不用 `CI`：
  本地开发终端/IDE 也常把 `CI=true` 全局设上，不能作为云端判据）。
- `XIAOMAN_ALLOW_LOCAL_BROWSER=1` → 本地显式 opt-in，仅用于排查，跑完必须退出浏览器。
- `XIAOMAN_BROWSER_GUARD=off` → 应急总开关（等同 opt-in，慎用）。

被拦截时退出码 2（表示"未执行"，不是测试失败）。
"""
import os
import sys

_ENV = "XIAOMAN_ALLOW_LOCAL_BROWSER"
_GUARD_OFF = "XIAOMAN_BROWSER_GUARD"
_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _truthy(value):
    return (value or "").strip().lower() in _TRUTHY


def _falsy(value):
    return (value or "").strip().lower() in _FALSY


def browser_allowed():
    if _falsy(os.environ.get(_GUARD_OFF)):
        return True
    return _truthy(os.environ.get("GITHUB_ACTIONS")) or _truthy(os.environ.get(_ENV))


def require_browser_allowed(script):
    if browser_allowed():
        return
    name = os.path.basename(str(script))
    sys.stderr.write(
        f"[browser-guard] {name} 需要启动无头浏览器；本地默认已禁用。\n"
        "  浏览器类验证请走云端 e2e.yml（GitHub Actions 会注入 GITHUB_ACTIONS=true 自动放行）。\n"
        f"  确需本地调试：先设置 {_ENV}=1，跑完必须显式关闭浏览器：\n"
        "    PowerShell: Get-Process chrome-headless-shell,chrome -ErrorAction SilentlyContinue | Stop-Process -Force\n"
    )
    raise SystemExit(2)
