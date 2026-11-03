#!/usr/bin/env python3
"""第三阶段端到端验证：设置页配置 -> 3 轮真实对话 -> IndexedDB/服务端持久化 -> 刷新恢复。

前置：本地栈已启动（8901 前端 / 8902 mock+代理 / 8903 TTS），且 .env 配的是 Kilo。
运行：python scripts/test_phase3_e2e.py
产物：artifacts/phase3/*.png、artifacts/phase3/report.json
"""
import json
import os
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: playwright 未安装（建议 .venv-win 安装后运行）")
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "phase3"
OUT.mkdir(parents=True, exist_ok=True)
URL = os.environ.get("TREEHOLE_URL", "http://127.0.0.1:8901/")
FALLBACK_CHROME = os.environ.get(
    "PLAYWRIGHT_CHROME",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1243" / "chrome-win64" / "chrome.exe"),
)

MSGS = [
    "今天加班到十点，脑子有点木",
    "我们老板又临时改需求，烦死了",
    "不过刚刚楼下买了杯热豆浆，稍微好一点",
]


def log(msg):
    print(msg, flush=True)


def visible_bubbles(page):
    return page.eval_on_selector_all(
        "#messages .msg-row:not(.hidden) .bubble", "els => els.map(e => e.innerText.trim())"
    )


def wait_for_reply(page, before_count, timeout=90):
    """等一条新的对方气泡出现（排除 typing 气泡）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        bubbles = page.eval_on_selector_all(
            "#messages .bubble.them:not(.typing)", "els => els.map(e => e.innerText.trim())"
        )
        if len(bubbles) > before_count:
            return bubbles
        page.wait_for_timeout(500)
    raise TimeoutError("等待回复超时")


def main():
    report = {"url": URL, "turns": [], "ok": False}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:  # 版本不匹配时退回本机已装 chromium
            log(f"默认 chromium 启动失败（{str(e).splitlines()[0]}），改用 {FALLBACK_CHROME}")
            browser = p.chromium.launch(headless=True, executable_path=FALLBACK_CHROME)
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#messages", timeout=20000)
        page.wait_for_timeout(1200)

        # 清空历史，保证本轮可复现
        page.evaluate("""async () => {
            localStorage.clear();
            if (window.ChatStore) { try { await ChatStore.clear(); } catch (e) {} }
        }""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#messages", timeout=20000)
        page.wait_for_timeout(1500)

        # ---- 1) 设置页配置 + 测试连接 ----
        page.click("#settingsBtn")
        page.wait_for_selector("#settingsModal:not(.hidden)", timeout=10000)
        page.evaluate("""() => {
            const set = (id, v) => {
              const el = document.getElementById(id);
              el.value = v;
              el.dispatchEvent(new Event('change', { bubbles: true }));
            };
            set('cfgMode', 'openai');
            set('cfgApi', 'https://api.kilo.ai/api/gateway');
            set('cfgModel', 'nvidia/nemotron-3.5-lightning:free');
            set('cfgApiKey', '');
        }""")
        page.click("#cfgTestBtn")
        page.wait_for_function(
            """() => {
                const t = document.getElementById('cfgTestResult').textContent.trim();
                return t.length > 0 && !t.includes('测试中');
            }""",
            timeout=40000,
        )
        test_result = page.text_content("#cfgTestResult").strip()
        log("测试连接 → " + test_result)
        report["test_connection"] = test_result
        page.screenshot(path=str(OUT / "01_settings_test_connection.png"))
        page.click("#settingsClose")
        page.wait_for_timeout(800)

        cfg = page.evaluate("() => API.loadCfg()")
        report["cfg"] = {k: cfg.get(k) for k in ("mode", "apiBase", "model")}

        # ---- 2) 三轮真实对话 ----
        baseline = len(page.eval_on_selector_all("#messages .bubble.them:not(.typing)", "e => e"))
        for i, text in enumerate(MSGS, 1):
            prev = baseline
            page.fill("#textInput", text)
            page.click("#sendBtn")
            wait_for_reply(page, baseline)
            # 等本轮彻底结束（sendBtn 恢复可用），避免把上一轮残留气泡当成本轮回复
            page.wait_for_function(
                "() => !document.getElementById('sendBtn').disabled", timeout=120000
            )
            page.wait_for_timeout(1200)
            bubbles = page.eval_on_selector_all(
                "#messages .bubble.them:not(.typing)", "els => els.map(e => e.innerText.trim())"
            )
            reply = " || ".join(bubbles[prev:]) or bubbles[-1]
            baseline = len(bubbles)
            log(f"第 {i} 轮 → {reply[:160]}")
            report["turns"].append({"user": text, "reply": reply})
            page.screenshot(path=str(OUT / f"02_turn{i}.png"))
            page.wait_for_timeout(600)

        # ---- 3) IndexedDB 持久化 ----
        sid = page.evaluate("() => API.sessionId()")
        idb_count = page.evaluate("""async () => {
            if (!window.ChatStore) return -1;
            const sid = API.sessionId();
            return (await ChatStore.list(sid, 999)).length;
        }""")
        report["session_id"] = sid
        report["indexeddb_messages"] = idb_count
        log(f"IndexedDB 消息数 = {idb_count}（sid={sid}）")

        # ---- 4) 刷新后历史仍在 ----
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#messages", timeout=20000)
        page.wait_for_timeout(2000)
        after = visible_bubbles(page)
        report["after_reload_bubbles"] = len(after)
        log(f"刷新后可见气泡 = {len(after)}")
        page.screenshot(path=str(OUT / "03_after_reload.png"))

        restored = any("热豆浆" in b or "豆浆" in b for b in after)
        report["history_restored"] = restored
        report["ok"] = bool(
            idb_count >= len(MSGS) and len(after) >= len(MSGS) and restored
            and all(t["reply"] and len(t["reply"]) > 2 for t in report["turns"])
        )
        browser.close()

    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log("RESULT=" + ("PASS" if report["ok"] else "FAIL"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
