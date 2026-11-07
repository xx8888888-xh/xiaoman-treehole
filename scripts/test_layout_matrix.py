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
    fontAttr: document.documentElement.getAttribute('data-font') || ''
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
          const rows = [...s.querySelectorAll('.msg-row')];
          const last = rows[rows.length - 1];
          if (last) for (let i = 0; i < 25; i++) {
            const c = last.cloneNode(true); c.classList.remove('hidden'); s.appendChild(c);
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
