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

            # —— ⑦d 关系连续性承诺（P0-3：前端版本常量 + 老用户 greet 告知 + 幂等） ——
            ver = await pg.evaluate("window.__xiaomanVersion")
            ok7d1 = ver == 2
            print("⑦d-1 前端版本常量:", "PASS v2" if ok7d1 else f"FAIL {ver!r}")
            check("⑦d-1 前端版本常量", ok7d1)

            # ⑦d-2 老用户（预置记忆 + 未告知）reload → greet 追加升级告知
            await pg.evaluate(
                "() => { localStorage.setItem('xiaoman_memory', "
                "JSON.stringify({'昵称':'阿秋','宠物':'橘猫'})); "
                "localStorage.removeItem('xiaoman_told_v'); location.reload(); }")
            told = await wait_contains(pg, "#messages", "升级了一下脑子", timeout=10)
            ok7d2 = told
            print("⑦d-2 老用户greet升级告知:", "PASS" if ok7d2 else "FAIL（未见告知文案）")
            check("⑦d-2 老用户greet升级告知", ok7d2)
            await pg.screenshot(path=os.path.join(SHOTS, "13_p03_update_tell.png"))

            # ⑦d-3 幂等：再 reload → 登记过版本不再说
            await pg.evaluate("() => location.reload()")
            await asyncio.sleep(4)   # 等 greet 渲染稳定（分条+打字动画）
            msgs3 = await pg.inner_text("#messages")
            n_rows = await pg.evaluate("document.querySelectorAll('#messages .msg-row').length")
            ok7d3 = n_rows >= 1 and "升级了一下脑子" not in msgs3
            print("⑦d-3 告知幂等不重复:", "PASS" if ok7d3 else f"FAIL (rows={n_rows})")
            check("⑦d-3 告知幂等不重复", ok7d3)

            # —— ⑦e 关系进展感知（P1-1：判定单测 + 端到端天数问答 + 里程碑 + 月度回放） ——
            rq = await pg.evaluate(
                "() => [__isRelationQ('我们认识多久了'), __isRelationQ('我们聊过多少次啦'), "
                "__isRelationQ('这个月我们都聊了什么'), __isRelationQ('你和你男朋友认识多久了'), "
                "__isRelationQ('我们老板又骂我')]")
            ok7e1 = rq == [True, True, True, False, False]
            print("⑦e-1 前端关系判定:", "PASS " + str(rq) if ok7e1 else "FAIL " + str(rq))
            check("⑦e-1 前端关系判定", ok7e1)

            # ⑦e-2 端到端：预置 30 天前首见 → 问天数 → 本地确定性应答（不走服务端话题池）
            await pg.evaluate(
                "() => { localStorage.setItem('xiaoman_first_met', "
                "String(Date.now() - 30*86400000)); }")
            await pg.fill("#textInput", "我们认识多久了")
            await pg.click("#sendBtn")
            days = await wait_contains(pg, "#messages", "认识30天", timeout=12)
            ok7e2 = "认识30天" in days
            print("⑦e-2 端到端天数问答:", "PASS" if ok7e2 else f"FAIL {days[-60:]!r}")
            check("⑦e-2 端到端天数问答", ok7e2)
            await pg.screenshot(path=os.path.join(SHOTS, "14_p11_relation_days.png"))

            # ⑦e-3 里程碑：rounds=99 → 发一轮（bump→100）→ 跨100播报（幂等键写入）
            await pg.evaluate(
                "() => { localStorage.setItem('xiaoman_rounds', '99'); "
                "localStorage.removeItem('xiaoman_ms_done'); }")
            await pg.fill("#textInput", "嗯")
            await pg.click("#sendBtn")
            ms = await wait_contains(pg, "#messages", "聊满100次", timeout=15)
            ok7e3 = "聊满100次" in ms
            print("⑦e-3 里程碑跨100播报:", "PASS" if ok7e3 else f"FAIL（未见里程碑行）")
            check("⑦e-3 里程碑跨100播报", ok7e3)

            # ⑦e-4 月度回放：预置 9 月真实记忆索引 → 清 month_done → reload greet 回放上月话题
            await pg.evaluate(
                "() => { localStorage.setItem('xiaoman_memories_v1', JSON.stringify(["
                "{id:'e1', text:'大事：期中考试', tags:['大事'], kind:'fact', ts: Date.now()-32*86400000, hits:0},"
                "{id:'e2', text:'宠物：橘猫', tags:['宠物'], kind:'fact', ts: Date.now()-31*86400000, hits:0}])); "
                "localStorage.removeItem('xiaoman_month_done'); location.reload(); }")
            replay = await wait_contains(pg, "#messages", "上个月你跟我聊过", timeout=12)
            ok7e4 = "上个月你跟我聊过" in replay and "期中考试" in replay
            print("⑦e-4 月度回放(真实记忆索引):", "PASS" if ok7e4 else f"FAIL {replay[-80:]!r}")
            check("⑦e-4 月度回放(真实记忆索引)", ok7e4)
            await pg.screenshot(path=os.path.join(SHOTS, "15_p11_month_replay.png"))

            # —— ⑦f 心跳问候情境化（P1-2：预置记忆 → 20 连发零重复 + 引用率≥60% + 值零加工） ——
            hb = await pg.evaluate(
                """() => {
                  // 沿 G8 同款 xorshift32（3 次预热），保证确定性
                  function xs(seed) { let s = (seed >>> 0) || 1; for (let i = 0; i < 3; i++) { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; }
                    return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return (s >>> 0) / 4294967296; }; }
                  localStorage.removeItem('xiaoman_heartbeat_state');
                  const now = Date.now();
                  localStorage.setItem('xiaoman_memories_v1', JSON.stringify([
                    {id:'f1', text:'大事：周五要交报告', tags:['大事'], kind:'fact', ts: now - 3*86400000, hits:0},
                    {id:'f2', text:'宠物：橘猫', tags:['宠物'], kind:'fact', ts: now - 3*86400000, hits:0}]));
                  const out = [];
                  const t = new Date();
                  for (let i = 0; i < 20; i++) out.push(Heartbeat.offlineGenerate(xs(201 + i), t));
                  return {
                    uniqId: new Set(out.map(r => r.id)).size,
                    uniqReply: new Set(out.map(r => r.reply)).size,
                    refs: out.filter(r => r.memoryId).length,
                    verbatim: out.filter(r => r.memoryId).every(r => r.reply.includes('周五要交报告') || r.reply.includes('橘猫')),
                    sample: out.find(r => r.memoryId).reply,
                  };
                }""")
            ok7f1 = hb["uniqId"] == 20 and hb["uniqReply"] == 20
            ok7f2 = hb["refs"] >= 12 and hb["verbatim"]
            print("⑦f-1 心跳20连发零重复:", "PASS" if ok7f1 else f"FAIL {hb['uniqId']}/{hb['uniqReply']}")
            check("⑦f-1 心跳20连发零重复", ok7f1)
            print("⑦f-2 心跳引用率≥60%+值零加工:", f"PASS {hb['refs']}/20 | {hb['sample']}" if ok7f2 else f"FAIL {hb['refs']}/20")
            check("⑦f-2 心跳引用率≥60%+值零加工", ok7f2)
            await pg.screenshot(path=os.path.join(SHOTS, "16_p12_heartbeat_ctx.png"))

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
