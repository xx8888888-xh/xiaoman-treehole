#!/usr/bin/env python3
"""test_live2d_original.py · 开源 original 模型接入验收（无音频、headless）

覆盖：
  1) 分层渲染 + 选档 + 首屏显存（≤12MB）
  2) 三级降级各跑一次：static → silhouette → hidden（真实请求失败触发）
  3) 零动画：无残留 rAF，间隔 1s 画面像素完全一致
  4) 实测帧率（rAF 采样 3s，≥55fps）
  5) 悬浮挂件头部特写（CSS 放大，不改引擎坐标）
  6) 截图：默认浅色 / 暗色 / 大字号 / 零动画 / 降级静态 / 降级剪影

用法（先起静态服务，端口避开 8901/8902/8903）：
  python -m http.server 8921 --bind 127.0.0.1 --directory web
  python scripts/test_live2d_original.py --base-url http://127.0.0.1:8921
"""
import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

MOBILE = {"width": 390, "height": 844}
MB = 1024 * 1024
FRAME_TARGET = 55.0


def appearance(**kw):
    base = {"preset": "default", "motion": True, "font": "normal", "iconsGray": True, "flat": False}
    base.update(kw)
    return base


class Report:
    def __init__(self):
        self.items = []

    def check(self, name, ok, detail=""):
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS  " if ok else "FAIL  ") + name + (("  · " + detail) if detail else ""))
        return ok


def new_page(browser, theme="light", app_prefs=None, routes=None):
    ctx = browser.new_context(viewport=MOBILE, device_scale_factor=2, color_scheme="light")
    page = ctx.new_page()
    logs = []
    page.on("console", lambda m: logs.append(m.text if m.type != "error" else "ERR " + m.text))
    page.on("pageerror", lambda e: logs.append("PAGEERROR " + str(e)))
    script = "try{localStorage.setItem('xiaoman_theme', %s); localStorage.setItem('xiaoman_appearance_v1', %s);}catch(e){}" % (
        json.dumps(theme), json.dumps(json.dumps(app_prefs or appearance())))
    page.add_init_script(script)
    # 全局 rAF 计数器：只统计「真正执行了的回调」，覆盖自家循环与任何第三方 ticker
    page.add_init_script("""(function () {
      window.__rafCount = 0;
      var raf = window.requestAnimationFrame.bind(window);
      window.requestAnimationFrame = function (cb) {
        return raf(function (t) { window.__rafCount += 1; cb(t); });
      };
    })();""")
    if routes:
        page.route("**/*", routes)
    return ctx, page, logs


def wait_layers(page, timeout=10000):
    page.wait_for_function("window.__stageMode && window.__stageMode !== 'idle'", timeout=timeout)
    page.wait_for_timeout(400)
    return page.evaluate("window.__stageMode")


def canvas_pixel(page):
    return page.evaluate("""() => {
      const c = document.querySelector('#stage canvas');
      if (!c) return null;
      const d = c.getContext('2d').getImageData(Math.floor(c.width/2), Math.floor(c.height*0.28), 1, 1).data;
      return [d[0], d[1], d[2], d[3]];
    }""")


def shot(page, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    page.screenshot(path=path, full_page=False)
    print("SHOT  " + path)
    return path


def measure_fps(page, seconds=3.0):
    return page.evaluate(
        """(sec) => new Promise((resolve) => {
          let n = 0; const t0 = performance.now();
          function tick(t) {
            n += 1;
            if (t - t0 >= sec * 1000) resolve(n / ((t - t0) / 1000));
            else requestAnimationFrame(tick);
          }
          requestAnimationFrame(tick);
        })""",
        seconds,
    )


def run(base_url, out_dir):
    rep = Report()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--mute-audio"])
        url = base_url.rstrip("/") + "/index.html"
        perf = {}

        # ---------- 1. 默认分层渲染 + 选档 + 显存 ----------
        ctx, page, logs = new_page(browser)
        page.goto(url, wait_until="load")
        mode = wait_layers(page)
        info = page.evaluate("""() => ({
          tier: window.__stageTier, vram: window.__stageVram,
          canvases: document.querySelectorAll('#stage canvas').length,
          vis: getComputedStyle(document.querySelector('#stage canvas')).visibility,
          note: !!document.querySelector('.stage-note'),
          fallback: !!document.querySelector('.stage-fallback'),
          raf: Stage._rafActive(), stageH: document.getElementById('stage').clientHeight,
        })""")
        rep.check("默认挂载进入分层渲染", mode == "layers", "mode=" + str(mode))
        rep.check("舞台内唯一 canvas 且可见", info["canvases"] == 1 and info["vis"] == "visible", json.dumps(info["vis"]))
        rep.check("模型源为 s070 纹理档", info["tier"] == "s070", str(info["tier"]))
        vram = info.get("vram") or {}
        total = vram.get("totalBytes", 0)
        rep.check("首屏显存 ≤12MB（实测分配）", 0 < total <= 12 * MB, "%.2fMB" % (total / MB))
        rep.check("默认无占位/失败提示", not info["note"] and not info["fallback"])
        rep.check("默认预设允许动画时 rAF 运行", info["raf"] is True)
        shot(page, out_dir, "01-default-light.png")
        perf["tier"] = info["tier"]
        perf["vramBytes"] = total
        perf["canvas"] = [vram.get("canvasW"), vram.get("canvasH")]

        fps = measure_fps(page, 3.0)
        perf["fps"] = round(fps, 1)
        rep.check("实测帧率 ≥%.0ffps（rAF 3s 采样）" % FRAME_TARGET, fps >= FRAME_TARGET, "%.1ffps" % fps)

        # 音频包络 → 7 档口型：连续 lipSync 喂入后嘴部像素必须变化
        mouth0 = page.evaluate("""() => {
          const c = document.querySelector('#stage canvas');
          const s = window.__stageVram.scale;
          const d = c.getContext('2d').getImageData(Math.round(617*s), Math.round(438*s), 1, 1).data;
          return [d[0], d[1], d[2], d[3]];
        }""")
        page.evaluate("""() => new Promise((res) => {
          let n = 0;
          const t = setInterval(() => { Stage.lipFrame(1); if (++n >= 10) { clearInterval(t); res(); } }, 16);
        })""")
        page.wait_for_timeout(80)
        mouth1 = page.evaluate("""() => {
          const c = document.querySelector('#stage canvas');
          const s = window.__stageVram.scale;
          const d = c.getContext('2d').getImageData(Math.round(617*s), Math.round(438*s), 1, 1).data;
          return [d[0], d[1], d[2], d[3]];
        }""")
        delta = max(abs(a - b) for a, b in zip(mouth0, mouth1))
        rep.check("口型：连续 1.0 喂入后嘴部像素变化（包络→7 档）", delta >= 8,
                 "before=%s after=%s Δ=%d" % (mouth0, mouth1, delta))

        errs = [x for x in logs if x.startswith("PAGEERROR")]
        rep.check("无页面级 JS 异常", not errs, errs[0][:120] if errs else "")
        ctx.close()

        # ---------- 2. 暗色 / 大字号 / 零动画 截图 ----------
        ctx, page, _ = new_page(browser, theme="dark", app_prefs=appearance(preset="default"))
        page.goto(url, wait_until="load")
        wait_layers(page)
        shot(page, out_dir, "02-dark.png")
        ctx.close()

        ctx, page, _ = new_page(
            browser, theme="light",
            app_prefs=appearance(preset="custom", motion=True, font="large", iconsGray=True, flat=True),
        )
        page.goto(url, wait_until="load")
        wait_layers(page)
        rep.check("大字号：html data-font=large", page.evaluate("document.documentElement.dataset.font") == "large")
        shot(page, out_dir, "03-large-font.png")
        ctx.close()

        # ---------- 3. 零动画：无 rAF、像素定格 ----------
        ctx, page, _ = new_page(
            browser, theme="light",
            app_prefs=appearance(preset="custom", motion=False, font="normal", iconsGray=True, flat=False),
        )
        page.goto(url, wait_until="load")
        wait_layers(page)
        page.wait_for_timeout(1200)
        idle = page.evaluate("""() => ({
          raf: Stage._rafActive(), mode: window.__stageMode,
          motion: document.documentElement.dataset.motion,
        })""")
        rep.check("零动画：html data-motion=off", idle["motion"] == "off")
        # 只看 Stage._rafActive() 会漏掉第三方/共享 ticker：用全局 rAF 计数实测 1s
        n0 = page.evaluate("window.__rafCount = 0; 0")
        page.wait_for_timeout(1000)
        n1 = page.evaluate("window.__rafCount")
        rep.check("零动画：1s 内全局 rAF 回调为 0（非仅自家 _rafActive）", n1 == 0,
                  "global_raf=%d own=%s" % (n1, idle["raf"]))
        rep.check("零动画：舞台自家 rAF 亦为关闭", idle["raf"] is False, str(idle))
        a = page.evaluate("document.querySelector('#stage canvas').toDataURL()")
        page.wait_for_timeout(1000)
        b = page.evaluate("document.querySelector('#stage canvas').toDataURL()")
        rep.check("零动画：间隔 1s 画面完全同一帧", a == b)
        shot(page, out_dir, "04-zero-motion.png")
        ctx.close()

        # ---------- 4. 三级降级（真实请求失败） ----------
        def make_route(abort_rules):
            def handler(route):
                u = route.request.url
                if any(u.endswith(sfx) for sfx in abort_rules):
                    route.abort()
                else:
                    route.continue_()
            return handler

        # 4.1 分层纹理缺一张 → 静态整图
        ctx, page, logs = new_page(browser, routes=make_route(["/layers/head.png"]))
        page.goto(url, wait_until="load")
        mode = wait_layers(page)
        px = canvas_pixel(page)
        vis = page.evaluate("getComputedStyle(document.querySelector('#stage canvas')).visibility")
        rep.check("降级①：纹理失败 → static 静态整图", mode == "static", "mode=" + str(mode))
        rep.check("降级①：画布可见且已绘制", vis == "visible" and px and px[3] > 200, str(px))
        shot(page, out_dir, "05-degrade-static.png")
        ctx.close()

        # 4.2 整图也失败 → 纯色剪影
        ctx, page, logs = new_page(browser, routes=make_route(["/layers/head.png", "/art/full.png"]))
        page.goto(url, wait_until="load")
        mode = wait_layers(page)
        px = canvas_pixel(page)
        vis = page.evaluate("getComputedStyle(document.querySelector('#stage canvas')).visibility")
        close_to_sil = bool(px) and px[3] > 200 and abs(px[0] - 46) < 40 and abs(px[2] - 116) < 40
        rep.check("降级②：整图失败 → silhouette 纯色剪影", mode == "silhouette", "mode=" + str(mode))
        rep.check("降级②：剪影色接近 #2E6E74", close_to_sil, str(px))
        rep.check("降级②：画布仍可见（保留布局）", vis == "visible")
        shot(page, out_dir, "06-degrade-silhouette.png")
        ctx.close()

        # 4.3 全部失败 → 隐藏但保留布局（含提示与重试）
        ctx, page, logs = new_page(
            browser,
            routes=make_route(["/layers/head.png", "/art/full.png", "/art/silhouette.png"]),
        )
        page.goto(url, wait_until="load")
        mode = wait_layers(page)
        page.wait_for_timeout(300)
        st = page.evaluate("""() => ({
          vis: getComputedStyle(document.querySelector('#stage canvas')).visibility,
          stageH: document.getElementById('stage').clientHeight,
          fallback: !!document.querySelector('.stage-fallback'),
          retry: !!document.querySelector('.stage-retry'),
        })""")
        rep.check("降级③：全部失败 → hidden", mode == "hidden", "mode=" + str(mode))
        rep.check("降级③：画布 visibility:hidden 但占位不塌", st["vis"] == "hidden" and st["stageH"] > 200, json.dumps(st))
        rep.check("降级③：给出安静提示 + 重试按钮", st["fallback"] and st["retry"])
        shot(page, out_dir, "07-degrade-hidden.png")
        ctx.close()

        # ---------- 5. 悬浮挂件头部特写 ----------
        ctx, page, _ = new_page(browser, app_prefs=appearance(preset="default", motion=True))
        page.goto(url, wait_until="load")
        wait_layers(page)
        page.click("#widgetBtn")
        page.wait_for_timeout(600)
        focus = page.evaluate("""() => {
          const stage = document.getElementById('stage');
          const c = document.querySelector('#stage canvas');
          const r = c.getBoundingClientRect(), sr = stage.getBoundingClientRect();
          return { cls: stage.className, cw: r.width, ch: r.height, sw: sr.width, sh: sr.height,
                   top: r.top - sr.top, focused: stage.classList.contains('stage-focus') };
        }""")
        rep.check("挂件模式：canvas 放大到 ≥3 倍高度且水平居中",
                 focus["focused"] and focus["ch"] >= 3 * focus["sh"],
                 json.dumps({k: round(v, 1) if isinstance(v, float) else v for k, v in focus.items()}))
        rep.check("挂件模式：头部落在圆形框内（顶部不越界）", -2 <= focus["top"] <= 20, str(round(focus["top"], 1)))
        shot(page, out_dir, "08-widget-focus.png")
        ctx.close()

        browser.close()

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "perf.json"), "w", encoding="utf-8") as f:
        json.dump(perf, f, ensure_ascii=False, indent=2)

    passed = sum(1 for x in rep.items if x["ok"])
    total_n = len(rep.items)
    print("\n=== %d/%d 通过 ===" % (passed, total_n))
    print("perf:", json.dumps(perf, ensure_ascii=False))
    return 0 if passed == total_n else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8921")
    ap.add_argument("--out", default=os.path.join("artifacts", "live2d"))
    args = ap.parse_args()
    sys.exit(run(args.base_url, args.out))

