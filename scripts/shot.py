#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shot.py · Playwright 截图脚本（安卓视口仿真，用于审美审查与功能验证）
用法: python3 scripts/shot.py [步骤]
  base    → 首屏（Live2D 加载 + 问候语）
  chat    → 模拟一轮倾诉对话后截图
  poke    → 点击 Live2D 角色截图
  crisis  → 触发危机拦截截图

环境变量:
  SHOT_STRICT=1  → 有 console error/pageerror 时退出码 1（默认仅打印，退出码 0）
"""
import os
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright


def _load_env():
    """端口/主机集中配置唯一真源：scripts/env.sh（经 bash source 读取）"""
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c", f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_WEB_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return out[0], int(out[1])
    except Exception:
        return "127.0.0.1", 8901


_HOST, _WEB_PORT = _load_env()
BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SHOTS = os.path.join(BASE, "docs", "shots")
os.makedirs(SHOTS, exist_ok=True)
URL = f"http://{_HOST}:{_WEB_PORT}/index.html"
STRICT = os.environ.get("SHOT_STRICT", "0") == "1"

def wait_ready(url, timeout=15):
    """等待服务就绪，返回 True/False"""
    import urllib.request
    waited = 0
    while waited < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
        waited += 1
    return False

def run(step):
    # 先探测服务就绪
    if not wait_ready(URL):
        print(f"❌ 前端服务 ({_WEB_PORT}) 未就绪，请先运行 dev_up.sh", flush=True)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 导航并等待 DOM 就绪
        page.goto(URL, wait_until="domcontentloaded")

        if step == "base":
            # 等待 Live2D 加载完成（等待 canvas 出现且非空）
            page.wait_for_selector("canvas", state="visible", timeout=15000)
            page.wait_for_timeout(2000)  # 给问候语动画留时间
            page.screenshot(path=os.path.join(SHOTS, "01_base.png"))

        elif step == "chat":
            page.fill("#textInput", "今天被老板当着全组的面骂了，方案毙了还说我能力不行")
            page.click("#sendBtn")
            # 等待打字动画 + 回复出现（等待消息容器有新内容）
            page.wait_for_selector("#messages .msg-row.them", state="attached", timeout=15000)
            page.screenshot(path=os.path.join(SHOTS, "02_typing.png"))
            page.wait_for_timeout(3000)  # 分条送达
            page.screenshot(path=os.path.join(SHOTS, "03_chat.png"))
            page.fill("#textInput", "能不能抱一下，就一下")
            page.click("#sendBtn")
            page.wait_for_selector("#messages .msg-row.them:last-child", state="attached", timeout=15000)
            page.wait_for_timeout(2000)
            page.screenshot(path=os.path.join(SHOTS, "04_intimate.png"))

        elif step == "poke":
            page.wait_for_selector("canvas", state="visible", timeout=15000)
            page.mouse.click(195, 220)
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(SHOTS, "05_poke.png"))

        elif step == "crisis":
            page.fill("#textInput", "有时候会觉得，活着挺没意思的")
            page.click("#sendBtn")
            page.wait_for_selector("#messages .msg-row.them", state="attached", timeout=15000)
            page.wait_for_timeout(2000)
            page.screenshot(path=os.path.join(SHOTS, "06_crisis.png"))

        else:
            print(f"❌ 未知步骤: {step}", flush=True)
            sys.exit(1)

        browser.close()

        if errors:
            print("CONSOLE_ERRORS:")
            for e in errors[:8]:
                print(" -", e[:180])
            if STRICT:
                sys.exit(1)
        else:
            print("NO_CONSOLE_ERRORS")

    print(f"SHOT_{step}_OK → {SHOTS}/")

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "base")
