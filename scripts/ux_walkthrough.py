#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户视角体检走查（非断言式）——模拟真实用户完整旅程，产出：
  1) transcript 全文（每轮 user/them 原文 + 往返时延）→ ux_audit_transcript.txt
  2) 关键界面截图 → docs/shots/ux_audit/*.png
  3) console 错误 / 4xx 汇总 → 一并写入 transcript 尾部
用法: python3 scripts/ux_walkthrough.py   （需先 bash scripts/dev_up.sh）
"""
import asyncio, json, os, sys, time

BASE = "http://127.0.0.1:8901"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "docs", "shots", "ux_audit")
OUT = os.path.join(ROOT, "docs", "shots", "ux_audit_transcript.txt")
os.makedirs(SHOTS, exist_ok=True)

TRANSCRIPT = []   # [(kind, text, meta)]
ERRORS, BAD_RESP = [], []


def log(kind, text, meta=""):
    TRANSCRIPT.append((kind, text, meta))
    print(f"[{kind}] {text}" + (f"  ({meta})" if meta else ""))


async def dump_msgs(pg):
    """抓全部消息：返回 [(side, text)]"""
    return await pg.evaluate(
        "() => [...document.querySelectorAll('#messages .msg-row')]"
        ".map(r => [r.classList.contains('them') ? 'them' : 'me', r.innerText.trim()])"
    )


async def say(pg, text, tag, shot=None, settle=2.6):
    """真实发送一条消息，等 them 回复完，记 transcript + 时延"""
    before = await dump_msgs(pg)
    t0 = time.monotonic()
    await pg.fill("#textInput", text)
    await pg.click("#sendBtn")
    # 等 them 新消息出现且打字动画结束（文本稳定）
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        now = await dump_msgs(pg)
        if len(now) > len(before):
            await asyncio.sleep(settle)          # 等分条+打字节奏走完
            now = await dump_msgs(pg)
            if len(now) >= len(before) + 1:
                break
    ms = int((time.monotonic() - t0) * 1000)
    new = (await dump_msgs(pg))[len(before):]
    log("USER", text, f"{ms}ms")
    for side, t in new:
        log(side.upper(), t)
    if shot:
        await pg.screenshot(path=os.path.join(SHOTS, shot))
    return new


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True, channel="chromium",
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                  "--autoplay-policy=no-user-gesture-required"])
        # ========== Phase A · 全新用户首面 ==========
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: ERRORS.append(str(e)))
        pg.on("console", lambda m: ERRORS.append(f"[console.{m.type}] {m.text}")
              if m.type in ("error",) else None)
        pg.on("response", lambda r: BAD_RESP.append((r.status, r.url)) if r.status >= 400 else None)

        t0 = time.monotonic()
        await pg.goto(BASE, wait_until="domcontentloaded")
        await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
        load_ms = int((time.monotonic() - t0) * 1000)
        await asyncio.sleep(2.5)  # 等 greet 开场
        await pg.screenshot(path=os.path.join(SHOTS, "A1_first_open.png"))
        log("SYS", f"首屏加载 {load_ms}ms；初始消息：")
        for side, t in await dump_msgs(pg):
            log(side.upper(), t)

        # 引导/自我介绍
        await say(pg, "你好呀", "greet")
        await say(pg, "我叫小林，今天休息来逛逛", "intro", shot="A2_chat.png")
        # 记忆点：宠物
        await say(pg, "我家猫叫年糕，三岁了，特别粘人", "pet")
        # 工作吐槽（不该误触抑郁）
        await say(pg, "今天上班好累啊，开了三个会", "work")
        # 闲聊追问
        await say(pg, "你喜欢猫还是狗呀", "chat")
        # 深夜心事（普通情绪，不该触发抑郁层——"睡不着"不在抑郁词表）
        await say(pg, "最近老睡不着，躺下就开始想事情", "night")
        await pg.screenshot(path=os.path.join(SHOTS, "A3_after_chat.png"))

        # ========== Phase B · 抽屉与卡片（同会话） ==========
        try:
            await pg.click("#memoryBtn", timeout=5000)
            await asyncio.sleep(1.2)
            await pg.screenshot(path=os.path.join(SHOTS, "B1_memory_drawer.png"))
            log("SYS", "记忆抽屉已打开截图")
            # 抽屉里的卡片入口（P2-3：抽屉内 cardBtn）
            card_btn = await pg.query_selector("#cardBtn")
            if card_btn:
                await card_btn.click()
                await asyncio.sleep(2.2)
                await pg.screenshot(path=os.path.join(SHOTS, "B2_friend_card.png"))
                log("SYS", "友谊卡片已打开截图（有数据态）")
                # 关闭返回
                close = await pg.query_selector("#cardClose, .card-close, [data-close='card']")
                if close:
                    await close.click()
                    await asyncio.sleep(0.8)
            back = await pg.query_selector("#drawerClose, .drawer-close, [data-close='drawer']")
            if back:
                await back.click()
                await asyncio.sleep(0.8)
        except Exception as e:
            log("SYS", f"抽屉/卡片走查异常: {e}")

        # ========== Phase C · 隔天再访（模拟 32h 后） ==========
        await pg.evaluate(
            "() => {"
            "  const k = 'xiaoman_heartbeat_state';"
            "  try { const s = JSON.parse(localStorage.getItem(k) || '{}');"
            "    s.last_ts = Date.now() - 32*3600*1000;"
            "    localStorage.setItem(k, JSON.stringify(s)); } catch(e) {}"
            "  location.reload();"
            "}"
        )
        await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
        await asyncio.sleep(3.0)  # 等 greet + 可能的心跳
        await pg.screenshot(path=os.path.join(SHOTS, "C1_return_greet.png"))
        log("SYS", "隔天再访（32h），greet 开场：")
        for side, t in await dump_msgs(pg):
            log(side.upper(), t)

        # 关系问答
        await say(pg, "我们认识多久了", "relation")
        # 记忆回访（P0 留存线核心）
        await say(pg, "你猜我家年糕现在在干嘛", "memory_recall")
        # 更新查询
        await say(pg, "你最近有什么更新吗", "update_query")
        await pg.screenshot(path=os.path.join(SHOTS, "C2_after_return.png"))

        # ========== Phase D · 情绪时刻 ==========
        # 抑郁信号 1 次（应轻问不甩热线）
        await say(pg, "最近感觉干什么都没意思", "depress_n1")
        # 危机词（应出关怀卡 12356）
        await say(pg, "我不想活了", "crisis", shot="D1_crisis.png")
        await asyncio.sleep(1.0)
        await pg.screenshot(path=os.path.join(SHOTS, "D2_crisis_care.png"))

        await ctx.close()
        await b.close()

    # ========== 产出 ==========
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("== 用户视角体检 transcript ==\n\n")
        for kind, text, meta in TRANSCRIPT:
            tag = {"USER": "我", "THEM": "小满", "ME": "我", "SYS": "·"}.get(kind, kind)
            suffix = f"  [{meta}]" if meta else ""
            f.write(f"{tag}: {text}{suffix}\n")
        f.write("\n\n== 错误汇总 ==\n")
        f.write(f"console/page errors: {len(ERRORS)}\n")
        for e in ERRORS[:10]:
            f.write(f"  - {e[:200]}\n")
        f.write(f"4xx/5xx responses: {len(BAD_RESP)}\n")
        for s, u in BAD_RESP[:10]:
            f.write(f"  - {s} {u[:150]}\n")
    print(f"\n✅ transcript → {OUT}")
    print(f"   screenshots → {SHOTS}")
    print(f"   console errors={len(ERRORS)}  bad responses={len(BAD_RESP)}")


if __name__ == "__main__":
    asyncio.run(main())
