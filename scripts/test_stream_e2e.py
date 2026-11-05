#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stream_e2e.py · W7 真流式端到端（headless + 全程静音）

自起前端(18901) + mock(18902)，不碰宿主 8901/8902/8903。
验证：首字增量出现 / 离线示例模式标注 / 无 TTS 请求 / speechSynthesis 未发声 / 零 JS 错误。
运行：python scripts/test_stream_e2e.py
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from browser_guard import require_browser_allowed

require_browser_allowed(__file__)

ROOT = Path(__file__).resolve().parents[1]
PROMPT = "我今天加班到十点老板还骂我"


def pick_port():
    """取一个系统分配的空闲端口，避免与其它 worktree/E2E 残留进程抢固定端口。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(url, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    socket.setdefaulttimeout(30)
    web_port, mock_port = pick_port(), pick_port()
    while mock_port == web_port:
        mock_port = pick_port()
    env = os.environ.copy()
    env.update({
        "AI_MODE": "mock", "AI_API_BASE": "", "AI_API_KEY": "",
        "XIAOMAN_MOCK_PORT": str(mock_port),
        "XIAOMAN_BIND_HOST": "127.0.0.1",
        "XIAOMAN_ALLOW_ORIGINS": f"http://127.0.0.1:{web_port}",
        "XIAOMAN_STREAM_DELAY": "0.02",
    })
    mock = subprocess.Popen([sys.executable, str(ROOT / "server" / "mock_api.py")],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    web = subprocess.Popen([sys.executable, "-m", "http.server", str(web_port),
                            "--bind", "127.0.0.1", "--directory", str(ROOT / "web")],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    results = []

    def check(name, ok, extra=""):
        results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}{(' · ' + extra) if extra else ''}")

    try:
        if not wait_ready(f"http://127.0.0.1:{mock_port}/health"):
            err = (mock.stderr.read().decode("utf-8", "ignore")[:2000]
                   if mock.poll() is not None else "")
            print(f"FAIL mock 服务未就绪 · rc={mock.poll()} · {err}")
            return 1
        if not wait_ready(f"http://127.0.0.1:{web_port}/index.html"):
            print("FAIL 前端服务未就绪")
            return 1

        from playwright.sync_api import sync_playwright
        errors, tts_hits = [], []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--mute-audio"])
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: tts_hits.append(r.url)
                    if r.method == "POST" and r.url.split("?")[0].rstrip("/").endswith("/tts")
                    else None)
            # 监听所有语音 API 调用（不依赖 TTS 配置，验证"确实没有任何发声动作"）
            page.add_init_script("""
              window.__speakCalls = [];
              try {
                const orig = window.speechSynthesis.speak.bind(window.speechSynthesis);
                window.speechSynthesis.speak = (u) => {
                  window.__speakCalls.push(String((u && u.text) || ''));
                  return orig(u);
                };
              } catch (e) { window.__speakHookError = String(e); }
            """)
            # 先在【同源】非 HTML 资源上写配置，确保 tts=off 在 app.js 首次执行前生效
            # （若先加载 index.html，默认 tts=server 会先触发一次浏览器朗读/问候）
            page.goto(f"http://127.0.0.1:{web_port}/biome.json", wait_until="domcontentloaded")
            page.evaluate(
                "cfg => localStorage.setItem('xiaoman_cfg', JSON.stringify(cfg))",
                {"mode": "mock", "mockBase": f"http://127.0.0.1:{mock_port}",
                 "tts": "off", "voice": "", "ttsRate": "normal",
                 "allowFreePersonal": False},
            )
            page.goto(f"http://127.0.0.1:{web_port}/index.html", wait_until="domcontentloaded")
            page.wait_for_selector("#textInput", state="visible", timeout=15000)
            tts_state = page.evaluate("""() => {
              try {
                const cfg = API.loadCfg();
                return { cfgTts: cfg.tts, provider: TTS.provider(cfg), chain: TTS.chain(cfg) };
              } catch (e) { return { error: String(e) }; }
            }""")
            print(f"  · TTS 配置: {tts_state}")

            page.fill("#textInput", PROMPT)
            t0 = time.perf_counter()
            page.click("#sendBtn")
            first_text, first_at, last_len = "", None, 0
            while time.perf_counter() - t0 < 6:
                txt = page.eval_on_selector_all(
                    "#messages .msg-row.them .bubble",
                    "els => els.map(e => e.textContent || '').join('')")
                if txt.strip():
                    if not first_text:
                        first_text = txt
                        first_at = time.perf_counter() - t0
                    last_len = len(txt)
                    if len(txt) > len(first_text) and time.perf_counter() - t0 > 0.6:
                        break
                page.wait_for_timeout(30)
            grew = len(first_text) < last_len
            check("① 流式首字出现", first_at is not None and first_at < 1.5,
                  f"{int((first_at or 9) * 1000)}ms")
            check("② 文本是增量长出来的（非一次性整段）", grew,
                  f"{len(first_text)}字 → {last_len}字")
            check("③ 首字 < 1.5s（P95 目标）", (first_at or 9) < 1.5,
                  f"{int((first_at or 9) * 1000)}ms")
            page.wait_for_timeout(1200)

            badge = page.evaluate("""() => {
              const b = document.getElementById('degradeBadge');
              const t = document.getElementById('degradeText');
              return {hidden: !b || b.classList.contains('hidden'), text: t ? t.textContent : ''};
            }""")
            check("④ mock 明确标注离线示例模式",
                  (not badge["hidden"]) and ("离线示例模式" in badge["text"]),
                  badge["text"])
            typing_hidden = page.evaluate(
                "() => document.getElementById('typingRow').classList.contains('hidden')")
            check("⑤ 出字后正在输入指示已收起", typing_hidden)
            voice = page.evaluate("""() => ({
              speaking: !!(window.speechSynthesis && window.speechSynthesis.speaking),
              pending: !!(window.speechSynthesis && window.speechSynthesis.pending),
              calls: (window.__speakCalls || []),
              hookError: window.__speakHookError || ''
            })""")
            check("⑥ 全程静音（无任何 speak 调用）",
                  (not voice["speaking"]) and len(voice["calls"]) == 0,
                  f"speaking={voice['speaking']} calls={voice['calls'][:2]} {voice['hookError']}")
            check("⑦ 未发起任何 TTS 请求（provider=off）", len(tts_hits) == 0,
                  f"{len(tts_hits)} 个" + (f" · {tts_hits[:2]}" if tts_hits else ""))
            check("⑧ 页面零 JS 错误", len(errors) == 0, "; ".join(errors[:2]))
            browser.close()
    finally:
        for proc in (mock, web):
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    passed = sum(1 for _, ok in results if ok)
    print(f"\n总计 {passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
