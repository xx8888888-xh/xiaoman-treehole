#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emulator_float_check.py · CI 模拟器悬浮窗验收 v3（P2-4b）
--------------------------------------------------------------------
v3 变更（v2 的 uiautomator dump 通道被证实有盲区后重写）：
  实测证据链：run 38853971176 —— 服务运行(ServiceRecord=1)且 04 截图经视觉
  确认悬浮球已渲染（桌面左上粉色圆球+白字满），但 uiautomator dump 不含
  TYPE_APPLICATION_OVERLAY 窗口节点（与 WebView DOM 不进 dump 同类盲区）。
  ⇒ 放弃 dump 通道，改为三通道证据：
  A) 窗口级：dumpsys window windows —— xiaoman 的 overlay 窗口(mType=2038)
     及其 mFrame 坐标（解析失败记 WARN 诊断，不作硬断言）
  B) 像素级：screencap -p 基线差分（PIL）——球可见/拖动位移/面板展开/收起回球
     /移除全部以像素差分断言（主证据通道）
  C) 链路级：console→logcat 桥（tag=XiaoManWeb [probe] msg me/them）——迷你窗
     内真实聊天证据（不依赖 DOM dump）
流程：⓪HOME+基线截图 → ①appops 授权 → ②意图通道拉起 FGS（exported=false，
  等价用户打开 App）→ ③球可见（像素差分球簇+dumpsys）→ ④拖动（swipe→像素
  位移>300px，含贴边吸附）→ ⑤单击展开迷你窗（大面积像素差分+大窗口出现）→
  ⑥迷你窗内真实聊天（坐标法+probe 双向断言）→ ⑦「收起」回球态（像素回球量级
  +大窗口消失）→ ⑧意图通道 float_stop（ServiceRecord=0+像素归零+窗口清空）→
  ⑨全程 logcat FATAL 抓取。
产物：emulator_04~09 六张交互截图 + float_dumpsys.txt（诊断上传）。
退出码：0=全部通过；1=有失败项（逐项 [FLOAT-PASS]/[FLOAT-FAIL]）。
"""
import re
import subprocess
import sys
import time

PKG = "com.xiaoman.treehole"
SVC = f"{PKG}/.FloatWindowService"
MAIN = f"{PKG}/.MainActivity"
RESULTS = []

# ---- 像素差分阈值（1080x2148 实测分辨率标定；按比例自适应其他屏）----
TOP_CROP = 120          # 裁掉状态栏+launcher时钟区（分钟跳变防误报）
DIFF_SENS = 25          # 灰度差>25 视为变化像素
BALL_MIN, BALL_MAX = 1200, 150000      # 球量级差异像素（60dp球≈19k px）
PANEL_MIN = 300000                       # 面板量级（≈892x1207≈1.08M px）
GONE_MAX = 8000                          # 移除后残留容忍（时钟跳变<3200px）


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
    return ok


def shot(name):
    sh(f"adb exec-out screencap -p > {name}", timeout=60)


def gray(path):
    from PIL import Image
    im = Image.open(path).convert("L")
    return im.crop((0, TOP_CROP, im.width, im.height))


def diff_stat(base, cur):
    """像素差分：返回 (变化像素数, bbox=None|(x1,y1,x2,y2) 已裁剪坐标)。"""
    try:
        from PIL import ImageChops
        d = ImageChops.difference(base, cur)
        mask = d.point(lambda v: 255 if v > DIFF_SENS else 0)
        box = mask.getbbox()
        cnt = mask.histogram()[255] if mask.mode == "L" else 0
        return cnt, box
    except Exception as e:
        return -1, None


def box_center(box):
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


def box_size(box):
    return box[2] - box[0], box[3] - box[1]


def dumpsys_windows():
    """dumpsys window windows 全文 → 落盘诊断 + 提取 xiaoman overlay 窗口列表。"""
    rc, out = sh("adb shell dumpsys window windows", timeout=60)
    try:
        with open("float_dumpsys.txt", "w", encoding="utf-8", errors="replace") as f:
            f.write(out)
    except Exception:
        pass
    wins = []
    for blk in re.split(r"Window #\d+", out):
        if PKG not in blk:
            continue
        tm = re.search(r"mType=(\d+)", blk)
        fm = re.search(r"mFrame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", blk)
        if fm:
            x1, y1, x2, y2 = map(int, fm.groups())
            wins.append({"type": int(tm.group(1)) if tm else -1,
                         "frame": (x1, y1, x2, y2)})
    return wins


def overlay_wins(wins):
    return [w for w in wins if w["type"] == 2038]


def fatal_in_logcat():
    _, out = sh('adb logcat -d | grep -i AndroidRuntime | grep -i xiaoman | tail -5')
    return out.strip()


def probe_lines():
    _, out = sh("adb logcat -d -s XiaoManWeb:V", timeout=30)
    return out


def svc_record_count():
    _, out = sh('adb shell dumpsys activity services ' + PKG + ' | grep -c "ServiceRecord.*FloatWindowService"')
    return out.strip()


def chat_round(panel_box):
    """迷你窗内真实聊天一轮：坐标法点输入区→text→ENTER→probe 断言。"""
    x1, y1, x2, y2 = panel_box
    wcx = (x1 + x2) // 2
    icy = y2 - int((y2 - y1) * 0.07)
    sh("adb logcat -c")
    sh(f"adb shell input tap {wcx} {icy}")
    time.sleep(1)
    sh('adb shell input text "jin%s tian%s xin%s qing%s bu%s hao"')
    time.sleep(1)
    sh("adb shell input keyevent 66")
    time.sleep(12)
    probes = probe_lines()
    ok_me = "[probe] msg me" in probes
    ok_them = "[probe] msg them" in probes
    if not (ok_me and ok_them):            # 重试一轮（键盘焦点时序兜底）
        sh(f"adb shell input tap {wcx} {icy}")
        time.sleep(1)
        sh("adb shell input keyevent 66")
        time.sleep(12)
        probes = probe_lines()
        ok_me = ok_me or "[probe] msg me" in probes
        ok_them = ok_them or "[probe] msg them" in probes
    check("迷你窗聊天：用户消息已发送（[probe] msg me）", ok_me)
    check("迷你窗聊天：小满已回复（[probe] msg them）", ok_them,
          next((l.strip()[:60] for l in probes.splitlines() if "msg them" in l), "无回复探针"))
    check("logcat 无 FATAL（聊天后）", fatal_in_logcat() == "")


def main():
    # ⓪ HOME 退后台 + 基线截图（无球桌面）
    sh("adb shell input keyevent 3")
    time.sleep(2)
    shot("float_base.png")
    base = gray("float_base.png")

    # ① 授权（等价手动允许「显示在其他应用上层」）
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW allow")
    time.sleep(1)
    rc, out = sh(f"adb shell cmd appops get {PKG} SYSTEM_ALERT_WINDOW")
    check("appops授权 SYSTEM_ALERT_WINDOW=allow", "allow" in out, out.strip()[:80])

    # ② 意图通道拉起：等价已授权用户打开 App → 悬浮球自动挂出
    rc, out = sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000")
    check("MainActivity autostart_float 通道无报错",
          rc == 0 and "Error" not in out and "Exception" not in out,
          out.strip().splitlines()[-1][:80] if out.strip() else "")
    time.sleep(4)                       # FGS 拉起 + 通知 + addView
    sh("adb shell input keyevent 3")    # HOME 隔离：球覆盖在桌面之上
    time.sleep(2)

    cnt = svc_record_count()
    check("FloatWindowService 运行中（ServiceRecord）", cnt not in ("", "0"),
          f"record={cnt}" if cnt else "dumpsys无输出")
    check("logcat 无 FATAL（起服务后）", fatal_in_logcat() == "")

    # ③ 球可见 —— 主证据：像素差分（球簇）；副证据：dumpsys overlay 窗口
    shot("emulator_04_float_ball.png")
    cur = gray("emulator_04_float_ball.png")
    cnt4, box4 = diff_stat(base, cur)
    ball_visible = box4 is not None and BALL_MIN < cnt4 < BALL_MAX
    check("悬浮球可见（像素差分球簇）", ball_visible, f"diff={cnt4}px box={box4}")
    wins = dumpsys_windows()
    ov = overlay_wins(wins)
    check("overlay 窗口已注册（dumpsys mType=2038）", len(ov) >= 1,
          f"overlay={len(ov)}/{len(wins)}" if wins else "dumpsys未解析出窗口(诊断已存)")

    if not ball_visible:
        # 球不可见时后续交互无从谈起，直接跳到停服务清理段
        print("[FLOAT-SKIP] 球不可见，跳过交互链（后续步骤验证停服务兜底）", flush=True)

    ball_center = box_center(box4) if ball_visible else None

    if ball_center:
        bx, by = ball_center
        # ④ 拖动（>SLOP 位移 600px；松手贴边吸附）→ 像素位移断言
        sh(f"adb shell input swipe {bx} {by} {bx + 600} {by + 700} 600")
        time.sleep(2)
        shot("emulator_05_float_drag.png")
        cur5 = gray("emulator_05_float_drag.png")
        cnt5, box5 = diff_stat(base, cur5)
        ball5 = box5 is not None and BALL_MIN < cnt5 < BALL_MAX + 60000
        moved = ball5 and abs(box_center(box5)[0] - bx) > 300
        check("悬浮球可拖动（像素位移>300px）", bool(moved),
              f"({bx},{by})→{box_center(box5) if ball5 else 'lost'} diff={cnt5}px")

        # ⑤ 单击球心（吸附后位置）→ 展开迷你窗：大面积像素差分 + 大 overlay 窗口
        tapx, tapy = box_center(box5) if ball5 else (bx + 600, by + 700)
        sh(f"adb shell input tap {tapx} {tapy}")
        time.sleep(7)                    # WebView 加载 + app init
        shot("emulator_06_float_panel.png")
        cur6 = gray("emulator_06_float_panel.png")
        cnt6, box6 = diff_stat(base, cur6)
        check("迷你窗展开（大面积像素差分）", cnt6 > PANEL_MIN, f"diff={cnt6}px")
        wins6 = dumpsys_windows()
        big = [w for w in overlay_wins(wins6) if (w["frame"][2] - w["frame"][0]) > 500]
        check("迷你窗大窗口已注册（dumpsys 宽>500px）", len(big) >= 1,
              f"{[w['frame'] for w in overlay_wins(wins6)]}" if wins6 else "dumpsys未解析(诊断已存)")
        check("logcat 无 FATAL（展开后）", fatal_in_logcat() == "")

        # 面板交互区：dumpsys 大窗口 frame 优先，像素 bbox 兜底
        panel_box = big[0]["frame"] if big else (box6 if box6 else (tapx - 400, tapy - 500, tapx + 400, tapy + 600))
        shot_6 = ("emulator_07_float_chat.png")

        # ⑥ 迷你窗内真实聊天一轮（probe 双向断言）
        chat_round(panel_box)
        shot(shot_6)

        # ⑦ 点「收起」：收软键盘 → 面板标题栏右侧按钮（关闭在最右，收起在其左）
        sh("adb shell input keyevent 111")
        time.sleep(1)
        px1, py1, px2, py2 = panel_box
        collapse_x = px2 - 256
        collapse_y = py1 + 48
        sh(f"adb shell input tap {collapse_x} {collapse_y}")
        time.sleep(3)
        shot("emulator_08_float_collapsed.png")
        cur8 = gray("emulator_08_float_collapsed.png")
        cnt8, box8 = diff_stat(base, cur8)
        back_ball = box8 is not None and BALL_MIN < cnt8 < BALL_MAX
        check("收起后回悬浮球（像素回球量级）", back_ball, f"diff={cnt8}px box={box8}")
        wins8 = dumpsys_windows()
        big_gone = not [w for w in overlay_wins(wins8) if (w["frame"][2] - w["frame"][0]) > 500]
        check("迷你窗已收起（大窗口消失）", big_gone,
              f"{[w['frame'] for w in overlay_wins(wins8)]}" if wins8 else "dumpsys未解析(诊断已存)")
        check("logcat 无 FATAL（收起后）", fatal_in_logcat() == "")

    # ⑧ 意图通道停服务（等价用户点「关闭」）→ 三重断言
    sh(f"adb shell am start -n {MAIN} --ez float_stop true -f 0x20000000")
    time.sleep(3)
    sh("adb shell input keyevent 3")
    time.sleep(2)
    shot("emulator_09_float_stopped.png")
    cur9 = gray("emulator_09_float_stopped.png")
    cnt9, box9 = diff_stat(base, cur9)
    ball_gone = cnt9 < GONE_MAX and (box9 is None or box_size(box9)[0] * box_size(box9)[1] < 30000)
    check("悬浮球已从屏幕移除（像素归零）", ball_gone, f"diff={cnt9}px box={box9}")
    cnt2 = svc_record_count()
    check("float_stop 干净退出（ServiceRecord=0）", cnt2 == "0", f"record={cnt2}")
    wins9 = dumpsys_windows()
    check("overlay 窗口已全部注销", len(overlay_wins(wins9)) == 0,
          f"overlay={len(overlay_wins(wins9))}" if wins9 else "dumpsys未解析(诊断已存)")
    check("logcat 无 FATAL（全程）", fatal_in_logcat() == "")

    ok = all(RESULTS)
    print(f"[FLOAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
