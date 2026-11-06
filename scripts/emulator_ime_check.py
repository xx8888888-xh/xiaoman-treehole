#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_ime_check.py · 横屏软键盘遮挡验收（云端 AVD 真机截图）
--------------------------------------------------------------------
问题：横屏时点输入框，软键盘弹起后，聊天区最后一条消息与输入框是否仍可见？
旧做法是看 CSS 推算（innerHeight 减键盘高度），但 adjustResize 是否真的生效、
WebView 是否被 IME 覆盖、消息列表是否因缩容把末条裁掉，只有真机截图能证明。

取证口径（全部来自运行时，不用 CSS 推算屏高）：
  1) 屏幕尺寸 = adb screencap PNG 的 IHDR 宽高（SurfaceFlinger 合成结果）。
  2) WebView 容器 bounds = uiautomator dump 里 android.webkit.WebView 的 bounds
     （对 DOM 是盲区，但容器本身可见）。
  3) 键盘是否弹起 = dumpsys input_method 的 mInputShown / mIsInputViewShown。
  4) IME 窗口高度 = dumpsys window windows 里 InputMethod 窗口的 mFrame 高度；
     同时用「WebView 底边到屏幕底边」交叉验证（adjustResize 时应两者接近）。
  5) 最后一条消息 / 输入框位置 = WebView DOM 的 getBoundingClientRect()
     （CDP），换算 device px = WebView 原点 + CSS px * devicePixelRatio。
  6) 遮挡像素 = max(0, 最后一条消息底边 - WebView 可视底边)。

用法：python3 scripts/emulator_ime_check.py（App 已启动、设备已转横屏）
依赖：requests、websockets（与 android_cdp.py 相同；不依赖 PIL）
退出码：0=全通过；1=有失败项（逐项 [IME-PASS]/[IME-FAIL]）
"""
import json
import re
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from android_cdp import PKG, Cdp, sh

PORT = 9226
RESULTS = []


def check(name, ok, info=""):
    tag = "[IME-PASS]" if ok else "[IME-FAIL]"
    print(f"{tag} {name}" + (f" | {info}" if info else ""), flush=True)
    RESULTS.append(ok)
    return ok


def shot(path):
    try:
        data = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=90).stdout
    except Exception:
        return None
    if data and len(data) > 1024 and data[:1] == b"\x89":
        with open(path, "wb") as f:
            f.write(data)
        return data
    return None


def png_size(data):
    """PNG IHDR：宽高（不依赖 PIL，CI 少装一个包就少一处故障点）。"""
    if not data or len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def ui_dump():
    for _ in range(3):
        sh("adb shell uiautomator dump /sdcard/window_dump.xml")
        sh("adb pull /sdcard/window_dump.xml ./window_dump.xml")
        try:
            with open("window_dump.xml", "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            if raw.strip():
                return ET.fromstring(raw)
        except Exception:
            pass
        time.sleep(1)
    return None


def webview_rect(root):
    if root is None:
        return None
    for node in root.iter("node"):
        if "WebView" in (node.get("class") or ""):
            m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.get("bounds") or "")
            if m:
                x1, y1, x2, y2 = map(int, m.groups())
                return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
                        "right": x2, "bottom": y2}
    return None


def ime_state():
    _, out = sh("adb shell dumpsys input_method")
    shown = bool(re.search(r"mInputShown\s*=\s*true", out))
    view = bool(re.search(r"mIsInputViewShown\s*=\s*true", out))
    return {"inputShown": shown, "inputViewShown": view}


def ime_frame():
    """dumpsys window windows 里 InputMethod 窗口的 mFrame → (x1,y1,x2,y2)。"""
    _, out = sh("adb shell dumpsys window windows")
    blocks = re.split(r"\n\s*Window #", out)
    for b in blocks:
        if "InputMethod" not in b and "inputmethod" not in b:
            continue
        m = re.search(r"mFrame=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", b)
        if not m:
            m = re.search(r"mFrame=Rect\((-?\d+),\s*(-?\d+)\s*-\s*(-?\d+),\s*(-?\d+)\)", b)
        if m:
            return tuple(map(int, m.groups()))
    return None


def main():
    cdp = Cdp(PORT)
    try:
        cdp.attach("index.html", timeout=120)
    except Exception as e:
        print(f"[IME-FAIL] CDP attach: {e}", flush=True)
        return 1

    # attach 只代表 URL commit，assets 还在加载时 DOM 是空的（首轮真机就栽在这）。
    diag = None
    end = time.time() + 60
    while time.time() < end:
        try:
            diag = cdp.eval("""() => ({
                ready: document.readyState,
                hasInput: !!document.querySelector("#textInput"),
                href: location.href,
                bodyClass: document.body ? document.body.className : "",
                text: (document.body ? document.body.innerText : "").slice(0, 120)
            })""")
            if diag and diag.get("hasInput") and diag.get("ready") == "complete":
                break
        except Exception:
            pass
        time.sleep(1)
    if not diag or not diag.get("hasInput"):
        print(f"[IME-FAIL] WebView DOM 未就绪: {diag}", flush=True)
        return 1
    print(f"[IME] DOM ready: {diag.get('href')} input=ok", flush=True)

    # 键盘隐藏态先截一张，作为"改后"的对照基线
    shot("r5_landscape_before_ime.png")

    # 设备像素比 + 输入框/发送键位置（DOM 真实盒子）
    geo = cdp.eval("""() => {
        const r = (el) => {
            if (!el) return null;
            const b = el.getBoundingClientRect();
            return {x: b.x, y: b.y, w: b.width, h: b.height,
                    bottom: b.bottom, right: b.right};
        };
        return {
            dpr: devicePixelRatio || 1,
            innerW: innerWidth, innerH: innerHeight,
            input: r(document.querySelector("#textInput")),
            send: r(document.querySelector("#sendBtn")),
            inputBar: r(document.querySelector(".inputbar")),
            chat: r(document.querySelector("#chatScroll"))
        };
    }""")
    wr = webview_rect(ui_dump())
    if not wr:
        print("[IME-FAIL] uiautomator 拿不到 WebView 容器 bounds", flush=True)
        return 1
    if not geo or not geo.get("input"):
        print("[IME-FAIL] DOM 拿不到 #textInput 盒子", flush=True)
        return 1

    dpr = geo["dpr"]
    tap_x = int(wr["x"] + (geo["input"]["x"] + geo["input"]["w"] / 2) * dpr)
    tap_y = int(wr["y"] + (geo["input"]["y"] + geo["input"]["h"] / 2) * dpr)

    # 用真实 ADB 点击（DOM focus() 不会触发 IME：必须走用户手势）
    sh(f"adb shell input tap {tap_x} {tap_y}")
    time.sleep(1.0)

    # 发一条消息：让"最后一条"是真实气泡而不是开场白
    sh("adb shell input text keyboardcheck")
    time.sleep(0.4)
    send = geo.get("send")
    if send:
        sx = int(wr["x"] + (send["x"] + send["w"] / 2) * dpr)
        sy = int(wr["y"] + (send["y"] + send["h"] / 2) * dpr)
        sh(f"adb shell input tap {sx} {sy}")
    time.sleep(3.0)

    # 再点输入框确保 IME 仍在前台（发送后部分输入法会收起）
    sh(f"adb shell input tap {tap_x} {tap_y}")

    # 等 IME 真正 shown（最多 12s）
    st = ime_state()
    end = time.time() + 12
    while time.time() < end and not (st["inputShown"] and st["inputViewShown"]):
        time.sleep(1)
        st = ime_state()
    time.sleep(1.4)   # 布局/滚动稳定

    data = shot("r5_landscape_ime.png")
    size = png_size(data)
    st2 = ime_state()
    frame = ime_frame()

    metrics = cdp.eval("""() => {
        const r = (el) => {
            if (!el) return null;
            const b = el.getBoundingClientRect();
            return {x: +b.x.toFixed(1), y: +b.y.toFixed(1),
                    w: +b.width.toFixed(1), h: +b.height.toFixed(1),
                    bottom: +b.bottom.toFixed(1), right: +b.right.toFixed(1)};
        };
        const rows = document.querySelectorAll("#messages .msg-row");
        const bubbles = document.querySelectorAll("#messages .bubble");
        const chat = document.querySelector("#chatScroll");
        const last = rows.length ? rows[rows.length - 1] : null;
        const lastB = bubbles.length ? bubbles[bubbles.length - 1] : null;
        return {
            dpr: devicePixelRatio || 1,
            innerW: innerWidth, innerH: innerHeight,
            rows: rows.length,
            chat: r(chat),
            chatScroll: chat ? {top: chat.scrollTop, h: chat.scrollHeight,
                                gap: chat.scrollHeight - chat.scrollTop - chat.clientHeight} : null,
            lastRow: r(last),
            lastBubble: r(lastB),
            inputBar: r(document.querySelector(".inputbar")),
            input: r(document.querySelector("#textInput")),
            focused: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null
        };
    }""")

    wr2 = webview_rect(ui_dump()) or wr
    metrics["webview"] = wr2
    metrics["screen"] = {"w": size[0], "h": size[1]} if size else None
    metrics["ime"] = {"shown": st2["inputShown"] and st2["inputViewShown"],
                      "frame": frame, "state": st2}

    # ---- 口径换算（device px）----
    last = metrics.get("lastRow") or metrics.get("lastBubble")
    inp = metrics.get("inputBar") or metrics.get("input")
    screen_h = size[1] if size else None
    vis_bottom = wr2["bottom"]
    keyboard_from_screen = (screen_h - vis_bottom) if screen_h else None
    keyboard_from_frame = (frame[3] - frame[1]) if frame else None

    last_bottom_dev = wr2["y"] + last["bottom"] * dpr if last else None
    input_bottom_dev = wr2["y"] + inp["bottom"] * dpr if inp else None
    occluded = max(0, round(last_bottom_dev - vis_bottom)) if last_bottom_dev else None
    input_over = max(0, round(input_bottom_dev - vis_bottom)) if input_bottom_dev else None
    chat_bottom_dev = wr2["y"] + metrics["chat"]["bottom"] * dpr if metrics.get("chat") else None
    last_clipped_by_chat = (max(0, round(last_bottom_dev - chat_bottom_dev))
                            if last_bottom_dev and chat_bottom_dev else None)

    metrics.update({
        "dpr": dpr,
        "visibleBottomDevice": vis_bottom,
        "keyboardHeightPx": keyboard_from_screen,
        "keyboardHeightFromImeFrame": keyboard_from_frame,
        "lastMessageBottomDevice": last_bottom_dev,
        "lastMessageOccludedPx": occluded,
        "inputBottomDevice": input_bottom_dev,
        "inputOverflowPx": input_over,
        "lastMessageClippedByChatPx": last_clipped_by_chat,
        "lastMessageVisible": bool(last and metrics["chat"]
                                   and last["bottom"] <= metrics["chat"]["bottom"] + 1
                                   and last["y"] >= metrics["chat"]["y"] - 1)
    })
    with open("r5_ime_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print("[IME] metrics " + json.dumps(metrics, ensure_ascii=False), flush=True)

    check("软键盘已弹起（dumpsys input_method）", metrics["ime"]["shown"],
          f"inputShown={st2['inputShown']} inputViewShown={st2['inputViewShown']}")
    check("WebView 因 adjustResize 缩短（键盘真的占了可视区）",
          bool(keyboard_from_screen and keyboard_from_screen > 200),
          f"screenH={screen_h} webviewBottom={vis_bottom} keyboard={keyboard_from_screen}px")
    check("IME 窗口高度与 WebView 缩短量一致（差值 ≤32px）",
          bool(keyboard_from_frame and keyboard_from_screen and
               abs(keyboard_from_frame - keyboard_from_screen) <= 32),
          f"dumpsys={keyboard_from_frame} screen-webview={keyboard_from_screen}")
    check("最后一条消息未被键盘遮挡（0px）", occluded == 0, f"occluded={occluded}px")
    check("输入框完整可见（0px 溢出）", input_over == 0, f"inputOverflow={input_over}px")
    check("最后一条消息在聊天可视区内（未被列表容器裁掉）",
          bool(metrics["lastMessageVisible"]),
          f"lastBottomCSS={last and last['bottom']} chatBottomCSS={metrics['chat'] and metrics['chat']['bottom']} "
          f"clippedByChat={last_clipped_by_chat}px")
    check("输入框获得焦点", str(metrics.get("focused")) == "textInput", f"focused={metrics.get('focused')}")

    cdp.close()
    print("[IME] summary " + json.dumps({
        "pass": sum(1 for x in RESULTS if x), "total": len(RESULTS)
    }), flush=True)
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
