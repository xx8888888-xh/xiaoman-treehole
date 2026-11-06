#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_e2e_walk.py · 端到端真机走查（云端 AVD，六态截图 + 实测值）
--------------------------------------------------------------------
六个状态，各一张 screencap（SurfaceFlinger 合成结果，不是 CSS 推算）：
  ① 冷启动首屏      e2e_01_cold.png
  ② 发一条消息后    e2e_02_chat.png
  ③ 语音播放中      e2e_03_voice.png   （口型/表情：采样 Stage 的真实 lipSync）
  ④ 浮窗模式        e2e_04_float.png   （appops 授权 + autostart_float 意图）
  ⑤ 横→竖→横回切    e2e_05_landscape.png / e2e_05_back_portrait.png
  ⑥ 20 轮后滚到底   e2e_06_rounds20.png

实测值全部来自运行时：CDP DOM rect、uiautomator WebView bounds、dumpsys window、
Stage._lipState()、screencap PNG 尺寸。脚本只负责采集，不做断言拦截——
本机只跑「采集 + 归因」，问题清单按实测值排序（见 e2e_walk_metrics.json）。

用法：python3 scripts/emulator_e2e_walk.py（App 已启动、TTS server 已在 8903 起）
依赖：requests、websockets
"""
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from android_cdp import Cdp, PKG, sh

PORT = 9224
MAIN = f"{PKG}/.MainActivity"
TTS_BASE = os.environ.get("E2E_TTS_BASE", "http://10.0.2.2:8903")
METRICS = {"states": {}, "notes": []}


def log(msg):
    print("[E2E] " + msg, flush=True)


def shot(name):
    """screencap 取图；返回 PNG 字节数（0 = 失败）。"""
    try:
        data = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=90).stdout
    except Exception as e:
        log(f"截图失败 {name}: {e}")
        return 0
    if data and len(data) > 1024 and data[:1] == b"\x89":
        with open(name, "wb") as f:
            f.write(data)
        log(f"截图 {name} ({len(data)} bytes)")
        return len(data)
    log(f"截图失败 {name}: 非 PNG")
    return 0


def png_size(path):
    """读 PNG IHDR：返回 (宽, 高) device px；失败 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except Exception:
        return None


def ui_dump():
    for _ in range(3):
        sh("adb shell uiautomator dump /sdcard/e2e_dump.xml")
        sh("adb pull /sdcard/e2e_dump.xml ./e2e_dump.xml")
        try:
            with open("e2e_dump.xml", "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            if raw.strip():
                return ET.fromstring(raw)
        except Exception:
            pass
        time.sleep(1)
    return None


def webview_bounds(root):
    if root is None:
        return None
    for node in root.iter("node"):
        if "WebView" in (node.get("class") or ""):
            m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.get("bounds") or "")
            if m:
                return dict(zip(("l", "t", "r", "b"), map(int, m.groups())))
    return None


STATE_JS = r"""
(() => {
  const q = (s) => document.querySelector(s);
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {l: +r.left.toFixed(1), t: +r.top.toFixed(1), r: +r.right.toFixed(1),
            b: +r.bottom.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1)};
  };
  const rows = [...document.querySelectorAll('#messages .msg-row:not(.hidden)')];
  const last = rows.length ? rows[rows.length - 1] : null;
  const lastB = last ? (last.querySelector('.bubble') || last) : null;
  const area = q('#chatArea') || q('#messages');
  const empty = q('#emptyState');
  const lip = window.Stage && Stage._lipState ? Stage._lipState() : null;
  const face = window.Stage && Stage._faceState ? Stage._faceState() : null;
  return {
    url: location.href, dpr: window.devicePixelRatio || 1,
    iw: window.innerWidth, ih: window.innerHeight,
    rows: rows.length,
    busy: typeof window.__isBusy === 'function' ? window.__isBusy() : null,
    emptyVisible: empty ? !(empty.classList.contains('hidden') || empty.getBoundingClientRect().height < 1) : null,
    topbar: rect(q('.topbar')),
    stage: rect(q('#stage')),
    area: rect(area),
    inputbar: rect(q('.inputbar')),
    lastBubble: rect(lastB),
    scroll: area ? {top: Math.round(area.scrollTop), h: area.scrollHeight,
                    ch: area.clientHeight,
                    gap: Math.round(area.scrollHeight - area.scrollTop - area.clientHeight)} : null,
    lip: lip, face: face,
    motion: (window.Appearance && Appearance.get) ? Appearance.get().motion : null,
    reduceMotion: (typeof matchMedia === 'function')
      ? matchMedia('(prefers-reduced-motion: reduce)').matches : null,
    recent: rows.slice(-6).map((r) => ((r.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 24))),
    emptyRows: rows.filter((r) => !(r.innerText || '').replace(/\s+/g, '')).length,
    chain: (window.TTS && TTS.chain) ? TTS.chain(API.loadCfg()) : null
  };
})()
"""

INIT_JS = r"""
(() => ({
  api: typeof window.API,
  bind: typeof window.__isBusy,
  cfg: (window.API && API.loadCfg) ? API.loadCfg() : null,
  stage: !!(window.Stage && Stage._lipState)
}))()
"""

SET_CFG_JS = ("(() => { API.saveCfg(" + json.dumps({"tts": "server", "ttsServer": TTS_BASE}) +
              "); return TTS.chain(API.loadCfg()); })()")

PROBE_JS = r"""
(() => {
  const i = document.querySelector('#textInput');
  const b = document.querySelector('#sendBtn');
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
  };
  return {input: rect(i), send: rect(b), value: i ? i.value : null,
          disabled: b ? !!b.disabled : null, rows: document.querySelectorAll('#messages .msg-row:not(.hidden)').length,
          active: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : '',
          dpr: window.devicePixelRatio || 1, ih: window.innerHeight};
})()
"""

SEND_JS = r"""
(() => {
  const i = document.querySelector('#textInput');
  const b = document.querySelector('#sendBtn');
  if (!i || !b) return {err: 'no input/send'};
  i.value = __TXT__;
  i.dispatchEvent(new Event('input', {bubbles: true}));
  if (b.disabled) return {err: 'send disabled'};
  b.click();
  return {ok: true, rows: document.querySelectorAll('#messages .msg-row:not(.hidden)').length};
})()
"""


def tap(cdp, key, origin):
    p = cdp.eval(PROBE_JS)
    box = p.get(key)
    if not box:
        return None
    dpr = p.get("dpr") or 1
    x = int(origin[0] + (box["left"] + box["right"]) / 2 * dpr)
    y = int(origin[1] + (box["top"] + box["bottom"]) / 2 * dpr)
    sh(f"adb shell input tap {x} {y}")
    return (x, y)


def wait_rows(cdp, target, timeout=40):
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        last = cdp.eval(PROBE_JS)
        if last.get("rows", 0) >= target and not last.get("busy"):
            return True, last
        time.sleep(0.6)
    return False, last


TOTAL_JS = r"""
(() => ({
  busy: typeof window.__isBusy === 'function' ? window.__isBusy() : null,
  total: (window.ChatPager && ChatPager.stats) ? ChatPager.stats().total : null,
  rows: document.querySelectorAll('#messages .msg-row:not(.hidden)').length
}))()
"""


def wait_total(cdp, target, timeout=30):
    """长会话：DOM 行数被 ChatPager 虚拟化封顶（VIEW=14），必须看 stats().total。"""
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        try:
            last = cdp.eval(TOTAL_JS) or {}
        except Exception:
            last = {}
        if (last.get("total") or 0) >= target and not last.get("busy"):
            return True, last
        time.sleep(0.6)
    return False, last


TTS_HOOK_JS = r"""
(() => {
  if (window.__ttsHooked) return 'already';
  const o = TTS.speakSafe;
  window.__ttsLog = [];
  window.TTS.speakSafe = function (t, f, end, silent) {
    const e = {txt: String(t || '').slice(0, 10), at: Math.round(performance.now())};
    __ttsLog.push(e);
    return o.call(TTS, t, f, (ok) => {
      e.ok = ok === true; e.endAt = Math.round(performance.now());
      if (typeof end === 'function') end(ok);
    }, (r) => {
      e.silent = true; e.spoke = r && r.spoke; e.prov = r && r.provider;
      if (typeof silent === 'function') silent(r);
    });
  };
  window.__ttsHooked = 1;
  return 'hooked';
})()
"""


def sample_lip(cdp, seconds=3.0, path="e2e_03_voice.png", shot_at=0.45):
    """高频采样 Stage 真实口型状态；峰值时截图（全程没到阈值就在末尾补一张）。"""
    t0 = time.time()
    samples = []
    shot_done = False
    shot_size = 0
    shot_at_s = None
    last = {}
    while time.time() - t0 < seconds:
        try:
            last = cdp.eval("(() => ({lip: Stage._lipState(), "
                            "face: Stage._faceState ? Stage._faceState() : null}))") or {}
        except Exception as e:
            samples.append({"err": str(e)[:80]})
            time.sleep(0.1)
            continue
        lip = last.get("lip") or {}
        open_v = float(lip.get("lipOpen") or 0)
        samples.append({"t": round(time.time() - t0, 3), "lipOpen": round(open_v, 3)})
        if not shot_done and open_v >= shot_at:
            shot_size = shot(path)
            shot_at_s = round(time.time() - t0, 3)
            shot_done = True
        time.sleep(0.04)
    if not shot_done:
        shot_size = shot(path)
    peak = max([x.get("lipOpen") or 0 for x in samples if "lipOpen" in x] or [0])
    nz = len([x for x in samples if (x.get("lipOpen") or 0) > 0.05])
    return {"samples": samples, "n": len(samples), "maxLip": round(peak, 3),
            "nonZeroFrames": nz, "shotAtPeak": shot_at_s, "shotBytes": shot_size,
            "face": last.get("face")}


def dumpsys_windows():
    _, out = sh("adb shell dumpsys window windows", timeout=60)
    return out


def overlays(out):
    """APPLICATION_OVERLAY 窗口数量 + frame（dumpsys 输出里 ty=APPLICATION_OVERLAY 段落）。"""
    frames, buf = [], []
    for ln in out.splitlines():
        if "Window #" in ln or "mAttrs" in ln:
            if buf and any("ty=APPLICATION_OVERLAY" in b for b in buf):
                frames.append(buf[:])
            buf = []
        buf.append(ln)
    if buf and any("ty=APPLICATION_OVERLAY" in b for b in buf):
        frames.append(buf[:])
    return len(frames)


def wm_size():
    _, out = sh("adb shell wm size")
    m = re.search(r"(\d+)x(\d+)", out or "")
    _, dens = sh("adb shell wm density")
    d = re.search(r"(\d+)", dens or "")
    return (int(m.group(1)), int(m.group(2))) if m else None, int(d.group(1)) if d else None


def main():
    dev, dens = wm_size()
    METRICS["device"] = {"screenPx": dev, "densityDpi": dens}
    log(f"设备 {dev} dpi={dens}")

    cdp = Cdp(port=PORT)
    try:
        url = cdp.attach("index.html", timeout=120)
    except Exception as e:
        log(f"附着失败: {e}")
        METRICS["fatal"] = f"attach failed: {e}"
        with open("e2e_walk_metrics.json", "w", encoding="utf-8") as f:
            json.dump(METRICS, f, ensure_ascii=False, indent=2)
        sys.exit(1)
    log(f"已附着 {url}")

    ready = {}
    for _ in range(40):
        try:
            ready = cdp.eval(INIT_JS) or {}
        except Exception:
            ready = {}
        if ready.get("api") == "object" and ready.get("bind") == "function":
            break
        time.sleep(1)
    log(f"页面就绪 api={ready.get('api')} bind={ready.get('bind')} stage={ready.get('stage')}")

    origin = None
    vb = webview_bounds(ui_dump())
    if vb:
        origin = (vb["l"], vb["t"])
        log(f"WebView bounds={vb}")

    # ---------------- ① 冷启动首屏 ----------------
    time.sleep(2)
    s1 = cdp.eval(STATE_JS) or {}
    b1 = shot("e2e_01_cold.png")
    METRICS["states"]["01_cold"] = {"shot": "e2e_01_cold.png", "bytes": b1,
                                     "png": png_size("e2e_01_cold.png"),
                                     "probe": s1, "webview": vb}

    # ---------------- ② 发一条消息后 ----------------
    rows0 = s1.get("rows") or 0
    if origin:
        tap(cdp, "input", origin)
        time.sleep(0.8)
        sh('adb shell input text "jin%s tian%s xin%s qing%s bu%s hao"')
        time.sleep(0.8)
        sh("adb shell input keyevent 111")   # 收键盘：截图要干净的可视区
        time.sleep(1.2)
        tap(cdp, "send", origin)
    else:
        log("无 WebView 原点，退回 JS 触发发送")
        cdp.eval(SEND_JS.replace("__TXT__", json.dumps("今天心情不好")))
    grew, p2 = wait_rows(cdp, rows0 + 2)
    time.sleep(1.2)
    s2 = cdp.eval(STATE_JS) or {}
    b2 = shot("e2e_02_chat.png")
    METRICS["states"]["02_chat"] = {"shot": "e2e_02_chat.png", "bytes": b2,
                                    "png": png_size("e2e_02_chat.png"),
                                    "replyArrived": grew, "probe": s2}

    # ---------------- ③ 语音播放中（口型/表情） ----------------
    try:
        chain = cdp.eval(SET_CFG_JS)
    except Exception as e:
        chain = f"err {e}"[:100]
    log(f"TTS 链: {chain}")
    try:
        hook = cdp.eval(TTS_HOOK_JS)
    except Exception as e:
        hook = f"err {e}"[:80]
    # 云端 AVD 默认命中 prefers-reduced-motion（实测 reduce=True）→ Stage 按契约把口型
    # 归零。第 ③ 项要拍"语音播放中的口型/表情"，这里按用户手动开启动效的路径打开
    # （Appearance.motion=true 等价于设置页关掉"减少动效"），仅影响本次走查截图。
    try:
        motion = cdp.eval("(() => { if (Appearance.setSwitch) Appearance.setSwitch('motion', true);"
                          " Stage.setMotionEnabled(true);"
                          " return (window.Appearance && Appearance.get) ? Appearance.get().motion : 'n/a'; })()")
    except Exception as e:
        motion = f"err {e}"[:80]
    log(f"动效已开: {motion}")
    rows1 = (s2.get("rows") or 0)
    if origin:
        tap(cdp, "input", origin)
        time.sleep(0.6)
        sh('adb shell input text "xie%s xie%s ni%s pei%s wo"')
        time.sleep(0.6)
        sh("adb shell input keyevent 111")
        time.sleep(1.0)
        tap(cdp, "send", origin)
    else:
        cdp.eval(SEND_JS.replace("__TXT__", json.dumps("谢谢你陪我")))
    ok3, p3 = wait_rows(cdp, rows1 + 2, timeout=40)
    voice = sample_lip(cdp, seconds=6.0)
    try:
        events = cdp.eval("window.__ttsLog || []")
    except Exception:
        events = None
    s3 = cdp.eval(STATE_JS) or {}
    METRICS["states"]["03_voice"] = {"shot": "e2e_03_voice.png" if voice["shotBytes"] else None,
                                      "replyArrived": ok3, "chain": chain, "hook": hook,
                                      "motionForced": motion, "ttsEvents": events,
                                      "lip": voice, "probe": s3}

    # ---------------- ④ 浮窗模式 ----------------
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW allow")
    sh("adb shell input keyevent 3")
    time.sleep(1.5)
    sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000")
    time.sleep(5)
    sh("adb shell input keyevent 3")   # HOME：桌宠覆盖在桌面之上
    time.sleep(2.5)
    wins = dumpsys_windows()
    n_over = overlays(wins)
    b4 = shot("e2e_04_float.png")
    METRICS["states"]["04_float"] = {"shot": "e2e_04_float.png", "bytes": b4,
                                      "png": png_size("e2e_04_float.png"),
                                      "overlayWindows": n_over}

    # 回到主界面（浮窗先落到后台；后续口径都在主界面）
    sh(f"adb shell am force-stop {PKG}")
    time.sleep(1)
    sh(f"adb shell am start -n {MAIN}")
    for _ in range(40):
        time.sleep(1)
        try:
            if (cdp.eval(INIT_JS) or {}).get("bind") == "function":
                break
        except Exception:
            pass
    time.sleep(1.5)

    # ---------------- ⑤ 横屏 → 竖屏 → 切回 ----------------
    sh("adb shell settings put system accelerometer_rotation 0")
    sh("adb shell settings put system user_rotation 0")
    time.sleep(2)
    s5a = cdp.eval(STATE_JS) or {}
    vb5a = webview_bounds(ui_dump())
    sh("adb shell settings put system user_rotation 1")
    time.sleep(3)
    s5b = cdp.eval(STATE_JS) or {}
    vb5b = webview_bounds(ui_dump())
    b5a = shot("e2e_05_landscape.png")
    sh("adb shell settings put system user_rotation 0")
    time.sleep(3)
    s5c = cdp.eval(STATE_JS) or {}
    vb5c = webview_bounds(ui_dump())
    b5c = shot("e2e_05_back_portrait.png")
    METRICS["states"]["05_rotate"] = {
        "portraitBefore": {"probe": s5a, "webview": vb5a},
        "landscape": {"probe": s5b, "webview": vb5b, "shot": "e2e_05_landscape.png", "bytes": b5a},
        "portraitBack": {"probe": s5c, "webview": vb5c,
                         "shot": "e2e_05_back_portrait.png", "bytes": b5c}
    }

    # ---------------- ⑥ 连续 20 轮后滚到底 ----------------
    _, tot0 = wait_total(cdp, 0, timeout=2)
    base_total = int(tot0.get("total") or 0)
    base_rows = int(tot0.get("rows") or 0)
    rounds, fails = [], 0
    js_tpl = SEND_JS.replace("__TXT__", json.dumps("第N轮：今天有点累，想和你说说"))
    for n in range(1, 21):
        try:
            r = cdp.eval(js_tpl.replace("第N轮", f"第{n}轮"))
        except Exception as e:
            r = {"err": str(e)[:80]}
        ok, p = wait_total(cdp, base_total + 2 * n, timeout=25)
        if not ok:
            fails += 1
        rounds.append({"n": n, "total": (p or {}).get("total"), "rows": (p or {}).get("rows"), "ok": ok,
                       "err": (r or {}).get("err") if isinstance(r, dict) else None})
        time.sleep(0.3)
    try:
        cdp.eval("(() => { const a = document.querySelector('#chatArea') || document.querySelector('#messages');"
                 " if (a) { a.scrollTop = a.scrollHeight; return a.scrollTop; } return null; })()")
    except Exception:
        pass
    time.sleep(1.5)
    s6 = cdp.eval(STATE_JS) or {}
    t6 = cdp.eval(TOTAL_JS) or {}
    b6 = shot("e2e_06_rounds20.png")
    METRICS["states"]["06_rounds20"] = {"shot": "e2e_06_rounds20.png", "bytes": b6,
                                         "png": png_size("e2e_06_rounds20.png"),
                                         "roundsFails": fails, "rounds": rounds,
                                         "baseTotal": base_total, "baseRows": base_rows,
                                         "total": t6.get("total"), "domRows": t6.get("rows"),
                                         "probe": s6}

    METRICS["ok"] = True
    with open("e2e_walk_metrics.json", "w", encoding="utf-8") as f:
        json.dump(METRICS, f, ensure_ascii=False, indent=2)
    summary = {
        "cold_rows": rows0,
        "cold_empty": (s1.get("emptyVisible")),
        "chat_rows": s2.get("rows"),
        "chat_gap": (s2.get("scroll") or {}).get("gap"),
        "voice_maxLip": voice.get("maxLip"),
        "voice_nz": voice.get("nonZeroFrames"),
        "voice_shot": bool(voice.get("shotBytes")),
        "float_overlay_windows": n_over,
        "landscape_iw": s5b.get("iw"), "landscape_ih": s5b.get("ih"),
        "back_portrait_ih": s5c.get("ih"),
        "rounds_total": s6.get("rows"), "rounds_fails": fails,
        "rounds_gap": (s6.get("scroll") or {}).get("gap")
    }
    log("汇总: " + json.dumps(summary, ensure_ascii=False))
    try:
        cdp.close()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
