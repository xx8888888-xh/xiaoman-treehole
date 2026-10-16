#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emulator_float_check.py · CI 模拟器悬浮窗验收（P2-4b native 配套）
--------------------------------------------------------------------
在 GitHub KVM 模拟器上验证 Android 原生悬浮窗完整交互链：
  ⓪ HOME 隔离（悬浮层覆盖在桌面之上，dump 无主 WebView 文本干扰）
  ① appops 授 SYSTEM_ALERT_WINDOW（等价用户在设置页手动允许）
  ② 经 MainActivity 意图通道拉起前台服务（--ez autostart_float true）
     ——服务 exported=false（安全设计），shell 无法直启，等价"用户打开 App 即挂球"
  ③ uiautomator dump 定位悬浮球（TextView text="满"）→ 截图
  ④ input swipe 拖动悬浮球 → dump 验证坐标已变 → 截图
  ⑤ 点球展开迷你窗 → dump 验证原生标题栏+WebView 挂载 → 截图
  ⑥ 迷你窗内真实聊天一轮（坐标法点输入框→input text→ENTER），
     证据通道=console→logcat 桥（tag=XiaoManWeb 的 [probe] msg me/them 行）：
     本模拟器 WebView DOM 不进 uiautomator dump（实测 2KB 空 dump），
     故聊天链路以 addMsg 渲染探针为证（只记方向+长度，不落对话内容）
  ⑦ 点「收起」（原生按钮）折叠回悬浮球 → dump 验证回球态+面板已移除 → 截图
  ⑧ 经 MainActivity 意图通道停服务（--ez float_stop true）→ dumpsys 验证
     ServiceRecord 归零 + 球已从 dump 消失 → 截图
  ⑨ 全程 logcat 抓 FATAL（浮窗路径崩溃即失败）
用法（emulator-runner script 内）：python3 scripts/emulator_float_check.py
退出码：0=全部通过；1=有失败项（逐项打印 [FLOAT-PASS]/[FLOAT-FAIL]）
"""
import re
import subprocess
import sys
import time

PKG = "com.xiaoman.treehole"
SVC = f"{PKG}/.FloatWindowService"
MAIN = f"{PKG}/.MainActivity"
BALL_TEXT = "满"          # 悬浮球 TextView 文本
PANEL_TITLE = "小满树洞"   # 迷你窗标题栏（实际文本「小满树洞 · 迷你窗」子串匹配）
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


def probe_lines():
    """拉取 console→logcat 桥（tag=XiaoManWeb）当前缓冲的全部行。"""
    _, out = sh("adb logcat -d -s XiaoManWeb:V", timeout=30)
    return out


def svc_record_count():
    """dumpsys 严格计数：运行中的 ServiceRecord 行（非运行=0）。"""
    rc, out = sh('adb shell dumpsys activity services ' + PKG + ' | grep -c "ServiceRecord.*FloatWindowService"')
    return out.strip()


def main():
    # ⓪ 主应用退后台（HOME）——悬浮球/迷你窗覆盖在桌面之上，dump 无主 WebView 文本干扰
    sh("adb shell input keyevent 3")
    time.sleep(1)

    # ① 授权（等价手动允许「显示在其他应用上层」）
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW allow")
    time.sleep(1)
    rc, out = sh(f"adb shell cmd appops get {PKG} SYSTEM_ALERT_WINDOW")
    check("appops授权 SYSTEM_ALERT_WINDOW=allow", "allow" in out, out.strip()[:80])

    # ② 意图通道拉起：等价已授权用户打开 App → 悬浮球自动挂出（onCreate/onNewIntent 两路均覆盖）
    #    -f 0x20000000 = FLAG_ACTIVITY_SINGLE_TOP（热实例走 onNewIntent，冷启动走 onCreate）
    rc, out = sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000")
    check("MainActivity autostart_float 通道无报错",
          rc == 0 and "Error" not in out and "Exception" not in out,
          out.strip().splitlines()[-1][:80] if out.strip() else "")
    time.sleep(4)          # FGS 拉起 + 通知 + 悬浮球 addView
    sh("adb shell input keyevent 3")   # 再 HOME：App 回前台会带回主 WebView，退后台隔离
    time.sleep(2)

    # 服务存活（严格 ServiceRecord 行计数）+ 无 FATAL
    cnt = svc_record_count()
    check("FloatWindowService 运行中（ServiceRecord）", cnt not in ("", "0"),
          f"record={cnt}" if cnt else "dumpsys无输出")
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

        # ⑥ 迷你窗内真实聊天一轮：本机 dump 无 WebView DOM（实测），
        #    输入框用 WebView 矩形底部坐标法，回复证据走 console→logcat [probe] 探针
        if webview:
            _, _, (wx1, wy1, wx2, wy2) = webview
            wcx = (wx1 + wx2) // 2
            icy = wy2 - int((wy2 - wy1) * 0.07)   # 底部 7% ≈ 输入行区域
            sh("adb logcat -c")                    # 清缓冲：聊天证据只取本轮
            sh(f"adb shell input tap {wcx} {icy}")
            time.sleep(1)
            sh('adb shell input text "jin%s tian%s xin%s qing%s bu%s hao"')
            time.sleep(1)
            sh("adb shell input keyevent 66")   # ENTER → web 端 submitText()
            time.sleep(12)                      # 等离线引擎回复渲染（分条+打字延迟）
            probes = probe_lines()
            check("迷你窗聊天：用户消息已发送（[probe] msg me）", "[probe] msg me" in probes)
            check("迷你窗聊天：小满已回复（[probe] msg them）", "[probe] msg them" in probes,
                  next((l.strip()[:60] for l in probes.splitlines() if "msg them" in l), "无回复探针"))
            check("logcat 无 FATAL（聊天后）", fatal_in_logcat() == "")
            shot("emulator_07_float_chat.png")

        # ⑦ 收起折叠回悬浮球（「收起」为迷你窗标题栏原生按钮，dump 可见）
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
            panel_gone = find_bounds(xml6, class_="android.webkit.WebView") is None
            check("收起后回悬浮球（球在+面板收）", ball3 is not None and panel_gone)
            shot("emulator_08_float_collapsed.png")

    # ⑧ 意图通道停服务（等价用户点「关闭」→ floatHide → ACTION_HIDE → stopSelf）
    sh(f"adb shell am start -n {MAIN} --ez float_stop true -f 0x20000000")
    time.sleep(3)
    sh("adb shell input keyevent 3")   # HOME 隔离后再验球已消失
    time.sleep(2)
    cnt2 = svc_record_count()
    xml7 = dump()
    ball_gone = find_bounds(xml7, text=BALL_TEXT) is None and find_bounds(xml7, content_desc=BALL_TEXT) is None
    check("float_stop 干净退出（ServiceRecord=0）", cnt2 == "0", f"record={cnt2}")
    check("悬浮球已从屏幕移除", ball_gone)
    check("logcat 无 FATAL（全程）", fatal_in_logcat() == "")

    ok = all(RESULTS)
    print(f"[FLOAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
