#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""viewports.py · 五档视口适配检查（溢出 / 可点区域 <44px / 文字裁切）

用法：python tools/perf/viewports.py --out artifacts/perf/viewports
输出：每档 PNG 截图 + report.json（问题清单）
"""
import argparse
import asyncio
import json
import os

from playwright.async_api import async_playwright

BASE = os.environ.get("PERF_URL", "http://127.0.0.1:8911/index.html")
MOCK = os.environ.get("PERF_MOCK", "http://127.0.0.1:8912")
CFG = {"mode": "offline", "mockBase": MOCK, "tts": "off"}

VIEWPORTS = [
    ("320x568", 320, 568),
    ("390x844", 390, 844),
    ("430x932", 430, 932),
    ("768x1024", 768, 1024),
    ("1200x900", 1200, 900),
]

CHECK_JS = r"""
() => {
  const out = { overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
                docW: document.documentElement.scrollWidth, winW: window.innerWidth,
                smallTaps: [], clipped: [], offscreen: [] };
  const sel = 'button, [role="button"], a, input, textarea, .icon-btn, .tab, .chip';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (getComputedStyle(el).visibility === "hidden" || getComputedStyle(el).display === "none") continue;
    const label = (el.id || el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 20);
    if (r.width < 44 || r.height < 44) {
      out.smallTaps.push({ label, w: Math.round(r.width), h: Math.round(r.height) });
    }
    if (r.right > window.innerWidth + 1 || r.left < -1) {
      out.offscreen.push({ label, left: Math.round(r.left), right: Math.round(r.right) });
    }
  }
  for (const el of document.querySelectorAll('#messages .bubble, .msg-text')) {
    if (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2) {
      out.clipped.push({ text: (el.textContent || '').slice(0, 18),
                        sh: el.scrollHeight, ch: el.clientHeight,
                        sw: el.scrollWidth, cw: el.clientWidth });
    }
  }
  return out;
}
"""


async def one(browser, name, w, h, outdir):
    ctx = await browser.new_context(viewport={"width": w, "height": h}, locale="zh-CN")
    await ctx.add_init_script(
        "localStorage.setItem('xiaoman_cfg', JSON.stringify(%s));" % json.dumps(CFG))
    page = await ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(BASE, wait_until="domcontentloaded")
    await asyncio.sleep(2.5)
    # 造 3 轮对话，避免只看空屏
    for i in range(3):
        await page.evaluate(
            """(t) => { const ta = document.getElementById('textInput');
                        ta.value = t; ta.dispatchEvent(new Event('input', { bubbles: true }));
                        document.getElementById('sendBtn').click(); }""",
            f"在吗？今天有点累，陪我聊一会儿（第{i + 1}轮）")
        await asyncio.sleep(0.9)
    res = await page.evaluate(CHECK_JS)
    res.update({"viewport": name, "w": w, "h": h, "pageErrors": errors})
    shot = os.path.join(outdir, f"{name}.png")
    await page.screenshot(path=shot, full_page=False)
    await ctx.close()
    return res


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/perf/viewports")
    args = ap.parse_args()
    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)
    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--mute-audio"])
        for name, w, h in VIEWPORTS:
            rows.append(await one(browser, name, w, h, outdir))
        await browser.close()
    bad = [r for r in rows if r["overflowX"] or r["smallTaps"] or r["clipped"] or r["offscreen"]]
    for r in rows:
        print(f"{r['viewport']}: overflow={r['overflowX']} smallTaps={len(r['smallTaps'])} "
              f"clipped={len(r['clipped'])} offscreen={len(r['offscreen'])}")
        for t in r["smallTaps"][:6]:
            print("   <44px:", t)
        for t in r["clipped"][:4]:
            print("   clipped:", t)
    with open(os.path.join(outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"bad viewports: {len(bad)}/{len(rows)} -> {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
