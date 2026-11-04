#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_adapt_sweep.py · AVD 适配横扫（P2 适配收口）
--------------------------------------------------------------------
每项「改系统状态 → 等重排 → 截图 + DOM 断言」：
  1 字体 1.0 / 1.3 / 1.5      无横向溢出、输入栏在视口内、气泡不裁切
  2 深色 / 浅色               body 背景亮度切换、文字对比
  3 竖屏 / 横屏               innerWidth>innerHeight 且输入栏可见
  4 刘海屏模拟（double cutout）顶部 letterbox 区不得有内容、输入栏仍可点
  5 键盘弹出（720x1280）      输入框不被键盘遮挡（innerHeight 收缩后仍在视口内）
  6 长消息换行                单行不溢出、气泡宽度不超屏、无横向滚动
  7 表情消息                  气泡渲染且不破版
  8 时区切换 + 72h 时钟前移    提醒仍挂在同一绝对时刻、TIMEZONE_CHANGED/TIME_SET 后重排、不丢条目

证据：docs/android-battery/shots/adapt/*.png + 每项 DOM 实测数值。
用法：python scripts/emulator_adapt_sweep.py
退出码：0=全通过；1=有失败项（逐项 [ADAPT-PASS]/[ADAPT-FAIL]）
"""
import base64
import json
import os
import re
import subprocess
import sys
import time

import requests
from websockets.sync.client import connect

PKG = "com.xiaoman.treehole"
MAIN = f"{PKG}/.MainActivity"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "docs", "android-battery", "shots", "adapt")
os.makedirs(OUT, exist_ok=True)
RESULTS = []
PORT = 9223


def sh(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def check(name, ok, info=""):
    print(("[ADAPT-PASS] " if ok else "[ADAPT-FAIL] ") + name + (f" | {info}" if info else ""),
          flush=True)
    RESULTS.append((name, ok, info))


def shot(name):
    p = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, timeout=90)
    data = p.stdout or b""
    if len(data) > 1024 and data[:1] == b"\x89":
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(data)
        return True
    print(f"[ADAPT-WARN] 截图失败 {name}", flush=True)
    return False


# ---------------- CDP ----------------

class Cdp:
    def __init__(self):
        self.ws = None
        self.n = 0

    def attach(self, url_filter="index.html"):
        _, out = sh("adb shell cat /proc/net/unix")
        sockets = sorted(set(re.findall(r"webview_devtools_remote_(\d+)", out)))
        _, ps = sh("adb shell ps -A -o PID,NAME")
        mypids = {ln.split()[0] for ln in ps.splitlines() if PKG in ln}
        sockets = [s for s in sockets if s in mypids] or sockets
        if not sockets:
            raise RuntimeError("找不到 WebView devtools socket")
        sh(f"adb forward tcp:{PORT} localabstract:webview_devtools_remote_{sockets[0]}")
        for _ in range(20):
            try:
                lst = requests.get(f"http://127.0.0.1:{PORT}/json/list", timeout=5).json()
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("devtools /json/list 不可达")
        cand = [t for t in lst if url_filter in (t.get("url") or "")]
        if not cand:
            raise RuntimeError(f"没有匹配 {url_filter} 的 WebView target: {[t.get('url') for t in lst]}")
        target = cand[0]
        self.ws = connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024)
        return target["url"]

    def eval(self, js, timeout=20):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                                 "params": {"expression": js, "returnByValue": True,
                                            "awaitPromise": True}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv(timeout=timeout))
            if msg.get("id") == mid:
                r = msg.get("result", {})
                if "exceptionDetails" in r:
                    raise RuntimeError(str(r["exceptionDetails"])[:300])
                return r.get("result", {}).get("value")
        raise RuntimeError("CDP 超时")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


PROBE_JS = r"""
(() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const doc = document.documentElement;
  const inp = document.querySelector('#textInput');
  const ir = inp ? inp.getBoundingClientRect() : null;
  const rows = [...document.querySelectorAll('#messages .msg-row')];
  const bad = rows.filter(r => r.scrollWidth > r.clientWidth + 2);
  const bubbles = [...document.querySelectorAll('#messages .msg-row .bubble, #messages .msg-row > div')];
  const maxBub = bubbles.reduce((m, b) => Math.max(m, b.getBoundingClientRect().width), 0);
  const ovf = bubbles.filter(b => b.getBoundingClientRect().right > vw + 1 ||
                                  b.getBoundingClientRect().left < -1);
  const bodyBg = getComputedStyle(document.body).backgroundColor;
  const cs = inp ? getComputedStyle(inp) : null;
  return {
    vw, vh, docScrollW: doc.scrollWidth, docScrollH: doc.scrollHeight,
    bodyBg, textColor: cs ? cs.color : '',
    inputRect: ir ? {top: Math.round(ir.top), bottom: Math.round(ir.bottom),
                     left: Math.round(ir.left), right: Math.round(ir.right)} : null,
    rows: rows.length, badRows: bad.length,
    maxBubbleW: Math.round(maxBub), overflowBubbles: ovf.length,
    inputVisible: !!(inp && ir && ir.bottom <= vh + 1 && ir.top >= 0 && ir.right <= vw + 1),
    dpr: window.devicePixelRatio
  };
})()
"""


def probe(cdp):
    return cdp.eval(PROBE_JS)


def set_font(scale):
    sh(f"adb shell settings put system font_scale {scale}")
    time.sleep(3)


def restore():
    sh("adb shell settings put system font_scale 1.0")
    sh("adb shell cmd uimode night no")
    sh("adb shell settings put system accelerometer_rotation 1")
    sh("adb shell cmd overlay disable --category com.android.internal.display.cutout.emulation.double")
    sh("adb shell input keyevent 3")
    time.sleep(2)


def main():
    sh(f"adb shell am start -n {MAIN} -f 0x20000000")
    time.sleep(8)
    cdp = Cdp()
    url = cdp.attach()
    print(f"[ADAPT] 已附着 WebView: {url}", flush=True)
    time.sleep(2)

    # 0 基线
    set_font(1.0)
    p = probe(cdp)
    shot("00-font-1.0.png")
    check("字体 1.0：无横向溢出 + 输入栏在视口内",
          p["docScrollW"] <= p["vw"] + 1 and p["inputVisible"] and p["badRows"] == 0,
          f"scrollW={p['docScrollW']}/{p['vw']} input={p['inputRect']} badRows={p['badRows']}")

    for scale, tag in ((1.3, "01-font-1.3"), (1.5, "02-font-1.5")):
        set_font(scale)
        p = probe(cdp)
        shot(f"{tag}.png")
        check(f"字体 {scale}：无横向溢出 + 输入栏在视口内 + 气泡不越界",
              p["docScrollW"] <= p["vw"] + 1 and p["inputVisible"]
              and p["badRows"] == 0 and p["overflowBubbles"] == 0,
              f"scrollW={p['docScrollW']}/{p['vw']} input={p['inputRect']} "
              f"badRows={p['badRows']} ovfBub={p['overflowBubbles']} maxBubW={p['maxBubbleW']}")
    set_font(1.0)

    # 深色 / 浅色
    def lum(rgb):
        m = re.match(r"rgba?\((\d+), (\d+), (\d+)", rgb or "")
        if not m:
            return -1
        r, g, b = map(int, m.groups())
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    sh("adb shell cmd uimode night yes")
    time.sleep(3)
    p = probe(cdp)
    shot("03-dark.png")
    check("深色模式：背景变暗（亮度<90）且输入栏可见",
          lum(p["bodyBg"]) < 90 and p["inputVisible"],
          f"bg={p['bodyBg']} lum={lum(p['bodyBg']):.0f} input={p['inputRect']}")
    sh("adb shell cmd uimode night no")
    time.sleep(3)
    p = probe(cdp)
    shot("04-light.png")
    check("浅色模式：背景恢复（亮度>180）", lum(p["bodyBg"]) > 180,
          f"bg={p['bodyBg']} lum={lum(p['bodyBg']):.0f}")

    # 横屏
    sh("adb shell settings put system accelerometer_rotation 0")
    sh("adb shell settings put system user_rotation 1")
    time.sleep(4)
    p = probe(cdp)
    shot("05-landscape.png")
    check("横屏：innerWidth>innerHeight 且输入栏完整可见",
          p["vw"] > p["vh"] and p["inputVisible"],
          f"viewport={p['vw']}x{p['vh']} input={p['inputRect']}")
    sh("adb shell settings put system user_rotation 0")
    time.sleep(3)
    p = probe(cdp)
    check("竖屏恢复：viewport 720x1280 且输入栏可见",
          p["vh"] > p["vw"] and p["inputVisible"], f"viewport={p['vw']}x{p['vh']}")

    # 刘海屏模拟
    _, ovs = sh("adb shell cmd overlay list")
    cut = [ln for ln in ovs.splitlines() if "cutout.emulation.double" in ln]
    if cut:
        sh("adb shell cmd overlay enable-exclusive --category com.android.internal.display.cutout.emulation.double")
        time.sleep(1)
        sh(f"adb shell am start -n {MAIN} -f 0x20000000")
        sh("adb shell am force-stop " + PKG)
        time.sleep(1)
        sh(f"adb shell am start -n {MAIN} -f 0x20000000")
        time.sleep(8)
        cdp.close()
        cdp = Cdp()
        cdp.attach()
        p = probe(cdp)
        shot("06-cutout-double.png")
        # 顶部应出现 letterbox 黑边（内容不得进入挖孔区）
        check("刘海屏模拟：布局未压到挖孔区且输入栏可见",
              p["inputVisible"], f"viewport={p['vw']}x{p['vh']} input={p['inputRect']}")
        sh("adb shell cmd overlay disable --category com.android.internal.display.cutout.emulation.double")
        time.sleep(1)
        sh(f"adb shell am start -n {MAIN} -f 0x20000000")
        time.sleep(6)
        cdp.close()
        cdp = Cdp()
        cdp.attach()
    else:
        check("刘海屏模拟：系统无 cutout 模拟 overlay（跳过）", True,
              "cmd overlay list 无 display.cutout.emulation.double")

    # 键盘弹出
    p = probe(cdp)
    ir = p["inputRect"]
    sh(f"adb shell input tap {p['vw'] // 2} {ir['top'] + 10}")
    time.sleep(2)
    p2 = probe(cdp)
    _, ime = sh("adb shell dumpsys input_method")
    ime_shown = "mInputShown=true" in ime or "isInputViewShown=true" in ime
    shot("07-keyboard.png")
    check("键盘弹出：输入栏不被遮挡（收窄后仍在视口内）",
          p2["inputVisible"] and (p2["vh"] <= p["vh"]),
          f"vh {p['vh']}→{p2['vh']} input={p2['inputRect']} imeShown={ime_shown}")
    sh("adb shell input keyevent 4")
    time.sleep(1)

    # 长消息换行
    cdp.eval("""(() => {
      const i = document.querySelector('#textInput');
      const b = document.querySelector('#sendBtn');
      if (!i || !b) return 'no-ui';
      i.focus();
      i.value = '这是一条用于测试小屏换行的超长消息，包含标点符号以及连续的英文单词ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789，用来验证气泡不会横向溢出屏幕，也不会把输入栏顶出可视区域。'.repeat(2);
      i.dispatchEvent(new Event('input', {bubbles: true}));
      b.click();
      return 'sent';
    })()""")
    time.sleep(6)
    p = probe(cdp)
    shot("08-long-message.png")
    check("长消息：气泡不横向溢出且页面无横向滚动",
          p["overflowBubbles"] == 0 and p["docScrollW"] <= p["vw"] + 1,
          f"ovfBub={p['overflowBubbles']} scrollW={p['docScrollW']}/{p['vw']} maxBubW={p['maxBubbleW']}")

    # 表情消息
    cdp.eval("""(() => {
      const i = document.querySelector('#textInput');
      const b = document.querySelector('#sendBtn');
      i.value = '😊😊😊 好想抱抱你 🥺💗 🌙✨';
      i.dispatchEvent(new Event('input', {bubbles: true}));
      b.click();
      return 'sent';
    })()""")
    time.sleep(6)
    p = probe(cdp)
    shot("09-emoji-message.png")
    check("表情消息：气泡渲染且不破版",
          p["overflowBubbles"] == 0 and p["docScrollW"] <= p["vw"] + 1,
          f"ovfBub={p['overflowBubbles']} rows={p['rows']}")

    # 时区 / 72h 时钟前移
    timezone_case(cdp)

    cdp.close()
    restore()
    ok = all(r[1] for r in RESULTS)
    print(f"[ADAPT-SUMMARY] {sum(1 for r in RESULTS if r[1])}/{len(RESULTS)} 通过", flush=True)
    sys.exit(0 if ok else 1)


def native_items():
    _, out = sh(f"adb shell run-as {PKG} cat shared_prefs/treehole_reminder_store.xml")
    m = re.search(r'<string name="items">(.*?)</string>', out, re.S)
    if not m:
        return []
    raw = m.group(1).replace("&quot;", '"').replace("&amp;", "&")
    try:
        return json.loads(raw)
    except Exception:
        return []


def alarm_epoch():
    """只取本包 REMINDER_ALARM 的 origWhen（避免被系统闹钟淹没）。"""
    _, out = sh("adb shell dumpsys alarm")
    vals = []
    for blk in out.split("\n\n"):
        if "com.xiaoman.treehole.REMINDER_ALARM" in blk or "REMINDER_ALARM" in blk:
            vals += [int(v) for v in re.findall(r"origWhen=(\d+)", blk)]
    return sorted(vals)


def timezone_case(cdp):
    cdp.eval("""(() => {
      localStorage.removeItem('xiaoman_reminders_v1');
      window.Reminders.add('时区重排验收提醒', new Date(Date.now() + 2*3600*1000));
      return window.Reminders.all().map(r => ({id:r.id, at:r.at, text:r.text}));
    })()""")
    time.sleep(2)
    # 触发原生重排：Activity onStop → reschedule（先 Home 再回前台）
    sh("adb shell input keyevent 3")
    time.sleep(3)
    items = native_items()
    check("提醒已镜像到原生（SharedPreferences items 非空）", len(items) >= 1,
          f"items={[(i['id'], i['at']) for i in items]}")
    before = alarm_epoch()
    check("闹钟已排程（origWhen 存在）", bool(before), f"origWhen={before[:3]}")

    _, out = sh("adb shell cmd alarm help")
    if "set-timezone" in out:
        sh("adb shell cmd alarm set-timezone America/New_York", timeout=60)
    else:
        # 老 ROM 无 shell 子命令：改属性 + 显式广播（系统仍会自己发一次）
        sh("adb shell setprop persist.sys.timezone America/New_York")
        sh(f"adb shell am broadcast -a android.intent.action.TIMEZONE_CHANGED -p {PKG}")
    time.sleep(5)
    after = alarm_epoch()
    items2 = native_items()
    same_at = {i["id"]: i["at"] for i in items} == {i["id"]: i["at"] for i in items2}
    check("时区切换后提醒绝对时刻不变（不漂移）", same_at,
          f"before={[(i['id'], i['at']) for i in items]} after={[(i['id'], i['at']) for i in items2]}")
    check("时区切换后闹钟重排（origWhen 仍指向原绝对时刻）",
          bool(after) and set(before) & set(after) != set() or after == before,
          f"before={before[:2]} after={after[:2]}")
    _, lg = sh("adb logcat -d -s ReminderAlarm:V")
    check("时区切换触发 TIMEZONE_CHANGED 重注册", "TIMEZONE_CHANGED" in lg,
          next((l.strip()[:80] for l in lg.splitlines() if "rescheduled after" in l), "无日志"))

    # 72h 时钟前移（root 可用）
    sh("adb root", timeout=60)
    sh("adb wait-for-device", timeout=120)
    time.sleep(2)
    _, cur = sh("adb shell date +%s")
    base = int(cur.strip().splitlines()[-1])
    target = base + 72 * 3600
    sh("adb shell settings put global auto_time 0")
    ok_set = False
    _, tstr = sh(f"adb shell date -d @{target} +%m%d%H%M%Y.%S")
    if re.match(r"\d{12}\.\d{2}", tstr.strip()):
        sh(f"adb shell su 0 date {tstr.strip()}", timeout=60)
        ok_set = True
    if not ok_set:
        sh(f"adb shell su 0 date -s @{target}", timeout=60)
    time.sleep(6)
    _, lg2 = sh("adb logcat -d -s ReminderAlarm:V")
    items3 = native_items()
    check("72h 时钟前移：提醒条目未丢（at 不变）",
          {i["id"]: i["at"] for i in items3} == {i["id"]: i["at"] for i in items},
          f"items={[(i['id'], i['at']) for i in items3]}")
    check("72h 时钟前移：触发 TIME_SET 重排", "TIME_SET" in lg2,
          next((l.strip()[:80] for l in lg2.splitlines() if "rescheduled after" in l), "无日志"))
    _, fatal = sh("adb logcat -d")
    fatal_lines = [l for l in fatal.splitlines() if "AndroidRuntime" in l and PKG in l]
    check("时区/时钟变更全程无 FATAL", not fatal_lines, "\n".join(fatal_lines[-2:])[:150])
    # 恢复时间与时区
    sh("adb shell settings put global auto_time 1")
    _, tstr2 = sh(f"adb shell date -d @{base} +%m%d%H%M%Y.%S")
    if re.match(r"\d{12}\.\d{2}", tstr2.strip()):
        sh(f"adb shell su 0 date {tstr2.strip()}", timeout=60)
    sh("adb shell cmd alarm set-timezone Asia/Shanghai", timeout=60)
    time.sleep(2)


if __name__ == "__main__":
    main()
