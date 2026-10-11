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

            # —— ⑦g 免费模型限流排队（P1-3：429 → 排队文案+不丢消息+危机不挡+恢复清队+跨会话注入） ——
            # 独立 context（localStorage 与主流程隔离）：cfg=openai 指向无服务端口，
            # route 拦截模拟 429/200，验证"不丢消息、不冷场报错"两条验收线
            ctx2 = await b.new_context(viewport={"width": 390, "height": 844})
            pg2 = await ctx2.new_page()
            err_g = []
            pg2.on("pageerror", lambda e: err_g.append(str(e)))
            api_state = {"mode": "429", "bodies": []}

            def _ok_body():
                import json as _j
                return _j.dumps({"choices": [{"message": {"content": _j.dumps(
                    {"reply": "信号回来啦||欠你的那句我看到了，这不就来了", "emotion": "happy",
                     "motion": None, "memory_updates": {}})}}]})

            async def _api_route(route):
                # 跨源（页面 8901 → API 8999）：CORS 头必须带，否则预检/响应都会 TypeError
                cors = {"Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "POST, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, Authorization"}
                if route.request.method == "OPTIONS":
                    await route.fulfill(status=204, headers=cors)
                    return
                api_state["bodies"].append(route.request.post_data or "")
                if api_state["mode"] == "429":
                    await route.fulfill(status=429, content_type="application/json",
                                        headers=cors,
                                        body='{"error":{"code":429,"message":"rate limit"}}')
                else:
                    await route.fulfill(status=200, content_type="application/json",
                                        headers=cors, body=_ok_body())

            await pg2.route("**/v1/chat/completions", _api_route)
            # 注：本环境 add_init_script 实测不执行（探针验证），沿仓库既有 evaluate 注入
            # 模式；cfg 每次调用惰性读取，页面加载后直接写即生效
            await pg2.goto(BASE, wait_until="domcontentloaded")
            await pg2.wait_for_selector("#textInput", state="visible", timeout=15000)
            await pg2.evaluate(
                "() => localStorage.setItem('xiaoman_cfg', JSON.stringify("
                "{mode:'openai', apiBase:'http://127.0.0.1:8999', apiKey:'test-key'}))")

            async def _wait_line(unlike="", timeout=10):
                end = time.time() + timeout
                v = ""
                while time.time() < end:
                    v = await pg2.evaluate("window.__lastLimitLine ? window.__lastLimitLine() : ''")
                    if v and v != unlike:
                        return v
                    await asyncio.sleep(0.5)
                return v

            # g-1 限流 → 排队文案真实渲染（诚实告知），非"信号飘走了"冷场文案，无页面错误
            await pg2.fill("#textInput", "我周五要交报告好烦")
            await pg2.click("#sendBtn")
            line1 = await _wait_line()
            ok_g1 = bool(line1) and ("记" in line1 or "留" in line1)
            if ok_g1:
                msgs_g = await wait_contains(pg2, "#messages", line1.split("||")[0], timeout=6)
                ok_g1 = line1.split("||")[0] in msgs_g and "信号飘走了" not in msgs_g
            ok_g1 = ok_g1 and not err_g
            print("⑦g-1 限流排队文案+无冷场报错:",
                  f"PASS | {line1[:24]}…" if ok_g1 else f"FAIL | line={line1!r} err={err_g}")
            check("⑦g-1 限流排队文案+无冷场报错", ok_g1)

            # g-2 消息入队不丢（localStorage 落盘）
            pend1 = await pg2.evaluate("() => __pendingMsgs().map(p => p.content)")
            ok_g2 = pend1 == ["我周五要交报告好烦"]
            print("⑦g-2 排队消息落盘不丢:", "PASS" if ok_g2 else f"FAIL {pend1}")
            check("⑦g-2 排队消息落盘不丢", ok_g2)

            # g-3 排队中连发：短池文案+不重样+追加入队
            await pg2.fill("#textInput", "而且老板还在催")
            await pg2.click("#sendBtn")
            line2 = await _wait_line(unlike=line1)
            pend2 = await pg2.evaluate("() => __pendingMsgs().map(p => p.content)")
            ok_g3 = bool(line2) and line2 != line1 and \
                pend2 == ["我周五要交报告好烦", "而且老板还在催"]
            print("⑦g-3 连发短文案不重样+追加:",
                  f"PASS | {line2[:16]}…" if ok_g3 else f"FAIL line2={line2!r} pend={pend2}")
            check("⑦g-3 连发短文案不重样+追加", ok_g3)

            # g-4 危机不被限流挡（客户端先行拦截，不入队——安全网优先级高于一切）
            await pg2.fill("#textInput", "我不想活了")
            await pg2.click("#sendBtn")
            crisis_g = await wait_contains(pg2, "#messages", "12356", timeout=12)
            pend3 = await pg2.evaluate("() => __pendingMsgs().length")
            ok_g4 = "12356" in crisis_g and pend3 == 2
            print("⑦g-4 限流下危机仍被拦截:", "PASS" if ok_g4 else
                  f"FAIL（crisis={'12356' in crisis_g}, pending={pend3}）")
            check("⑦g-4 限流下危机仍被拦截", ok_g4)

            # g-5 恢复（200）：补话清队，且请求上下文含欠的 2 条（模型看得到才答得上）
            api_state["mode"] = "200"
            n_before = len(api_state["bodies"])
            await pg2.fill("#textInput", "在吗")
            await pg2.click("#sendBtn")
            recov_g = await wait_contains(pg2, "#messages", "信号回来啦", timeout=12)
            pend4 = await pg2.evaluate("() => __pendingMsgs().length")
            body_new = api_state["bodies"][n_before:] if len(api_state["bodies"]) > n_before else []
            ok_g5 = "信号回来啦" in recov_g and pend4 == 0 and \
                any("周五要交报告" in b and "老板还在催" in b for b in body_new)
            print("⑦g-5 恢复补话+清队+上下文含欠账:",
                  f"PASS | body含欠账={any('周五要交报告' in b for b in body_new)}" if ok_g5
                  else f"FAIL recov={'信号回来啦' in recov_g} pend={pend4} bodies={len(body_new)}")
            check("⑦g-5 恢复补话+清队+上下文含欠账", ok_g5)
            await pg2.screenshot(path=os.path.join(SHOTS, "17_p13_limit_queue.png"))

            # g-6/g-7 跨会话恢复：预置 pending → reload → greet 提示 + 注回上下文
            await pg2.evaluate(
                "() => { localStorage.setItem('xiaoman_pending', JSON.stringify("
                "[{content:'上周说的搬家的事', at: Date.now()-86400000}])); location.reload(); }")
            await pg2.wait_for_selector("#textInput", state="visible", timeout=15000)
            # greet 分条发送：等末条"信号不太好"出现（此时前序条已全部渲染）——
            # 等首条会在 typing 队列中途返回，末条未出造成时序假 FAIL（06:51 轮实测）
            greet_g = await wait_contains(pg2, "#messages", "信号不太好", timeout=12)
            ok_g7 = "我都记着" in greet_g and "信号不太好" in greet_g
            print("⑦g-7 重访 greet 排队提示:", "PASS" if ok_g7 else f"FAIL {greet_g[-60:]!r}")
            check("⑦g-7 重访 greet 排队提示", ok_g7)
            n2 = len(api_state["bodies"])
            await pg2.fill("#textInput", "在吗现在")
            await pg2.click("#sendBtn")
            for _ in range(20):
                if len(api_state["bodies"]) > n2:
                    break
                await asyncio.sleep(0.5)
            body2 = api_state["bodies"][n2] if len(api_state["bodies"]) > n2 else ""
            pend5 = await pg2.evaluate("() => __pendingMsgs().length")
            ok_g6 = ("搬家" in body2) and pend5 == 0
            print("⑦g-6 跨会话注回+补话清队:",
                  f"PASS | 注回={'搬家' in body2}" if ok_g6 else
                  f"FAIL body含搬家={'搬家' in body2} pend={pend5}")
            check("⑦g-6 跨会话注回+补话清队", ok_g6)
            await ctx2.close()

            # —— ⑦h 小满的自我叙事（P2-1：真实 bump 链路 + 叙事消息数字零虚构 + 零报错） ——
            ctx3 = await b.new_context(viewport={"width": 390, "height": 844})
            pg3 = await ctx3.new_page()
            err_h = []
            pg3.on("pageerror", lambda e: err_h.append(str(e)))
            await pg3.goto(BASE, wait_until="domcontentloaded")
            await pg3.wait_for_selector("#textInput", state="visible", timeout=15000)

            # h-1 真实 bump 链路：发 3 条消息（2 条含哈哈）→ 小本本数字精确
            for txt in ["哈哈哈今天好累", "你也是哈哈哈", "嗯"]:
                await pg3.fill("#textInput", txt)
                await pg3.click("#sendBtn")
                await asyncio.sleep(1.2)   # 等 send 全链路（API=mock 回复）
            snap_h = await pg3.evaluate("() => Stats.snapshot()")
            ok_h1 = snap_h["msgs"] >= 3 and snap_h["haha"] == 2 and snap_h["days"] >= 1
            print("⑦h-1 真实bump链路(msgs≥3/haha=2):",
                  "PASS" if ok_h1 else f"FAIL {snap_h}")
            check("⑦h-1 真实bump链路(msgs≥3/haha=2)", ok_h1)

            # h-2 叙事消息生成：预置高数据 + debugPing 批量 → 数字与 localStorage 一致
            narr = await pg3.evaluate(
                """() => {
                  localStorage.setItem('xiaoman_heartbeat_state', JSON.stringify({}));
                  localStorage.setItem('xiaoman_memories_v1', '[]');
                  const days = [];
                  for (let i = 0; i < 12; i++) days.push(new Date(Date.now() - i*86400000).toDateString());
                  localStorage.setItem('xiaoman_stats_v1', JSON.stringify(
                    { msgs: 45, haha: 7, night: 3, days, emoji: {'🤣': 9} }));
                  function xs(seed) { let s = (seed >>> 0) || 1; for (let i = 0; i < 3; i++) { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; }
                    return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return (s >>> 0) / 4294967296; }; }
                  const out = [];
                  for (let i = 0; i < 30; i++) out.push(Heartbeat.offlineGenerate(xs(i * 13 + 7), new Date()));
                  return {
                    selfMsgs: out.filter(r => r.self).map(r => r.reply),
                    uniq: new Set(out.map(r => r.id)).size,
                  };
                }""")
            self_msgs = narr["selfMsgs"]
            # A 层数字必须与预置一致（零虚构：7/12/3/9 只能来自真实小本本）
            ok_h2 = any(("7次" in m or "12天" in m or "3次" in m or ("🤣" in m and "9次" in m)) for m in self_msgs)
            print("⑦h-2 叙事消息数字零虚构:", f"PASS | 叙事{len(self_msgs)}条 | {self_msgs[0][:30]}…" if ok_h2
                  else f"FAIL self={len(self_msgs)} {self_msgs[:2]}")
            check("⑦h-2 叙事消息数字零虚构", ok_h2)

            # h-3 30 连发零重复（叙事+记忆+通用池混合路径）
            ok_h3 = narr["uniq"] == 30
            print("⑦h-3 30连发零重复:", "PASS" if ok_h3 else f"FAIL uniq={narr['uniq']}/30")
            check("⑦h-3 30连发零重复", ok_h3)

            # h-4 叙事模板不虚构用户世界（浏览器侧同 G9-6 审计）
            audit = await pg3.evaluate(
                """() => {
                  const texts = [...Heartbeat.ME_POOL,
                    ...Object.values(Heartbeat.MEMSEE_TPL).map(f => f('测试值'))];
                  const banned = /(你那边(?!天好吗|呢)|外面|天气|下雨|气温|你电脑|你手机)/;
                  return texts.filter(t => banned.test(t));
                }""")
            ok_h4 = len(audit) == 0 and not err_h
            print("⑦h-4 模板审计+零控制台错误:", "PASS" if ok_h4 else f"FAIL {audit} err={err_h}")
            check("⑦h-4 模板审计+零控制台错误", ok_h4)
            await pg3.screenshot(path=os.path.join(SHOTS, "18_p21_self_narrative.png"))
            await ctx3.close()

            # —— ⑦i Q 版悬浮挂件（P2-4：收起贴边/提醒播报+摆手/展开还原/零报错） ——
            ctx4 = await b.new_context(viewport={"width": 390, "height": 844})
            pg4 = await ctx4.new_page()
            err_i = []
            pg4.on("pageerror", lambda e: err_i.append(str(e)))
            await pg4.goto(BASE, wait_until="domcontentloaded")
            await pg4.wait_for_selector("#textInput", state="visible", timeout=15000)
            await pg4.wait_for_timeout(1500)   # 等模型加载

            # i-1 收起成挂件：主 UI 隐藏，stage 缩成贴边圆形
            await pg4.click("#widgetBtn")
            await pg4.wait_for_timeout(600)
            wi = await pg4.evaluate(
                """() => ({
                  active: window.__widgetActive === true,
                  cls: document.body.classList.contains('widget-mode'),
                  stagePos: getComputedStyle(document.getElementById('stage')).position,
                  stageRounded: getComputedStyle(document.getElementById('stage')).borderRadius,
                  chatHidden: getComputedStyle(document.getElementById('chatArea')).display === 'none',
                })""")
            ok_i1 = wi["active"] and wi["cls"] and wi["stagePos"] == "fixed" and wi["chatHidden"]
            print("⑦i-1 收起成挂件(贴边圆形):", "PASS" if ok_i1 else f"FAIL {wi}")
            check("⑦i-1 收起成挂件(贴边圆形)", ok_i1)
            await pg4.screenshot(path=os.path.join(SHOTS, "19_p24_widget.png"))

            # i-2 挂件播报提醒（真实链路：到期提醒 → Heartbeat.tick → pushFn → sendSplit → 气泡）
            await pg4.evaluate(
                """() => {
                  localStorage.setItem('xiaoman_reminders_v1', JSON.stringify(
                    [{id:'w1', text:'该喝水了', at: Date.now() - 5000, done: false}]));
                  return Heartbeat.tick();
                }""")
            await pg4.wait_for_timeout(3000)
            bub = await pg4.evaluate(
                """() => {
                  const b = document.getElementById('widgetBubble');
                  return { shown: b.classList.contains('show'), text: (b.textContent || '').slice(0, 40) };
                }""")
            ok_i2 = bub["shown"] and "喝水" in bub["text"]
            print("⑦i-2 挂件播报提醒+气泡:", f"PASS | {bub['text']}" if ok_i2 else f"FAIL {bub}")
            check("⑦i-2 挂件播报提醒+气泡", ok_i2)

            # i-3 摆手等可爱动作链路（复用 Shake 动作组，不报错即过）
            await pg4.evaluate("() => Widget.wiggle('Shake')")
            await pg4.wait_for_timeout(500)
            ok_i3 = not err_i
            print("⑦i-3 摆手动作链路:", "PASS" if ok_i3 else f"FAIL err={err_i}")
            check("⑦i-3 摆手动作链路", ok_i3)

            # i-4 点挂件展开回主界面（视图还原）
            await pg4.click("#stage")
            await pg4.wait_for_timeout(700)
            wi2 = await pg4.evaluate(
                """() => ({
                  active: window.__widgetActive === false,
                  cls: !document.body.classList.contains('widget-mode'),
                  chatShown: getComputedStyle(document.getElementById('chatArea')).display !== 'none',
                })""")
            ok_i4 = wi2["active"] and wi2["cls"] and wi2["chatShown"] and not err_i
            print("⑦i-4 展开还原主界面:", "PASS" if ok_i4 else f"FAIL {wi2}")
            check("⑦i-4 展开还原主界面", ok_i4)
            await ctx4.close()

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
