"""布局矩阵走查：软键盘高度 / 系统字体放大 / 横竖屏切换瞬时布局。

用法（本地满负荷时间窗 21:00-06:00 内）：
  $env:XIAOMAN_ALLOW_LOCAL_BROWSER='1'; $env:XIAOMAN_WEB_PORT='8911';
  $env:XIAOMAN_MOCK_PORT='8912'; python scripts/test_layout_matrix.py

判定硬线（与 scripts/emulator_e2e_walk.py 同尺，横竖屏一把尺）：
  任意采样时刻  inputbar.bottom <= viewportH - 8
另验：提示条「重试」按钮中心可命中；短横屏出现提示条时排队条必须收起（与提示条重试语义重复）。

本脚本不抓网页；如需命令行探活，统一用 curl.exe 或 Invoke-WebRequest -UseBasicParsing。

截图与指标：默认 artifacts/layout_matrix/，可用 XIAOMAN_LAYOUT_SHOTS 覆盖。
"""
import asyncio
import json
import os
import sys

from browser_guard import require_browser_allowed

require_browser_allowed(__file__)

from playwright.async_api import async_playwright

HOST = os.environ.get("XIAOMAN_HOST", "127.0.0.1")
PORT = int(os.environ.get("XIAOMAN_WEB_PORT", "8901"))
MOCK = int(os.environ.get("XIAOMAN_MOCK_PORT", "8902"))
BASE = f"http://{HOST}:{PORT}/index.html"
# 直连模式 + 断言内拦截 500：与 emulator_e2e_walk.py 的失败态同一条产品路径
# （mock 模式失败会走本地引擎降级，不会出"再发一次"提示条）
CFG = {"mode": "openai", "apiBase": f"http://{HOST}:8913", "apiKey": "test",
       "tts": "off", "heartbeatOn": False}
SHOTS = os.environ.get("XIAOMAN_LAYOUT_SHOTS") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "layout_matrix")

results = []
metrics = {}


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" · " + str(detail)) if detail else ""))


MEASURE = """() => {
  const rect = el => { const b = el.getBoundingClientRect();
    return {l:+b.left.toFixed(1), t:+b.top.toFixed(1), r:+b.right.toFixed(1),
            b:+b.bottom.toFixed(1), w:+b.width.toFixed(1), h:+b.height.toFixed(1)}; };
  const visible = el => !!(el && !el.classList.contains('hidden'));
  const shown = el => visible(el) && getComputedStyle(el).display !== 'none'
                    && el.getBoundingClientRect().height > 0;
  const noticeEl = document.getElementById('noticeBar');
  const voiceEl = document.getElementById('voiceTip');
  const retry = document.getElementById('noticeRetry');
  let hit = false, hitEl = '';
  if (retry && !retry.classList.contains('hidden')) {
    const b = retry.getBoundingClientRect();
    const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    hit = !!el && (el === retry || retry.contains(el));
    hitEl = el ? (el.id || el.className || el.tagName) : '';
  }
  const pending = document.getElementById('pendingBar');
  const pendingShown = visible(pending) && getComputedStyle(pending).display !== 'none';
  const inter = (a, b) => Math.max(0, Math.min(a.r, b.r) - Math.max(a.l, b.l))
                        * Math.max(0, Math.min(a.b, b.b) - Math.max(a.t, b.t));
  const jumpEl = document.getElementById('jumpBottom');
  let jump = null;
  if (jumpEl) {
    const d = getComputedStyle(jumpEl).display;
    const vis = d !== 'none' && jumpEl.getBoundingClientRect().height > 0;
    jump = { exists: true, display: d, visible: vis, rect: rect(jumpEl),
             aria: jumpEl.getAttribute('aria-label') || '' };
    if (vis) {
      const b = jumpEl.getBoundingClientRect();
      const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      jump.hit = !!el && (el === jumpEl || jumpEl.contains(el));
      jump.overlapInputbar = +inter(jump.rect, rect(document.querySelector('.inputbar'))).toFixed(1);
      jump.overlapNotice = +inter(jump.rect, rect(noticeEl)).toFixed(1);
      jump.overlapPending = +inter(jump.rect, rect(pending)).toFixed(1);
      jump.overlapVoice = +inter(jump.rect, rect(voiceEl)).toFixed(1);
      jump.overlapRetry = +inter(jump.rect, rect(retry)).toFixed(1);
    }
  }
  return {
    iw: innerWidth, ih: innerHeight,
    inputbar: rect(document.querySelector('.inputbar')),
    noticeVisible: shown(noticeEl),
    notice: rect(noticeEl),
    retryVisible: shown(retry), retry: rect(retry), retryHit: hit, retryHitEl: String(hitEl),
    voiceTipVisible: shown(voiceEl),
    voiceTip: rect(voiceEl),
    pendingVisible: pendingShown,
    pendingDisplay: pending ? getComputedStyle(pending).display : '',
    pending: rect(pending),
    stage: rect(document.getElementById('stage')),
    stageDisplay: getComputedStyle(document.getElementById('stage')).display,
    chatScroll: rect(document.getElementById('chatScroll')),
    chatScrollTop: document.getElementById('chatScroll').scrollTop,
    chatScrollHeight: document.getElementById('chatScroll').scrollHeight,
    chatClientHeight: document.getElementById('chatScroll').clientHeight,
    fs: getComputedStyle(document.documentElement).getPropertyValue('--fs').trim(),
    fontAttr: document.documentElement.getAttribute('data-font') || '',
    jump
  };
}"""


async def measure(pg):
    return await pg.evaluate(MEASURE)


async def ensure_overlays(pg):
    """测量前把语音提示/排队条拉到可见：语音提示有 TTL，排队条在横屏修复里应被收起。"""
    await pg.evaluate("""() => {
      if (!document.getElementById('voiceTip').classList.contains('hidden')) return;
      window.__onVoiceUnavailable && window.__onVoiceUnavailable('server');
    }""")
    await pg.evaluate(
        "() => document.getElementById('pendingBar').classList.remove('hidden')")
    await pg.wait_for_timeout(60)


def assert_case(name, m, need_retry=True, pending_must_be_hidden=False):
    ih, ib = m["ih"], m["inputbar"]["b"]
    ok_line = ib <= ih - 8
    check(f"{name}: 输入栏底边 <= 视口-8（{ib} <= {ih - 8}）", ok_line, ib - (ih - 8))
    if need_retry:
        check(f"{name}: 提示条可见且「重试」可命中", m["noticeVisible"] and m["retryVisible"] and m["retryHit"],
              (m["noticeVisible"], m["retryVisible"], m["retryHit"]))
    if pending_must_be_hidden:
        check(f"{name}: 提示条期间排队条已收起", not m["pendingVisible"], m["pendingVisible"])


# 流式节奏探针：在真实渲染器上跑一次 apply（不联网），逐帧记录气泡已显示字数。
# WATCH 只观察、不新建气泡；PROBE 先起一段流再观察。
_WATCH_BODY = """(opts) => new Promise((resolve) => {
  const t0 = performance.now();
  const samples = [];
  let last = -1;
  const read = () => {
    const s = window.__streamProbeState && window.__streamProbeState();
    return s ? s.shown : 0;
  };
  const tick = () => {
    const n = read();
    if (n !== last) { samples.push([Math.round(performance.now() - t0), n]); last = n; }
    if (performance.now() - t0 < opts.ms) requestAnimationFrame(tick);
    else resolve({ samples, state: window.__streamProbeState() });
  };
  __START__
  requestAnimationFrame(tick);
})"""

PROBE = _WATCH_BODY.replace("__START__", "window.__streamProbe(opts.text);")
WATCH = _WATCH_BODY.replace("__START__", "")

SHORT_TXT = "先把窗户关好，别着凉。"
LONG_TXT = ("今晚不用把所有事都想明白。先把眼前这一件事做完，剩下的明天再说也来得及。"
           "你已经撑了很久，现在允许自己慢一点，喝口水，把肩膀放松。"
           "如果心里还是乱，就先把呼吸放慢，一句一句地说给我听。我在，不着急。") * 2


def probe_stats(r):
    s = r["samples"]
    steps = [s[i][1] - s[i - 1][1] for i in range(1, len(s))]
    return {
        "first": s[0][1] if s else 0,
        "final": s[-1][1] if s else 0,
        "updates": len(steps),
        "max_step": max(steps) if steps else 0,
        "full_at": s[-1][0] if s else None,
        "state": r["state"],
    }


async def main():
    os.makedirs(SHOTS, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--mute-audio"])
        ctx = await browser.new_context(viewport={"width": 414, "height": 860})
        pg = await ctx.new_page()

        await pg.goto(BASE, wait_until="load")
        await pg.wait_for_timeout(900)
        await pg.evaluate(
            """(cfg) => { localStorage.setItem('xiaoman_cfg', JSON.stringify(cfg)); }""", CFG)
        await pg.reload(wait_until="load")
        await pg.wait_for_timeout(900)

        # ---------- 0) 横屏基线（无提示条）：确认既有修复没回退 ----------
        await pg.set_viewport_size({"width": 782, "height": 363})
        await pg.wait_for_timeout(300)
        base = await measure(pg)
        metrics["landscape_before"] = base
        check("横屏基线 782x363：输入栏底边 <= 355", base["inputbar"]["b"] <= 355,
              base["inputbar"]["b"])

        # ---------- 1) 造出最坏叠层：失败提示条 + 语音提示 + 排队条 ----------
        for pat in ("**/v1/chat/completions", "**/api/chat/stream", "**/api/chat"):
            await pg.route(pat, lambda route: route.fulfill(
                status=500, content_type="application/json", body='{"error":"boom"}'))
        await pg.set_viewport_size({"width": 414, "height": 860})
        await pg.wait_for_timeout(200)
        await pg.fill("#textInput", "在吗")
        await pg.click("#sendBtn")
        await pg.wait_for_selector("#noticeBar:not(.hidden)", timeout=15000)
        # 语音不可用提示（横屏里这 32px 是压垮输入栏的最后一根稻草）
        await pg.evaluate("() => window.__onVoiceUnavailable && window.__onVoiceUnavailable('server')")
        # 排队条强制可见：验证短横屏下它会被 has-notice 规则收掉
        await pg.evaluate("() => document.getElementById('pendingBar').classList.remove('hidden')")
        await pg.wait_for_selector("#voiceTip:not(.hidden)", timeout=8000)
        # toast 默认 2200ms，且会压在提示条「重试」上方（实测 h=364 时命中元素是 toast）；
        # 等它自己收掉再量按钮可点性，避免把"提示条按钮可点"误判成红。
        await pg.wait_for_timeout(2600)
        await pg.wait_for_selector(".toast", state="hidden", timeout=5000)

        # ---------- 2) 竖屏软键盘高度扫描（视口高度 = 860 - 键盘高度）----------
        for h in (860, 584, 484, 380, 364, 340, 284):
            await pg.set_viewport_size({"width": 414, "height": h})
            await pg.wait_for_timeout(220)
            await ensure_overlays(pg)
            m = await measure(pg)
            metrics[f"portrait_h{h}"] = m
            assert_case(f"竖屏 h={h}", m)
        await pg.screenshot(path=os.path.join(SHOTS, "portrait_h284.png"))

        # ---------- 3) 横屏软键盘高度扫描 ----------
        for h in (363, 320, 300, 280, 260, 240):
            await pg.set_viewport_size({"width": 782, "height": h})
            await pg.wait_for_timeout(260)
            await ensure_overlays(pg)
            m = await measure(pg)
            metrics[f"landscape_h{h}"] = m
            if h <= 260:
                # 既有设计：极端矮横屏里状态条（含提示条/语音提示）整体让位，
                # 只保输入栏与聊天；这里锁住"让位"这件事，不要求重试按钮存在。
                assert_case(f"横屏 h={h}", m, need_retry=False)
                check(f"横屏 h={h}: 极端矮视口状态条按既有设计让位",
                      not m["noticeVisible"] and not m["voiceTipVisible"],
                      (m["noticeVisible"], m["voiceTipVisible"]))
            else:
                assert_case(f"横屏 h={h}", m, pending_must_be_hidden=True)
        await pg.screenshot(path=os.path.join(SHOTS, "landscape_h240.png"))

        # ---------- 4) 字体放大：产品自带 data-font=large（--fs 1.125）与系统 1.3 ----------
        await pg.set_viewport_size({"width": 782, "height": 363})
        await pg.wait_for_timeout(260)
        await pg.evaluate("() => document.documentElement.setAttribute('data-font','large')")
        await pg.wait_for_timeout(200)
        m = await measure(pg)
        metrics["landscape_font_large"] = m
        assert_case("横屏 字体 large（--fs=1.125）", m, pending_must_be_hidden=True)
        check("横屏 字体 large：字号确实放大", m["fs"] not in ("1", ""), m["fs"])

        await pg.evaluate("() => document.documentElement.style.setProperty('--fs','1.3')")
        await pg.wait_for_timeout(200)
        m = await measure(pg)
        metrics["landscape_font_130"] = m
        assert_case("横屏 字体 1.3×（系统放大近似）", m, pending_must_be_hidden=True)
        await pg.screenshot(path=os.path.join(SHOTS, "landscape_font_130.png"))

        # ---------- 5) 横竖屏切换瞬时布局：逐帧采样，不许中途出视口 ----------
        await pg.evaluate("() => { document.documentElement.removeAttribute('data-font');"
                          " document.documentElement.style.removeProperty('--fs'); }")
        steps = 12
        worst = None
        for i in range(steps + 1):
            w = round(414 + (782 - 414) * i / steps)
            h = round(860 + (363 - 860) * i / steps)
            await pg.set_viewport_size({"width": w, "height": h})
            await pg.wait_for_timeout(60)
            await ensure_overlays(pg)
            m = await measure(pg)
            over = m["inputbar"]["b"] - (m["ih"] - 8)
            if worst is None or over > worst["over"]:
                worst = {"step": i, "w": w, "h": h, "over": round(over, 1),
                         "inputbar_b": m["inputbar"]["b"], "retryHit": m["retryHit"],
                         "noticeVisible": m["noticeVisible"]}
            if over > 0:
                print(f"  transient step {i} {w}x{h}: over={over:.1f} notice={m['noticeVisible']}")
            if i == steps // 2:
                await pg.screenshot(path=os.path.join(SHOTS, "rotate_mid.png"))
        metrics["rotate_worst"] = worst
        check("切换瞬时：所有采样帧输入栏不出视口", worst["over"] <= 0, worst)

        # 收尾再验一次最终横屏位置（确认没被中途状态卡住）
        await pg.wait_for_timeout(300)
        m = await measure(pg)
        metrics["after_rotate"] = m
        assert_case("切换结束 782x363", m, pending_must_be_hidden=True)

        # ---------- 6) 键盘收起：舞台立刻恢复 + 滚动位置不跳 ----------
        # 拉长消息列表，保证收起前后都有滚动余量；否则 scrollTop 会被物理夹到 0/底部，
        # 那是滚动范围变化，不是我们布局规则造成的"跳变"，会误判。
        await pg.evaluate("""() => {
          const s = document.getElementById('chatScroll');
          const host = document.getElementById('messages');
          const typing = document.getElementById('typingRow');
          // 只克隆真实消息行；typingRow 是 host 的最后一个 .msg-row，直接取 last 会把它
          // 复制 25 份并去掉 hidden（每份 39px），在列表尾部撑出上千 px 空白。
          const rows = [...host.querySelectorAll('.msg-row')]
            .filter(r => r !== typing && !r.classList.contains('window-tip'));
          const last = rows[rows.length - 1];
          if (last) for (let i = 0; i < 25; i++) {
            const c = last.cloneNode(true);
            c.removeAttribute('id');
            c.classList.remove('hidden', 'window-tip');
            c.removeAttribute('data-fresh');
            host.insertBefore(c, typing);
          }
        }""")
        # 两组：竖屏键盘（414×364→860）；横屏键盘收起（782×300→363，此组修前 scrollTop 120→1599）
        for tag, (w_open, h_open, w_closed, h_closed) in (
                ("竖屏", (414, 364, 414, 860)), ("横屏", (782, 300, 782, 363))):
            await pg.set_viewport_size({"width": w_open, "height": h_open})
            await pg.wait_for_timeout(350)
            await pg.evaluate("() => { document.getElementById('chatScroll').scrollTop = 120; }")
            await pg.wait_for_timeout(120)
            before = await measure(pg)
            await pg.set_viewport_size({"width": w_closed, "height": h_closed})
            await pg.wait_for_timeout(60)  # 一帧多，测"立刻"而不是等动画结束
            after = await measure(pg)
            metrics[f"collapse_{tag}_before"] = before
            metrics[f"collapse_{tag}_after"] = after
            check(f"收起 {tag} {w_open}x{h_open}->{w_closed}x{h_closed}: 舞台立刻可见且尺寸恢复",
                  after["stageDisplay"] != "none" and after["stage"]["h"] >= 158,
                  (before["stageDisplay"], before["stage"]["h"],
                   after["stageDisplay"], after["stage"]["h"]))
            check(f"收起 {tag}: scrollTop 前后一致（{before['chatScrollTop']} -> {after['chatScrollTop']}）",
                  before["chatScrollTop"] == after["chatScrollTop"],
                  (before["chatScrollTop"], after["chatScrollTop"],
                   before["chatClientHeight"], after["chatClientHeight"]))
        await pg.screenshot(path=os.path.join(SHOTS, "collapse_restored.png"))

        # ---------- 7) 「回到最新」浮钮：上翻可见、可命中、不压控件、点击回底 ----------
        await pg.set_viewport_size({"width": 414, "height": 860})
        await pg.wait_for_timeout(300)
        await ensure_overlays(pg)
        await pg.evaluate("""() => { const s = document.getElementById('chatScroll');
          s.scrollTop = Math.max(0, s.scrollHeight - s.clientHeight - 900); }""")
        await pg.wait_for_timeout(250)
        m = await measure(pg)
        metrics["jump_scrolled"] = m
        j = m["jump"] or {}
        jr = j.get("rect") or {}
        check("浮钮 414x860 上翻历史后可见", bool(j.get("visible")), j.get("display"))
        check("浮钮：中心可命中（elementFromPoint 落在按钮上）", bool(j.get("hit")), j.get("aria"))
        check("浮钮：触控目标 >= 44px", jr.get("w", 0) >= 44 and jr.get("h", 0) >= 44, jr)
        zeros = {k: j.get(k) for k in ("overlapInputbar", "overlapNotice", "overlapPending",
                                       "overlapVoice", "overlapRetry")}
        check("浮钮：不与输入栏/提示条/排队条/语音提示/重试重叠",
              all(v == 0 for v in zeros.values()), zeros)
        # 上翻期间来一条新消息：浮钮仍在，条数写进 aria-label（底栏在列表末端、视口外）
        await pg.evaluate(
            "() => window.ChatPager.push({text: '刚泡了杯热茶，慢慢喝。'})")
        await pg.wait_for_timeout(200)
        m2 = await measure(pg)
        j2 = m2["jump"] or {}
        check("上翻期间来新消息：浮钮保持可见且带条数",
              bool(j2.get("visible")) and "条新消息" in (j2.get("aria") or ""), j2.get("aria"))
        await pg.screenshot(path=os.path.join(SHOTS, "jump_button.png"))
        await pg.click("#jumpBottom")
        m3 = None
        gap = None
        for _ in range(12):   # 平滑滚动：轮询到贴底或 1.2s 超时
            await pg.wait_for_timeout(100)
            m3 = await measure(pg)
            gap = m3["chatScrollHeight"] - m3["chatScrollTop"] - m3["chatClientHeight"]
            if gap <= 1:
                break
        check(f"浮钮点击后贴底（gap={gap} <= 1）", gap is not None and gap <= 1, gap)
        check("浮钮点击后自动收起", not (m3["jump"] or {}).get("visible"), m3["jump"])

        # ---------- 8) 加载骨架：三条静态线 / 顶栏高度不推挤 / 语义 / 切换可逆 ----------
        await pg.set_viewport_size({"width": 414, "height": 860})
        await pg.wait_for_timeout(280)
        await pg.evaluate("() => window.ChatPager._setLoading(false)")
        await pg.wait_for_timeout(80)
        skel_idle = await pg.evaluate("""() => {
          const bar = document.querySelector('#messages .window-tip');
          const host = document.getElementById('messages');
          return { h: +bar.getBoundingClientRect().height.toFixed(1),
                   text: bar.textContent.trim(), sh: host.scrollHeight };
        }""")
        await pg.evaluate("() => window.ChatPager._setLoading(true)")
        await pg.wait_for_timeout(90)
        skel_busy = await pg.evaluate("""() => {
          const bar = document.querySelector('#messages .window-tip');
          const host = document.getElementById('messages');
          const lines = [...bar.querySelectorAll('.sk-line')].map((n) => {
            const b = n.getBoundingClientRect();
            return { w: +b.width.toFixed(1), h: +b.height.toFixed(1),
                     anim: getComputedStyle(n).animationName };
          });
          return { cls: bar.className, text: bar.textContent.trim(),
                   aria: bar.getAttribute('aria-label'), busy: bar.getAttribute('aria-busy'),
                   lines, h: +bar.getBoundingClientRect().height.toFixed(1), sh: host.scrollHeight };
        }""")
        metrics["skeleton_idle"], metrics["skeleton_busy"] = skel_idle, skel_busy
        lines = skel_busy["lines"]
        check("骨架：loading 时顶栏显示 3 条占位线", len(lines) == 3, len(lines))
        check("骨架：线宽递减（最后一条更短，像一句话的轮廓）",
              len(lines) == 3 and lines[0]["w"] > lines[1]["w"] > lines[2]["w"],
              [l["w"] for l in lines])
        check("骨架：线高 2.5–4px（静态占位，不是按钮/文案）",
              len(lines) == 3 and all(2.5 <= l["h"] <= 4 for l in lines), [l["h"] for l in lines])
        check("骨架：全部静态（animationName=none，无 shimmer/循环）",
              all(l["anim"] == "none" for l in lines), [l["anim"] for l in lines])
        check("骨架：busy 语义可读（aria-busy=true + aria-label）",
              skel_busy["busy"] == "true" and bool(skel_busy["aria"]),
              (skel_busy["busy"], skel_busy["aria"]))
        check(f"骨架：顶栏高度与 idle 一致（{skel_busy['h']} vs {skel_idle['h']}，±2px）",
              abs(skel_busy["h"] - skel_idle["h"]) <= 2, (skel_idle["h"], skel_busy["h"]))
        check("骨架：切换不推挤列表（messages scrollHeight ±2px）",
              abs(skel_busy["sh"] - skel_idle["sh"]) <= 2, (skel_idle["sh"], skel_busy["sh"]))
        await pg.wait_for_timeout(240)
        still = await pg.evaluate("""() => [...document.querySelectorAll('#messages .sk-line')]
          .map((n) => +n.getBoundingClientRect().width.toFixed(1))""")
        check("骨架：两帧后宽度不变（真静态，没有循环动画起落）",
              still == [l["w"] for l in lines], (still, [l["w"] for l in lines]))
        await pg.screenshot(path=os.path.join(SHOTS, "skeleton_busy.png"))
        await pg.evaluate("() => window.ChatPager._setLoading(false)")
        await pg.wait_for_timeout(90)
        skel_back = await pg.evaluate("""() => {
          const bar = document.querySelector('#messages .window-tip');
          return { lines: bar.querySelectorAll('.sk-line').length,
                   busy: bar.getAttribute('aria-busy'), label: bar.getAttribute('aria-label'),
                   text: bar.textContent.trim() };
        }""")
        check("骨架：切回 idle 后占位线清空、文案恢复、aria 撤销",
              skel_back["lines"] == 0 and skel_back["busy"] is None
              and skel_back["label"] is None and bool(skel_back["text"]), skel_back)

        # ---------- 9) 流式逐字：短句整段 / 长句逐帧 / 收尾不瞬移 / 不抢滚动 ----------
        await pg.evaluate("""() => {
          document.documentElement.setAttribute('data-motion', 'on');
          document.getElementById('voiceTip').classList.add('hidden');
          window.ChatPager.jumpLatest();
        }""")
        await pg.wait_for_timeout(320)

        short_r = probe_stats(await pg.evaluate(PROBE, {"text": SHORT_TXT, "ms": 220}))
        metrics["stream_short"] = short_r
        check(f"短回复 {len(SHORT_TXT)} 字：首帧即整段落屏（{short_r['first']} 字）",
              short_r["first"] == len(SHORT_TXT), short_r["first"])
        check("短回复：不额外拆碎步（渲染更新 ≤1 次）", short_r["updates"] <= 1, short_r["updates"])
        await pg.wait_for_timeout(160)

        long_r = probe_stats(await pg.evaluate(PROBE, {"text": LONG_TXT, "ms": 1200}))
        metrics["stream_long"] = long_r
        total = len(LONG_TXT)
        check(f"长回复 {total} 字：首帧只落一小段（{long_r['first']} < {total // 4}）",
              long_r["first"] < total // 4, long_r["first"])
        check(f"长回复：逐帧增长不倒退（{long_r['updates']} 次更新，单帧峰值 {long_r['max_step']} 字）",
              long_r["updates"] >= 8 and long_r["max_step"] <= 24, long_r)
        check(f"长回复：{long_r['full_at']}ms 补齐（350–1400ms，不瞬移也不拖尾）",
              long_r["full_at"] is not None and 350 <= long_r["full_at"] <= 1400,
              long_r["full_at"])
        check(f"长回复：最终字数与目标一致（{long_r['final']}/{total}）",
              long_r["final"] == total, (long_r["final"], total))
        check("短/长节奏差：短句 1 帧收口，长句至少 8 帧且时长 ≥3× 短句",
              long_r["updates"] >= max(8, 3 * short_r["updates"])
              and (long_r["full_at"] or 0) >= 3 * (short_r["full_at"] or 1),
              (short_r["full_at"], short_r["updates"], long_r["full_at"], long_r["updates"]))

        await pg.evaluate("(t) => window.__streamProbe(t)", SHORT_TXT)
        await pg.wait_for_timeout(60)
        fresh1 = await pg.evaluate("() => window.__streamProbeState()")
        await pg.wait_for_timeout(420)
        fresh2 = await pg.evaluate("() => window.__streamProbeState()")
        check("行入场：新行 data-fresh=1，260ms 后自动清除",
              bool(fresh1 and fresh1.get("fresh")) and not (fresh2 and fresh2.get("fresh")),
              (fresh1, fresh2))
        check("行入场：只播注册过的 msg-in 一次性动画",
              bool(fresh1) and fresh1.get("anim") == "msg-in", fresh1 and fresh1.get("anim"))

        await pg.evaluate("() => document.documentElement.setAttribute('data-motion', 'off')")
        await pg.wait_for_timeout(60)
        await pg.evaluate("(t) => window.__streamProbe(t)", SHORT_TXT)
        await pg.wait_for_timeout(60)
        minimal = await pg.evaluate("() => window.__streamProbeState()")
        check("极简模式：新行可见但不播动画（animationName=none，rowH>0）",
              bool(minimal) and minimal.get("anim") == "none"
              and (minimal.get("rowH") or 0) > 0, minimal)
        await pg.evaluate("() => document.documentElement.removeAttribute('data-motion')")
        await pg.wait_for_timeout(400)

        await pg.evaluate("() => window.ChatPager.jumpLatest()")
        await pg.wait_for_timeout(320)
        await pg.evaluate("""() => { const s = document.getElementById('chatScroll');
          s.style.scrollBehavior = 'auto'; s.scrollTop = s.scrollHeight; }""")
        gap0 = await pg.evaluate("""() => { const s = document.getElementById('chatScroll');
          return { gap: s.scrollHeight - s.scrollTop - s.clientHeight, sh: s.scrollHeight }; }""")
        await pg.evaluate(PROBE, {"text": LONG_TXT, "ms": 700})
        gap1 = await pg.evaluate("""() => { const s = document.getElementById('chatScroll');
          return { gap: s.scrollHeight - s.scrollTop - s.clientHeight, sh: s.scrollHeight }; }""")
        check("贴底时流式继续跟随（gap <= 2，且内容确实变长）",
              gap1["gap"] <= 2 and gap1["sh"] > gap0["sh"], (gap0, gap1))
        await pg.wait_for_timeout(500)

        # 流式进行中上翻：逐字继续，但绝不把已经上翻的视口拽回底部（ResizeObserver 逻辑不碰）
        await pg.evaluate("() => window.ChatPager.jumpLatest()")
        await pg.wait_for_timeout(900)
        await pg.evaluate("""() => { const s = document.getElementById('chatScroll');
          s.style.scrollBehavior = 'auto'; s.scrollTop = s.scrollHeight; }""")
        await pg.evaluate("(t) => window.__streamProbe(t)", LONG_TXT)
        await pg.wait_for_timeout(110)
        await pg.evaluate("""() => { const s = document.getElementById('chatScroll');
          s.scrollTop = Math.max(0, s.scrollHeight - s.clientHeight - 900); }""")
        await pg.wait_for_timeout(140)
        top0 = await pg.evaluate("() => document.getElementById('chatScroll').scrollTop")
        up_r = probe_stats(await pg.evaluate(WATCH, {"ms": 620}))
        top1 = await pg.evaluate("() => document.getElementById('chatScroll').scrollTop")
        jumpv = (await measure(pg))["jump"] or {}
        metrics["stream_scrolled"] = {"top0": top0, "top1": top1, "updates": up_r["updates"]}
        check(f"流式中上翻：逐字继续（{up_r['updates']} 次更新）", up_r["updates"] >= 6,
              up_r["updates"])
        check("流式中上翻：全文补完，一帧没丢", up_r["final"] == len(LONG_TXT), up_r["final"])
        check(f"流式中上翻：不被拽回底部（scrollTop {top0} -> {top1}，±2px）",
              abs(top1 - top0) <= 2, (top0, top1, up_r["state"]))
        check("流式中上翻：「回到最新」浮钮仍可见", bool(jumpv.get("visible")), jumpv.get("display"))

        await pg.evaluate("() => window.__onVoiceUnavailable && window.__onVoiceUnavailable('server')")
        await pg.wait_for_timeout(240)
        top2 = await pg.evaluate("() => document.getElementById('chatScroll').scrollTop")
        voice_vis = await pg.evaluate(
            "() => !document.getElementById('voiceTip').classList.contains('hidden')")
        check("语音提示出现不抢滚动（scrollTop 不变 ±2px）",
              abs(top2 - top1) <= 2, (top1, top2))
        check("语音提示对照组：提示确实可见", bool(voice_vis))
        await pg.screenshot(path=os.path.join(SHOTS, "stream_scrolled_up.png"))

        await pg.click("#jumpBottom")
        gap = None
        for _ in range(12):
            await pg.wait_for_timeout(100)
            m4 = await measure(pg)
            gap = m4["chatScrollHeight"] - m4["chatScrollTop"] - m4["chatClientHeight"]
            if gap <= 1:
                break
        check(f"上翻后点浮钮回到最新（gap={gap} <= 1）", gap is not None and gap <= 1, gap)

        await browser.close()

    failed = [r for r in results if not r[1]]
    json_path = os.path.join(SHOTS, "layout_matrix_metrics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"\nRESULT {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)}")
    print(f"metrics: {json_path}")
    if failed:
        for n, _, d in failed:
            print("  FAIL", n, d)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
