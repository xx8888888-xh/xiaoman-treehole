#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份 round-trip 实测（W16）：导出 → 清空 → 导入 → 逐项核对

核对项：
  1) 提醒（提醒表 + 闹钟）：id / 文案 / 时刻 / 完成态
  2) 小本本统计：msgs / haha / night / 按天计数 / 表情计数
  3) 服务端会话（data/sessions）：记忆槽 state.memories、slots、落盘消息数
  4) 聊天记录：IndexedDB 里的消息条数与原文

"清空"用 CDP Storage.clearDataForOrigin 抹掉 localStorage + IndexedDB，
同时把服务端旧会话清成空——等价于换机/清容器。

端口取自 scripts/env.sh，可用 XIAOMAN_* 覆盖（默认 8901/8902）。
用法：python scripts/test_roundtrip.py
退出码：全部通过 0，否则 1。
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
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        parts = (out + "|||").split("|")
        host = (os.environ.get("XIAOMAN_HOST") or parts[0] or "127.0.0.1").strip()
        web = int(os.environ.get("XIAOMAN_WEB_PORT") or parts[1] or 8901)
        mock = int(os.environ.get("XIAOMAN_MOCK_PORT") or parts[2] or 8902)
        return (host, web, mock)
    except Exception:
        return (os.environ.get("XIAOMAN_HOST", "127.0.0.1"),
                int(os.environ.get("XIAOMAN_WEB_PORT", 8901)),
                int(os.environ.get("XIAOMAN_MOCK_PORT", 8902)))


HOST, WEB_PORT, MOCK_PORT = _load_env()
ORIGIN = f"http://{HOST}:{WEB_PORT}"
BASE = f"{ORIGIN}/index.html"
MOCK = f"http://{HOST}:{MOCK_PORT}"

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    return ok


def api(path, data=None, method="GET"):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(MOCK + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


async def wait_for(fn, timeout=12, step=0.4):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if await fn():
                return True
        except Exception:
            pass
        await asyncio.sleep(step)
    return False


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(accept_downloads=True, viewport={"width": 430, "height": 860})
        pg = await ctx.new_page()
        page_errors = []
        pg.on("pageerror", lambda e: page_errors.append(str(e)))
        await pg.goto(BASE)
        # 指向本次要测的 mock（并关掉 TTS，避免测试期间发声）
        await pg.evaluate(
            "cfg => localStorage.setItem('xiaoman_cfg', JSON.stringify(cfg))",
            {"mode": "mock", "mockBase": MOCK, "tts": "off"},
        )
        await pg.reload()
        await pg.wait_for_timeout(1000)

        sid = await pg.evaluate("localStorage.getItem('xiaoman_sid')")
        check("页面启动并拿到会话 id", bool(sid), sid or "")
        if not sid:
            await browser.close()
            return 1

        # 用服务端接口预置记忆槽与记忆（模拟 w11 已经积累的长期记忆）
        slots = [
            {"id": "rt-1", "key": "宠物", "value": "年糕", "kind": "pet", "emotion": 1,
             "confidence": 0.9, "source": "chat", "created": 1000, "updated": 1001,
             "last_used": 1002, "uses": 3},
            {"id": "rt-2", "key": "昵称", "value": "阿秋", "kind": "identity", "emotion": 0,
             "confidence": 0.8, "source": "chat", "created": 1000, "updated": 1001,
             "last_used": 0, "uses": 1},
        ]
        api("/api/session/import", {
            "session_id": sid,
            "state": {"memories": {"宠物": "年糕", "昵称": "阿秋"}, "turns": 4,
                      "crisis_count": 1, "last_topic": "加班"},
            "messages": [{"role": "user", "content": "先埋一条历史", "at": 1}],
            "slots": {"version": 1, "slots": slots},
        }, "POST")

        # 1) 通过界面加一条提醒（时刻精确到分钟，之后要逐项核对）
        await pg.click("#memoryBtn")
        await pg.wait_for_selector("#reminderText", timeout=5000)
        await pg.fill("#reminderText", "round-trip 喝水")
        at_local = "2026-11-07T08:00"
        await pg.fill("#reminderAt", at_local)
        await pg.click("#reminderSave")
        await pg.wait_for_timeout(400)
        remind_ls = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_reminders_v1')||'[]')")
        check("界面新增提醒落库", len(remind_ls) == 1, json.dumps(remind_ls, ensure_ascii=False)[:120])
        await pg.click("#memoryClose")

        # 2) 发一条消息：统计与聊天记录、服务端落盘都应有数
        await pg.fill("#textInput", "哈哈哈，我在呢")
        await pg.click("#sendBtn")
        ok_reply = await wait_for(lambda: pg.evaluate(
            "document.querySelectorAll('.msg-row.them').length >= 1"), timeout=15)
        check("mock 链路回了一条", ok_reply)

        stats_ls = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}')")
        check("统计计数已记录", stats_ls.get("msgs") == 1 and stats_ls.get("haha", 0) >= 1,
              json.dumps(stats_ls, ensure_ascii=False))

        async def server_ready():
            s = await asyncio.to_thread(api, f"/api/session/export?session_id={sid}")
            return len(s.get("messages") or []) >= 2
        check("服务端已落盘消息", await wait_for(server_ready))

        # 3) 导出
        await pg.click("#settingsBtn")
        await pg.wait_for_selector("#backupExport", state="visible", timeout=5000)
        async with pg.expect_download() as dl_info:
            await pg.click("#backupExport")
        dl = await dl_info.value
        path = await dl.path()
        with open(path, "r", encoding="utf-8") as f:
            backup = json.load(f)
        check("导出 schema v3", backup.get("version") == 3, str(backup.get("version")))
        check("导出含提醒", len(backup.get("reminders") or []) == 1,
              json.dumps(backup.get("reminders"), ensure_ascii=False))
        check("导出含统计", (backup.get("stats") or {}).get("msgs") == 1,
              json.dumps(backup.get("stats"), ensure_ascii=False))
        srv = backup.get("server") or {}
        check("导出含服务端会话+记忆槽", len((srv.get("slots") or {}).get("slots") or []) == 2,
              str(len((srv.get("slots") or {}).get("slots") or [])))
        check("导出含服务端记忆", (srv.get("state") or {}).get("memories", {}).get("宠物") == "年糕",
              json.dumps((srv.get("state") or {}).get("memories"), ensure_ascii=False))
        exported_msgs = len(backup.get("messages") or [])
        check("导出含聊天记录", exported_msgs >= 1, str(exported_msgs))

        # 4) 清空：本机 localStorage + IndexedDB（含聊天库）与服务端旧会话
        cdp = await ctx.new_cdp_session(pg)
        await cdp.send("Storage.clearDataForOrigin", {"origin": ORIGIN, "storageTypes": "all"})
        await cdp.detach()
        api("/api/session/import", {"session_id": sid, "state": {}, "messages": [],
                                    "slots": {"version": 1, "slots": []}}, "POST")
        gone = await asyncio.to_thread(api, f"/api/session/export?session_id={sid}")
        check("清空后服务端为空", (gone.get("state") or {}).get("memories") == {}
              and (gone.get("slots") or {}).get("slots") == [], json.dumps(gone.get("state"), ensure_ascii=False))

        await pg.reload()
        # 清空把配置也抹了：重新指回 9022，否则导入会打到默认 8902（CORS 不放行）
        await pg.evaluate(
            "cfg => localStorage.setItem('xiaoman_cfg', JSON.stringify(cfg))",
            {"mode": "mock", "mockBase": MOCK, "tts": "off"},
        )
        await pg.reload()
        await pg.wait_for_timeout(800)
        clean = await pg.evaluate(
            "({r:(localStorage.getItem('xiaoman_reminders_v1')), s:(localStorage.getItem('xiaoman_stats_v1'))})")
        check("清空后本机无提醒/无统计", clean["r"] is None and clean["s"] is None,
              json.dumps(clean, ensure_ascii=False))
        new_sid = await pg.evaluate("localStorage.getItem('xiaoman_sid')")
        check("清空后是新的会话 id（模拟换机）", bool(new_sid) and new_sid != sid, str(new_sid))

        # 5) 导入
        await pg.click("#settingsBtn")
        await pg.wait_for_selector("#backupFile", state="attached", timeout=5000)
        await pg.set_input_files("#backupFile", path)
        reloaded = await wait_for(lambda: pg.evaluate(
            "typeof localStorage.getItem('xiaoman_reminders_v1') === 'string'"), timeout=15)
        check("导入后页面自动刷新并恢复提醒", reloaded)
        await pg.wait_for_timeout(600)

        # 6) 逐项核对
        remind2 = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_reminders_v1')||'[]')")
        want = [(r["id"], r["text"], r["at"], r["done"]) for r in backup["reminders"]]
        got = [(r["id"], r["text"], r["at"], r["done"]) for r in remind2]
        check("提醒逐项一致（id/文案/时刻/完成态）", got == want, f"want={want} got={got}")

        stats2 = await pg.evaluate("JSON.parse(localStorage.getItem('xiaoman_stats_v1')||'{}')")
        keys = ["msgs", "haha", "night", "days", "perDay", "emoji"]
        same = all(stats2.get(k) == backup["stats"].get(k) for k in keys)
        check("统计逐项一致", same, f"want={backup['stats']} got={stats2}")

        msgs2 = await pg.evaluate("window.ChatStore ? ChatStore.list(localStorage.getItem('xiaoman_sid'), 500) : []")
        check("聊天记录还原（条数一致、原文一致）",
              len(msgs2) >= exported_msgs and all(
                  m["content"] in [x["content"] for x in backup["messages"]] for m in msgs2),
              f"exported={exported_msgs} restored={len(msgs2)}")

        srv2 = await asyncio.to_thread(api, f"/api/session/export?session_id={new_sid}")
        mem_ok = (srv2.get("state") or {}).get("memories") == srv.get("state", {}).get("memories")
        slot_ok = sorted((x["key"], x["value"], x["uses"]) for x in (srv2.get("slots") or {}).get("slots") or []) \
            == sorted((x["key"], x["value"], x["uses"]) for x in (srv.get("slots") or {}).get("slots") or [])
        state_ok = all((srv2.get("state") or {}).get(k) == srv.get("state", {}).get(k)
                       for k in ("turns", "crisis_count", "last_topic"))
        check("服务端记忆槽逐项一致（memories/slots/state）", mem_ok and slot_ok and state_ok,
              f"mem={mem_ok} slots={slot_ok} state={state_ok}")
        check("服务端历史已还原", len(srv2.get("messages") or []) == len(srv.get("messages") or []),
              f"want={len(srv.get('messages') or [])} got={len(srv2.get('messages') or [])}")

        shown = await pg.evaluate("document.querySelectorAll('.msg-row').length")
        check("界面可见还原后的消息", shown >= 2, str(shown))
        check("无 JS 运行时错误", not page_errors, "; ".join(page_errors[:3]))

        await browser.close()

    ok = all(c[1] for c in CHECKS)
    print(f"\nROUNDTRIP {sum(1 for c in CHECKS if c[1])}/{len(CHECKS)} 通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
