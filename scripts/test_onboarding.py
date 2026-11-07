#!/usr/bin/env python3
"""P0-1 首日引导钉子户铺设 · 端到端验收（路线图 docs/PRODUCT_ROADMAP.md）

验收标准（路线图原文）：
  ① 新会话 10 轮内钉子户记忆 ≥3 条（称呼/大事/宠物——不搞表单，聊着聊着就存了）
  ② 再见面开场能主动引用其中 1 条（大事优先）

前置：scripts/dev_up.sh（前端 8901 + mock 8902；TTS 不参与本用例）
退出码：任一检查点 FAIL → 1；全部通过 → 0。
"""
import asyncio
import os
import subprocess
import sys
import time
import urllib.request

from browser_guard import require_browser_allowed

require_browser_allowed(__file__)

from playwright.async_api import async_playwright

from ci_artifacts import write_result


def _load_env():
    """端口/主机集中配置唯一真源：scripts/env.sh（经 bash source 读取）"""
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c",
             f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_WEB_PORT|$XIAOMAN_MOCK_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return (os.environ.get("XIAOMAN_HOST") or out[0],
                int(os.environ.get("XIAOMAN_WEB_PORT") or out[1]),
                int(os.environ.get("XIAOMAN_MOCK_PORT") or out[2]))
    except Exception:
        return (os.environ.get("XIAOMAN_HOST", "127.0.0.1"),
                int(os.environ.get("XIAOMAN_WEB_PORT", 8901)),
                int(os.environ.get("XIAOMAN_MOCK_PORT", 8902)))


HOST, WEB_PORT, MOCK_PORT = _load_env()
BASE = f"http://{HOST}:{WEB_PORT}/index.html"
SHOTS = os.environ.get("XIAOMAN_SHOTS_DIR") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "shots"))
MAX_TURNS = 10  # 验收口径：新会话 10 轮内


def wait_ready(url, timeout=15):
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


async def wait_them_count(pg, n, timeout=20):
    """等待 .them 消息数达到 n（greet/回复都可能分多条，只增不减）"""
    end = time.time() + timeout
    while time.time() < end:
        cnt = await pg.evaluate("document.querySelectorAll('#messages .msg-row.them').length")
        if cnt >= n:
            return cnt
        await asyncio.sleep(0.5)
    return cnt


async def mem_dump(pg):
    try:
        return await pg.evaluate(
            "JSON.stringify(window.MemoryStore ? MemoryStore.all() : [])")
    except Exception:
        return "[]"


async def wait_mem_contains(pg, needle, timeout=15):
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        last = await mem_dump(pg)
        if needle in last:
            return True, last
        await asyncio.sleep(0.5)
    return False, last


async def wait_hook(pg, needle, timeout=15):
    """等待 hook 芯片文本包含 needle（首日引导链的确定性话术）"""
    end = time.time() + timeout
    txt = ""
    while time.time() < end:
        try:
            txt = await pg.inner_text("#hookBar")
        except Exception:
            txt = ""
        if needle in txt:
            return True, txt
        await asyncio.sleep(0.5)
    return False, txt


async def send(pg, text):
    await pg.fill("#textInput", text)
    await pg.click("#sendBtn")


async def main():
    os.makedirs(SHOTS, exist_ok=True)
    if not wait_ready(f"http://{HOST}:{WEB_PORT}/index.html"):
        print(f"❌ 前端服务 ({WEB_PORT}) 未就绪，请先运行 scripts/dev_up.sh", flush=True)
        return 1

    errors, bad = [], []
    console_logs = []   # ⑧ 复诊用：开场是否真的被调用/播放（[probe] 前缀）
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))
        return ok

    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True, channel="chromium",
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                  "--autoplay-policy=no-user-gesture-required"])
        try:
            pg = await b.new_page(viewport={"width": 390, "height": 844})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: console_logs.append(m.text))
            pg.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)

            # ⓪ 全新身份：清 localStorage（xiaoman_sid / 记忆库全清）→ reload
            await pg.goto(BASE, wait_until="domcontentloaded")
            await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
            await pg.evaluate("localStorage.clear()")
            await pg.reload(wait_until="domcontentloaded")
            await pg.wait_for_selector("#textInput", state="visible", timeout=15000)

            # ① 首访 greet：无昵称引用（首日尚未铺设）
            await wait_them_count(pg, 1, 12)
            msgs = await pg.inner_text("#messages")
            ok1 = "阿秋" not in msgs and len(msgs.strip()) > 4
            print(f"① 首访 greet 无引用: {'PASS' if ok1 else 'FAIL'} | {msgs.strip()[:30]!r}")
            check("① 首访 greet 无引用", ok1)

            # ② 首轮对话 → 称呼引导 hook（确定性，非随机）
            base_them = await pg.evaluate("document.querySelectorAll('#messages .msg-row.them').length")
            await send(pg, "在吗，随便聊聊")
            await wait_them_count(pg, base_them + 1, 20)
            ok2, hook_txt = await wait_hook(pg, "称呼")
            print(f"② 称呼引导 hook: {'PASS' if ok2 else 'FAIL'} | {hook_txt.strip()[:36]!r}")
            check("② 称呼引导 hook", ok2)

            # ③ 用户给称呼 → 钉子户①（昵称，清洗"就行"）
            await send(pg, "叫我阿秋就行")
            ok3, dump = await wait_mem_contains(pg, "昵称：阿秋")
            print(f"③ 昵称落库(清洗'就行'): {'PASS' if ok3 else 'FAIL'} | {dump[:80]!r}")
            check("③ 昵称落库", ok3)

            # ④ 下一轮 → 大事引导 hook
            await send(pg, "嗯，今天过得还行")
            ok4, hook_txt = await wait_hook(pg, "大事")
            print(f"④ 大事引导 hook: {'PASS' if ok4 else 'FAIL'} | {hook_txt.strip()[:36]!r}")
            check("④ 大事引导 hook", ok4)

            # ⑤ 用户说大事 → 钉子户②（大事，清洗"啦"）
            await send(pg, "我下周要考试啦")
            ok5, _ = await wait_mem_contains(pg, "大事：下周要考试")
            print(f"⑤ 大事落库(清洗'啦'): {'PASS' if ok5 else 'FAIL'}")
            check("⑤ 大事落库", ok5)

            # ⑥ 自然聊到宠物 → 钉子户③（宠物）
            await send(pg, "我家养了一只猫，叫团子")
            ok6, _ = await wait_mem_contains(pg, "宠物：猫")
            print(f"⑥ 宠物落库: {'PASS' if ok6 else 'FAIL'}")
            check("⑥ 宠物落库", ok6)

            # ⑦ 验收口径①：10 轮内钉子户（kind=pin）≥3 条
            turns_used = 5  # ②③④⑤⑥ 共 5 轮用户消息（含引导应答）；第 5 轮铺满第 3 个钉子户
            pins = await pg.evaluate(
                "JSON.stringify((MemoryStore.all()||[]).filter(m=>m.kind==='pin').map(m=>m.text))")
            import json as _json
            pin_list = _json.loads(pins)
            ok7 = len(pin_list) >= 3 and turns_used <= MAX_TURNS
            print(f"⑦ 钉子户≥3条({len(pin_list)}, 轮次{turns_used}≤{MAX_TURNS}): {'PASS' if ok7 else 'FAIL'} | {pin_list}")
            check("⑦ 钉子户≥3条", ok7)

            # ⑧ 再见面（reload，localStorage 保留=同一个身份）→ greet 主动引用大事+称呼
            # "再见面"指跨天首次进站：f951149 起开场按本地日历日判定（web/js/greet_day.js），
            # 同日重入按设计只播"话没丢"，不播时段问候/称呼/大事引用；故把 greet_day 回拨到昨天，
            # 还原真实的隔天再访场景（断言不变）。
            # greet 分条发送带"真实延迟"动画，轮询等待而非固定 sleep
            await pg.evaluate(
                "() => localStorage.setItem('xiaoman_greet_day', GreetDay.dayKey(Date.now()-86400000))")
            console_logs.append("=== reload-for-greet ===")
            await pg.reload(wait_until="domcontentloaded")
            await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
            end = time.time() + 15
            msgs2 = ""
            while time.time() < end:
                msgs2 = await pg.inner_text("#messages")
                # 等开场引用真正落屏（app.js: "对了，你上次说${大事}——怎么样啦？"）。
                # 旧口径只等"上次"二字：聊天回复里也会出现"上次"，恢复历史后立刻命中，
                # 开场根本没播也会误判 PASS（main 上曾就此假绿）。
                if "对了，你上次说" in msgs2:
                    break
                await asyncio.sleep(0.5)
            ok8 = ("阿秋" in msgs2) and ("考试" in msgs2) and ("对了，你上次说" in msgs2)
            print(f"⑧ 再访 greet 引用(称呼+大事): {'PASS' if ok8 else 'FAIL'} | {msgs2.strip()[-70:]!r}")
            if not ok8:
                # 复诊证据：开场是否被调用/播放，以及 greet_day / profile 的实际值
                since = ""
                for line in console_logs:
                    if line == "=== reload-for-greet ===":
                        since = ""
                        continue
                    if any(k in line for k in ("[probe] greet", "GREET_FAIL", "HISTORY", "PAGER_FAIL",
                                               "history restored", "INIT_FAIL", "BIND_FAIL")):
                        since += line + " | "
                snap = await pg.evaluate(
                    "() => JSON.stringify({greet_day: localStorage.getItem('xiaoman_greet_day'),"
                    " today: GreetDay.dayKey(Date.now()),"
                    " mem: JSON.parse(localStorage.getItem('xiaoman_memory')||'{}'),"
                    " pending: JSON.parse(localStorage.getItem('xiaoman_pending')||'[]').length})")
                print(f"⑧ 复诊: console={since[:200]!r} state={snap[:200]!r}")
            check("⑧ 再访 greet 引用", ok8)
            await pg.screenshot(path=os.path.join(SHOTS, "12_p01_onboarding_greet.png"))

            # ⑨ 工程健康：零 JS 错误 + 无非 favicon 的 HTTP≥400
            ok9 = not errors and not [x for x in bad if "favicon" not in x[1]]
            print(f"⑨ 零JS错误/无HTTP≥400: {'PASS' if ok9 else 'FAIL'} | err={errors[:2]} bad={bad[:2]}")
            check("⑨ 工程健康", ok9)

        finally:
            await b.close()

    failed = [n for n, ok in results if not ok]
    print(f"[SUMMARY] 检查点 {len(results) - len(failed)}/{len(results)} 通过"
          + (f"，FAIL: {failed}" if failed else ""))
    for name, ok in results:
        if not ok:
            print(f"FAIL {name} · 见上方同名检查点的实时输出", flush=True)
    write_result("test_onboarding", results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
