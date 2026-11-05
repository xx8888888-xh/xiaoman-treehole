#!/usr/bin/env python3
"""test_raf_budget.py · 耗电 P0 验收：全局 rAF 预算（CDP 采样）

为什么不能用 Stage._rafActive()：
  它只反映舞台自家的 rafId，覆盖不到 PIXI.Ticker.shared 或任何第三方 ticker。
  本脚本在页面最早阶段包装 window.requestAnimationFrame，只在回调「真正执行」时 +1，
  读数全部经 CDP Runtime.evaluate 读取，是页面真实出帧次数。

场景与阈值（--seconds 默认 60）：
  A 静止（零动画 / motion=off）        ≤10 次/秒
  B 连续对话（动画开，每 ~3s 发一条）   ≤65 次/秒
  C document.hidden=true 后 5 秒       增量为 0；恢复可见 1 秒内重新出帧
  D pagehide（真正卸载）               舞台 destroy：canvas 移除、零 rAF
  E 桌宠页（pet.html）：动画 → hidden 归零 → pagehide 销毁

用法（先起静态服务与 mock，端口避开 8901/8902/8903）：
  python -m http.server 8921 --bind 127.0.0.1 --directory web
  XIAOMAN_MOCK_PORT=8922 python server/mock_api.py
  python scripts/test_raf_budget.py --web-url http://127.0.0.1:8921 \
      --mock-base http://127.0.0.1:8922 --seconds 60
"""
import argparse
import json
import os
import sys
import time

from browser_guard import require_browser_allowed

require_browser_allowed(__file__)

from playwright.sync_api import sync_playwright

RAF_INIT = """(function () {
  window.__rafCount = 0;
  var raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = function (cb) {
    return raf(function (t) { window.__rafCount += 1; cb(t); });
  };
})();"""

MOBILE = {"width": 390, "height": 844}


def app_prefs(motion):
    if motion:
        return {"preset": "lively", "motion": True, "font": "normal", "iconsGray": False, "flat": False}
    return {"preset": "minimal", "motion": False, "font": "large", "iconsGray": True, "flat": True}


def open_page(browser, motion, mock_base):
    ctx = browser.new_context(viewport=MOBILE, device_scale_factor=2, color_scheme="light")
    page = ctx.new_page()
    page.add_init_script(
        "try{localStorage.setItem('xiaoman_theme','light');"
        "localStorage.setItem('xiaoman_appearance_v1', %s);"
        "localStorage.setItem('xiaoman_cfg', %s);}catch(e){}"
        % (
            json.dumps(json.dumps(app_prefs(motion), ensure_ascii=False)),
            json.dumps(json.dumps(
                {"mode": "mock", "mockBase": mock_base, "tts": "off",
                 "voice": "zh-CN-XiaoyiNeural", "ttsRate": "normal"},
                ensure_ascii=False)),
        ))
    page.add_init_script(RAF_INIT)
    cdp = ctx.new_cdp_session(page)
    return ctx, page, cdp


def count(cdp):
    r = cdp.send("Runtime.evaluate", {"expression": "window.__rafCount||0", "returnByValue": True})
    return int(r["result"]["value"])


def reset(cdp):
    cdp.send("Runtime.evaluate", {"expression": "window.__rafCount=0", "returnByValue": False})


def wait_stage(page):
    page.wait_for_function("window.__stageMode && window.__stageMode !== 'idle'", timeout=20000)
    page.wait_for_timeout(600)


def set_hidden(page, hidden):
    page.evaluate(
        """(hidden) => {
          if (hidden) {
            Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
          } else {
            delete document.hidden;
          }
          document.dispatchEvent(new Event('visibilitychange'));
        }""",
        hidden,
    )


def sample(cdp, page, seconds):
    """逐秒通过 CDP 读增量，返回 (总次数, 每秒列表, 实际耗时秒)"""
    per = []
    reset(cdp)
    prev = 0
    t0 = time.time()
    for _ in range(seconds):
        page.wait_for_timeout(1000)
        now = count(cdp)
        per.append(now - prev)
        prev = now
    elapsed = max(0.001, time.time() - t0)
    return prev, per, elapsed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-url", default="http://127.0.0.1:8921")
    ap.add_argument("--mock-base", default="http://127.0.0.1:8922")
    ap.add_argument("--seconds", type=int, default=60, help="静止/对话各采样多少秒")
    ap.add_argument("--out", default=os.path.join("artifacts", "raf"))
    args = ap.parse_args(argv)

    base = args.web_url.rstrip("/")
    results = {"seconds": args.seconds, "checks": [], "scenarios": {}}
    failures = []

    def check(name, ok, detail):
        results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS  " if ok else "FAIL  ") + name + "  · " + detail)
        if not ok:
            failures.append(name)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--mute-audio"])

        # ---------- A 静止（零动画） ----------
        ctx, page, cdp = open_page(browser, False, args.mock_base)
        page.goto(base + "/index.html", wait_until="load")
        wait_stage(page)
        total, per, elapsed = sample(cdp, page, args.seconds)
        rate = total / elapsed
        results["scenarios"]["idle"] = {
            "total": total, "seconds": round(elapsed, 1), "perSecond": round(rate, 2), "series": per,
            "stageMode": page.evaluate("window.__stageMode"),
            "ownRaf": page.evaluate("Stage._rafActive()"),
        }
        check("A 静止 %ds：rAF ≤10/s" % args.seconds, rate <= 10.0,
              "总 %d 次 / %.1fs = %.2f/s（自家 raf=%s）" % (total, elapsed, rate, results["scenarios"]["idle"]["ownRaf"]))
        ctx.close()

        # ---------- B 连续对话（动画开） ----------
        ctx, page, cdp = open_page(browser, True, args.mock_base)
        page.goto(base + "/index.html", wait_until="load")
        wait_stage(page)
        them0 = page.evaluate("document.querySelectorAll('#messages .msg-row.them').length")
        reset(cdp)
        t0 = time.time()
        prev = 0
        per = []
        for i in range(args.seconds):
            if i % 3 == 0:
                try:
                    page.fill("#textInput", "我今天有点累，和我说说话吧（%d）" % i)
                    page.click("#sendBtn")
                except Exception:
                    pass
            page.wait_for_timeout(1000)
            now = count(cdp)
            per.append(now - prev)
            prev = now
        elapsed = max(0.001, time.time() - t0)
        them1 = page.evaluate("document.querySelectorAll('#messages .msg-row.them').length")
        rate = prev / elapsed
        results["scenarios"]["chat"] = {
            "total": prev, "seconds": round(elapsed, 1), "perSecond": round(rate, 2), "series": per,
            "replies": them1 - them0,
        }
        check("B 连续对话 %ds：rAF ≤65/s" % args.seconds, rate <= 65.0,
              "总 %d 次 / %.1fs = %.2f/s；新增回复 %d 条" % (prev, elapsed, rate, them1 - them0))

        # ---------- C document.hidden 归零 & 恢复 ----------
        set_hidden(page, True)
        page.wait_for_timeout(200)
        total_hidden, _, _ = sample(cdp, page, 5)
        results["scenarios"]["hidden5s"] = total_hidden
        check("C hidden 后 5 秒：rAF 归零", total_hidden == 0,
              "5s 内 %d 次；Stage._envPaused()=%s" % (total_hidden, page.evaluate("Stage._envPaused()")))
        set_hidden(page, False)
        page.wait_for_timeout(1000)
        resume = count(cdp)
        results["scenarios"]["resume1s"] = resume
        check("C 恢复可见 1 秒：重新出帧", resume >= 30,
              "1s 内 %d 次；Stage._rafActive()=%s" % (resume, page.evaluate("Stage._rafActive()")))

        # ---------- D pagehide 真正卸载 ----------
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: false}))")
        page.wait_for_timeout(400)
        st = page.evaluate("""() => ({
          canvas: document.querySelectorAll('#stage canvas').length,
          mode: window.__stageMode, own: Stage._rafActive(), paused: Stage._envPaused(),
        })""")
        results["scenarios"]["destroy"] = st
        after, _, _ = sample(cdp, page, 3)
        check("D pagehide：destroy 清理画布与循环且零 rAF",
              st["canvas"] == 0 and st["own"] is False and after == 0,
              "canvas=%d mode=%s own=%s 3s_rAF=%d" % (st["canvas"], st["mode"], st["own"], after))
        ctx.close()

        # ---------- E 桌宠页 ----------
        ctx, page, cdp = open_page(browser, True, args.mock_base)
        page.goto(base + "/pet.html", wait_until="load")
        page.wait_for_function("window.__pet && window.__pet.state().mounted", timeout=15000)
        page.wait_for_timeout(800)
        total, per, elapsed = sample(cdp, page, 5)
        results["scenarios"]["petAnim"] = {"total": total, "perSecond": round(total / elapsed, 2), "series": per}
        check("E 桌宠动画：持续出帧（≥30/s，仅验证未停帧）", total / elapsed >= 30,
              "%.1f/s" % (total / elapsed))
        set_hidden(page, True)
        page.wait_for_timeout(200)
        hidden_total, _, _ = sample(cdp, page, 5)
        results["scenarios"]["petHidden5s"] = hidden_total
        check("E 桌宠收起/不可见：5 秒内 rAF 归零", hidden_total == 0, "%d 次" % hidden_total)
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: false}))")
        page.wait_for_timeout(400)
        st = page.evaluate("""() => ({
          canvas: document.querySelectorAll('#petStage canvas').length,
          mounted: window.__pet.state().mounted,
        })""")
        after, _, _ = sample(cdp, page, 3)
        check("E 桌宠 pagehide：销毁画布且零 rAF",
              st["canvas"] == 0 and st["mounted"] is False and after == 0,
              "canvas=%d mounted=%s 3s_rAF=%d" % (st["canvas"], st["mounted"], after))
        ctx.close()

        browser.close()

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "raf_budget.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n=== %d/%d 通过 ===" % (len(results["checks"]) - len(failures), len(results["checks"])))
    print("idle:", results["scenarios"]["idle"]["total"], "次 /",
          results["scenarios"]["idle"]["seconds"], "s =",
          results["scenarios"]["idle"]["perSecond"], "/s")
    print("chat:", results["scenarios"]["chat"]["total"], "次 /",
          results["scenarios"]["chat"]["seconds"], "s =",
          results["scenarios"]["chat"]["perSecond"], "/s")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
