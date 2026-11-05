"""功能闭环回归：设置开关 → 真实行为、提醒 CRUD、小本本、首次引导、错误边界。

对应审计缺口：
A 首次使用：空状态/引导出现，发出第一条后收起，跨会话仍在
B 设置：语音偏好 → TTS 链；免费通道 → 路由；主动问候 → 配置链路
C 提醒：UI 新建/编辑/取消/即将到来/触发后记录（不再只靠对话）
D 小本本：说话后数字增长、刷新后保留、7 天柱状
E 错误边界：429 排队、5xx 降级、localStorage 满、IndexedDB 不可用

全部静音（tts=off），headless + --mute-audio，不占 8901-8903。
"""
import asyncio
import datetime
import json
import os
import subprocess
import sys

from browser_guard import require_browser_allowed

require_browser_allowed(__file__)

from playwright.async_api import async_playwright


def _env():
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c", f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_WEB_PORT|$XIAOMAN_MOCK_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return (os.environ.get("XIAOMAN_HOST") or out[0] or "127.0.0.1",
                int(os.environ.get("XIAOMAN_WEB_PORT") or out[1] or 8901),
                int(os.environ.get("XIAOMAN_MOCK_PORT") or out[2] or 8902))
    except Exception:
        return (os.environ.get("XIAOMAN_HOST", "127.0.0.1"),
                int(os.environ.get("XIAOMAN_WEB_PORT", 8901)),
                int(os.environ.get("XIAOMAN_MOCK_PORT", 8902)))


HOST, PORT, MOCK = _env()
BASE = f"http://{HOST}:{PORT}/index.html"
CFG = {"mode": "mock", "mockBase": f"http://{HOST}:{MOCK}", "tts": "off", "heartbeatOn": True}

results = []
errors = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" · " + str(detail)) if detail else ""))


async def boot(pg):
    await pg.goto(BASE, wait_until="load")
    await pg.wait_for_timeout(900)


async def apply_cfg(pg):
    await pg.evaluate(
        """(cfg) => {
            localStorage.setItem('xiaoman_cfg', JSON.stringify(cfg));
            localStorage.removeItem('xiaoman_reminders_v1');
            localStorage.removeItem('xiaoman_stats_v1');
            localStorage.removeItem('xiaoman_heartbeat_state');
        }""", CFG)
    await pg.reload(wait_until="load")
    await pg.wait_for_timeout(900)


async def open_settings(pg):
    await pg.click("#settingsBtn")
    await pg.wait_for_selector("#settingsModal:not(.hidden)", timeout=8000)


async def close_settings(pg):
    await pg.click("#settingsClose")
    await pg.wait_for_timeout(200)


async def open_drawer(pg):
    await pg.click("#memoryBtn")
    await pg.wait_for_selector("#memoryDrawer:not(.hidden)", timeout=8000)
    await pg.wait_for_timeout(200)


async def close_drawer(pg):
    await pg.click("#memoryClose")
    await pg.wait_for_timeout(200)


async def send(pg, text):
    await pg.fill("#textInput", text)
    await pg.click("#sendBtn")
    await pg.wait_for_timeout(250)


async def send_and_wait(pg, text, timeout=25000):
    them_before = await pg.locator("#messages .msg-row.them").count()
    await send(pg, text)
    await pg.wait_for_function(
        f"() => document.querySelectorAll('#messages .msg-row.them').length > {them_before}",
        timeout=timeout)
    await pg.wait_for_timeout(300)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--mute-audio"])
        ctx = await browser.new_context(viewport={"width": 414, "height": 860})
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))

        # ---------- A 首次使用 ----------
        await boot(pg)
        await pg.wait_for_timeout(800)
        check("新用户空状态出现", await pg.locator("#emptyState").is_visible())
        check("空状态有三个引导入口", await pg.locator(".quick-chip").count() == 3)
        await apply_cfg(pg)
        check("清数据后空状态仍在，不白屏", await pg.locator("#emptyState").is_visible())

        # 点「随便说一句」应聚焦输入框
        await pg.click("#quickSay")
        check("引导入口可聚焦输入框", await pg.evaluate("document.activeElement && document.activeElement.id") == "textInput")
        await pg.click("#quickRemind")
        await pg.wait_for_selector("#memoryDrawer:not(.hidden)", timeout=8000)
        check("引导入口能打开提醒表单", await pg.locator("#reminderText").is_visible())
        await close_drawer(pg)

        # ---------- D 对话 + 小本本 ----------
        await send_and_wait(pg, "今天有点累")
        check("发出第一条后空状态收起", await pg.locator("#emptyState").is_hidden())
        await open_drawer(pg)
        nums = await pg.locator("#statsBox .stat-num").all_text_contents()
        check("小本本：说过的话 ≥1", nums and int(nums[0]) >= 1, nums)
        check("小本本：连续打卡 ≥1", nums and int(nums[1]) >= 1, nums)
        check("小本本：7 天柱状 7 根", await pg.locator("#statsBox .bar-col").count() == 7)
        await close_drawer(pg)

        # ---------- C 提醒 CRUD ----------
        await open_drawer(pg)
        when = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        await pg.fill("#reminderText", "喝水")
        await pg.fill("#reminderAt", when)
        await pg.click("#reminderSave")
        await pg.wait_for_timeout(300)
        items = await pg.locator("#reminderList .reminder-item:not(.done)").count()
        check("提醒：UI 新建成功", items == 1, items)
        check("提醒：文案/时间入清单", "喝水" in (await pg.locator("#reminderList .reminder-text").first.inner_text()))
        stored = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_reminders_v1') || '[]')")
        check("提醒：真实落 localStorage", len(stored) == 1 and stored[0]["text"] == "喝水", stored)

        # 编辑
        await pg.click("#reminderList .r-edit")
        await pg.wait_for_timeout(200)
        check("提醒：编辑态出现保存修改", (await pg.locator("#reminderSave").inner_text()) == "保存修改")
        await pg.fill("#reminderText", "喝热水")
        await pg.click("#reminderSave")
        await pg.wait_for_timeout(300)
        check("提醒：编辑后清单更新", "喝热水" in (await pg.locator("#reminderList .reminder-text").first.inner_text()))

        # 触发后记录（模拟心跳投递成功后的 markDone，UI 要能看到留痕）
        await pg.evaluate(
            """(() => {
                const l = JSON.parse(localStorage.getItem('xiaoman_reminders_v1') || '[]');
                Reminders.markDone([l[0].id]);
            })()""")
        await close_drawer(pg)
        await open_drawer(pg)
        done = await pg.locator("#reminderList .reminder-item.done").count()
        check("提醒：触发后留下「已提醒过」记录", done >= 1, done)
        check("提醒：已提醒记录有清空按钮", await pg.locator("#reminderClearDone").count() == 1)

        # 取消（再建一条后删除）
        when2 = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        await pg.fill("#reminderText", "给他打电话")
        await pg.fill("#reminderAt", when2)
        await pg.click("#reminderSave")
        await pg.wait_for_timeout(300)
        check("提醒：第二条建立", await pg.locator("#reminderList .reminder-item:not(.done)").count() == 1)
        await pg.locator("#reminderList .reminder-item:not(.done) .r-cancel").first.click()
        await pg.wait_for_timeout(300)
        check("提醒：取消后清单为空", await pg.locator("#reminderList .reminder-item:not(.done)").count() == 0)
        check("提醒：已提醒记录不因取消而丢", await pg.locator("#reminderList .reminder-item.done").count() >= 1)
        await close_drawer(pg)

        # ---------- B 设置开关 → 行为 ----------
        await open_settings(pg)
        check("设置：主动问候默认开", await pg.locator("#cfgHeartbeat").is_checked())
        check("设置：免费通道默认关（隐私）", not await pg.locator("#cfgAllowFree").is_checked())

        # 关掉主动问候 → 配置真的变（Heartbeat 每个 tick 现读它）
        await pg.uncheck("#cfgHeartbeat")
        await pg.wait_for_timeout(250)
        check("设置：关主动问候 → cfg 落盘为 false",
              await pg.evaluate("API.loadCfg().heartbeatOn") is False)
        await pg.check("#cfgHeartbeat")   # 还原

        # 免费通道 → 路由规划真的变
        await pg.select_option("#cfgMode", "openai")
        await pg.wait_for_timeout(250)
        route_off = await pg.evaluate("API.routePlan(API.loadCfg(), {native: true})")
        await pg.check("#cfgAllowFree")
        await pg.wait_for_timeout(250)
        route_on = await pg.evaluate("API.routePlan(API.loadCfg(), {native: true})")
        check("设置：免费通道关 → 安卓无在线通道", route_off == [], route_off)
        check("设置：免费通道开 → 路由变为 native", route_on == ["native"], route_on)
        await pg.uncheck("#cfgAllowFree")
        await pg.select_option("#cfgMode", "mock")
        await pg.wait_for_timeout(250)

        # 语音：选择 server（无服务端地址）/ browser / off，链与说明都要跟着变
        await pg.select_option("#cfgTts", "server")
        await pg.wait_for_timeout(250)
        hint_server = await pg.locator("#ttsHint").inner_text()
        chain_server = await pg.evaluate("TTS.chain(API.loadCfg())")
        check("语音：选服务端但未配置地址 → 自动落本机", chain_server[:1] == ["browser"] or chain_server[:1] == ["native"],
              chain_server)
        check("语音：提示文案说明实际后端", "实际发声" in hint_server, hint_server)
        check("语音：此时音色提示为不生效", "不会生效" in (await pg.locator("#voiceHint").inner_text()))

        await pg.select_option("#cfgTts", "browser")
        await pg.wait_for_timeout(250)
        chain_local = await pg.evaluate("TTS.chain(API.loadCfg())")
        check("语音：选本机 → 链首为本机语音", chain_local[:1] == ["browser"], chain_local)

        await pg.select_option("#cfgTts", "off")
        await pg.wait_for_timeout(250)
        check("语音：关闭 → 链为 none", await pg.evaluate("TTS.chain(API.loadCfg())") == ["none"])
        check("语音：关闭文案明确静音", "不会出声" in (await pg.locator("#ttsHint").inner_text()))
        await pg.select_option("#cfgTts", "off")   # 保持静音
        await close_settings(pg)

        # ---------- D 跨会话保留 ----------
        await pg.reload(wait_until="load")
        await pg.wait_for_timeout(1500)
        check("刷新后：已有用户消息 → 空状态不重现", await pg.locator("#emptyState").is_hidden())
        await open_drawer(pg)
        nums2 = await pg.locator("#statsBox .stat-num").all_text_contents()
        check("刷新后：小本本数字保留", nums2 and int(nums2[0]) >= 1, nums2)
        check("刷新后：提醒历史保留", await pg.locator("#reminderList .reminder-item.done").count() >= 1)
        await close_drawer(pg)
        await ctx.close()

        # ---------- E 错误边界：429 ----------
        ctx2 = await browser.new_context(viewport={"width": 414, "height": 860})
        pg2 = await ctx2.new_page()
        pg2.on("pageerror", lambda e: errors.append("429:" + str(e)))
        await boot(pg2)
        await apply_cfg(pg2)

        async def fulfill_429(route):
            await route.fulfill(status=429, content_type="application/json", body='{"error":"rate limit"}')

        await pg2.route("**/api/chat/stream", fulfill_429)
        await send(pg2, "在吗")
        await pg2.wait_for_timeout(2500)
        pending_vis = await pg2.locator("#pendingBar").is_visible()
        pending_txt = await pg2.locator("#pendingText").inner_text()
        check("429：消息入队提示（不假装回复）", pending_vis and "等着回" in pending_txt,
              pending_txt)
        rows_429 = await pg2.locator("#messages .msg-row").count()
        check("429：仍有兜底回复，不白屏", rows_429 >= 2, rows_429)
        await pg2.unroute("**/api/chat/stream")

        # ---------- 5xx：全通道失败 → 离线兜底 + 降级徽标 ----------
        async def fulfill_500(route):
            await route.fulfill(status=500, content_type="application/json", body='{"error":"boom"}')

        for pat in ("**/api/chat/stream", "**/api/chat", "**/v1/chat/completions"):
            await pg2.route(pat, fulfill_500)
        await send(pg2, "今天发生了好多事")
        await pg2.wait_for_timeout(4000)
        check("500：仍给出回复，不卡死", await pg2.locator("#messages .msg-row.them").count() >= 2)
        check("500：降级徽标可见（用户知道不是真模型）",
              await pg2.locator("#degradeBadge").is_visible(),
              await pg2.locator("#degradeText").inner_text())
        for pat in ("**/api/chat/stream", "**/api/chat", "**/v1/chat/completions"):
            await pg2.unroute(pat)
        await ctx2.close()

        # ---------- E 存储满 ----------
        ctx3 = await browser.new_context(viewport={"width": 414, "height": 860})
        pg3 = await ctx3.new_page()
        pg3.on("pageerror", lambda e: errors.append("quota:" + str(e)))
        await pg3.add_init_script(
            """(() => {
                const orig = Storage.prototype.setItem;
                Storage.prototype.setItem = function (k, v) {
                    if (String(k).indexOf('xiaoman_') === 0) {
                        const e = new Error('quota'); e.name = 'QuotaExceededError'; throw e;
                    }
                    return orig.call(this, k, v);
                };
            })()""")
        await boot(pg3)
        await send(pg3, "你好呀")
        await pg3.wait_for_timeout(3500)
        notice = pg3.locator("#noticeBar")
        check("存储满：出现可读提示条", await notice.is_visible(),
              await pg3.locator("#noticeText").inner_text())
        check("存储满：提示里给出行动（备份/清理）",
              "备份" in (await pg3.locator("#noticeText").inner_text()) or
              "空间" in (await pg3.locator("#noticeText").inner_text()))
        check("存储满：聊天没崩", await pg3.locator("#messages .msg-row").count() >= 2)
        await ctx3.close()

        # ---------- IndexedDB 不可用（隐私模式/被清空） ----------
        ctx4 = await browser.new_context(viewport={"width": 414, "height": 860})
        pg4 = await ctx4.new_page()
        pg4.on("pageerror", lambda e: errors.append("idb:" + str(e)))
        await pg4.add_init_script(
            "Object.defineProperty(window, 'indexedDB', {value: undefined, configurable: true});")
        await boot(pg4)
        check("IDB 不可用：提示本机记录不保留", await pg4.locator("#noticeBar").is_visible(),
              await pg4.locator("#noticeText").inner_text())
        check("IDB 不可用：仍能正常聊天输入", await pg4.locator("#textInput").is_visible())
        await ctx4.close()

        await browser.close()

    check("全程零页面 JS 错误", not errors, "; ".join(errors[:3]))
    failed = [r for r in results if not r[1]]
    print(f"\nRESULT {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

