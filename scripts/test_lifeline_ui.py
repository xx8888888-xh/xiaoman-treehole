#!/usr/bin/env python3
"""端到端验收：记忆/提醒/心跳 三系统 UI 集成

退出码：凡有断言的步骤任一 FAIL → 1；全过 → 0。
（第 5 步为纯视觉注入截图、CONSOLE_ERRORS 为信息项，不计入失败。）
端口/主机来自 scripts/env.sh；截图路径基于脚本位置（与 cwd 无关）。
"""
import asyncio
import os
import subprocess
import sys

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


_HOST, _WEB_PORT, _MOCK_PORT, _TTS_PORT = _load_env()
URL = f"http://{_HOST}:{_WEB_PORT}/index.html"
SHOTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "shots"))


async def main():
    results = []  # (步骤名, 是否通过)

    def check(name, ok):
        results.append((name, bool(ok)))
        return ok

    os.makedirs(SHOTS, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, channel="chromium", args=["--no-sandbox", "--disable-dev-shm-usage", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        try:
            pg = await b.new_page(viewport={"width": 390, "height": 844})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            await pg.goto(URL, wait_until="domcontentloaded")
            await pg.wait_for_selector("#textInput", state="visible", timeout=15000)
            await asyncio.sleep(2)

            # 1) 设提醒
            await pg.fill("#textInput", "明天早上八点提醒我吃药")
            await pg.click("#sendBtn")
            await asyncio.sleep(3)
            tip = await pg.inner_text("#messages")
            ok1 = "提醒" in tip and ("08:00" in tip or "8:00" in tip)
            print("REMINDER_SET:", "PASS" if ok1 else "FAIL", "|", [l for l in tip.split(chr(10)) if "喊你" in l or "提" in l][:1])
            check("REMINDER_SET", ok1)

            # 2) 记忆写入
            await pg.fill("#textInput", "我叫阿秋，我喜欢喝杨枝甘露")
            await pg.click("#sendBtn")
            await asyncio.sleep(4)
            mems = await pg.evaluate("JSON.stringify(MemoryStore.all().map(m=>m.text))")
            ok2 = "阿秋" in mems
            print("MEMORY_WRITE:", "PASS" if ok2 else "FAIL", "|", mems[:120])
            check("MEMORY_WRITE", ok2)

            # 3) 记忆抽屉（提醒清单+记忆）
            await pg.click("#memoryBtn")
            await asyncio.sleep(1)
            drawer = await pg.inner_text("#memoryDrawer")
            ok3 = "定好的提醒" in drawer and ("吃药" in drawer)
            print("DRAWER_LIST:", "PASS" if ok3 else "FAIL")
            check("DRAWER_LIST", ok3)
            await pg.screenshot(path=os.path.join(SHOTS, "07_memory_reminder_drawer.png"))
            await pg.click("#memoryClose")
            await asyncio.sleep(0.5)

            # 4) 心跳主动消息（debug 直接生成，调用 heartbeat.js 暴露的 debugPing）
            ping = await pg.evaluate("Heartbeat.debugPing()")
            print("HEARTBEAT_GEN:", "PASS" if ping.get("reply") else "FAIL", "|", str(ping.get("reply"))[:60])
            check("HEARTBEAT_GEN", bool(ping.get("reply")))

            # 5) 心跳 UI 渲染（注：本步为视觉截图，手工注入 DOM 展示文案，
            #    非真实 pushFn 链路的功能断言；真实推送链路待前端补测试钩子后再覆盖）
            await pg.evaluate("""
              async (d) => {
                const parts = String(d.reply||"…").split("||");
                for (const seg of parts) {
                  const row = document.createElement("div");
                  row.className = "msg-row them";
                  row.innerHTML = `<div class="bubble">${seg}</div><div class="meta">小满</div>`;
                  document.querySelector("#messages").appendChild(row);
                }
                document.querySelector("#chatScroll").scrollTop = 99999;
              }
            """, ping)
            await asyncio.sleep(1)
            await pg.screenshot(path=os.path.join(SHOTS, "08_heartbeat_ping.png"))
            print("HEARTBEAT_RENDER: 仅视觉截图（非功能断言）")

            # 6) 提醒到期投递验证（把已有提醒时间改到过去，tick 一次）
            #    依赖第 1 步写入成功；若 localStorage 空列表则根因在前一步，明确跳过避免误报
            n_rem = await pg.evaluate("""
              () => {
                const K = "xiaoman_reminders_v1";
                const list = JSON.parse(localStorage.getItem(K) || "[]");
                if (list.length) { list[0].at = Date.now() - 5000; localStorage.setItem(K, JSON.stringify(list)); }
                return list.length;
              }
            """)
            if n_rem == 0:
                print("REMINDER_FIRE: SKIP（因步骤1未写入提醒，无到期数据）")
            else:
                fired = await pg.evaluate("Reminders.due().length")
                print("REMINDER_FIRE:", "PASS" if fired >= 1 else "FAIL")
                check("REMINDER_FIRE", fired >= 1)

            print("CONSOLE_ERRORS:", "NONE" if not errors else errors[:2])
        finally:
            await b.close()

    failed = [n for n, ok in results if not ok]
    print(f"[SUMMARY] 检查点 {len(results) - len(failed)}/{len(results)} 通过"
          + (f"，FAIL: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
