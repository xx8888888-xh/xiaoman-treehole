#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_chat_check.py · AVD 主界面「消息真的发出去了」冒烟
--------------------------------------------------------------------
背景（为什么写这个脚本）：
  android-ci.yml 原来用 `adb shell input tap 140 615` 点输入框、`tap 296 615`
  点发送。这两个数字是照 pixel_4 截图猜的，实测落在舞台空白区，于是：
  textInput 一直是空的、sendBtn 一直 disabled、聊天根本没发出去——而后面
  没有任何断言，流水线照样"绿"。绿的那一轮等于什么都没验。

现在的做法：
  1) 点击坐标不再写死：用 WebView DevTools（debug 包已开 setWebContentsDebuggingEnabled）
     取 #textInput / #sendBtn 的 getBoundingClientRect()，乘 devicePixelRatio，
     再加 WebView 在屏幕上的原点（uiautomator dump 里 android.webkit.WebView 的 bounds）。
     uiautomator dump 对 WebView **DOM** 是盲区（本仓库实测空壳），但 WebView 容器本身
     在 dump 里，所以原点从这里取，DOM 盒子从 CDP 取——两者都是真实运行时数据。
  2) 点完必须证明焦点真的进了输入框：document.activeElement.id === "textInput"。
  3) 发完必须同时满足三条：textInput.value 非空 → sendBtn.disabled === false
     → #messages 的 .msg-row 比发送前多，且 logcat 里有 [probe] msg me / msg them。
     任一条不成立就 exit 1。

用法：python3 scripts/emulator_chat_check.py（App 需已启动；CI 里由 workflow 起）
依赖：requests、websockets
退出码：0=全通过；1=有失败项（逐项 [CHAT-PASS]/[CHAT-FAIL]）
"""
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from android_cdp import PKG, Cdp, sh

PORT = 9224
RESULTS = []
TEXT = "jin tian xin qing bu hao"


def check(name, ok, info=""):
    tag = "[CHAT-PASS]" if ok else "[CHAT-FAIL]"
    print(f"{tag} {name}" + (f" | {info}" if info else ""), flush=True)
    RESULTS.append(ok)
    return ok


def shot(name):
    try:
        data = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=90).stdout
    except Exception:
        return
    if data and len(data) > 1024 and data[:1] == b"\x89":
        with open(name, "wb") as f:
            f.write(data)


def ui_dump():
    """uiautomator dump → 解析后的 XML 根节点（失败返回 None，调用方降级）。"""
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


def webview_origin(root):
    """WebView 容器在屏幕上的原点 (x, y)：dump 里 class=android.webkit.WebView 的 bounds。"""
    if root is None:
        return None
    for node in root.iter("node"):
        if "WebView" in (node.get("class") or ""):
            m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.get("bounds") or "")
            if m:
                l, t, r, b = map(int, m.groups())
                return l, t, r, b
    return None


PROBE_JS = r"""
(() => {
  const i = document.querySelector('#textInput');
  const b = document.querySelector('#sendBtn');
  const msgs = document.querySelectorAll('#messages .msg-row:not(.hidden)');
  const rect = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
  };
  return {
    input: rect(i),
    send: rect(b),
    value: i ? i.value : null,
    disabled: b ? !!b.disabled : null,
    busy: typeof window.__isBusy === "function" ? window.__isBusy() : null,
    rows: msgs.length,
    active: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : '',
    dpr: window.devicePixelRatio || 1,
    innerHeight: window.innerHeight
  };
})()
"""

# 页面是否真的初始化完成：app.js 在 API 缺失时会整段中断（bind 不执行），
# 表现是输入框永远 disabled、消息发不出去。先单独断言，避免把根因误报成"点偏"。
INIT_JS = r"""
(() => ({
  api: typeof window.API,
  bind: typeof window.__isBusy,
  rows: document.querySelectorAll('#messages .msg-row:not(.hidden)').length
}))()
"""


def tap_center(cdp, key, origin):
    """按 DOM 真实盒子中心点击；返回 (x, y) 或 None。"""
    p = cdp.eval(PROBE_JS)
    box = p.get(key)
    if not box:
        return None, p
    ox, oy = origin[0], origin[1]
    dpr = p.get("dpr") or 1
    x = int(ox + (box["left"] + box["right"]) / 2 * dpr)
    y = int(oy + (box["top"] + box["bottom"]) / 2 * dpr)
    sh(f"adb shell input tap {x} {y}")
    return (x, y), p


def probes():
    _, out = sh("adb logcat -d -s XiaoManWeb:V", timeout=60)
    return out


def wait_probe(pattern, timeout=60):
    """页面侧探针是异步（注入后 1.2s 起、页面未就绪会重试）：等到出现或超时。"""
    end = time.time() + timeout
    while time.time() < end:
        line = next((ln.strip() for ln in probes().splitlines() if pattern in ln), "")
        if line:
            return line
        time.sleep(2)
    return ""


def dump_webview_logs():
    """页面起不来时留证据：chromium/WebView 的日志 + 当前前台 Activity（不过 shell 管道，Windows 也能跑）。"""
    _, act = sh("adb shell dumpsys activity activities", timeout=60)
    cur = next((ln.strip() for ln in act.splitlines() if "mResumedActivity" in ln), "")
    print(f"[CHAT-DIAG] 前台 Activity: {cur[:160]}", flush=True)
    _, lg = sh("adb logcat -d", timeout=90)
    hits = [ln for ln in lg.splitlines()
            if re.search(r"chromium|WebView|net::ERR|console", ln, re.I)]
    for ln in hits[-25:]:
        print("[CHAT-DIAG] " + ln[:220], flush=True)


def main():
    # 1) 附着 WebView（等 page 真正 commit；慢 runner 上冷启动可能要几十秒）
    cdp = Cdp(port=PORT)
    try:
        url = cdp.attach("index.html", timeout=90)
    except Exception as e:
        check("附着主界面 WebView（DevTools）", False, str(e)[:200])
        dump_webview_logs()
        print(f"[CHAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过", flush=True)
        sys.exit(1)
    print(f"[CHAT] 已附着: {url}", flush=True)

    # 2) 等页面把输入框渲染出来，并确认 app.js 真的初始化完（API/绑定都在）
    ready = False
    init = {}
    for _ in range(30):
        try:
            init = cdp.eval(INIT_JS)
            if init.get("api") == "object" and init.get("bind") == "function":
                ready = True
                break
        except Exception:
            pass
        time.sleep(1)
    check("主界面 DOM 就绪且 app.js 初始化完成（window.API / __isBusy）", ready,
          f"api={init.get('api')} bind={init.get('bind')} rows={init.get('rows')}")
    if not ready:
        dump_webview_logs()

    # 3) 注入探针：主界面这条 WebView 必须拿得到 window.AndroidTTS
    tts_line = wait_probe("[probe] AndroidTTS injected=1 /www/index.html", 60)
    check("主界面 WebView 注入 AndroidTTS（[probe] AndroidTTS injected=1）",
          bool(tts_line), tts_line[:100] or "logcat 无注入探针")

    if not ready:
        print(f"[CHAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过", flush=True)
        sys.exit(1)
    time.sleep(1)

    # 4) WebView 屏幕原点（ui-dump 里的容器 bounds；拿不到就如实报 FAIL 而不是猜）
    box = webview_origin(ui_dump())
    if not box:
        check("uiautomator dump 能拿到 WebView 容器 bounds", False,
              "dump 中没有 android.webkit.WebView 节点，拒绝用硬编码坐标继续")
        print("[CHAT-SUMMARY] %d/%d 通过" % (sum(RESULTS), len(RESULTS)), flush=True)
        sys.exit(1)
    origin = (box[0], box[1])
    print(f"[CHAT] WebView bounds={box} origin={origin}", flush=True)

    p0 = cdp.eval(PROBE_JS)
    rows_before = p0["rows"]
    check("输入框/发送键 DOM 可见", bool(p0.get("input") and p0.get("send")),
          f"input={p0.get('input')} send={p0.get('send')} rows={rows_before}")

    # 3) 点输入框（中心来自真实 DOM 盒子），并验证焦点真的进去了
    sh("adb logcat -c")
    xy, p1 = tap_center(cdp, "input", origin)
    focused = False
    for _ in range(8):
        time.sleep(0.5)
        p1 = cdp.eval(PROBE_JS)
        if p1.get("active") == "textInput":
            focused = True
            break
    check("点击输入框后获得焦点（activeElement=textInput）", focused,
          f"tap={xy} active={p1.get('active')}")

    # 4) 输入文字（adb input text 只支持 ASCII，用拼音；%s=空格）
    typed_text = TEXT.replace(" ", "%s")
    sh(f'adb shell input text "{typed_text}"')
    typed = False
    for _ in range(10):
        time.sleep(0.4)
        p2 = cdp.eval(PROBE_JS)
        if (p2.get("value") or "").strip():
            typed = True
            break
    else:
        p2 = cdp.eval(PROBE_JS)
    check("textInput.text 非空（真的打进字了）", typed, f"value={p2.get('value')!r}")
    check("sendBtn 不再是 disabled", p2.get("disabled") is False,
          f"disabled={p2.get('disabled')} busy={p2.get('busy')}")

    # 5) 收软键盘再点发送（坐标同样按 DOM 盒子动态取）
    sh("adb shell input keyevent 111")
    time.sleep(1.2)
    xy2, _ = tap_center(cdp, "send", origin)

    # 6) 等新气泡：msg-row 变多 + 输入框被清空
    grew, p3 = False, None
    for _ in range(30):
        time.sleep(1)
        try:
            p3 = cdp.eval(PROBE_JS)
        except Exception:
            continue
        if p3["rows"] > rows_before and not (p3.get("value") or "").strip():
            grew = True
            break
    check("#messages 出现新气泡（.msg-row 增加）", grew,
          f"before={rows_before} after={p3['rows'] if p3 else '?'} tap={xy2} "
          f"keyboardClosed_h={p3['innerHeight'] if p3 else '?'}")
    shot("emulator_02_chat.png")

    # 7) logcat 双向探针：本轮用户消息与回复都渲染过
    time.sleep(3)
    lg = probes()
    ok_me = "[probe] msg me" in lg
    ok_them = "[probe] msg them" in lg
    check("聊天链路：用户消息已渲染（[probe] msg me）", ok_me)
    check("聊天链路：小满已回复（[probe] msg them）", ok_them,
          next((l.strip()[:80] for l in lg.splitlines() if "msg them" in l), "无回复探针"))
    _, fatal = sh("adb logcat -d")
    bad = [l for l in fatal.splitlines() if "AndroidRuntime" in l and PKG in l]
    check("聊天全程 logcat 无 FATAL", not bad, "\n".join(bad[-2:])[:150])

    cdp.close()
    ok = all(RESULTS)
    print(f"[CHAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
