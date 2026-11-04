"""外观预设 × 三个独立开关的自动化用例。

覆盖：
- 首次启动默认极简（零动画 / 大字号 / 灰阶 / 扁平），首帧不闪回
- 极简 / 默认 / 生动三档的 dataset、字号、图标颜色、过渡
- 改任一开关 → custom；刷新后保持
- 系统 prefers-reduced-motion: reduce 时默认极简，且允许用户手动切"生动"覆盖
- 极简模式 CSS 动画命中 0；:focus-visible 焦点环可见
"""
import asyncio
import json
import os
import subprocess
import sys

from playwright.async_api import async_playwright


def _env():
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c", f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_WEB_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return (os.environ.get("XIAOMAN_HOST") or out[0] or "127.0.0.1",
                int(os.environ.get("XIAOMAN_WEB_PORT") or out[1] or 8901))
    except Exception:
        return (os.environ.get("XIAOMAN_HOST", "127.0.0.1"),
                int(os.environ.get("XIAOMAN_WEB_PORT", 8901)))


HOST, PORT = _env()
BASE = f"http://{HOST}:{PORT}/index.html"

FIRST_PAINT_SCRIPT = """
window.__firstPaint = null;
requestAnimationFrame(() => {
  const d = document.documentElement.dataset;
  window.__firstPaint = {preset: d.preset, motion: d.motion, font: d.font, icons: d.icons};
});
"""

STATE = """
(() => {
  const d = document.documentElement.dataset;
  const icon = document.querySelector('#settingsBtn');
  const anim = document.getAnimations ? document.getAnimations().length : -1;
  const fs = getComputedStyle(document.body).fontSize;
  return JSON.stringify({
    preset: d.preset, motion: d.motion, font: d.font, icons: d.icons, flat: d.flat,
    cssAnimations: anim,
    iconColor: icon ? getComputedStyle(icon).color : null,
    iconTransition: icon ? getComputedStyle(icon).transitionDuration : null,
    bodyFontPx: parseFloat(fs),
    hint: (document.querySelector('#apHint') || {}).textContent || '',
    stored: localStorage.getItem('xiaoman_appearance_v1'),
  });
})()
"""

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" · " + detail) if detail else ""))


async def state(pg):
    return json.loads(await pg.evaluate(STATE))


async def open_settings(pg):
    await pg.click("#settingsBtn")
    await pg.wait_for_selector("#settingsModal:not(.hidden)", timeout=8000)


async def set_preset(pg, name):
    await open_settings(pg)
    await pg.click(f'#apPresets [data-preset="{name}"]')
    await pg.wait_for_timeout(150)
    await pg.click("#settingsClose")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # ---------- 1. 首次启动（无存储）默认极简，首帧即生效 ----------
        ctx = await browser.new_context(viewport={"width": 414, "height": 860})
        pg = await ctx.new_page()
        await pg.add_init_script(FIRST_PAINT_SCRIPT)
        await pg.goto(BASE, wait_until="load")
        await pg.wait_for_timeout(600)
        fp = await pg.evaluate("window.__firstPaint")
        st = await state(pg)
        check("首帧默认极简", fp and fp.get("preset") == "minimal" and fp.get("font") == "large",
              json.dumps(fp, ensure_ascii=False))
        check("极简 dataset", st["preset"] == "minimal" and st["motion"] == "off"
              and st["font"] == "large" and st["icons"] == "gray" and st["flat"] == "on")
        check("极简 CSS 动画命中 0", st["cssAnimations"] == 0, str(st["cssAnimations"]))
        r, g, b = [int(x) for x in st["iconColor"].replace("rgb(", "").replace(")", "").split(",")[:3]]
        check("极简图标为灰阶", abs(r - g) <= 4 and abs(g - b) <= 4, st["iconColor"])
        base_font = st["bodyFontPx"]

        # ---------- 2. 三档预设 ----------
        await set_preset(pg, "default")
        st = await state(pg)
        check("默认预设：标准字号", st["preset"] == "default" and st["font"] == "normal" and st["bodyFontPx"] < base_font)
        check("默认预设：灰阶 + 零动画", st["icons"] == "gray" and st["motion"] == "off" and st["cssAnimations"] == 0)

        await set_preset(pg, "lively")
        st = await state(pg)
        check("生动预设：彩色图标", st["preset"] == "lively" and st["icons"] == "color"
              and r != int(st["iconColor"].split(",")[0].replace("rgb(", "").strip()))
        check("生动预设：仅 160ms 状态过渡", st["motion"] == "on" and "0.16" in (st["iconTransition"] or ""),
              st["iconTransition"])
        check("生动预设 CSS 动画仍为 0（无装饰循环）", st["cssAnimations"] == 0)

        # ---------- 3. 独立开关 → 自定义 ----------
        await open_settings(pg)
        await pg.click("#apMotion")
        await pg.wait_for_timeout(150)
        st = await state(pg)
        check("开关动画 → custom", st["preset"] == "custom" and st["motion"] == "off"
              and "自定义" in st["hint"], st["hint"])
        await pg.click("#apIcons")
        await pg.wait_for_timeout(100)
        await pg.click("#apIcons")
        await pg.wait_for_timeout(100)
        await pg.click('#apFonts [data-font="xlarge"]')
        await pg.wait_for_timeout(150)
        st = await state(pg)
        check("字号特大 → custom 且变大", st["preset"] == "custom" and st["font"] == "xlarge"
              and st["bodyFontPx"] > base_font, str(st["bodyFontPx"]))
        await pg.click("#settingsClose")

        # ---------- 4. 刷新后保持，首帧不闪 ----------
        await pg.reload(wait_until="load")
        await pg.wait_for_timeout(500)
        fp2 = await pg.evaluate("window.__firstPaint")
        st = await state(pg)
        check("刷新后外观保持", st["preset"] == "custom" and st["font"] == "xlarge")
        check("刷新首帧不闪回", fp2 and fp2.get("font") == "xlarge", json.dumps(fp2, ensure_ascii=False))

        # ---------- 5. 键盘可达：focus-visible 焦点环 ----------
        await pg.evaluate("document.activeElement && document.activeElement.blur()")
        for _ in range(3):
            await pg.keyboard.press("Tab")
        focus = await pg.evaluate(
            "(() => { const e=document.activeElement; const s=getComputedStyle(e);"
            " return JSON.stringify({tag:e.tagName,id:e.id,outline:s.outlineStyle,width:s.outlineWidth,color:s.outlineColor}); })()"
        )
        f = json.loads(focus)
        check("键盘焦点可见", f["outline"] not in ("none", "") and float(f["width"].replace("px", "")) >= 1, focus)

        await ctx.close()

        # ---------- 6. 系统 reduced-motion：默认极简，可手动覆盖 ----------
        ctx2 = await browser.new_context(viewport={"width": 414, "height": 860}, reduced_motion="reduce")
        pg2 = await ctx2.new_page()
        await pg2.goto(BASE, wait_until="load")
        await pg2.wait_for_timeout(500)
        st = await state(pg2)
        check("reduce 下默认极简", st["preset"] == "minimal" and st["motion"] == "off")
        await set_preset(pg2, "lively")
        st = await state(pg2)
        check("reduce 下手动覆盖生动", st["preset"] == "lively" and st["motion"] == "on")
        await ctx2.close()
        await browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\nRESULT {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
