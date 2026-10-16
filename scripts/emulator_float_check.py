#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emulator_float_check.py · CI 模拟器悬浮窗验收（P2-4 native 配套）
--------------------------------------------------------------------
在 GitHub KVM 模拟器上验证 Android 原生悬浮窗完整交互链：
  ① appops 授予 SYSTEM_ALERT_WINDOW（等价用户在设置页手动允许）
  ② start-foreground-service 拉起悬浮球
  ③ uiautomator dump 定位悬浮球（TextView text="满"）→ 截图
  ④ input swipe 拖动悬浮球 → dump 验证坐标已变 → 截图
  ⑤ 点球展开迷你对话窗 → dump 验证面板（WebView+标题）→ 截图
  ⑥ 迷你窗内真实聊天一轮（tap EditText→input→ENTER）→ 截图回复
  ⑦ 点「收起」折叠回悬浮球 → dump 验证回球态 → 截图
  ⑧ 全程 logcat 抓 FATAL（浮窗路径崩溃即失败）
用法（emulator-runner script 内）：python3 scripts/emulator_float_check.py
退出码：0=全部通过；1=有失败项（逐项打印 [FLOAT-PASS]/[FLOAT-FAIL]）
"""
import re
import subprocess
import sys
import time

PKG = "com.xiaoman.treehole"
SVC = f"{PKG}/.FloatWindowService"
BALL_TEXT = "满"          # 悬浮球 TextView 文本
PANEL_TITLE = "小满树洞"   # 迷你窗标题栏
COLLAPSE_TEXT = "收起"     # 收起按钮
RESULTS = []


def sh(cmd, timeout=60):
    """跑 adb/host 命令，返回 (rc, stdout+stderr)。"""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def check(name, ok, info=""):
    tag = "[FLOAT-PASS]" if ok else "[FLOAT-FAIL]"
    print(f"{tag} {name}" + (f" | {info}" if info else ""), flush=True)
    RESULTS.append(ok)


def dump():
    """uiautomator dump 并返回 XML 文本（失败返回 ''）。"""
    sh("adb shell uiautomator dump /sdcard/fdump.xml", timeout=60)
    rc, xml = sh("adb shell cat /sdcard/fdump.xml", timeout=30)
    return xml if rc == 0 and "<hierarchy" in xml else ""


def find_bounds(xml, **attrs):
    """在 dump XML 里按属性找节点，返回 (cx, cy, rect)。支持 text/class/content-desc。"""
    for key, val in attrs.items():
        pat = re.compile(
            r'<node[^>]*\b' + key + r'="[^"]*' + re.escape(val) + r'[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
        m = pat.search(xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2, (x1, y1, x2, y2)
    return None


def shot(name):
    sh(f"adb exec-out screencap -p > {name}", timeout=60)


def fatal_in_logcat():
    _, out = sh('adb logcat -d | grep -i AndroidRuntime | grep -i xiaoman | tail -5')
    return out.strip()


def dump_texts(xml):
    """提取 dump 里所有非空 text 属性（用于前后对比检测新回复）。"""
    return set(re.findall(r' text="([^"]+)"', xml))


def has_cjk(s):
    return any('\u4e00' <= c <= '\u9fff' for c in s)


def main():
    # ⓪ 主应用退后台（HOME）——悬浮球/迷你窗覆盖在桌面之上，dump 无主 WebView 文本干扰
    sh("adb shell input keyevent 3")
    time.sleep(1)

    # ① 授权（等价手动允许「显示在其他应用上层」）
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW allow")
    time.sleep(1)
    rc, out = sh(f"adb shell cmd appops get {PKG} SYSTEM_ALERT_WINDOW")
    check("appops授权 SYSTEM_ALERT_WINDOW=allow", "allow" in out, out.strip()[:80])

    # ② 前台服务拉起悬浮球（等价已授权用户点「开启悬浮球」→ floatShow）
    rc, out = sh(f"adb shell am start-foreground-service -n {SVC}")
    check("start-foreground-service 无报错", rc == 0 and "Error" not in out and "Exception" not in out,
          out.strip().splitlines()[-1][:80] if out.strip() else "")
    time.sleep(4)

    # 服务存活 + 无 FATAL
    rc, out = sh(f"adb shell dumpsys activity services {PKG} | grep -c FloatWindowService")
    check("FloatWindowService 运行中", rc == 0 and out.strip() not in ("", "0"), out.strip()[:40])
    check("logcat 无 FATAL（起服务后）", fatal_in_logcat() == "")

    # ③ 定位悬浮球
    xml = dump()
    ball = find_bounds(xml, text=BALL_TEXT) or find_bounds(xml, content_desc=BALL_TEXT)
    check("悬浮球可见（dump 定位 TextView=满）", ball is not None)
    shot("emulator_04_float_ball.png")

    if ball:
        bx, by, rect0 = ball
        # ④ 拖动悬浮球（>SLOP 判定距离，600px 位移）
        sh(f"adb shell input swipe {bx} {by} {bx + 600} {by + 700} 600")
        time.sleep(2)
        xml2 = dump()
        ball2 = find_bounds(xml2, text=BALL_TEXT) or find_bounds(xml2, content_desc=BALL_TEXT)
        moved = ball2 is not None and abs(ball2[0] - bx) > 300
        check("悬浮球可拖动（位移>300px）", moved,
              f"({bx},{by})→({ball2[0]},{ball2[1]})" if ball2 else "ball lost")
        shot("emulator_05_float_drag.png")

        # ⑤ 点球（Tap=位移<20px）展开迷你窗
        tapx, tapy = (ball2[0], ball2[1]) if ball2 else (bx + 600, by + 700)
        sh(f"adb shell input tap {tapx} {tapy}")
        time.sleep(7)   # WebView 加载 + app init
        xml3 = dump()
        title = find_bounds(xml3, text=PANEL_TITLE)
        webview = find_bounds(xml3, class_="android.webkit.WebView")
        check("迷你窗展开（标题栏可见）", title is not None)
        check("迷你窗 WebView 挂载", webview is not None)
        check("logcat 无 FATAL（展开后）", fatal_in_logcat() == "")
        shot("emulator_06_float_panel.png")

        # ⑥ 迷你窗内真实聊天一轮（前后对比：发送 ascii → 新 CJK 节点=回复）
        texts_before = dump_texts(xml3)
        edit = find_bounds(xml3, class_="android.widget.EditText") or \
               find_bounds(xml3, text="跟小满说点什么") or find_bounds(xml3, content_desc="给小满发消息")
        check("迷你窗输入框可见", edit is not None)
        if edit:
            ex, ey, _ = edit
            sh(f"adb shell input tap {ex} {ey}")
            time.sleep(1)
            sh('adb shell input text "jin%s tian%s xin%s qing%s bu%s hao"')
            time.sleep(1)
            sh("adb shell input keyevent 66")   # ENTER → web 端 submitText()
            time.sleep(9)   # 等离线引擎回复渲染
            xml4 = dump()
            new_texts = [t for t in dump_texts(xml4) - texts_before if has_cjk(t) and len(t) >= 4]
            check("迷你窗聊天发送+回复（新CJK节点）", bool(new_texts),
                  (new_texts[0][:40] if new_texts else "无新回复节点"))
            check("logcat 无 FATAL（聊天后）", fatal_in_logcat() == "")
            shot("emulator_07_float_chat.png")

        # ⑦ 收起折叠回悬浮球
        xml5 = dump()
        collapse = find_bounds(xml5, text=COLLAPSE_TEXT)
        if not collapse:
            collapse = find_bounds(dump(), text=COLLAPSE_TEXT)
        check("收起按钮可见", collapse is not None)
        if collapse:
            sh(f"adb shell input tap {collapse[0]} {collapse[1]}")
            time.sleep(3)
            xml6 = dump()
            ball3 = find_bounds(xml6, text=BALL_TEXT) or find_bounds(xml6, content_desc=BALL_TEXT)
            edit_gone = find_bounds(xml6, class_="android.widget.EditText") is None
            check("收起后回悬浮球（球在+面板收）", ball3 is not None and edit_gone)
            shot("emulator_08_float_collapsed.png")

    # ⑧ 服务关闭收尾（等价用户点「关闭悬浮窗」）
    sh(f"adb shell am stopservice -n {SVC}")
    time.sleep(2)
    rc, out = sh(f"adb shell dumpsys activity services {PKG} | grep -c FloatWindowService")
    # stopservice 后 dumpsys 计数归 0 或服务不再运行
    check("stopservice 干净退出", out.strip() in ("", "0") or rc != 0, out.strip()[:40])
    check("logcat 无 FATAL（全程）", fatal_in_logcat() == "")

    ok = all(RESULTS)
    print(f"[FLOAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
