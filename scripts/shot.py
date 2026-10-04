#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shot.py · Playwright 截图脚本（安卓视口仿真，用于审美审查与功能验证）
用法: python3 scripts/shot.py [步骤]
  base    → 首屏（Live2D 加载 + 问候语）
  chat    → 模拟一轮倾诉对话后截图
  poke    → 点击 Live2D 角色截图
"""
import sys, time, os
from playwright.sync_api import sync_playwright

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SHOTS = os.path.join(BASE, "docs", "shots")
os.makedirs(SHOTS, exist_ok=True)
URL = "http://127.0.0.1:8901/index.html"

def run(step):
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(URL)
        page.wait_for_timeout(7000)  # 等 Live2D 加载+开场白
        if step == "base":
            page.screenshot(path=f"{SHOTS}/01_base.png")
        elif step == "chat":
            page.fill("#textInput", "今天被老板当着全组的面骂了，方案毙了还说我能力不行")
            page.click("#sendBtn")
            page.wait_for_timeout(4000)   # 打字中
            page.screenshot(path=f"{SHOTS}/02_typing.png")
            page.wait_for_timeout(9000)   # 分条送达
            page.screenshot(path=f"{SHOTS}/03_chat.png")
            page.fill("#textInput", "能不能抱一下，就一下")
            page.click("#sendBtn")
            page.wait_for_timeout(13000)
            page.screenshot(path=f"{SHOTS}/04_intimate.png")
        elif step == "poke":
            page.mouse.click(195, 220)
            page.wait_for_timeout(1500)
            page.screenshot(path=f"{SHOTS}/05_poke.png")
        elif step == "crisis":
            page.fill("#textInput", "有时候会觉得，活着挺没意思的")
            page.click("#sendBtn")
            page.wait_for_timeout(9000)
            page.screenshot(path=f"{SHOTS}/06_crisis.png")
        page.screenshot(path=f"{SHOTS}/{step}_errors_debug.png") if False else None
        browser.close()
        if errors:
            print("CONSOLE_ERRORS:")
            for e in errors[:8]: print(" -", e[:180])
        else:
            print("NO_CONSOLE_ERRORS")
    print(f"SHOT_{step}_OK → {SHOTS}/")

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "base")
