"""test_paging.py · 历史分页 + 首屏显存 + 滚动帧率（w9 专项 E2E，全程静音）

覆盖：
1) 首屏 30 条 → 上翻加载到 300 条（真实 IndexedDB + ChatStore.listBefore 游标）
2) 200 轮上下滚动 DOM 节点数恒定
3) Live2D 纹理档位 1024/2048 的 WebGL 上传字节（显存前后对比）
4) 长会话滚动 rAF 采样（目标 ≥50fps）
5) 断言只看「最后 k 行」与窗口首尾，不用绝对序号（ANALYSIS §16.6）

运行：python scripts/test_paging.py
环境：headless Chromium + --mute-audio；localStorage xiaoman_cfg.tts=off
"""
import asyncio
import http.server
import json
import os
import socketserver
import sys
import threading

from browser_guard import require_browser_allowed

require_browser_allowed(__file__)

from playwright.async_api import async_playwright

from ci_artifacts import shots_dir, write_result

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
PORT = int(os.environ.get("XIAOMAN_PAGING_PORT", "8919"))
# CI 用 compose 的 web（8901）而非自建 8919：python http.server 对 .wasm/.moc3/.png 的
# Content-Type 不可靠，Live2D 纹理档位读不到（tier=None、字节 0）。本地默认仍自建静态服务。
USE_COMPOSE = os.environ.get("XIAOMAN_PAGING_USE_COMPOSE") == "1"
_HOST = os.environ.get("XIAOMAN_HOST", "127.0.0.1")
_WEB_PORT = int(os.environ.get("XIAOMAN_WEB_PORT", "8901"))
BASE_URL = f"http://{_HOST}:{_WEB_PORT}" if USE_COMPOSE else f"http://127.0.0.1:{PORT}"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" · " + detail) if detail else ""))


TEX_PROBE = """
window.__texBytes = 0;
window.__texCalls = 0;
function hook(proto) {
  if (!proto || !proto.texImage2D) return;
  const orig = proto.texImage2D;
  proto.texImage2D = function () {
    try {
      const a = arguments;
      let w = 0, h = 0;
      const src = a[a.length - 1];
      if (a.length >= 9) { w = Number(a[3]) || 0; h = Number(a[4]) || 0; }
      else if (src && typeof src === "object" && (src.width || src.videoWidth)) {
        w = src.width || src.videoWidth || 0; h = src.height || src.videoHeight || 0;
      }
      if (w && h) { window.__texBytes += w * h * 4; window.__texCalls += 1; }
    } catch (e) {}
    return orig.apply(this, arguments);
  };
}
hook(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
hook(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
"""

# 首屏之前就写入：TTS 关闭 + 稳定的会话 id（避免每次随机导致历史漂移）
CONFIG_PROBE = """
try {
  localStorage.setItem('xiaoman_cfg', JSON.stringify({tts: 'off', ttsServer: '', ttsBase: ''}));
} catch (e) {}
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def start_server():
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    srv = Server(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def seed_history(page, n=300):
    """清空并写入 n 条历史；同时把 greet 去重键写成末条，刷新后不再插开场。"""
    # 首屏 greet 的打字机是异步落库的：等 DB 条数连续两次不变，再清空重灌，
    # 否则残留的开场白会混进 300 条里（多一条、DOM 多两个装饰节点）。
    await page.evaluate(
        """async () => {
          const sid = API.sessionId();
          // 等首屏 greet 真正落库（打字机结束才 append；只等 DOM 占位行会太早）
          for (let i = 0; i < 50; i++) {
            if (await ChatStore.count(sid) > 0) break;
            await new Promise(r => setTimeout(r, 200));
          }
          await new Promise(r => setTimeout(r, 600));
          let prev = -1, stable = 0;
          for (let i = 0; i < 24 && stable < 2; i++) {
            const n = await ChatStore.count(sid);
            if (n === prev) stable += 1; else stable = 0;
            prev = n;
            await new Promise(r => setTimeout(r, 300));
          }
        }"""
    )
    return await page.evaluate(
        """async (n) => {
          const sid = API.sessionId();
          await ChatStore.clear(sid);
          const base = Date.now() - n * 1000;
          for (let i = 0; i < n; i++) {
            await ChatStore.append(sid, {
              role: i % 2 ? 'assistant' : 'user',
              content: 'm' + i,
              at: base + i * 1000
            });
          }
          const total = await ChatStore.count(sid);
          localStorage.setItem('xiaoman_greet_at', String(Date.now()));
          localStorage.setItem('xiaoman_greet_tail', 'm' + (n - 1));
          return {sid, total};
        }""",
        n,
    )


async def paging_and_fps(browser):
    ctx = await browser.new_context(
        viewport={"width": 420, "height": 900}, device_scale_factor=1
    )
    await ctx.add_init_script(CONFIG_PROBE)
    await ctx.add_init_script("window.__texTier = '1024';")
    page = await ctx.new_page()
    page.on("pageerror", lambda e: print("  [pageerror]", e))
    await page.goto(f"{BASE_URL}/index.html", wait_until="domcontentloaded")
    await page.wait_for_function("() => window.ChatPager && window.ChatStore")
    seeded = await seed_history(page, 300)
    print(f"[PAGING] seeded sid={seeded['sid']} count={seeded['total']}")
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => window.ChatPager && ChatPager.stats().total >= 30", timeout=15000
    )

    stats = await page.evaluate("() => ChatPager.stats()")
    check("首屏只渲染 30 条内的窗口", stats["total"] == 30, f"total={stats['total']}")
    check("首屏最后 k 行是 m299", stats["last"] == "m299", f"last={stats['last']}")
    last_bubble = await page.evaluate(
        """() => {
          const rows = [...document.querySelectorAll('#messages .msg-row')]
            .filter(r => / (me|them)$/.test(r.className));
          const b = rows.length ? rows[rows.length - 1].querySelector('.bubble') : null;
          return b ? b.innerText.trim() : '';
        }"""
    )
    check("DOM 末条气泡来自最新历史", last_bubble.endswith("m299"), repr(last_bubble[-40:]))

    baseline_nodes = await page.evaluate(
        "() => document.querySelectorAll('#messages *').length"
    )

    # 真实滚动触底：滚到顶部 + 派发 scroll，直到不剩更早记录
    history = []
    for i in range(80):
        before = await page.evaluate("() => ChatPager.stats()")
        await page.evaluate(
            """() => {
              const s = document.getElementById('chatScroll');
              s.scrollTop = 0;
              s.dispatchEvent(new Event('scroll'));
            }"""
        )
        await page.wait_for_timeout(420)
        after = await page.evaluate("() => ChatPager.stats()")
        history.append((after["total"], after["start"], after["exhausted"]))
        if after["exhausted"] and after["start"] == 0:
            break
        if after["total"] == before["total"] and after["start"] == before["start"] and i > 3:
            # 节流窗口内没触发：直接翻一页，保证测试不被 UI 节流拖死
            await page.evaluate("() => ChatPager.older()")
            await page.wait_for_timeout(120)
    stats = await page.evaluate("() => ChatPager.stats()")
    check("30 → 300+ 条历史全部可取回", stats["total"] >= 300, f"total={stats['total']}")
    check("翻到最旧一条 m0", stats["first"] == "m0", f"first={stats['first']}")
    check("到底提示已就绪", stats["exhausted"] is True, f"exhausted={stats['exhausted']}")
    await page.screenshot(path=str(shots_dir("paging") / "01_exhausted_top.png"))

    # 窗口内不重复（最后 k 行陷阱：只看当前窗口）
    texts = await page.evaluate(
        """() => [...document.querySelectorAll('#messages .msg-row')]
          .filter(r => / (me|them)$/.test(r.className))
          .map(r => (r.querySelector('.bubble') || {}).innerText || '')"""
    )
    check("窗口内无重复消息", len(texts) == len(set(texts)), f"rows={len(texts)}")
    check("窗口行数固定为 14", len(texts) == 14, f"rows={len(texts)}")
    check("窗口从 m0 开始", texts[0].startswith("m0"), repr(texts[0]))

    # 200 轮滚动：DOM 节点数恒定
    node_report = await page.evaluate(
        """async () => {
          const counts = [];
          const rows = [];
          let rounds = 0;
          while (rounds < 200) {
            await ChatPager.older();
            counts.push(document.querySelectorAll('#messages *').length);
            rows.push(ChatPager.stats().rendered);
            rounds += 1;
            if (ChatPager.stats().start === 0 && ChatPager.stats().exhausted) {
              ChatPager.jumpLatest();
              counts.push(document.querySelectorAll('#messages *').length);
              rows.push(ChatPager.stats().rendered);
              rounds += 1;
            }
          }
          const uniq = [...new Set(counts)];
          const spread = Math.max(...counts) - Math.min(...counts);
          return {uniq, spread, rows: [...new Set(rows)], first: counts[0], stats: ChatPager.stats()};
        }"""
    )
    check(
        "200 轮滚动窗口行数恒定（14）",
        node_report["rows"] == [14],
        f"rows={node_report['rows']} rendered={node_report['stats']['rendered']}",
    )
    check(
        "200 轮滚动元素总数保持常数级（不随历史增长）",
        node_report["spread"] <= 2,
        f"baseline={baseline_nodes} 200轮={node_report['uniq']} spread={node_report['spread']}",
    )
    print(
        f"[NODES] baseline={baseline_nodes} 200轮取值={node_report['uniq']} "
        f"spread={node_report['spread']} 每轮行数=14 total={node_report['stats']['total']}"
    )

    # 滚动帧率：rAF 采样 2s，滚动条在顶部/中段往返
    fps = await page.evaluate(
        """async () => {
          const s = document.getElementById('chatScroll');
          s.scrollTop = s.scrollHeight;
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
          const t0 = performance.now();
          let frames = 0;
          const deltas = [];
          let last = t0;
          await new Promise(resolve => {
            function step(now) {
              frames += 1;
              deltas.push(now - last);
              last = now;
              const p = Math.min(1, (now - t0) / 2000);
              s.scrollTop = (1 - p) * (s.scrollHeight - s.clientHeight);
              if (p < 1) requestAnimationFrame(step);
              else resolve();
            }
            requestAnimationFrame(step);
          });
          const dur = (performance.now() - t0) / 1000;
          const mem = performance.memory ? performance.memory.usedJSHeapSize : 0;
          return {fps: frames / dur, frames, dur, mem};
        }"""
    )
    check("滚动帧率 ≥50fps", fps["fps"] >= 50, f"avg={fps['fps']:.1f}fps frames={fps['frames']}")
    print(
        f"[FPS] avg={fps['fps']:.2f}fps frames={fps['frames']} "
        f"dur={fps['dur']:.2f}s heap={fps['mem']/1048576:.1f}MB"
    )
    await ctx.close()


async def tex_bytes(browser, tier):
    ctx = await browser.new_context(
        viewport={"width": 420, "height": 900}, device_scale_factor=1
    )
    await ctx.add_init_script(CONFIG_PROBE)
    await ctx.add_init_script(TEX_PROBE)
    await ctx.add_init_script(f"window.__texTier = '{tier}';")
    page = await ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(f"{BASE_URL}/index.html", wait_until="domcontentloaded")
    try:
        # 先等真实纹理上传完（1x1 白图会提前满足 >0，不能作为就绪信号）
        floor = 8000000 if tier == "1024" else 30000000
        await page.wait_for_function(
            f"() => window.__stageTier === '{tier}' && window.__texBytes >= {floor}",
            timeout=25000,
        )
        await page.wait_for_timeout(400)
        data = await page.evaluate(
            """() => ({
              bytes: window.__texBytes,
              calls: window.__texCalls,
              tier: window.__stageTier || null,
              heap: performance.memory ? performance.memory.usedJSHeapSize : 0
            })"""
        )
    except Exception as e:
        data = {"bytes": 0, "calls": 0, "tier": None, "err": str(e), "errors": errors}
    await ctx.close()
    return data


async def vive(browser):
    a = await tex_bytes(browser, "1024")
    b = await tex_bytes(browser, "2048")
    mb = lambda n: n / 1048576.0
    print(
        f"[VRAM] 1024: tier={a.get('tier')} textures={a.get('calls')} "
        f"bytes={a.get('bytes')} ({mb(a.get('bytes', 0)):.2f}MB) heap={mb(a.get('heap', 0)):.1f}MB"
    )
    print(
        f"[VRAM] 2048: tier={b.get('tier')} textures={b.get('calls')} "
        f"bytes={b.get('bytes')} ({mb(b.get('bytes', 0)):.2f}MB) heap={mb(b.get('heap', 0)):.1f}MB"
    )
    check(
        "首屏纹理显存 ≤12MB（1024 档）",
        a.get("bytes", 0) > 0 and mb(a["bytes"]) <= 12,
        f"{mb(a.get('bytes', 0)):.2f}MB tier={a.get('tier')}",
    )
    check(
        "2048 档可复现（前后对比基线）",
        b.get("bytes", 0) > a.get("bytes", 0) * 2.5,
        f"2048={mb(b.get('bytes', 0)):.2f}MB vs 1024={mb(a.get('bytes', 0)):.2f}MB",
    )
    if a.get("err"):
        print("  [vram err]", a["err"], a.get("errors"))


async def main():
    srv = None
    if USE_COMPOSE:
        print(f"[SERVER] compose web -> {BASE_URL}")
    else:
        srv = start_server()
        print(f"[SERVER] http://127.0.0.1:{PORT} -> {WEB}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--mute-audio",
                "--use-gl=swiftshader",
                "--enable-unsafe-swiftshader",
                "--autoplay-policy=user-gesture-required",
            ],
        )
        try:
            await paging_and_fps(browser)
            await vive(browser)
        finally:
            await browser.close()
    if srv:
        srv.shutdown()

    fails = [r for r in results if not r[1]]
    print(f"\nSUMMARY pass={len(results) - len(fails)}/{len(results)}")
    if fails:
        for name, _ok, detail in fails:
            print(f"  FAIL {name} · {detail}")
    write_result("test_paging", results, extra={"base_url": BASE_URL})
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
