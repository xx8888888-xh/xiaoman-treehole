#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单条消息删除 / 编辑重发 / 统计清空 的界面实测（W16）

覆盖：
  1) 悬停出现"⋯"，菜单动态生成（改 index.html 之外的 DOM）
  2) 删除：二次确认 → 气泡消失、计数回退、记忆引用清掉、本机库/服务端同步、
     紧随其后的回复一并清掉（不留"无主答案"）
  3) 编辑后重发：原文就地更新、旧回复整串清掉、按新文案重新回一条、服务端同步
  4) 统计清空：二次确认、清空后 localStorage 与界面都归零，一次点击不会误清
用法：python scripts/test_msg_edit.py（端口同 scripts/env.sh，可 XIAOMAN_* 覆盖）
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

from playwright.async_api import async_playwright


def _load_env():
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c",
             f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_WEB_PORT|$XIAOMAN_MOCK_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        parts = (out + "|||").split("|")
        return ((os.environ.get("XIAOMAN_HOST") or parts[0] or "127.0.0.1").strip(),
                int(os.environ.get("XIAOMAN_WEB_PORT") or parts[1] or 8901),
                int(os.environ.get("XIAOMAN_MOCK_PORT") or parts[2] or 8902))
    except Exception:
        return ("127.0.0.1", int(os.environ.get("XIAOMAN_WEB_PORT", 8901)),
                int(os.environ.get("XIAOMAN_MOCK_PORT", 8902)))


HOST, WEB_PORT, MOCK_PORT = _load_env()
ORIGIN = f"http://{HOST}:{WEB_PORT}"
BASE = f"{ORIGIN}/index.html"
MOCK = f"http://{HOST}:{MOCK_PORT}"
CFG = {"mode": "mock", "mockBase": MOCK, "tts": "off"}

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    return ok


def api(path):
    with urllib.request.urlopen(MOCK + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


async def wait_for(fn, timeout=12, step=0.35):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if await fn():
                return True
        except Exception:
            pass
        await asyncio.sleep(step)
    return False


async def settle(pg, timeout=20):
    """等聊天流稳定：连续两次读到的本机消息条数不变"""
    prev = -1
    end = time.time() + timeout
    while time.time() < end:
        n = await pg.evaluate("ChatStore.count(localStorage.getItem('xiaoman_sid'))")
        if n == prev:
            return n
        prev = n
        await pg.wait_for_timeout(400)
    return prev


async def store_msgs(pg):
    return await pg.evaluate(
        "ChatStore.list(localStorage.getItem('xiaoman_sid'), 100)"
        ".then(rs => rs.map(r => ({role: r.role, content: r.content, at: r.at})) )")


async def send_and_wait(pg, text, want_them=1):
    before = await pg.evaluate("document.querySelectorAll('.msg-row.them').length")
    await pg.fill("#textInput", text)
    await pg.click("#sendBtn")
    await wait_for(lambda: pg.evaluate(
        f"document.querySelectorAll('.msg-row.them').length >= {before + want_them}"), timeout=15)
    await settle(pg)


async def reply_texts_after_last_me(pg):
    """取最后一条"我"消息之后的所有回复文案（本机记录为准，避开 DOM 的 ⋯ 按钮）"""
    msgs = await store_msgs(pg)
    idx = -1
    for i, m in enumerate(msgs):
        if m["role"] == "user":
            idx = i
    return [m["content"] for m in msgs[idx + 1:] if m["role"] == "assistant"]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 430, "height": 900})
        pg = await ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(BASE)
        await pg.evaluate("cfg => localStorage.setItem('xiaoman_cfg', JSON.stringify(cfg))", CFG)
        await pg.reload()
        await pg.wait_for_timeout(900)
        sid = await pg.evaluate("localStorage.getItem('xiaoman_sid')")

        # 预置一条老记忆：删除消息时它应该被一起清掉
        await pg.evaluate("() => localStorage.setItem('xiaoman_memory', JSON.stringify({'状态':'很累'}))")

        # ---- 删除 ----
        deleted_text = "哈哈哈 我今天很累"
        await send_and_wait(pg, deleted_text)
        stats_before = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}').msgs")
        check("发送后有计数", stats_before == 1, str(stats_before))
        old_replies = await reply_texts_after_last_me(pg)

        me_row = pg.locator(".msg-row.me").last
        await me_row.hover()
        dot = me_row.locator(".msg-more")
        opacity = await dot.evaluate("el => getComputedStyle(el).opacity")
        check("悬停显示消息操作按钮（动态 DOM）", float(opacity) > 0, opacity)
        await dot.click(force=True)
        menu = pg.locator(".msg-menu")
        await menu.wait_for(state="visible", timeout=3000)
        check("菜单是动态生成的（DOM 里没有静态节点）",
              await pg.evaluate("document.querySelectorAll('.msg-menu').length") == 1)
        items = await menu.locator("button").all_inner_texts()
        check("菜单含删除/编辑", "删除这条" in items and "编辑后重发" in items, str(items))

        await menu.get_by_text("删除这条").click()
        armed = await menu.get_by_text("再点一次删除").count()
        check("删除需二次确认", armed == 1)
        stats_still = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}').msgs")
        check("第一次点击不删（防误触）", stats_still == 1, str(stats_still))
        await menu.get_by_text("再点一次删除").click()
        await settle(pg)

        rows_after = await pg.evaluate("document.querySelectorAll('.msg-row.me').length")
        check("气泡已从界面消失", rows_after == 0, str(rows_after))
        stats_after = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}')")
        check("计数已同步回退", stats_after.get("msgs") == 0, json.dumps(stats_after, ensure_ascii=False))
        mem_after = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_memory')||'{}')")
        check("相关记忆引用被清掉", mem_after == {}, json.dumps(mem_after, ensure_ascii=False))

        msgs_now = await store_msgs(pg)
        contents = [m["content"] for m in msgs_now]
        check("本机记录里也没有这条（无幽灵节点）", deleted_text not in contents, str(len(contents)))
        check("旧回复整串清掉（不留无主答案）",
              all(t not in contents for t in old_replies), str(old_replies))

        srv_after = await asyncio.to_thread(api, f"/api/session/export?session_id={sid}")
        srv_contents = [m.get("content") for m in (srv_after.get("messages") or [])]
        check("服务端历史同步删除", deleted_text not in srv_contents, str(len(srv_contents)))
        check("服务端记忆同步清掉", "很累" not in json.dumps(
            srv_after.get("state", {}).get("memories", {}), ensure_ascii=False),
            json.dumps(srv_after.get("state", {}).get("memories"), ensure_ascii=False))

        # 界面与本机记录一致（不存在"界面删了、库里还留着"的幽灵）
        dom_them = await pg.evaluate("document.querySelectorAll('.msg-row.them').length")
        db_them = len([m for m in msgs_now if m["role"] == "assistant"])
        check("界面与本机记录条数一致", dom_them == db_them, f"{dom_them}/{db_them}")

        # ---- 编辑后重发 ----
        await send_and_wait(pg, "今天吃了面")
        old_reply = await reply_texts_after_last_me(pg)
        check("编辑前有回复可清理", len(old_reply) >= 1, str(old_reply))

        me_row = pg.locator(".msg-row.me").last
        await me_row.hover()
        await me_row.locator(".msg-more").click(force=True)
        await pg.locator(".msg-menu").get_by_text("编辑后重发").click()
        editor = pg.locator(".msg-edit-area")
        await editor.wait_for(state="visible", timeout=3000)
        check("编辑弹层带原文本", await editor.input_value() == "今天吃了面", await editor.input_value())
        await editor.fill("我今天好累")
        await pg.get_by_text("保存并重发").click()
        await wait_for(lambda: pg.evaluate(
            "JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}').msgs === 1"), timeout=15)
        await settle(pg)

        me_text = await pg.locator(".msg-row.me").last.inner_text()
        check("气泡文本已更新", "我今天好累" in me_text, me_text[:40])
        msgs_edit = await store_msgs(pg)
        db_msg = [m["content"] for m in msgs_edit]
        check("本机存储已更新", "我今天好累" in db_msg and "今天吃了面" not in db_msg, str(db_msg))
        check("旧回复没留下（整串已清）", all(t not in db_msg for t in old_reply),
              f"{old_reply} -> {db_msg}")
        srv_edit = await asyncio.to_thread(api, f"/api/session/export?session_id={sid}")
        srv_contents = [m.get("content") for m in (srv_edit.get("messages") or [])]
        check("服务端历史已更新", "我今天好累" in srv_contents and "今天吃了面" not in srv_contents,
              str(len(srv_contents)))
        dom_them = await pg.evaluate("document.querySelectorAll('.msg-row.them').length")
        db_them = len([m for m in msgs_edit if m["role"] == "assistant"])
        check("重发后界面与本机记录一致", dom_them == db_them, f"{dom_them}/{db_them}")
        stats_edit = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}').msgs")
        check("编辑重发不重复计数", stats_edit == 1, str(stats_edit))

        # ---- 统计清空 ----
        await pg.click("#memoryBtn")
        await pg.wait_for_selector("#statsClear", state="visible", timeout=5000)
        before_clear = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}').msgs")
        await pg.click("#statsClear")
        await pg.wait_for_timeout(250)
        armed_label = await pg.evaluate("document.getElementById('statsClear').textContent")
        still = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}').msgs")
        check("清空需二次确认（第一次只变文案）", "确认清空" in armed_label and still == before_clear,
              f"{armed_label} / {still}")
        await pg.click("#statsClear")
        await pg.wait_for_timeout(400)
        cleared = await pg.evaluate("localStorage.getItem('xiaoman_stats_v1')")
        check("二次确认后统计清空", cleared is None or json.loads(cleared).get("msgs") == 0,
              cleared or "null")
        blank = await pg.evaluate("document.getElementById('statsBox').innerText.length")
        check("清空后页面状态同步刷新", blank > 0, str(blank))
        await pg.click("#memoryClose")

        check("无 JS 运行时错误", not errors, "; ".join(errors[:3]))
        await browser.close()

    ok = all(c[1] for c in CHECKS)
    print(f"\nMSG-EDIT {sum(1 for c in CHECKS if c[1])}/{len(CHECKS)} 通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
