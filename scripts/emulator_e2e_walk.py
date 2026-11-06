#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_e2e_walk.py · 全链路回归走查（云端 AVD，单 run 一条链跑完）
--------------------------------------------------------------------
链序（同一 run 内顺序执行，每步一行 metrics + 一条汇总表行）：
  ① 冷启动首屏        e2e_01_cold.png
  ② 发一条消息后      e2e_02_chat.png
  ③ 语音播放 + 口型    e2e_03_voice.png       （默认极简 motion=false）
  ③b 同段语音满帧对照  e2e_03b_voice_motion.png（motion=true）
  ④ 浮窗模式          e2e_04_float.png       （appops 授权 + autostart_float 意图）
  ④b 同日重入          （杀进程再开：不得追加第二次时段问候）
  ⑤ 横→竖→回横屏       e2e_05_landscape.png / e2e_05_portrait.png / e2e_05_back_landscape.png
  ⑥ 20 轮后滚到底      e2e_06_rounds20.png
  ⑦ 语音失败提示       （出现 → 存活期自动收起 → 重绘不复活）

实测值全部来自运行时：CDP DOM rect、uiautomator WebView bounds、dumpsys window、
Stage._lipState()、screencap PNG 尺寸。

硬门（GateFail → metrics + 汇总表 + exit 1，CI 变红）：
  - replyArrived=false / 没有新的 speakSafe 事件 → 该步骤 FAIL，不允许对静音采样；
  - 口型 0 帧（motion=false / motion=true 各测一次）；
  - 浮窗没有 APPLICATION_OVERLAY 窗口；
  - 同日重启后 total 增加或时段问候变多（重入不该重复问候）；
  - 键盘收起后横屏 chatH < 48.5（放不下一颗气泡）；
  - 20 轮里有任何一轮回复没到齐；
  - 语音失败提示不出现 / 到期不消失 / 重绘又复活 / 失败日志丢失。
汇总表写到 e2e_walk_summary.md（artifact 一并上传），失败也写。

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
from e2e_walk_gate import GateFail, require_reply, require_true, require_voice

PORT = 9224
MAIN = f"{PKG}/.MainActivity"
APP_URL = "https://appassets.androidplatform.net/www/index.html"
TTS_BASE = os.environ.get("E2E_TTS_BASE", "http://10.0.2.2:8903")
METRICS = {"states": {}, "notes": []}


def log(msg):
    print("[E2E] " + msg, flush=True)


def dump_metrics():
    with open("e2e_walk_metrics.json", "w", encoding="utf-8") as f:
        json.dump(METRICS, f, ensure_ascii=False, indent=2)


# 汇总表：每步一行（步骤 / 通过与否 / 关键数字）；同一 step 重复写就原地更新
SUMMARY = []


def set_row(step, ok, key):
    row = {"step": step, "ok": bool(ok), "key": str(key)}
    for i, r in enumerate(SUMMARY):
        if r["step"] == step:
            SUMMARY[i] = row
            break
    else:
        SUMMARY.append(row)
    log(f"[{'PASS' if ok else 'FAIL'}] {step} · {key}")


def summary_text():
    lines = ["| 步骤 | 结果 | 关键数字 |", "|---|---|---|"]
    for r in SUMMARY:
        lines.append("| %s | %s | %s |" % (r["step"], "PASS" if r["ok"] else "FAIL", r["key"]))
    return "\n".join(lines)


def write_summary():
    text = summary_text()
    try:
        with open("e2e_walk_summary.md", "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as e:
        log(f"写汇总表失败: {e}")
    log("汇总表:\n" + text)
    return text


def gate(step, fn, *args):
    """硬门：失败 → 写 metrics + 汇总表 + 非零退出。

    上一轮的 0/143 是"消息没发出去、流程继续跑、对静音采样还判过"造成的；
    这里让该步骤直接 FAIL（workflow 不吞退出码，CI 随之为红）。
    """
    try:
        fn(*args)
    except GateFail as e:
        METRICS["ok"] = False
        METRICS.setdefault("failures", []).append({"step": step, "reason": str(e)})
        set_row(step, False, str(e))
        dump_metrics()
        write_summary()
        log("FAIL " + str(e))
        raise SystemExit(1)


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
    chatScroll: rect(q('#chatScroll')),
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

FILL_JS = r"""
(() => {
  const i = document.querySelector('#textInput');
  if (!i) return {err: 'no input'};
  i.value = __TXT__;
  i.dispatchEvent(new Event('input', {bubbles: true}));
  return {ok: true, value: i.value};
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


def wait_row_count(cdp, target, timeout=20):
    """只看 DOM 行数，不等 busy（说话中也要能确认"用户消息已落屏"）。"""
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        try:
            last = cdp.eval(PROBE_JS) or {}
        except Exception:
            last = {}
        if (last.get("rows") or 0) >= target:
            return True, last
        time.sleep(0.4)
    return False, last


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
  window.__pageTag = 'p' + Math.round(performance.now());
  window.__lipFeed = {frames: 0, max: 0, firstAt: 0, lastAt: 0, uiMax: 0};
  window.TTS.speakSafe = function (t, f, end, silent) {
    const e = {txt: String(t || '').slice(0, 10), at: Math.round(performance.now()),
               frames: 0, maxLevel: 0};
    __ttsLog.push(e);
    const wrapped = function (lv) {
      const F = window.__lipFeed;
      F.frames += 1;
      e.frames += 1;
      const v = Number(lv) || 0;
      if (v > F.max) F.max = v;
      if (v > e.maxLevel) e.maxLevel = v;
      if (!F.firstAt) F.firstAt = Math.round(performance.now());
      F.lastAt = Math.round(performance.now());
      if (window.Stage && Stage._lipState) {
        const st = Stage._lipState();
        if (st.lipOpen > F.uiMax) F.uiMax = st.lipOpen;
      }
      if (typeof f === 'function') f(lv);
    };
    return o.call(TTS, t, wrapped, (ok) => {
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

LIP_PROBE_JS = r"""
(() => {
  const st = (window.Stage && Stage._lipState) ? Stage._lipState() : null;
  const face = (window.Stage && Stage._faceState) ? Stage._faceState() : null;
  const F = window.__lipFeed || {};
  const LS = (window.Stage && Stage._lipStats) ? Stage._lipStats() : null;
  return {
    t: Math.round(performance.now() * 10) / 10,
    lipOpen: st ? st.lipOpen : null,
    lipHold: st ? st.lipHold : null,
    // 引擎里的真实参数（不是 Stage 的局部变量）：嘴到底动没动看这两个
    lipSync: face ? face.lipSync : null,
    mouth: face ? face.mouth : null,
    mix: face ? face.mouthMix : null,
    faceNull: face === null,
    frames: F.frames || 0,
    // 真正画到画布的说话帧（motion 开≈满帧 / 关≈15fps）：证明"只省帧不削幅"
    paint: LS ? LS.paints : null,
    feedMax: F.max || 0,
    mode: (window.Stage && Stage._mode) ? Stage._mode() : null,
    raf: (window.Stage && Stage._rafActive) ? Stage._rafActive() : null,
    tag: window.__pageTag || ''
  };
})()
"""


def type_and_send(cdp, origin, text):
    """输入并发送一条消息，带"真的落下去了"的校验与兜底。

    慢 AVD 上 tap 输入框后 IME 附着可能 >1s；固定 0.6s 就 adb input text 会把字打在
    还没聚焦的框上（本脚本上一版就是这样丢了第 ③ 条）。这里逐次校验 #textInput.value，
    重试两次仍为空就走 JS 兜底，绝不带着空输入框继续往下走。
    """
    typed = text.replace(" ", "%s")
    value = ""
    tries = 0
    for attempt in range(2):
        tries = attempt + 1
        if origin:
            tap(cdp, "input", origin)
        time.sleep(1.4 if attempt else 0.9)     # 等 IME 真正附着
        sh(f'adb shell input text "{typed}"')
        time.sleep(0.9)
        p = cdp.eval(PROBE_JS) or {}
        value = (p.get("value") or "").strip()
        if value:
            break
        log(f"输入第 {tries} 次没落进输入框（value 空），重试")
    sh("adb shell input keyevent 111")          # 收键盘：截图要干净的可视区
    time.sleep(1.0)
    p = cdp.eval(PROBE_JS) or {}
    value = (p.get("value") or "").strip()
    mode = "tap"
    # 记下发送瞬间的页面时钟：之后只认 at >= sentAt 的 speakSafe 事件，
    # 避免把上一轮回复的后续气泡当成这一轮的"新语音"（上一版就是这样误判过）。
    sent_at = cdp.eval("Math.round(performance.now())")
    if value:
        if origin:
            tap(cdp, "send", origin)
        else:
            cdp.eval(SEND_JS.replace("__TXT__", json.dumps(text)))
            mode = "js-send"
    else:
        cdp.eval(SEND_JS.replace("__TXT__", json.dumps(text)))
        mode = "js-fallback"
        log("输入框始终为空，已走 JS 兜底发送")
    time.sleep(0.8)
    after = cdp.eval(PROBE_JS) or {}
    return {"mode": mode, "tries": tries, "value": value,
            "disabled": p.get("disabled"), "rowsAfterSend": after.get("rows"),
            "sentAt": sent_at}


def wait_voice(cdp, n0, timeout=25.0, min_at=None):
    """等一条新的 speakSafe 事件（说明回复真的在播），返回事件与等待时长。

    min_at：页面时钟下限（发送瞬间）。上一轮回复的后续气泡可能在"打字"期间才
    开始播，只看 len(__ttsLog) 会把它们当成本轮新语音；必须按 at >= min_at 过滤。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            logv = cdp.eval("window.__ttsLog || []") or []
        except Exception:
            logv = []
        fresh = [e for e in logv[n0:] if (min_at is None or (e.get("at") or 0) >= min_at)]
        if fresh:
            return fresh[-1], round(time.time() - t0, 2)
        time.sleep(0.25)
    return None, round(time.time() - t0, 2)


def wait_voice_done(cdp, quiet_ms=900, timeout=30):
    """等上一轮语音真正收尾：连续 quiet_ms 没有新的包络帧，且页面不 busy。"""
    end = time.time() + timeout
    q = r"""
    (() => ({
      now: Math.round(performance.now()),
      last: (window.__lipFeed || {}).lastAt || 0,
      busy: typeof window.__isBusy === 'function' ? window.__isBusy() : null
    }))()
    """
    while time.time() < end:
        try:
            st = cdp.eval(q) or {}
        except Exception:
            st = {}
        if not st.get("busy") and (st.get("now") or 0) - (st.get("last") or 0) > quiet_ms:
            return True
        time.sleep(0.4)
    return False


def js_fill_tap(cdp, origin, text):
    """JS 只填值（省掉慢机上的 10s 键入），发送仍用 adb tap。

    不能用 JS click 发送：合成点击不算用户手势，WebView 不给音频播放授权，
    TTS 会 ok=false（上一跑 ③b 就是这样：frames=2、maxLevel=0、maxLipSync=0）。
    """
    sent_at = cdp.eval("Math.round(performance.now())")
    r = cdp.eval(FILL_JS.replace("__TXT__", json.dumps(text))) or {}
    time.sleep(0.6)
    if origin:
        tap(cdp, "send", origin)
    else:                                   # 没有 WebView 坐标时才退回合成点击
        cdp.eval(SEND_JS.replace("__TXT__", json.dumps(text)))
    time.sleep(0.8)
    p = cdp.eval(PROBE_JS) or {}
    return {"mode": "js-fill+tap", "sentAt": sent_at, "fill": r.get("ok"),
            "rowsAfterSend": p.get("rows")}


GREET_JS = r"""
(() => {
  const rows = [...document.querySelectorAll('#messages .msg-row:not(.hidden) .bubble')]
    .filter(b => !b.classList.contains('typing'));
  const texts = rows.map(b => (b.innerText || '').replace(/\s+/g, ' ').trim());
  const greetLike = texts.filter(t =>
    /早呀|中午好呀|下午好|晚上好呀|这个点还没睡|深夜树洞营业中/.test(t));
  return {
    total: (window.ChatPager && ChatPager.stats) ? ChatPager.stats().total : null,
    rows: rows.length,
    greetCount: greetLike.length,
    greetLike: greetLike.slice(-3)
  };
})()
"""


def wait_reentry(cdp, before_total, timeout=20, settle=1.5):
    """重启后等同日重入判定：total 不应因"又开场"而增加。

    历史恢复是异步的：先等 total 回到重启前的值，再停 settle 观察是否有新开场。
    """
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        try:
            last = cdp.eval(GREET_JS) or {}
        except Exception:
            last = {}
        if (last.get("total") or 0) >= before_total:
            time.sleep(settle)
            try:
                last = cdp.eval(GREET_JS) or last
            except Exception:
                pass
            return last
        time.sleep(0.5)
    return last


def sample_voice(cdp, path="e2e_03_voice.png", seconds=5.0, shot_at=0.30):
    """播放期间高频采样：局部 lipOpen + 引擎 lipSync/mouthMix + 喂帧计数。

    截图阈值取**引擎参数** lipSync（嘴真的张开了），不再用 Stage 局部变量；
    统计"播放中段 1 秒"的喂帧数，用来证明帧数与音频时长/包络正相关。
    """
    t0 = time.time()
    samples, null_faces = [], 0
    shot_done, shot_size, shot_t, peak = False, 0, None, 0.0
    while time.time() - t0 < seconds:
        try:
            p = cdp.eval(LIP_PROBE_JS) or {}
        except Exception as e:
            samples.append({"err": str(e)[:80]})
            time.sleep(0.03)
            continue
        rec = {"t": round(time.time() - t0, 3), "pageT": p.get("t"),
               "open": round(float(p.get("lipOpen") or 0), 3),
               "sync": round(float(p.get("lipSync") or 0), 4),
               "mouth": p.get("mouth"), "mix": p.get("mix"),
               "frames": p.get("frames"), "paint": p.get("paint"),
               "mode": p.get("mode"), "raf": p.get("raf"), "tag": p.get("tag")}
        samples.append(rec)
        if p.get("faceNull"):
            null_faces += 1
        focus = rec["sync"] if rec["sync"] is not None else rec["open"]
        if focus > peak:
            peak = focus
        if not shot_done and focus >= shot_at:
            shot_size = shot(path)
            shot_t = rec["t"]
            shot_done = True
        time.sleep(0.035)
    if not shot_done:
        shot_size = shot(path)
    ok = [x for x in samples if "open" in x]
    nz_open = len([x for x in ok if x["open"] > 0.05])
    nz_sync = len([x for x in ok if (x["sync"] or 0) > 0.02])
    # 播放中段：以第一次"引擎嘴型真的张开"为起点，取 1.0s 窗口
    start = next((x["t"] for x in ok if (x["sync"] or 0) > 0.02), None)
    mid = None
    if start is not None:
        win = [x for x in ok if start <= x["t"] <= start + 1.0]
        if win:
            paints = [x for x in win if x.get("paint") is not None]
            mid = {"from": round(start, 2), "to": round(start + 1.0, 2),
                   "feedFrames": int((win[-1]["frames"] or 0) - (win[0]["frames"] or 0)),
                   # 真正画到画布的说话帧：motion=true≈满帧，motion=false≈15fps
                   "paintFrames": int(paints[-1]["paint"] - paints[0]["paint"]) if paints else None,
                   "samples": len(win),
                   "nonzeroSync": len([x for x in win if (x["sync"] or 0) > 0.02]),
                   "maxSync": round(max((x["sync"] or 0) for x in win), 4)}
    return {"samples": samples, "n": len(samples),
            "maxLipOpen": round(max([x["open"] for x in ok] or [0]), 3),
            "maxLipSync": round(max([(x["sync"] or 0) for x in ok] or [0]), 4),
            "nonZeroLipOpen": nz_open, "nonZeroLipSync": nz_sync,
            "tailSync": [round((x["sync"] or 0), 4) for x in ok[-3:]],
            "nullFaceSamples": null_faces,
            "pageTags": sorted({x["tag"] for x in ok if x.get("tag")}),
            "modes": sorted({str(x["mode"]) for x in ok}), "midSecond": mid,
            "paintTotal": (int(ok[-1]["paint"] - ok[0]["paint"])
                           if ok and ok[-1].get("paint") is not None and ok[0].get("paint") is not None else None),
            "shotAt": shot_t, "shotBytes": shot_size}


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


def ime_state():
    """软键盘是否弹起（收键盘前先确认，避免 BACK 被 WebView 当成返回）。"""
    _, out = sh("adb shell dumpsys input_method")
    return {"inputShown": bool(re.search(r"mInputShown\s*=\s*true", out)),
            "inputViewShown": bool(re.search(r"mIsInputViewShown\s*=\s*true", out))}


def main():
    dev, dens = wm_size()
    METRICS["device"] = {"screenPx": dev, "densityDpi": dens}
    log(f"设备 {dev} dpi={dens}")

    cdp = Cdp(port=PORT)
    try:
        url = cdp.attach("index.html", timeout=120, fallback_url=APP_URL)
    except Exception as e:
        log(f"附着失败: {e}")
        METRICS["fatal"] = f"attach failed: {e}"
        dump_metrics()
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
    set_row("01_cold", b1 > 0,
            f"rows={s1.get('rows')} empty={s1.get('emptyVisible')} "
            f"vw={s1.get('iw')}x{s1.get('ih')} shot={b1}B")
    gate("01_cold", require_true, "01_cold", b1 > 0, "冷启动截图失败（bytes=%s）" % b1)

    # ---------------- ② 发一条消息后 ----------------
    rows0 = s1.get("rows") or 0
    sent2 = type_and_send(cdp, origin, "jin tian xin qing bu hao")
    log(f"② 发送结果: {sent2}")
    grew, p2 = wait_rows(cdp, rows0 + 2, timeout=30)
    # 硬门：回复没到达就判红，不允许带着空回复继续往下走
    gate("02_chat", require_reply, "02_chat", grew,
         {"sent": sent2, "rows": (p2 or {}).get("rows"), "busy": (p2 or {}).get("busy")})
    time.sleep(1.2)
    s2 = cdp.eval(STATE_JS) or {}
    b2 = shot("e2e_02_chat.png")
    METRICS["states"]["02_chat"] = {"shot": "e2e_02_chat.png", "bytes": b2,
                                    "png": png_size("e2e_02_chat.png"),
                                    "replyArrived": grew, "sent": sent2, "probe": s2}
    set_row("02_chat", grew,
            f"replyArrived={grew} rows={s2.get('rows')} "
            f"gap={(s2.get('scroll') or {}).get('gap')}p")

    def _mid(lip):
        return (lip.get("midSecond") or {})

    def _dur(events, ev):
        """语音时长取收尾后的 tts 日志（wait_voice 抓的是开始瞬间快照，那时 endAt 还没写）。"""
        t = (ev or {}).get("txt")
        for e in reversed(events or []):
            e = e or {}
            if e.get("endAt") and (t is None or e.get("txt") == t):
                return e["endAt"] - (e.get("at") or 0)
        return None

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
    # 不再强制开启动效：用首次安装默认的极简预设（motion=false）+ AVD 默认
    # prefers-reduced-motion，正好验证"默认用户说话时嘴到底动不动"。
    try:
        motion = cdp.eval("(window.Appearance && Appearance.get) ? Appearance.get().motion : null")
    except Exception as e:
        motion = f"err {e}"[:80]
    log(f"默认动效状态: {motion}")
    wait_voice_done(cdp)                 # ② 回复的后续气泡先播完，避免串台
    try:
        n0 = len(cdp.eval("window.__ttsLog || []") or [])
    except Exception:
        n0 = 0
    rows1 = (s2.get("rows") or 0)
    sent3 = type_and_send(cdp, origin, "xie xie ni pei wo")
    log(f"③ 发送结果: {sent3}")
    # 等"真的有一条新的语音在播"，再开采样窗口——避免像上一轮那样对着静音采样
    ev, waited = wait_voice(cdp, n0, timeout=30, min_at=sent3.get("sentAt"))
    log(f"③ 语音开始: {ev} waited={waited}s")
    # 硬门：没有新的语音事件 = 回复没开始播，绝不对着静音采样口型
    gate("03_voice", require_voice, "03_voice", ev)
    voice = sample_voice(cdp, seconds=5.5)
    ok3, p3 = wait_rows(cdp, rows1 + 2, timeout=6)
    try:
        events = cdp.eval("window.__ttsLog || []")
    except Exception:
        events = None
    try:
        total3 = cdp.eval(TOTAL_JS)
    except Exception:
        total3 = None
    s3 = cdp.eval(STATE_JS) or {}
    lip_ok = (voice.get("maxLipSync") or 0) > 0 and (voice.get("nonZeroLipSync") or 0) > 0
    METRICS["states"]["03_voice"] = {
        "shot": "e2e_03_voice.png" if voice["shotBytes"] else None,
        "replyArrived": ok3, "sent": sent3, "voiceEvent": ev, "voiceWaitS": waited,
        "chain": chain, "hook": hook, "motionDefault": motion, "lipOk": lip_ok,
        "ttsEvents": events, "chatPager": total3, "lip": voice, "probe": s3
    }
    gate("03_voice", require_reply, "03_voice", ok3,
         {"sent": sent3, "rows": (p3 or {}).get("rows")})
    gate("03_voice", require_true, "03_voice", lip_ok,
         "语音播放期间口型帧为 0（maxLipSync=%s nonZeroLipSync=%s event=%s）"
         % (voice.get("maxLipSync"), voice.get("nonZeroLipSync"), ev))
    _v3m = voice.get("midSecond") or {}
    set_row("03_voice_motion_false", True,
            f"maxLipSync={voice.get('maxLipSync')} nz={voice.get('nonZeroLipSync')} "
            f"paint/feed={_v3m.get('paintFrames')}/{_v3m.get('feedFrames')} "
            f"durationMs={_dur(events, ev)}")

    # ---------------- ③b 非默认预设（motion=true）同段语音对照 ----------------
    # 同一段 TTS fixture（text 不影响 cmd provider 的 WAV）：比较满帧路径的
    # 帧率/幅度，证明修复没把 motion=true 一起改坏、也没削口型幅度。
    try:
        motion_on = cdp.eval(
            "(() => { Appearance.setSwitch('motion', true); return Appearance.get().motion; })()")
    except Exception as e:
        motion_on = f"err {e}"[:80]
    log(f"③b motion 开关 → {motion_on}")
    time.sleep(0.8)                     # appearance:change → Stage.setMotionEnabled(true)
    wait_voice_done(cdp)                # ③ 的第二个气泡先播完；否则会被当成 ③b 的"新语音"
    try:
        n1 = len(cdp.eval("window.__ttsLog || []") or [])
    except Exception:
        n1 = 0
    rows2 = (cdp.eval(PROBE_JS) or {}).get("rows") or 0
    # motion=true 下 adb 键入会被慢渲染拖到 10s；对照测量改成 JS 填值 + adb tap 发送
    sent3b = js_fill_tap(cdp, origin, "zai shuo yi bian")
    log(f"③b 发送结果: {sent3b}")
    grew_user, pu = wait_row_count(cdp, rows2 + 1, timeout=15)
    gate("03b_motion", require_true, "03b_motion", grew_user,
         "用户消息没有落屏（sent=%s rows=%s）" % (sent3b, (pu or {}).get("rows")))
    ev_b, waited_b = wait_voice(cdp, n1, timeout=30, min_at=sent3b.get("sentAt"))
    gate("03b_motion", require_voice, "03b_motion", ev_b)
    voice_b = sample_voice(cdp, path="e2e_03b_voice_motion.png", seconds=5.5)
    ok3b, p3b = wait_row_count(cdp, rows2 + 2, timeout=15)
    gate("03b_motion", require_reply, "03b_motion", ok3b,
         {"sent": sent3b, "rows": (p3b or {}).get("rows")})
    try:
        events_b = cdp.eval("window.__ttsLog || []")
    except Exception:
        events_b = None
    lip_ok_b = (voice_b.get("maxLipSync") or 0) > 0 and (voice_b.get("nonZeroLipSync") or 0) > 0
    METRICS["states"]["03b_motion_voice"] = {
        "shot": "e2e_03b_voice_motion.png" if voice_b["shotBytes"] else None,
        "replyArrived": ok3b, "sent": sent3b, "voiceEvent": ev_b, "voiceWaitS": waited_b,
        "motion": motion_on, "ttsEvents": events_b, "lip": voice_b, "lipOk": lip_ok_b
    }
    gate("03b_motion", require_true, "03b_motion", lip_ok_b,
         "motion=true 语音期间口型帧为 0（maxLipSync=%s nonZero=%s event=%s）"
         % (voice_b.get("maxLipSync"), voice_b.get("nonZeroLipSync"), ev_b))
    _v3bm = voice_b.get("midSecond") or {}
    set_row("03b_voice_motion_true", lip_ok_b,
            f"maxLipSync={voice_b.get('maxLipSync')} nz={voice_b.get('nonZeroLipSync')} "
            f"paint/feed={_v3bm.get('paintFrames')}/{_v3bm.get('feedFrames')} "
            f"durationMs={_dur(events_b, ev_b)}")
    # 还原默认极简：后续 ④⑤⑥ 口径与之前一致
    try:
        motion_off = cdp.eval(
            "(() => { Appearance.setSwitch('motion', false); return Appearance.get().motion; })()")
    except Exception as e:
        motion_off = f"err {e}"[:80]
    log(f"③b motion 还原 → {motion_off}")
    time.sleep(0.5)

    METRICS["lipCompare"] = {
        # 默认极简（零动画）：只省帧（≈15fps），幅度不削
        "motionFalse": {"maxLipSync": voice.get("maxLipSync"),
                        "feedFramesPerSec": _mid(voice).get("feedFrames"),
                        "paintFramesPerSec": _mid(voice).get("paintFrames"),
                        "paintTotal": voice.get("paintTotal"),
                        "durationMs": _dur(events, ev)},
        # motion=true（满帧）：帧率更高，幅度应与零动画同源（lipFor 不再看 motion）
        "motionTrue": {"maxLipSync": voice_b.get("maxLipSync"),
                       "feedFramesPerSec": _mid(voice_b).get("feedFrames"),
                       "paintFramesPerSec": _mid(voice_b).get("paintFrames"),
                       "paintTotal": voice_b.get("paintTotal"),
                       "durationMs": _dur(events_b, ev_b)},
    }
    _mt, _mf = METRICS["lipCompare"]["motionTrue"], METRICS["lipCompare"]["motionFalse"]
    METRICS["lipCompare"]["verdict"] = {
        # 节流不削幅度：零动画路径至少保住满帧路径 90% 的幅度（幅度来自同一 lipFor）
        "motionFalseAmplitudeKept": (_mf["maxLipSync"] or 0) >= (_mt["maxLipSync"] or 0) * 0.9,
        # 零动画只省帧：设计目标 15fps（LIP_EVERY=4），≤15.5 留半帧量化余量
        "motionFalseThrottled": (_mf["paintFramesPerSec"] is not None
                                 and _mf["paintFramesPerSec"] <= 15.5),
        # 满帧路径"每喂一帧画一帧"（画帧≈喂入帧），不受设备快慢影响；节流路径画帧≈喂入帧/4
        "motionTruePaintsEveryFeed": (_mt["feedFramesPerSec"] is not None
                                      and (_mt["paintFramesPerSec"] or 0)
                                      >= 0.9 * _mt["feedFramesPerSec"]),
    }
    log("口型对照: " + json.dumps(METRICS["lipCompare"], ensure_ascii=False))

    # 重启前的会话量：用于"同日重入不重复问候"的硬断言（在主页面取，避免浮窗目标干扰）
    try:
        reentry_before = int((cdp.eval(TOTAL_JS) or {}).get("total") or 0)
        reentry_pre = cdp.eval(GREET_JS) or {}
    except Exception:
        reentry_before, reentry_pre = 0, {}

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
    gate("04_float", require_true, "04_float", n_over >= 1,
         "浮窗模式没有 APPLICATION_OVERLAY 窗口（overlayWindows=%s）" % n_over)
    set_row("04_float", n_over >= 1, f"overlayWindows={n_over} shot={b4}B")

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

    # 硬门：本次已在当天开过场，杀进程再开不得追加第二次时段问候
    reentry = wait_reentry(cdp, reentry_before)
    dup = int(reentry.get("total") or 0) - reentry_before
    reentry_ok = dup <= 0 and int(reentry.get("greetCount") or 0) <= int(reentry_pre.get("greetCount") or 0)
    log(f"同日重入: before={reentry_before}(greet={reentry_pre.get('greetCount')}) "
        f"after={reentry.get('total')}(greet={reentry.get('greetCount')}) "
        f"greetLike={reentry.get('greetLike')}")
    gate("05_reentry", require_true, "05_reentry", reentry_ok,
         "同日重启后不应再追加时段问候（before=%s after=%s greetLike=%s）"
         % (reentry_before, reentry.get("total"), reentry.get("greetLike")))
    METRICS["states"]["04b_reentry"] = {
        "beforeTotal": reentry_before, "afterTotal": reentry.get("total"),
        "preGreetCount": reentry_pre.get("greetCount"),
        "afterGreetCount": reentry.get("greetCount"),
        "greetLike": reentry.get("greetLike"), "ok": reentry_ok}
    set_row("04b_reentry", reentry_ok,
            f"total {reentry_before}→{reentry.get('total')} "
            f"greet {reentry_pre.get('greetCount')}→{reentry.get('greetCount')}")

    # ---------------- ⑤ 横屏 → 竖屏 → 回横屏（键收起后 chatH 硬门） ----------------
    sh("adb shell settings put system accelerometer_rotation 0")
    sh("adb shell input keyevent 111")           # 先确保键盘收起
    time.sleep(1.0)

    def rot_snap(name):
        s = cdp.eval(STATE_JS) or {}
        return {"probe": s, "chatH": (s.get("chatScroll") or {}).get("h"),
                "webview": webview_bounds(ui_dump()),
                "shot": name, "bytes": shot(name)}

    sh("adb shell settings put system user_rotation 1")   # 横屏
    time.sleep(3)
    # 横屏弹键盘再收起：验证极矮视口规则在键盘收起后恢复，chatH 至少放得下一颗气泡（48.5px）
    ime = {"tapped": False, "shown": False, "collapsed": None}
    vb_l = webview_bounds(ui_dump())
    p_l = cdp.eval(PROBE_JS) or {}
    if vb_l and p_l.get("input"):
        box, dpr = p_l["input"], (p_l.get("dpr") or 1)
        sh("adb shell input tap %d %d" % (
            int(vb_l["l"] + (box["left"] + box["right"]) / 2 * dpr),
            int(vb_l["t"] + (box["top"] + box["bottom"]) / 2 * dpr)))
        ime["tapped"] = True
        time.sleep(2.0)
        st = ime_state()
        ime["shown"] = bool(st.get("inputShown") and st.get("inputViewShown"))
        if ime["shown"]:                         # 已确认弹起才按 BACK，避免被当成页面返回
            sh("adb shell input keyevent 4")
            time.sleep(2.0)
        st2 = ime_state()
        ime["collapsed"] = not (st2.get("inputShown") and st2.get("inputViewShown"))
    land1 = rot_snap("e2e_05_landscape.png")
    sh("adb shell settings put system user_rotation 0")   # 竖屏
    time.sleep(3)
    port = rot_snap("e2e_05_portrait.png")
    sh("adb shell settings put system user_rotation 1")   # 回横屏
    time.sleep(3)
    land2 = rot_snap("e2e_05_back_landscape.png")

    def _orient(snap):
        s = snap["probe"]
        return bool((s.get("iw") or 0) > (s.get("ih") or 0))

    def _chat_ok(snap):
        return (snap.get("chatH") or 0) >= 48.5

    rot_ok = _orient(land1) and _chat_ok(land1) and _chat_ok(port) and _orient(land2) and _chat_ok(land2)
    METRICS["states"]["05_rotate"] = {
        "ime": ime, "landscape": land1, "portrait": port, "backLandscape": land2,
        "chatHmin": 48.5, "ok": rot_ok}
    set_row("05_rotate", rot_ok,
            f"landscape {land1['probe'].get('iw')}x{land1['probe'].get('ih')} chatH={land1['chatH']}"
            f" | portrait chatH={port['chatH']}"
            f" | backLandscape {land2['probe'].get('iw')}x{land2['probe'].get('ih')} chatH={land2['chatH']}"
            f" | IME shown={ime['shown']} collapsed={ime['collapsed']}")
    gate("05_rotate", require_true, "05_rotate", rot_ok,
         "横→竖→回横屏后 chatH 必须 ≥48.5（landscape=%s portrait=%s backLandscape=%s ime=%s）"
         % (land1["chatH"], port["chatH"], land2["chatH"], ime))

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
    gate("06_rounds20", require_true, "06_rounds20", fails == 0,
         "20 轮长会话里有 %s 轮回复没到齐（baseTotal=%s total=%s）"
         % (fails, base_total, t6.get("total")))
    set_row("06_rounds20", fails == 0,
            f"rounds=20 fails={fails} total {base_total}→{t6.get('total')} "
            f"domRows={t6.get('rows')} gap={(s6.get('scroll') or {}).get('gap')}p")

    # ---------------- ⑦ 语音失败提示：出现 → 存活期自动收起 → 重绘不复活 ----------------
    VOICE_TIP_JS = r"""
(() => {
  const el = document.getElementById('voiceTip');
  let logs = [];
  try { logs = JSON.parse(sessionStorage.getItem('xiaoman_voice_fail_log') || '[]'); } catch (e) {}
  const ctl = window.__voiceTip ? window.__voiceTip() : null;
  return {
    visible: !!el && !el.classList.contains('hidden'),
    text: (document.getElementById('voiceTipText') || {}).textContent || '',
    logs: logs.length,
    last: logs.length ? logs[logs.length - 1].reason : null,
    ttl: ctl ? ctl.ttlMs : null,
  };
})()
"""
    # 走生产同一条入口（TTS 失败回调调用的就是它），不另外造失败
    cdp.eval("window.__onVoiceUnavailable && window.__onVoiceUnavailable('e2e_fail'), 1")
    time.sleep(0.4)
    vt0 = cdp.eval(VOICE_TIP_JS) or {}
    ttl_ms = vt0.get("ttl") or 8000
    time.sleep(ttl_ms / 1000.0 + 1.2)
    vt1 = cdp.eval(VOICE_TIP_JS) or {}
    cdp.eval("window.__voiceTip && window.__voiceTip().render(), 1")   # 模拟一次重绘
    time.sleep(0.3)
    vt2 = cdp.eval(VOICE_TIP_JS) or {}
    vt_ok = (bool(vt0.get("visible")) and int(vt0.get("logs") or 0) >= 1
             and not vt1.get("visible") and int(vt1.get("logs") or 0) == int(vt0.get("logs") or 0)
             and not vt2.get("visible") and int(vt2.get("logs") or 0) >= 1)
    METRICS["states"]["07_voice_tip"] = {"shown": vt0, "afterTtl": vt1,
                                          "afterRender": vt2, "ok": vt_ok}
    gate("07_voice_tip", require_true, "07_voice_tip", vt_ok,
         "语音失败提示应出现并在 %sms 后自动收起、重绘不复活且失败日志仍保留（%r）"
         % (ttl_ms, {"shown": vt0, "afterTtl": vt1, "afterRender": vt2}))
    set_row("07_voice_tip", vt_ok,
            f"shown={vt0.get('visible')} logs={vt0.get('logs')} ttl={ttl_ms}ms"
            f" → afterTtl visible={vt1.get('visible')}"
            f" → afterRender visible={vt2.get('visible')} logs={vt2.get('logs')}")

    # ---------------- 汇总 ----------------
    METRICS["keyChecks"] = {
        "reentryNoDuplicateGreeting": reentry_ok,
        "chatHAfterImeClose": {"min": 48.5,
                               "landscape": land1["chatH"], "portrait": port["chatH"],
                               "backLandscape": land2["chatH"],
                               "ok": _chat_ok(land1) and _chat_ok(port) and _chat_ok(land2)},
        "voiceTipNoRevive": vt_ok,
    }
    METRICS["ok"] = all(r["ok"] for r in SUMMARY)
    dump_metrics()
    write_summary()
    log("关键三项: " + json.dumps(METRICS["keyChecks"], ensure_ascii=False))
    try:
        cdp.close()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
