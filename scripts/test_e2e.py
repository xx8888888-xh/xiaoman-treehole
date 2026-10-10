#!/usr/bin/env python3
"""端到端实测：真实聊天链路 + 语音请求落点 + 危机拦截 + 截图

退出码：任一有断言的检查点 FAIL → 1；全部通过 → 0。
端口/主机来自 scripts/env.sh（可用环境变量 XIAOMAN_* 覆盖）；截图路径基于脚本位置。
"""
import asyncio
import os
import subprocess
import sys
import time
import urllib.request

from playwright.async_api import async_playwright


def _load_env():
    """端口/主机集中配置唯一真源：scripts/env.sh（经 bash source 读取）"""
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c",
             f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_WEB_PORT|$XIAOMAN_MOCK_PORT|$XIAOMAN_TTS_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return out[0], int(out[1]), int(out[2]), int(out[3])
    except Exception:
        return "127.0.0.1", 8901, 8902, 8903


HOST, WEB_PORT, MOCK_PORT, TTS_PORT = _load_env()
BASE = f"http://{HOST}:{WEB_PORT}/index.html"
TTS_ORIGIN = f"{HOST}:{TTS_PORT}"
SHOTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "shots"))


def wait_ready(url, timeout=15):
    """就绪探测：等待服务返回 200"""
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


async def wait_contains(pg, sel, needle, timeout=10):
    """轮询等待某选择器文本包含 needle（替代盲目 sleep）"""
    end = time.time() + timeout
    txt = ""
    while time.time() < end:
        try:
            txt = await pg.inner_text(sel)
        except Exception:
            txt = ""
        if needle in txt:
            return txt
        await asyncio.sleep(0.5)
    return txt


async def mem_texts(pg):
    """安全读取页面 MemoryStore（缺失/改名返回 None，不抛异常）"""
    try:
        return await pg.evaluate(
            "JSON.stringify((window.MemoryStore ? MemoryStore.all() : []).map(m=>m.text))")
    except Exception:
        return None


async def wait_mem_contains(pg, needle, timeout=15):
    """轮询等待 MemoryStore 落库包含 needle（避免误匹配用户回显文本）"""
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        last = await mem_texts(pg)
        if last and needle in last:
            return last
        await asyncio.sleep(0.5)
    return last


async def main():
    os.makedirs(SHOTS, exist_ok=True)
    if not wait_ready(f"http://{HOST}:{WEB_PORT}/index.html"):
        print(f"❌ 前端服务 ({WEB_PORT}) 未就绪，请先运行 scripts/dev_up.sh", flush=True)
        return 1

    tts_hits, bad, errors = [], [], []
    results = []  # (检查点, 是否通过)

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
            pg.on("request", lambda r: tts_hits.append(r.url) if "/tts?" in r.url else None)
            pg.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)

            await pg.goto(BASE, wait_until="domcontentloaded")
            await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
            print("① 页面加载:", await pg.title())

            # —— 真实对话：工作吐槽 ——
            await pg.fill("#textInput", "我今天加班到十点老板还骂我")
            await pg.click("#sendBtn")
            await pg.wait_for_selector("#messages .msg-row.them", state="attached", timeout=15000)
            await asyncio.sleep(3)
            msgs = await pg.inner_text("#messages")
            n_them = await pg.evaluate("document.querySelectorAll('#messages .msg-row.them').length")
            ok2 = n_them >= 1 and len(msgs.strip()) > 8
            print(f"② 对话回复: {'PASS' if ok2 else 'FAIL'} (小满消息数={n_them}, 含安慰词={any(k in msgs for k in ('骂','累','委屈'))})")
            check("② 对话回复", ok2)

            # —— 语音请求落点（必须是 TTS 端口） ——
            print("③ TTS 请求数:", len(tts_hits))
            for u in tts_hits[:3]:
                print("    ->", u[:95])
            ports = {u.split("//")[1].split("/")[0] for u in tts_hits}
            ok4 = ports == {TTS_ORIGIN}
            print("④ TTS 端口:", "PASS " + str(sorted(ports)) if ok4 else "FAIL " + str(sorted(ports)))
            check("④ TTS 端口", ok4)

            await pg.screenshot(path=os.path.join(SHOTS, "09_e2e_chat.png"))

            # —— 语音是否真的产出音频帧（用 fetch 直测同一 URL） ——
            audio = await pg.evaluate("""
              async (origin) => {
                const r = await fetch('http://' + origin + '/tts?text=' + encodeURIComponent('我在听'), {method:'GET'});
                const buf = await r.arrayBuffer();
                return {status: r.status, bytes: buf.byteLength, type: r.headers.get('content-type')};
              }
            """, TTS_ORIGIN)
            print("⑤ 音频直测:", audio)

            # —— 危机拦截 ——
            await pg.fill("#textInput", "我不想活了")
            await pg.click("#sendBtn")
            msgs2 = await wait_contains(pg, "#messages", "12356", timeout=12)
            ok6 = "12356" in msgs2
            print("⑥ 危机热线 12356:", "PASS" if ok6 else "FAIL")
            check("⑥ 危机热线 12356", ok6)
            await pg.screenshot(path=os.path.join(SHOTS, "10_e2e_crisis.png"))

            # —— 记忆写入 + 抽屉 ——
            await pg.fill("#textInput", "我叫阿秋，我养了一只橘猫叫团子")
            await pg.click("#sendBtn")
            mems = await wait_mem_contains(pg, "阿秋", timeout=15)
            print("⑦ 记忆:", mems if mems is not None else "MemoryStore 不可用")

            # —— ⑦b 泛指不覆盖具体（P0-2 遗留守卫：前端判定单测 + 端到端不劣化） ——
            gp = await pg.evaluate(
                "() => [__genericPet('那只猫'), __genericPet('橘猫'), "
                "__genericPet('一只狗'), __genericPet('英短猫')]")
            ok7b1 = gp == [True, False, True, False]
            print("⑦b-1 前端泛指判定:", "PASS " + str(gp) if ok7b1 else "FAIL " + str(gp))
            check("⑦b-1 前端泛指判定", ok7b1)

            n_before = await pg.evaluate("document.querySelectorAll('#messages .msg-row').length")
            await pg.fill("#textInput", "我家那只猫拆家了")
            await pg.click("#sendBtn")
            stable, last_n = 0, n_before
            for _ in range(30):   # 等回复渲染稳定（全部分条+打字动画结束后才走记忆合并）
                await asyncio.sleep(0.5)
                n_now = await pg.evaluate("document.querySelectorAll('#messages .msg-row').length")
                stable = stable + 1 if n_now == last_n else 0
                last_n = n_now
                if n_now > n_before + 1 and stable >= 3:
                    break
            m = await pg.evaluate("() => JSON.parse(localStorage.getItem('xiaoman_memory') || '{}')")
            ok7b2 = m.get("宠物") == "橘猫"
            print("⑦b-2 端到端记忆不劣化:", "PASS 宠物=橘猫" if ok7b2 else f"FAIL 宠物={m.get('宠物')!r}")
            check("⑦b-2 端到端记忆不劣化", ok7b2)

            # —— ⑦c 老板姓名守卫（P0-2 遗留守卫 II：前端判定单测 + 端到端垃圾句不劣化） ——
            bo = await pg.evaluate(
                "() => [__bossNameOk('张三'), __bossNameOk('又骂我'), "
                "__bossNameOk('老王'), __bossNameOk('老折腾我'), "
                "__bossNameOk('王总'), __bossNameOk('今天心情')]")
            ok7c1 = bo == [True, False, True, False, True, False]
            print("⑦c-1 前端姓名判定:", "PASS " + str(bo) if ok7c1 else "FAIL " + str(bo))
            check("⑦c-1 前端姓名判定", ok7c1)

            await pg.fill("#textInput", "我们老板张三")
            await pg.click("#sendBtn")
            await wait_mem_contains(pg, "张三", timeout=15)
            await pg.fill("#textInput", "我们老板又骂我")
            await pg.click("#sendBtn")
            stable, last_n2, n_base = 0, 0, await pg.evaluate(
                "document.querySelectorAll('#messages .msg-row').length")
            for _ in range(30):
                await asyncio.sleep(0.5)
                n_now = await pg.evaluate("document.querySelectorAll('#messages .msg-row').length")
                stable = stable + 1 if n_now == last_n2 else 0
                last_n2 = n_now
                if n_now > n_base + 1 and stable >= 3:
                    break
            m = await pg.evaluate("() => JSON.parse(localStorage.getItem('xiaoman_memory') || '{}')")
            ok7c2 = m.get("老板") == "张三"
            print("⑦c-2 端到端姓名不劣化:", "PASS 老板=张三" if ok7c2 else f"FAIL 老板={m.get('老板')!r}")
            check("⑦c-2 端到端姓名不劣化", ok7c2)

            await pg.click("#memoryBtn")
            await asyncio.sleep(1)
            drawer = await pg.inner_text("#memoryDrawer")
            ok8 = "阿秋" in drawer
            print("⑧ 抽屉含阿秋:", "PASS" if ok8 else "FAIL")
            check("⑧ 抽屉含阿秋", ok8)
            await pg.screenshot(path=os.path.join(SHOTS, "11_e2e_memory_drawer.png"))

            # —— 清空记忆（验证修复：新库也要清空） ——
            await pg.click("#memoryClear")
            await asyncio.sleep(1)
            left = await mem_texts(pg)
            ok9 = left in ("[]", "")
            print("⑨ 清空记忆后剩余:", left, "->", "PASS" if ok9 else "FAIL")
            check("⑨ 清空记忆", ok9)

            print("⑩ 控制台错误:", errors if errors else "无")
            print("⑪ HTTP>=400:", [x for x in bad if "favicon" not in x[1]] or "无")
        finally:
            await b.close()

    failed = [n for n, ok in results if not ok]
    print(f"[SUMMARY] 检查点 {len(results) - len(failed)}/{len(results)} 通过"
          + (f"，FAIL: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
