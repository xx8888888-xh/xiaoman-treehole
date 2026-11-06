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
import os
import re
import struct
import sys
import time
import xml.etree.ElementTree as ET

import requests

from android_cdp import PKG, Cdp, sh

PORT = 9226
APP_URL = "https://appassets.androidplatform.net/www/index.html"
RESULTS = []

DEFAULT_DOM_TIMEOUT = 60.0
DOM_TIMEOUT_ENV = "IME_DOM_TIMEOUT"


def _positive_float(raw, default):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def dom_timeout(argv=None):
    """DOM 就绪等待上限（秒）：--dom-timeout > IME_DOM_TIMEOUT > 默认 60。"""
    argv = sys.argv[1:] if argv is None else argv
    for i, a in enumerate(argv):
        val = None
        if a == "--dom-timeout" and i + 1 < len(argv):
            val = argv[i + 1]
        elif a.startswith("--dom-timeout="):
            val = a.split("=", 1)[1]
        if val is not None:
            return _positive_float(val, DEFAULT_DOM_TIMEOUT)
    return _positive_float(os.environ.get(DOM_TIMEOUT_ENV), DEFAULT_DOM_TIMEOUT)


def webview_version():
    """当前 WebView 实现与版本（不同 AVD 镜像的 CDP 行为差异常来自这里）。"""
    _, out = sh("adb shell dumpsys webviewupdate")
    m = re.search(r"Current WebView package[^:]*:\s*\((.+?)\)", out)
    if m:
        return m.group(1).strip()
    m = re.search(r"Current WebView package[^:]*:\s*([^\r\n]+)", out)
    return m.group(1).strip() if m else "unknown"


def wait_dom_ready(cdp, timeout=None, poll=1.0, selector="#textInput", log=print):
    """等 WebView DOM 真正可用：readyState === "complete" 且能取到已知元素。

    attach 只代表 URL commit，assets 还在加载时 DOM 是空的。
    旋转/键盘会让旧的 devtools target 失效：eval 连续失败 3 次就重新列举
    targets 并按 webSocketDebuggerUrl 重连（Cdp.attach 内部完成）。
    返回 (最后一份 diag, 最后一次异常, 尝试次数)——失败时用于打印现场。
    """
    timeout = dom_timeout() if timeout is None else timeout
    log(f"[IME] wait DOM (timeout={timeout:g}s selector={selector})")
    end = time.time() + timeout
    diag, last_err, fails, attempts = None, None, 0, 0
    while time.time() < end:
        attempts += 1
        try:
            diag = cdp.eval(f"""() => ({{
                ready: document.readyState,
                hasInput: !!document.querySelector("{selector}"),
                href: location.href,
                bodyClass: document.body ? document.body.className : "",
                text: (document.body ? document.body.innerText : "").slice(0, 120)
            }})""")
            fails = 0
            if diag and diag.get("hasInput") and diag.get("ready") == "complete":
                return diag, None, attempts
            if attempts % 10 == 0:
                log(f"[IME] waiting DOM attempt={attempts} diag={diag}")
        except Exception as e:
            last_err = e
            fails += 1
            if fails >= 3:
                log(f"[IME] CDP eval 连续失败 {fails} 次，重新枚举 targets 重连: {e}")
                try:
                    cdp.attach(cdp.url_filter)
                except Exception as re:
                    last_err = re
                fails = 0
        time.sleep(poll)
    return diag, last_err, attempts


def collect_diag(cdp, diag=None, err=None):
    """失败现场原始证据：probe 原文 / readyState / 当前 URL / WebView 版本。

    旧实现失败只打 `{}`：既看不出是「页面没加载」还是「Runtime.evaluate
    没返回值」，也看不出 target 连到了哪个 WebView。
    """
    info = {"probe": None, "readyState": None, "url": None,
            "webviewVersion": webview_version(), "waitDiag": diag}
    try:
        info["probe"] = cdp.eval(
            """() => "ready=" + document.readyState + " url=" + location.href +
               " body=" + (document.body ? document.body.tagName : "none") +
               " input=" + !!document.querySelector("#textInput")""")
        info["readyState"] = cdp.eval("() => document.readyState")
        info["url"] = cdp.eval("() => location.href")
    except Exception as e:
        info["evalError"] = str(e)[:300]
    if err:
        info["lastWaitError"] = str(err)[:300]
    return info


def cdp_targets(port=PORT):
    """直接读 /json/list（attach 已做过 adb forward，端口还在）。"""
    try:
        return requests.get(f"http://127.0.0.1:{port}/json/list", timeout=5).json()
    except Exception as e:
        return {"error": str(e)}


def attach_diag(port=PORT):
    """attach 失败现场：targets 原文 + devtools socket + App PID + 前台 Activity。"""
    _, unix = sh("adb shell cat /proc/net/unix")
    _, pid = sh(f"adb shell pidof {PKG}")
    _, act = sh("adb shell dumpsys activity activities")
    resumed = re.findall(r"mResumedActivity:\s*(.+)", act)
    return {
        "targets": cdp_targets(port),
        "sockets": sorted(set(re.findall(r"webview_devtools_remote_(\d+)", unix))),
        "pid": pid.strip(),
        "resumedActivity": [x.strip() for x in resumed][:3],
    }


def restart_app(wait=12, log=print):
    """WebView 卡在 url=""（loadUrl 从未 commit）时，重启 Activity 是唯一可靠恢复。"""
    log(f"[IME] 重启 App 以恢复 WebView（{PKG}/.MainActivity）")
    sh(f"adb shell am force-stop {PKG}")
    sh(f"adb shell am start -n {PKG}/.MainActivity")
    time.sleep(wait)


def attach_with_recovery(cdp, log=print):
    """先正常 attach；失败则打印现场并重启 App 再试一次。

    实测 run 42250110526：App 窗口在、WebView target 存在，但 url 连续 120s 为 ""，
    说明 loadUrl 从未 commit（旧的 120s 只报「没有匹配 target」，把现场丢掉了）。
    """
    try:
        cdp.attach("index.html", timeout=120, fallback_url=APP_URL)
        return True, None
    except Exception as first:
        log(f"[IME] CDP attach 失败: {first}")
        log("[IME] attach diag: "
            + json.dumps(attach_diag(cdp.port), ensure_ascii=False, default=str))
        restart_app(log=log)
        try:
            cdp.attach("index.html", timeout=120, fallback_url=APP_URL)
            return True, first
        except Exception as second:
            log(f"[IME] 重启后仍无法 attach: {second}")
            return False, second


def dom_geo(cdp):
    """输入框/发送键/输入栏/聊天区的 DOM 盒子（CSS px）。"""
    return cdp.eval("""() => {
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


def visible_row_count(cdp):
    """真正可见的消息行数（排除常驻的 .window-tip / .hidden 占位条）。"""
    return cdp.eval("""() => Array.from(document.querySelectorAll("#messages .msg-row"))
        .filter(e => !e.classList.contains("hidden")
            && !e.classList.contains("window-tip")
            && e.getBoundingClientRect().height > 0).length""")


def check(name, ok, info=""):
    tag = "[IME-PASS]" if ok else "[IME-FAIL]"
    print(f"{tag} {name}" + (f" | {info}" if info else ""), flush=True)
    RESULTS.append(ok)
    return ok


def shot(path):
    # exec-out screencap 在部分 AVD/GPU 组合下会返回空或被截断（首轮真机就没落盘）。
    # 改为设备内落盘 + adb pull，SurfaceFlinger 合成结果一致且稳定。
    remote = f"/sdcard/{os.path.basename(path)}"
    sh(f"adb shell screencap -p {remote}")
    sh(f"adb pull {remote} {path}")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    return data if len(data) > 1024 else None


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
    """dumpsys window windows 里 InputMethod 窗口的 mFrame + mGivenVisibleInsets。

    注意：mFrame 是 IME 整个 surface，横屏下常覆盖全屏内容区（实测 997px），
    真正被键盘占用的高度 = mFrame 高度 - visibleTop 内缩（997 - 409 = 588）。
    旧实现直接把 mFrame 高度当键盘高度，于是横屏永远差 409px 误判失败。
    """
    _, out = sh("adb shell dumpsys window windows")
    blocks = re.split(r"\n\s*Window #", out)
    for b in blocks:
        if "InputMethod" not in b and "inputmethod" not in b:
            continue
        m = re.search(r"mFrame=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", b)
        if not m:
            m = re.search(r"mFrame=Rect\((-?\d+),\s*(-?\d+)\s*-\s*(-?\d+),\s*(-?\d+)\)", b)
        if not m:
            continue
        visible_top = 0
        mv = re.search(r"mGivenVisibleInsets=\[(?:-?\d+),(-?\d+)\]\[(?:-?\d+),(-?\d+)\]", b)
        if mv:
            visible_top = int(mv.group(1))
        return {"frame": tuple(map(int, m.groups())), "visibleTop": visible_top}
    return None


def ensure_landscape():
    """真机横屏：settings 在部分 AVD 上不生效，再用 emulator console rotate 兜底。"""
    size = None
    for _ in range(3):
        data = shot("r5_orientation_probe.png")
        size = png_size(data) if data else None
        if size and size[0] > size[1]:
            return True, size
        sh("adb shell settings put system accelerometer_rotation 0")
        sh("adb shell settings put system user_rotation 1")
        sh("adb shell wm user-rotation lock 1")
        sh("adb emu rotate")
        time.sleep(4)
    return bool(size and size[0] > size[1]), size


def main():
    ok, size = ensure_landscape()
    if not ok:
        print(f"[IME-FAIL] 设备未进入横屏，screenshot={size}", flush=True)
        return 1
    print(f"[IME] landscape screenshot {size[0]}x{size[1]}", flush=True)

    cdp = Cdp(PORT)
    # 锁屏/息屏时 Activity 可能在前台但不 draw，WebView 不 commit（实测 target url 恒空）。
    sh("adb shell input keyevent KEYCODE_WAKEUP")
    sh("adb shell wm dismiss-keyguard")
    attached, attach_err = attach_with_recovery(cdp)
    if not attached:
        print(f"[IME-FAIL] CDP attach: {attach_err}", flush=True)
        _, logs = sh("adb logcat -d -t 200")
        print("[IME] logcat tail:\n" + logs[-3000:], flush=True)
        return 1

    # attach 只代表 URL commit，assets 还在加载时 DOM 是空的（首轮真机就栽在这）。
    diag, err, attempts = wait_dom_ready(cdp)
    if not diag or not diag.get("hasInput"):
        ev = collect_diag(cdp, diag, err)
        print("[IME-FAIL] WebView DOM 未就绪: "
              + json.dumps(ev, ensure_ascii=False, default=str), flush=True)
        return 1
    print(f"[IME] DOM ready: {diag.get('href')} input=ok attempts={attempts}", flush=True)

    # 键盘隐藏态先截一张，作为"改后"的对照基线
    shot("r5_landscape_before_ime.png")

    # 设备像素比 + 输入框/发送键位置（DOM 真实盒子）
    geo = dom_geo(cdp)
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

    # 等 IME 真正 shown（最多 12s）
    st = ime_state()
    end = time.time() + 12
    while time.time() < end and not (st["inputShown"] and st["inputViewShown"]):
        time.sleep(1)
        st = ime_state()
    time.sleep(1.4)   # 布局/滚动稳定

    # 键盘弹起后布局已变（顶栏让位 / 输入栏浮底），设备坐标必须全部重取：
    # 沿用弹起前的 send 坐标会点到键盘上——这正是首轮“空态没消失”的原因。
    wr2 = webview_rect(ui_dump()) or wr
    geo2 = dom_geo(cdp) or {}
    rows_before = visible_row_count(cdp)

    # 发一条消息：让“最后一条”是真实气泡而不是开场白
    sh("adb shell input text keyboardcheck")
    time.sleep(0.4)
    send = geo2.get("send")
    if send:
        sx = int(wr2["x"] + (send["x"] + send["w"] / 2) * dpr)
        sy = int(wr2["y"] + (send["y"] + send["h"] / 2) * dpr)
        sh(f"adb shell input tap {sx} {sy}")
    time.sleep(3.0)
    rows_after = visible_row_count(cdp)
    if rows_after <= rows_before:
        print(f"[IME] 发送未生效（rows={rows_before}->{rows_after}），按重取坐标重试一次",
              flush=True)
        wr3 = webview_rect(ui_dump()) or wr2
        geo3 = dom_geo(cdp) or {}
        sh("adb shell input text keyboardcheck")
        time.sleep(0.4)
        send = geo3.get("send")
        if send:
            sx = int(wr3["x"] + (send["x"] + send["w"] / 2) * dpr)
            sy = int(wr3["y"] + (send["y"] + send["h"] / 2) * dpr)
            sh(f"adb shell input tap {sx} {sy}")
        time.sleep(3.0)
        rows_after = visible_row_count(cdp)
    print(f"[IME] message rows: {rows_before} -> {rows_after}", flush=True)

    # 发送后部分输入法会收起：用重取坐标再点一次输入框
    st = ime_state()
    if not (st["inputShown"] and st["inputViewShown"]):
        i = (dom_geo(cdp) or {}).get("input")
        if i:
            sh(f"adb shell input tap {int(wr2['x'] + (i['x'] + i['w'] / 2) * dpr)} "
               f"{int(wr2['y'] + (i['y'] + i['h'] / 2) * dpr)}")
            end = time.time() + 8
            while time.time() < end and not ime_state()["inputViewShown"]:
                time.sleep(1)
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
        // chat pager 会在 #messages 里常驻两条 .msg-row.window-tip（默认 .hidden），
        // 直接取 rows[rows.length-1] 拿到的是隐藏占位条（rect 全 0）→ 误判"最后一条不可见"。
        const visible = (el) => !el.classList.contains("hidden")
            && !el.classList.contains("window-tip")
            && el.getBoundingClientRect().height > 0;
        const rows = Array.from(document.querySelectorAll("#messages .msg-row")).filter(visible);
        const bubbles = Array.from(document.querySelectorAll("#messages .bubble")).filter(visible);
        const chat = document.querySelector("#chatScroll");
        const last = rows.length ? rows[rows.length - 1] : null;
        const lastB = (last && last.querySelector(".bubble"))
            || (bubbles.length ? bubbles[bubbles.length - 1] : null);
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
            focused: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null,
            // 布局取证：极矮横屏规则是否命中、顶栏到底有没有让位
            mediaShort: matchMedia("(orientation: landscape) and (max-height: 260px)").matches,
            topbar: (() => {
                const t = document.querySelector(".topbar");
                if (!t) return null;
                return {display: getComputedStyle(t).display, ...r(t)};
            })(),
            stage: r(document.querySelector("#stage"))
        };
    }""")

    wr2 = webview_rect(ui_dump()) or wr
    metrics["webview"] = wr2
    metrics["screen"] = {"w": size[0], "h": size[1]} if size else None
    metrics["ime"] = {"shown": st2["inputShown"] and st2["inputViewShown"],
                      "frame": frame["frame"] if frame else None,
                      "visibleTop": frame["visibleTop"] if frame else None,
                      "state": st2}

    # ---- 口径换算（device px）----
    def _nonempty(rect):
        # 横屏矮视口下 .msg-row 会被压成 0x0；用 0 高盒量「遮挡」只会得到假通过。
        return rect if rect and rect.get("w") and rect.get("h") else None

    last = _nonempty(metrics.get("lastRow")) or _nonempty(metrics.get("lastBubble"))
    inp = metrics.get("inputBar") or metrics.get("input")
    screen_h = size[1] if size else None
    vis_bottom = wr2["bottom"]
    keyboard_from_screen = (screen_h - vis_bottom) if screen_h else None
    keyboard_from_frame = (frame["frame"][3] - frame["frame"][1] - frame["visibleTop"]
                           if frame else None)

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
        # 布局取证四件套：改布局前后直接对比这四个数
        "viewportH": metrics.get("innerH"),
        "topbarH": (metrics.get("topbar") or {}).get("h"),
        "chatH": (metrics.get("chat") or {}).get("h"),
        "inputBarBottom": (metrics.get("inputBar") or {}).get("bottom"),
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
    check("IME 可见高度（mFrame - visibleTop）与 WebView 缩短量一致（差值 ≤32px）",
          bool(keyboard_from_frame and keyboard_from_screen and
               abs(keyboard_from_frame - keyboard_from_screen) <= 32),
          f"imeVisible={keyboard_from_frame} frame={metrics['ime']['frame']} "
          f"visibleTop={metrics['ime']['visibleTop']} screen-webview={keyboard_from_screen}")
    check("最后一条消息未被键盘遮挡（0px）", occluded == 0, f"occluded={occluded}px")
    # dpr=2.75 下 CSS→设备像素取整会带 ≤1.4px 的误差（实测 1px），不是遮挡。
    check("输入框完整可见（≤2px 取整容差）", input_over <= 2, f"inputOverflow={input_over}px")
    check("聊天区有实际高度（≥120 CSS px，横屏+键盘极矮视口）",
          bool(metrics.get("chatH") and metrics["chatH"] >= 120),
          f"chatH={metrics.get('chatH')} viewportH={metrics.get('viewportH')} "
          f"topbarH={metrics.get('topbarH')} inputBarBottom={metrics.get('inputBarBottom')}")
    check("最后一条消息在聊天可视区内（未被列表容器裁掉）",
          bool(metrics["lastMessageVisible"]),
          f"lastBottomCSS={last and last['bottom']} chatBottomCSS={metrics['chat'] and metrics['chat']['bottom']} "
          f"clippedByChat={last_clipped_by_chat}px")
    check("输入框获得焦点", str(metrics.get("focused")) == "textInput", f"focused={metrics.get('focused')}")

    if not all(RESULTS):
        # 断言失败也留现场：probe 原文 / readyState / URL / WebView 版本。
        print("[IME-DIAG] "
              + json.dumps(collect_diag(cdp, diag), ensure_ascii=False, default=str), flush=True)

    # ---- 收起键盘：证明顶栏/入口会恢复，不是只在矮视口里永久消失 ----
    if st2["inputShown"] and st2["inputViewShown"]:
        sh("adb shell input keyevent KEYCODE_BACK")
        time.sleep(1.8)
    st3 = ime_state()
    restored = cdp.eval("""() => {
        const t = document.querySelector(".topbar");
        const c = document.querySelector("#chatScroll");
        const b = document.querySelector(".inputbar");
        return {
            topbarDisplay: t ? getComputedStyle(t).display : null,
            topbarH: t ? +t.getBoundingClientRect().height.toFixed(1) : null,
            chatH: c ? +c.getBoundingClientRect().height.toFixed(1) : null,
            inputBarBottom: b ? +b.getBoundingClientRect().bottom.toFixed(1) : null,
            viewportH: innerHeight,
            focused: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null
        };
    }""")
    shot("r5_landscape_ime_closed.png")
    print("[IME] restored " + json.dumps(restored, ensure_ascii=False), flush=True)
    check("收起键盘后顶栏恢复（横屏入口不丢失）",
          bool(restored and restored.get("topbarDisplay") not in (None, "none")
               and not (st3["inputShown"] and st3["inputViewShown"])),
          f"imeShown={st3['inputShown'] and st3['inputViewShown']} "
          f"topbar={restored and restored.get('topbarDisplay')} "
          f"topbarH={restored and restored.get('topbarH')} "
          f"viewportH={restored and restored.get('viewportH')}")
    # 键盘收起后视口回到 363 CSS px，旁白/状态条全部回来：聊天区若被压到比一条气泡还矮，
    # 最后一条消息就会被列表容器裁掉。单条气泡实测 48.5 CSS px（#chatScroll 194 轮实测）。
    check("收起键盘后聊天区容得下一条完整气泡（≥48.5 CSS px）",
          bool(restored and restored.get("chatH") and restored["chatH"] >= 48.5),
          f"chatH={restored and restored.get('chatH')} "
          f"viewportH={restored and restored.get('viewportH')} "
          f"topbarH={restored and restored.get('topbarH')} "
          f"inputBarBottom={restored and restored.get('inputBarBottom')}")

    cdp.close()
    print("[IME] summary " + json.dumps({
        "pass": sum(1 for x in RESULTS if x), "total": len(RESULTS)
    }), flush=True)
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
