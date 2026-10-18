#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全界面操作走查截图（ui_tour）——每个界面+每个操作各截一张：
  产出 docs/shots/ui_tour/*.png + manifest.txt（截图↔操作对照清单）
用法: python3 scripts/ui_tour.py  （需先 bash scripts/dev_up.sh）
"""
import asyncio, os, time

BASE = "http://127.0.0.1:8901"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "docs", "shots", "ui_tour")
os.makedirs(SHOTS, exist_ok=True)

MANIFEST = []   # (file, operation)
ERRORS = []

def op(fname, desc):
    MANIFEST.append((fname, desc))
    print(f"[OP] {fname}  <- {desc}", flush=True)

async def shot(pg, fname, desc, settle=1.0):
    await asyncio.sleep(settle)
    await pg.screenshot(path=os.path.join(SHOTS, fname))
    op(fname, desc)

async def dump_msgs(pg):
    return await pg.evaluate(
        "() => [...document.querySelectorAll('#messages .msg-row')]"
        ".map(r => (r.classList.contains('them') ? 'them' : 'me') + ': ' + r.innerText.trim())"
    )

async def robust_click(pg, sel, timeout=5000):
    """原生点击 → JS 直点兜底（绕过命中测试，对付瞬态遮挡的 actionability 拦截）"""
    try:
        await pg.click(sel, timeout=timeout)
    except Exception as e:
        print(f"   [robust] {sel} 原生点击超时({type(e).__name__})，JS 直点兜底", flush=True)
        await pg.evaluate(f"document.querySelector('{sel}') && document.querySelector('{sel}').click()")
        await asyncio.sleep(0.6)


async def ensure_input(pg):
    """输入框可见性自愈：mic 录音态残留等会遮住输入框——先试再点 micBtn 复位，不行就刷新"""
    try:
        await pg.wait_for_selector("#textInput", state="visible", timeout=6000)
        return
    except Exception:
        pass
    for act in ("mic_toggle", "reload"):
        try:
            if act == "mic_toggle":
                await pg.click("#micBtn", timeout=3000)
            else:
                await pg.reload(wait_until="domcontentloaded")
            await pg.wait_for_selector("#textInput", state="visible", timeout=8000)
            await asyncio.sleep(1.0)
            print("   [自愈] 输入框经", act, "恢复", flush=True)
            return
        except Exception:
            continue
    raise RuntimeError("输入框无法恢复可见")


async def say(pg, text, settle=2.8):
    """发送一条消息，等回复渲染完（打字动画结束后文本稳定）"""
    await ensure_input(pg)
    before = await dump_msgs(pg)
    await pg.fill("#textInput", text)
    await pg.click("#sendBtn")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        now = await dump_msgs(pg)
        if len(now) > len(before):
            await asyncio.sleep(settle)
            now = await dump_msgs(pg)
            if len(now) >= len(before) + 1:
                break
    for line in (await dump_msgs(pg))[len(before):]:
        print("   ", line[:80], flush=True)

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True, channel="chromium",
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                  "--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: ERRORS.append(str(e)))
        pg.on("console", lambda m: ERRORS.append(f"[console.{m.type}] {m.text}")
              if m.type == "error" else None)

        # ===== A 全新用户 =====
        await pg.goto(BASE, wait_until="domcontentloaded")
        await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
        await asyncio.sleep(3.0)
        await shot(pg, "A1_first_open.png", "打开应用首面，小满主动打招呼（开场白渲染）")
        await say(pg, "你好呀")
        await shot(pg, "A2_onboarding.png", "发送「你好呀」→ 新用户引导/称呼询问回复")
        await say(pg, "我叫小林，我家猫叫年糕，三岁了特别粘人")
        await shot(pg, "A3_intro_memory.png", "自我介绍（名字+宠物）→ 观察称呼与记忆钩子条")
        await say(pg, "今天上班好累啊，开了三个会")
        await shot(pg, "A4_daily_chat.png", "日常吐槽 → 普通陪伴回复（不应误触抑郁层）")
        try:
            await pg.click("#stage", timeout=5000)
        except Exception as e:
            print("   stage 点击异常:", e, flush=True)
        await shot(pg, "A5_poke.png", "点击 Live2D 舞台（摸头）→ 表情/动作反馈", settle=2.2)
        try:
            await pg.click("#micBtn", timeout=5000)
            await shot(pg, "A6_mic.png", "点击语音按钮 → 语音输入态", settle=1.5)
            await pg.click("#micBtn", timeout=3000)
            await asyncio.sleep(0.8)
        except Exception as e:
            print("   mic 异常(无麦克风环境属预期):", e, flush=True)
            await shot(pg, "A6_mic.png", "语音按钮点击后形态（无麦环境下的降级表现）")
        try:
            await pg.click("#widgetBtn", timeout=5000)
            await shot(pg, "A7_widget_open.png", "点击悬浮挂件 → Q 版挂件气泡展开", settle=1.8)
            # v2: 挂件激活态下 widgetBtn 被 stage 拦截层接管——点舞台才是退出
            # （首轮实测点 widgetBtn 3s 超时、气泡常开遮住后续 settingsClose）
            try:
                await pg.click("#stage", timeout=3000)
            except Exception:
                await pg.evaluate("var b=document.getElementById('widgetBubble'); if(b) b.classList.remove('show');")
            try:
                await pg.wait_for_selector("#textInput", state="visible", timeout=6000)
            except Exception:
                await pg.reload(wait_until="domcontentloaded")
                await pg.wait_for_selector("#textInput", state="visible", timeout=10000)
            await shot(pg, "A8_widget_closed.png", "点击舞台退出挂件 → 回到主界面（输入框恢复）", settle=1.0)
        except Exception as e:
            print("   widget 异常:", e, flush=True)

        # ===== B 抽屉三件套 =====
        await say(pg, "提醒我明早八点给年糕喂饭")
        await shot(pg, "B1_reminder_set.png", "发送提醒请求 → 确认回复")
        await robust_click(pg, "#memoryBtn")
        await shot(pg, "B2_memory_drawer.png", "打开记忆抽屉 → 记忆条目+提醒清单+取消按钮", settle=1.5)
        try:
            cancel = await pg.query_selector("#reminderList button")
            if cancel:
                await cancel.click()
                await shot(pg, "B3_reminder_cancel.png", "点击「取消提醒」→ toast 反馈", settle=1.0)
        except Exception as e:
            print("   取消提醒异常:", e, flush=True)
        await robust_click(pg, "#memoryClose")
        await asyncio.sleep(0.8)
        await robust_click(pg, "#settingsBtn")
        await shot(pg, "B4_settings.png", "打开设置面板 → API/TTS 配置项", settle=1.2)
        await robust_click(pg, "#settingsClose")
        await asyncio.sleep(0.8)
        await robust_click(pg, "#cardBtn")
        await shot(pg, "B5_friend_card.png", "打开友谊卡片 → 手绘风卡片渲染", settle=2.5)
        await robust_click(pg, "#cardClose", timeout=3000)
        await asyncio.sleep(0.8)

        # ===== C 关系与记忆 =====
        await say(pg, "我们认识多久了")
        await shot(pg, "C1_relation_days.png", "关系问答「认识多久」→ 相识天数播报")
        await say(pg, "你猜年糕现在在干嘛")
        await shot(pg, "C2_memory_recall.png", "记忆回访「猜年糕在干嘛」→ 宠物记忆召回")

        # ===== D 危机安全 =====
        await say(pg, "我不想活了")
        await shot(pg, "D1_crisis_care.png", "危机词 → 关怀卡（AI 边界+12356 转介）", settle=3.5)

        # ===== E 隔天再访 =====
        await pg.evaluate(
            "() => {"
            "  try { const k='xiaoman_heartbeat_state'; const s=JSON.parse(localStorage.getItem(k)||'{}');"
            "    s.last_ts = Date.now()-32*3600*1000; localStorage.setItem(k, JSON.stringify(s)); } catch(e){}"
            "  location.reload(); }"
        )
        await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
        await asyncio.sleep(4.0)
        await shot(pg, "E1_return_heartbeat.png", "模拟 32h 后再访 → 心跳情境化问候")

        # ===== F 悬浮窗迷你模式 =====
        await pg.goto(BASE + "/?float=1", wait_until="domcontentloaded")
        await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
        await asyncio.sleep(3.0)
        await shot(pg, "F1_float_mode.png", "悬浮窗迷你模式（?float=1）→ 迷你对话窗形态")
        await say(pg, "在迷你窗里也能聊天吗")
        await shot(pg, "F2_float_chat.png", "迷你窗内发消息 → 复用完整对话引擎")

        # ===== G 清空记忆 =====
        await pg.goto(BASE, wait_until="domcontentloaded")
        await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
        await asyncio.sleep(2.0)
        try:
            await pg.click("#memoryBtn", timeout=5000)
            await asyncio.sleep(1.0)
            await pg.click("#memoryClear", timeout=5000)
        except Exception as e:
            print("   清空记忆点击异常:", e, flush=True)
        await shot(pg, "G1_memory_clear.png", "点击「清空记忆」→ toast + 记忆清空（抽屉空态）", settle=1.2)

        await ctx.close()
        await b.close()

    mf = os.path.join(SHOTS, "manifest.txt")
    with open(mf, "w", encoding="utf-8") as f:
        f.write("UI 全旅程操作截图清单\n====================\n\n")
        for fname, desc in MANIFEST:
            f.write(f"{fname}\n    操作: {desc}\n\n")
        f.write(f"console/page errors: {len(ERRORS)}\n")
        for e in ERRORS[:10]:
            f.write(f"  - {e[:200]}\n")
    print(f"\n✅ {len(MANIFEST)} 张截图 → {SHOTS}")
    print(f"   manifest → {mf}")
    print(f"   console errors={len(ERRORS)}")

if __name__ == "__main__":
    asyncio.run(main())
