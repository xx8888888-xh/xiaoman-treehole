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
guards = []   # 新增用例的「它防止什么回归」一句话，收尾统一打印


def check(name, ok, detail="", guard=""):
    results.append((name, bool(ok), detail))
    if guard:
        guards.append((name, guard))
    print(("PASS " if ok else "FAIL ") + name + ((" · " + str(detail)) if detail else ""))


def _rgb(hexs):
    h = str(hexs).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(hexs):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hexs)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class TruncServer:
    """直连通道（8913）的中断/重连剧本服务器。

    - trunc：先吐半截 JSON 增量，然后不回 chunked 终止块直接断开 socket
      → 浏览器 fetch 报网络错误，走 api.js 的"partial 保留"路径；
    - ok：正常吐完整 JSON 增量并以 0 长度块收尾，模拟重连成功。
    只服务 /v1/chat/completions，其它路径 404；CORS 预检也在这里应答
    （Authorization 头会触发 OPTIONS 预检，不能省）。
    """

    TRUNC = "trunc"
    OK = "ok"

    def __init__(self, port=8913):
        self.port = port
        self.mode = self.TRUNC
        self.hits = 0
        self._server = None

    @staticmethod
    def _chunk(writer, payload):
        writer.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)

    async def close(self):
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    @staticmethod
    async def _read_request(reader):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = await reader.read(4096)
            if not chunk:
                break
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(b":")
            headers[k.strip().lower().decode("latin-1")] = v.strip().decode("latin-1")
        return (lines[0] if lines else b""), headers, rest

    async def _handle(self, reader, writer):
        try:
            reqline, headers, rest = await self._read_request(reader)
            method = (reqline.split(b" ")[0] if reqline else b"").upper()
            need = int(headers.get("content-length") or 0)
            body = rest
            while len(body) < need:
                chunk = await reader.read(need - len(body))
                if not chunk:
                    break
                body += chunk
            origin = headers.get("origin") or "*"
            cors = (f"Access-Control-Allow-Origin: {origin}\r\n"
                    "Access-Control-Allow-Methods: POST, OPTIONS\r\n"
                    "Access-Control-Allow-Headers: Authorization, Content-Type\r\n"
                    "Access-Control-Max-Age: 600\r\n")
            if method == "OPTIONS":
                writer.write(b"HTTP/1.1 204 No Content\r\n" + cors.encode()
                             + b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            if reqline.split(b" ")[1:2] != [b"/v1/chat/completions"]:
                writer.write(b"HTTP/1.1 404 Not Found\r\n" + cors.encode()
                             + b"Content-Type: application/json\r\n"
                             + b"Content-Length: 2\r\nConnection: close\r\n\r\n{}")
                await writer.drain()
                return
            self.hits += 1
            if self.mode == self.TRUNC:
                # 半截 JSON：没有结束引号，收不到 done；0.35s 后硬断，模拟"说到一半断线"
                pieces = ['{"reply":"我先听', "你说，不着急"]
                writer.write(b"HTTP/1.1 200 OK\r\n" + cors.encode()
                             + b"Content-Type: text/event-stream\r\n"
                             + b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
                for piece in pieces:
                    payload = json.dumps(
                        {"choices": [{"delta": {"content": piece}}]},
                        ensure_ascii=False).encode()
                    self._chunk(writer, b"data: " + payload + b"\n\n")
                    await writer.drain()
                    await asyncio.sleep(0.35)
                # 故意不写 0\r\n\r\n：客户端读到 ERR_INCOMPLETE_CHUNKED_ENCODING
                return
            full = ('{"reply":"我回来了，刚才说到哪了？慢点说也行。",'
                    '"emotion":"gentle","motion":null}')
            writer.write(b"HTTP/1.1 200 OK\r\n" + cors.encode()
                         + b"Content-Type: text/event-stream\r\n"
                         + b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
            for i in range(0, len(full), 9):
                payload = json.dumps(
                    {"choices": [{"delta": {"content": full[i:i + 9]}}]},
                    ensure_ascii=False).encode()
                self._chunk(writer, b"data: " + payload + b"\n\n")
                await writer.drain()
                await asyncio.sleep(0.03)
            self._chunk(writer, b"data: [DONE]\n\n")
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


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
    trunc = TruncServer()   # 8913：直连通道的中断/重连剧本
    await trunc.start()
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

        # ---------- 10) 流式被中断 / 重连：半截保留、不露协议 JSON、重连只追加 ----------
        # 防回归：半截流在收尾时把原始 JSON 片段当正文写进气泡（用户看到 {"reply": …）。
        await pg.set_viewport_size({"width": 414, "height": 860})
        await pg.wait_for_timeout(250)
        for _ in range(30):
            if not await pg.evaluate("() => !!(window.__isBusy && window.__isBusy())"):
                break
            await pg.wait_for_timeout(100)
        await pg.evaluate(
            """() => { window.ChatPager.jumpLatest();
                       document.getElementById('noticeBar').classList.add('hidden');
                       document.getElementById('pendingBar').classList.add('hidden'); }""")
        await pg.wait_for_timeout(140)
        base10 = await pg.evaluate("""() => ({
          them: [...document.querySelectorAll('#messages .bubble.them:not(.typing)')].length,
          rows: [...document.querySelectorAll('#messages .msg-row:not(#typingRow)')].length,
          scroll: document.getElementById('chatScroll').scrollTop
        })""")
        trunc.mode = TruncServer.TRUNC
        await pg.fill("#textInput", "在吗，我说个事")
        await pg.click("#sendBtn")
        for _ in range(40):
            await pg.wait_for_timeout(100)
            if not await pg.evaluate("() => !!(window.__isBusy && window.__isBusy())"):
                break
        a10 = await pg.evaluate("""() => {
          const bubbles = [...document.querySelectorAll('#messages .bubble.them:not(.typing)')];
          const last = bubbles[bubbles.length - 1];
          const s = document.getElementById('chatScroll');
          return { text: last ? last.textContent : '', count: bubbles.length,
                   typing: !document.getElementById('typingRow').classList.contains('hidden'),
                   notice: !document.getElementById('noticeBar').classList.contains('hidden'),
                   pending: !document.getElementById('pendingBar').classList.contains('hidden'),
                   seq: bubbles.slice(-2).map(b => b.textContent.slice(0, 6)),
                   gap: s.scrollHeight - s.scrollTop - s.clientHeight };
        }""")
        metrics["stream_interrupt"] = a10
        PART = "我先听你说，不着急"   # 半截流能完整解析出的正文（2 字安全缓冲之后）
        check("中断：半截正文保留，且不把 {\"reply\" 之类的协议 JSON 写进气泡",
              a10["text"] == PART and '{"' not in a10["text"],
              (a10["text"], a10["count"]), guard="防止断线收尾把原始 JSON 片段当正文露给用户")
        check("中断：这一轮只多一条气泡（不重复、不盖旧行）",
              a10["count"] == base10["them"] + 1,
              (base10["them"], a10["count"]),
              guard="防止 partial 收尾既 replace 又 addMsg，屏幕上出现两条半截回复")
        check("中断：不误报「没送到」（提示条/排队条都不出现）",
              not a10["notice"] and not a10["pending"], (a10["notice"], a10["pending"]),
              guard="防止已经收到半截回复还弹失败重试，用户点重发把同一句说两遍")
        check("中断：「正在想…」已收起（不留空打字框）", not a10["typing"], a10["typing"],
              guard="防止断线后 typing 行卡在屏幕上，看起来像永远在等")
        check("中断：仍然贴底（gap <= 2）", a10["gap"] <= 2, a10["gap"],
              guard="防止半截收尾走 replace 时把已贴底的视口弹开")

        # 重连：同一通道恢复正常，再发一条只追加，不覆盖上一条半截
        trunc.mode = TruncServer.OK
        await pg.fill("#textInput", "刚才断了？")
        await pg.click("#sendBtn")
        for _ in range(50):
            await pg.wait_for_timeout(100)
            if not await pg.evaluate("() => !!(window.__isBusy && window.__isBusy())"):
                break
        b10 = await pg.evaluate("""() => {
          const bubbles = [...document.querySelectorAll('#messages .bubble.them:not(.typing)')];
          const s = document.getElementById('chatScroll');
          return { count: bubbles.length,
                   last: bubbles[bubbles.length - 1] ? bubbles[bubbles.length - 1].textContent : '',
                   prev: bubbles[bubbles.length - 2] ? bubbles[bubbles.length - 2].textContent : '',
                   gap: s.scrollHeight - s.scrollTop - s.clientHeight };
        }""")
        metrics["stream_reconnect"] = b10
        check("重连：恢复正常回复且只追加一条气泡",
              b10["count"] == a10["count"] + 1
              and b10["last"] == "我回来了，刚才说到哪了？慢点说也行。",
              (a10["count"], b10["count"], b10["last"]),
              guard="防止重连后把上一条半截当成新回复就地覆盖，历史少一句")
        check("重连：上一条半截原文一字不变（顺序也保持在前面）",
              b10["prev"] == PART, b10["prev"],
              guard="防止重连的整窗重建动到已完成的半截气泡，出现回退/串行")
        check("重连：回到底部，新回复完整可见（gap <= 2）", b10["gap"] <= 2, b10["gap"],
              guard="防止重连回复到达后不跟随，最后一句停在视口外")
        check(f"中断/重连：两次发送都真实打到直连通道（hits={trunc.hits} >= 2）",
              trunc.hits >= 2, trunc.hits,
              guard="防止用例被路由拦截架空，实际没走真实 partial 路径")

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
        check(f"上翻后点浮钮回到最新（gap={gap} <= 1）", gap is not None and gap <= 1,
              {"gap": gap, "st": m4["chatScrollTop"], "sh": m4["chatScrollHeight"],
               "ch": m4["chatClientHeight"]})

        # ---------- 11) 长消息折行与省略 ----------
        await pg.set_viewport_size({"width": 414, "height": 860})
        await pg.evaluate("""() => {
          for (const id of ['voiceTip', 'noticeBar', 'pendingBar'])
            document.getElementById(id).classList.add('hidden');
          window.ChatPager.jumpLatest();
        }""")
        await pg.wait_for_timeout(220)
        CJK = ("折行测试" + "这是一段很长的中文消息，用来验证气泡在高密度文本下会自然折行，"
               "而不是把横向撑破；每行都该在气泡右边界前断开。") * 4
        ASC = "AAAA" + "A" * 128
        await pg.evaluate("([a, b]) => { window.ChatPager.push({text: a});"
                          " window.ChatPager.push({text: b}); }", [CJK, ASC])
        await pg.wait_for_timeout(280)
        lm = await pg.evaluate("""() => {
          const bubbles = [...document.querySelectorAll('#messages .bubble')];
          const find = p => bubbles.find(b => b.textContent.startsWith(p));
          const info = el => { if (!el) return null;
            const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
            return { w: +r.width.toFixed(1), h: +r.height.toFixed(1),
                     cw: el.clientWidth, sw: el.scrollWidth,
                     lh: parseFloat(cs.lineHeight) || 0 }; };
          const s = document.getElementById('chatScroll');
          return { cjk: info(find('折行测试')), asc: info(find('AAAA')),
                   chat: { cw: s.clientWidth, sw: s.scrollWidth },
                   doc: { cw: document.documentElement.clientWidth,
                          sw: document.documentElement.scrollWidth } };
        }""")
        metrics["long_text"] = lm
        cjk, asc, chat = lm["cjk"], lm["asc"], lm["chat"]
        check(f"长中文：折成多行（{round(cjk['h'] / cjk['lh'])} 行，>= 5）",
              cjk["h"] / cjk["lh"] >= 5, (cjk["h"], cjk["lh"]),
              guard="防止长段中文被压成一行横向滚出屏幕")
        check(f"长中文：气泡宽度受列宽约束（{cjk['w']} <= {round(chat['cw'] * 0.86)}）",
              cjk["w"] <= chat["cw"] * 0.86 + 2, (cjk["w"], chat["cw"]),
              guard="防止气泡按内容宽度撑开，把 80% 上限改回无限宽")
        check("长中文：气泡内无横向溢出（scrollWidth <= clientWidth）",
              cjk["sw"] <= cjk["cw"] + 1, (cjk["sw"], cjk["cw"]),
              guard="防止 overflow-wrap 被去掉后，长段落出现内部横向滚动条")
        check(f"长英文串（132 个连续字母）：断词折行（{round(asc['h'] / asc['lh'])} 行），不撑破列表",
              asc["h"] / asc["lh"] >= 2 and asc["sw"] <= asc["cw"] + 1,
              (asc["h"], asc["lh"], asc["sw"], asc["cw"]),
              guard="防止 URL/长英文 token 不断词，把整列横向撑破")
        check("长消息：列表与文档都没有横向溢出",
              chat["sw"] <= chat["cw"] + 1 and lm["doc"]["sw"] <= lm["doc"]["cw"] + 1,
              (chat, lm["doc"]),
              guard="防止单条长消息让整页出现横向漂移（iOS 橡皮筋式左右晃）")

        # 钩子条：短横屏单行省略号 / 竖屏正常折行（"收起"而不是"从中间剪断"）
        HOOK = "如果今晚还是睡不着，就想想你最想对谁说话，把那个名字留在心里也可以。"
        await pg.evaluate("""(t) => {
          const bar = document.getElementById('hookBar');
          bar.innerHTML = ''; const chip = document.createElement('span');
          chip.className = 'hook-chip'; chip.textContent = t; bar.appendChild(chip);
          bar.classList.remove('hidden');
        }""", HOOK)
        await pg.set_viewport_size({"width": 782, "height": 363})
        await pg.wait_for_timeout(220)
        hook_l = await pg.evaluate("""() => {
          const c = document.querySelector('.hook-chip'); const r = c.getBoundingClientRect();
          const cs = getComputedStyle(c);
          return { h: +r.height.toFixed(1), cw: c.clientWidth, sw: c.scrollWidth,
                   ws: cs.whiteSpace, ov: cs.overflow, tov: cs.textOverflow,
                   display: cs.display }; }""")
        check("钩子条·短横屏：单行 + 省略号生效（确实省略了内容）",
              hook_l["ws"] == "nowrap" and hook_l["ov"] == "hidden"
              and hook_l["tov"] == "ellipsis" and hook_l["sw"] > hook_l["cw"]
              and hook_l["h"] <= 52,
              hook_l, guard="防止短横屏把钩子句竖切成半截（末尾停在句子中间）")
        await pg.set_viewport_size({"width": 414, "height": 860})
        await pg.wait_for_timeout(220)
        hook_p = await pg.evaluate("""() => {
          const c = document.querySelector('.hook-chip'); const r = c.getBoundingClientRect();
          const cs = getComputedStyle(c);
          return { h: +r.height.toFixed(1), cw: c.clientWidth, sw: c.scrollWidth,
                   ws: cs.whiteSpace, display: cs.display }; }""")
        check("钩子条·竖屏：正常折行、不裁字（多行且无横向溢出）",
              hook_p["ws"] == "normal" and hook_p["h"] >= 60
              and hook_p["sw"] <= hook_p["cw"] + 1,
              hook_p, guard="防止竖屏沿用横屏的单行省略，把完整句子裁掉")
        await pg.evaluate("""() => { const bar = document.getElementById('hookBar');
          bar.innerHTML = ''; bar.classList.add('hidden'); }""")


        await browser.close()

    await trunc.close()

    failed = [r for r in results if not r[1]]
    json_path = os.path.join(SHOTS, "layout_matrix_metrics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"\nRESULT {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)}")
    print(f"metrics: {json_path}")
    if guards:
        print(f"\n新增用例防回归说明（{len(guards)} 条）：")
        for n, g in guards:
            print(f"  - {n}：{g}")
    if failed:
        for n, _, d in failed:
            print("  FAIL", n, d)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
