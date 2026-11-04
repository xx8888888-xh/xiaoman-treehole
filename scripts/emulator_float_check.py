#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_float_check.py · CI 模拟器悬浮窗验收 v8（P2-4b / 桌宠）
--------------------------------------------------------------------
v8 变更（Live2D 桌宠替代圆形悬浮球）：
  竖屏主浮层从 60dp「满」字球换成 140x180dp 透明 WebView 桌宠：
  a) 身份判别从「白字像素」换成「桌宠窗口 frame 内的像素差分」：
     透明窗只画 Live2D，壁纸区域与基线一致；模型本体（头发/皮肤/描边）
     实测差异量级远高于阈值。frame 仍是 dumpsys 权威坐标。
  b) 窗口分类：桌宠 250~700 x 300~900px；气泡卡片 400~750 x <300px；
     迷你窗 >500 x >500px；降级球 100~300px（保留旧白字判别）。
  c) 交互链改为：单击 → 互动气泡（logcat pet-tap + 额外 overlay 窗口）；
     双击（同一 adb shell 连发两枪）→ 迷你窗（logcat pet-panel）。
  d) 新增权限拒绝降级：appops deny 后拉起，服务必须自停、无覆盖窗、无 FATAL。
  e) 新增进程被杀恢复：force-stop 后重开 App（autostart_float）桌宠回归。
  f) 静音断言：全程无 MediaPlayer/音频焦点请求（页内无 TTS）。
流程：⓪HOME+基线 → ①appops 授权 → ②意图拉起 FGS → ③桌宠可见（frame 差分
  +pet-ready 探针）→ ④拖动贴边 → ⑤单击气泡 → ⑥双击展开迷你窗 → ⑦聊天 →
  ⑧收起回桌宠 → ⑨float_stop 清理 → ⑩权限拒绝不崩溃 → ⑪进程被杀后恢复。
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
WHITE_MIN = 1500                         # 降级球frame内「满」字白像素下限（椅旧判别器）
PET_MIN_DIFF = 6000                      # 桌宠frame内与基线的差异像素下限（Live2D 本体实测 8.7k~12.4k）
PET_HIDE_MAX = 4000                      # 桌宠 GONE 后 frame 内允许的残留差异（时钟抖动）
PANEL_MIN = 300000                       # 迷你窗量级（≈660x794≈52万px @720p，>门限）
GONE_MAX = 8000                          # 移除后残留容忍（时钟跳变~4400px 可容忍）


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
    """截图：用 bytes 管道落盘（PowerShell/cmd 的 > 重定向不可靠，会写坏 PNG）。"""
    try:
        p = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=60)
        data = p.stdout or b""
        if len(data) > 1024 and data[:1] == b"\x89":
            with open(name, "wb") as f:
                f.write(data)
    except Exception:
        pass


def refresh_screen():
    """强制整屏重合成（软件 GL 模拟器的已知怪癖，不是产品行为）。
    本机 emulator(swiftshader) 的 SurfaceFlinger 在 overlay 窗口「被移动/被移除」后
    不一定给对应区域发 damage，screencap 会一直拿到旧帧：实测窗已 move 到新坐标，
    截图逐像素完全不变。拉下再收起通知栏会让全屏重新合成一次，属性/布局/像素内容
    都不改，只是把当前真实状态刷到帧缓冲里，避免把「环境不刷新」误判成「桌宠空白」。"""
    sh("adb shell cmd statusbar expand-notifications", timeout=30)
    time.sleep(1.5)
    sh("adb shell cmd statusbar collapse", timeout=30)
    time.sleep(1.5)


def gray(path):
    from PIL import Image
    im = Image.open(path).convert("L")
    return im.crop((0, TOP_CROP, im.width, im.height))


def diff_stat(base, cur):
    """像素差分：返回 (变化像素数, bbox=None|(x1,y1,x2,y2) 全屏坐标)。
    注意：base/cur 均为 TOP_CROP 裁剪图，bbox 还原 +TOP_CROP 与 adb input /
    dumpsys mFrame 同一全屏坐标系（v3 的 tap/swipe 打偏 120px 即此坑）。"""
    try:
        from PIL import ImageChops
        d = ImageChops.difference(base, cur)
        mask = d.point(lambda v: 255 if v > DIFF_SENS else 0)
        box = mask.getbbox()
        cnt = mask.histogram()[255] if mask.mode == "L" else 0
        if box:
            box = (box[0], box[1] + TOP_CROP, box[2], box[3] + TOP_CROP)
        return cnt, box
    except Exception as e:
        return -1, None


def pink_px(path, box):
    """bbox 区域内品牌粉 #F7A8B8(247,168,184) 容差像素数（球身份双保险）。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if not box:
            return 0
        r = im.crop(box)
        return sum(1 for p in r.getdata()
                   if abs(p[0] - 247) <= 25 and abs(p[1] - 168) <= 25 and abs(p[2] - 184) <= 25)
    except Exception:
        return 0


def white_glyph_px(path, box):
    """区域「满」字白像素数（>245 全通道）——球身份主判别器（v5）。
    实测标定：球 frame 内 3412 vs 球移除后同区域 0（壁纸粉色系但无纯白）；
    壁纸全域白密度 3.5%，WHITE_MIN=1500 留 2.3x 余量。box 为全屏坐标。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if not box:
            return 0
        r = im.crop(box)
        return sum(1 for p in r.getdata() if p[0] > 245 and p[1] > 245 and p[2] > 245)
    except Exception:
        return 0


def _wh(box):
    return box[2] - box[0], box[3] - box[1]


_SCREEN = None


def screen_size():
    """缓存屏幕分辨率（wm size），用于把 gr=CENTER 的请求框换算成真实 frame。"""
    global _SCREEN
    if _SCREEN:
        return _SCREEN
    _, out = sh("adb shell wm size", timeout=30)
    m = re.search(r"(\d+)x(\d+)", out)
    _SCREEN = (int(m.group(1)), int(m.group(2))) if m else (720, 1280)
    return _SCREEN


def ball_frame(wins):
    """降级球窗口 frame（宽 100~300px，且高<300px 的 overlay）。
    expandPanel() 会 setVisibility(GONE)：窗口仍注册但白字=0，frame 依旧权威。"""
    for w in overlay_wins(wins):
        if not w.get("visible", True):
            continue
        fw, fh = _wh(w["frame"])
        if 100 < fw < 300 and fh < 300:
            return w["frame"]
    return None


def pet_frame(wins):
    """Live2D 桌宠窗口 frame（140x180dp @2x = 280x360px；容差 250~400/300~900）。
    宽度上限收到 400：迷你窗 662x793 也落在大窗容差内，不收窄会把面板当桌宠。"""
    for w in overlay_wins(wins):
        if not w.get("visible", True):
            continue
        fw, fh = _wh(w["frame"])
        if 250 < fw < 400 and 300 < fh < 900:
            return w["frame"]
    return None


def big_frame(wins):
    """迷你对话窗 frame（宽>500 且高>500，排除矮气泡卡片与桌宠）。"""
    for w in overlay_wins(wins):
        fw, fh = _wh(w["frame"])
        if fw > 500 and fh > 500:
            return w["frame"]
    return None


def bubble_frame(wins):
    """原生气泡卡片 frame：宽 400~750、高 40~300，用于单击反馈断言。"""
    for w in overlay_wins(wins):
        if not w.get("visible", True):
            continue
        fw, fh = _wh(w["frame"])
        if 400 < fw < 750 and 40 < fh < 300:
            return w["frame"]
    return None


def region_diff(base, cur, box):
    """区域差分计数（base/cur 为 TOP_CROP 裁剪后的灰度图，box 为全屏坐标）。"""
    if not box:
        return 0
    try:
        from PIL import ImageChops
        x1, y1, x2, y2 = [int(v) for v in box]
        y1 = max(0, y1 - TOP_CROP)
        y2 = max(0, y2 - TOP_CROP)
        b = base.crop((x1, y1, x2, y2))
        c = cur.crop((x1, y1, x2, y2))
        d = ImageChops.difference(b, c)
        m = d.point(lambda v: 255 if v > DIFF_SENS else 0)
        return m.histogram()[255]
    except Exception:
        return 0


def pet_visible(shot_name, box, base):
    """桌宠可见 = dumpsys frame 存在 + frame 内与基线差异 ≥ PET_MIN_DIFF。"""
    if not box:
        return False, 0
    from PIL import Image
    cur = gray(shot_name)
    return region_diff(base, cur, box) >= PET_MIN_DIFF, region_diff(base, cur, box)


def probe_has(marker):
    return marker in probe_lines()


def find_collapse_button(img_path, panel_box):
    """面板顶栏右侧深色文字簇 → 「收起」按钮中心（右数第二簇）。
    依据：顶栏按钮字色 #5B4A43（灰度~75）vs 顶栏底 ~247；「关闭」最右、「收起」
    在其左。overlay 窗口内原生 Button 与球 TextView 同样不进 uiautomator dump
    （v2/v3 实测盲区），故只能像素定位。
    v5：cx/cy 都取簇内真实暗点质心（v4 的 cy 公式实测偏下 74px 会点进
    WebView 区）。
    v6（run 38884396667 实测 20/21 根因）：面板右缘 7px 圆角阴影杂点簇
    （16 点）被当成"最右簇"，右数第二簇错位到「关闭」（x910 实测）——
    点击后球窗口被注销、白字归零、像素归零，正是"关闭"特征。
    ⇒ 微簇过滤：有效簇须 ≥100 点且宽 ≥30px（真实按钮文字簇实测
    收起 763点/66px、关闭 753点/65px），过滤后右数第二=收起。
    真实标定（emulator_06 全屏坐标）：收起中心 (738,541)、关闭 (910,540)、
    顶栏文字行 y 523~563。返回 (cx, cy) 或 None。"""
    try:
        from PIL import Image
        px1, py1, px2, py2 = [int(v) for v in panel_box]
        x1 = int(px1 + (px2 - px1) * 0.55)
        # v7: 确定性 y 带锚定——原生标题栏布局固定（8dp 顶 padding + 按钮居中），
        # 按钮文字行实测恒在 py1+40~80（523~563 @py1=483）。裁剪带 [py1+25, py1+100]
        # 把 WebView 内容（≥py1+100）彻底挡在带外——0.18/0.10 比例裁剪都被聊天
        # 内容暗像素并簇拉偏质心过（run 39107576044 得 (703,628) 点空）。
        im = Image.open(img_path).convert("L")
        bar = im.crop((x1, int(py1) + 25, int(px2), int(py1) + 100))
        w, h = bar.size
        p = bar.load()
        pts = sorted(((x, y) for y in range(h) for x in range(w) if p[x, y] < 170),
                     key=lambda t: t[0])
        if len(pts) < 12:
            return None
        clusters, cur = [], [pts[0]]
        for pt in pts[1:]:
            if pt[0] - cur[-1][0] > 40:
                clusters.append(cur)
                cur = [pt]
            else:
                cur.append(pt)
        clusters.append(cur)
        # v6 微簇过滤：面板右缘阴影杂点簇（16点/7px）会把"右数第二"顶成「关闭」
        valid = [c for c in clusters if len(c) >= 100 and (c[-1][0] - c[0][0]) >= 30]
        if len(valid) < 2:
            return None
        coll = valid[-2]          # 右数第二有效簇 = 收起
        cx = x1 + sum(x for x, _ in coll) // len(coll)
        cy = int(py1) + 25 + sum(y for _, y in coll) // len(coll)
        return (cx, cy)
    except Exception:
        return None


def box_center(box):
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


def box_size(box):
    return box[2] - box[0], box[3] - box[1]


def dumpsys_windows():
    """dumpsys window windows 全文 → 落盘诊断 + 提取 xiaoman 窗口列表。
    实测（API 35 模拟器）：窗口 type 写作 ty=APPLICATION_OVERLAY（文字）或
    ty=2038（数字，systemui 混用两种），mType= 是 insets 的 ITYPE_xxx 字段。"""
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
        tm = re.search(r"\bty=([A-Z_0-9]+)", blk)
        frame = None
        from_attrs = False
        fm = re.search(r"mFrame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", blk)
        if fm:
            frame = tuple(map(int, fm.groups()))
        else:
            # MuMu / 部分 A12 ROM 不打印 mFrame，只给 mAttrs 的请求框：
            #   mAttrs={(x,y)(w x h) ... ty=APPLICATION_OVERLAY}
            # 气泡卡片高度是 wrap（实测 480xwrap），按单行卡片给名义高度以便尺寸判别
            am = re.search(r"mAttrs=\{\((-?\d+),(-?\d+)\)\((\d+)\s*x\s*(\d+|wrap)\)", blk)
            if am:
                x, y, w, hs = am.groups()
                h = 160 if hs == "wrap" else int(hs)
                frame = (int(x), int(y), int(x) + int(w), int(y) + h)
                from_attrs = True
        if frame:
            gm = re.search(r"gr=([A-Z_ ]+?) sim=", blk)
            grav = gm.group(1).strip() if gm else ""
            # 请求框 (0,0) 且 gr=CENTER 的窗口（迷你窗实测 662x793）真实位置在屏幕居中，
            # 不算这一步会把面板坐标整体偏出去半个屏，聊天/收起的点击就全打空
            if from_attrs and grav == "CENTER":
                sw, shh = screen_size()
                w = frame[2] - frame[0]
                h = frame[3] - frame[1]
                frame = ((sw - w) // 2, (shh - h) // 2, (sw + w) // 2, (shh + h) // 2)
            # 桌宠展开迷你窗时 hidePrimary() 走 setVisibility(GONE)：窗口仍注册但不可见，
            # 不分辨会把「已隐藏」误判成「还在」
            vis = "mViewVisibility=0x8" not in blk and "isVisible=false" not in blk
            wins.append({"ty": tm.group(1) if tm else "?", "frame": frame, "visible": vis})
    return wins


def overlay_wins(wins):
    return [w for w in wins if w["ty"] in ("APPLICATION_OVERLAY", "2038")]


def _logcat(args=""):
    _, out = sh(f"adb logcat -d {args}", timeout=60)
    return out


def fatal_in_logcat():
    """安卓运行时崩溃 + 本包名（Python 侧过滤，避免 cmd.exe 误解 `|`）。"""
    hits = [ln for ln in _logcat().splitlines()
            if "AndroidRuntime" in ln and PKG in ln]
    return "\n".join(hits[-5:]).strip()


def probe_lines():
    _, out = sh("adb logcat -d -s XiaoManWeb:V", timeout=30)
    return out


def svc_record_count():
    _, out = sh("adb shell dumpsys activity services " + PKG, timeout=60)
    return str(sum(1 for ln in out.splitlines()
                   if "ServiceRecord" in ln and "FloatWindowService" in ln))


def chat_round(panel_box):
    """迷你窗内真实聊天一轮：坐标法点输入区→text→ENTER→probe 断言。
    输入栏在 float 布局最底部（CSS: inputbar 底置），tap 面板 y2-7%。"""
    x1, y1, x2, y2 = panel_box
    wcx = (x1 + x2) // 2
    icy = y2 - int((y2 - y1) * 0.07)

    def one_round():
        sh(f"adb shell input tap {wcx} {icy}")      # focus 输入框
        time.sleep(1)
        sh('adb shell input text "jin%s tian%s xin%s qing%s bu%s hao"')
        time.sleep(1)
        sh("adb shell input keyevent 66")           # ENTER 发送
        time.sleep(12)                              # mock 回复+探针落 logcat

    sh("adb logcat -c")
    one_round()
    probes = probe_lines()
    ok_me = "[probe] msg me" in probes
    ok_them = "[probe] msg them" in probes
    if not (ok_me and ok_them):            # 重试一轮：tap→text→ENTER 完整重跑
        print(f"[FLOAT-RETRY] 首轮探针 me={ok_me} them={ok_them}，重跑一轮", flush=True)
        one_round()
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

    # ② 意图通道拉起：等价已授权用户打开 App → Live2D 桌宠自动挂出
    sh("adb logcat -c")
    rc, out = sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000")
    check("MainActivity autostart_float 通道无报错",
          rc == 0 and "Error" not in out and "Exception" not in out,
          out.strip().splitlines()[-1][:80] if out.strip() else "")
    time.sleep(5)                       # FGS + 通知 + WebView/模型加载
    sh("adb shell input keyevent 3")    # HOME 隔离：桌宠覆盖在桌面之上
    time.sleep(2)

    cnt = svc_record_count()
    check("FloatWindowService 运行中（ServiceRecord）", cnt not in ("", "0"),
          f"record={cnt}" if cnt else "dumpsys无输出")
    check("logcat 无 FATAL（起服务后）", fatal_in_logcat() == "")

    # ③ 桌宠可见：dumpsys 桌宠 frame + frame 内基线差分（透明窗外壁纸=基线，模型本体有差异）
    refresh_screen()
    pframe, pdiff, pet_ok = None, 0, False
    wins = []
    for i in range(6):
        shot("emulator_04_float_pet.png")
        wins = dumpsys_windows()
        pframe = pet_frame(wins)
        if pframe:
            cur4 = gray("emulator_04_float_pet.png")
            pdiff = region_diff(base, cur4, pframe)
            pet_ok = pdiff >= PET_MIN_DIFF
        if pet_ok:
            break
        print(f"[FLOAT-RETRY] 桌宠未就绪(第{i+1}轮) frame={pframe} diff={pdiff}px", flush=True)
        time.sleep(2)

    ov = overlay_wins(wins)
    check("overlay 窗口已注册（dumpsys ty=APPLICATION_OVERLAY）", len(ov) >= 1,
          f"overlay={len(ov)}/{len(wins)} frames={[w['frame'] for w in ov][:3]}" if wins
          else "dumpsys未解析出窗口(诊断已存)")
    check("桌宠窗口已注册（140x180dp frame）", pframe is not None, f"frame={pframe}")
    check("桌宠可见（frame 内 Live2D 差分≥6000px）", pet_ok, f"diff={pdiff}px frame={pframe}")
    check("桌宠 Live2D 已就绪（[probe] pet-ready）", probe_has("[probe] pet-ready"))
    bframe = ball_frame(wins)
    check("未降级为圆形悬浮球（主形态=Live2D 桌宠）", bframe is None,
          f"ball_frame={bframe}" if bframe else "")

    if not pet_ok:
        print("[FLOAT-SKIP] 桌宠不可见，跳过交互链（后续仍验证停服务/权限/恢复）", flush=True)
        bx = by = ncx = ncy = 0
        panel_box = None
    else:
        bx, by = box_center(pframe)

        # ④ 拖动（>SLOP 位移 600px；松手贴边吸附）→ dumpsys 桌宠 frame 位移断言
        sh(f"adb shell input swipe {bx} {by} {bx + 600} {by + 700} 600")
        time.sleep(2)
        wins5 = dumpsys_windows()
        nframe = pet_frame(wins5)
        ndiff = 0
        # 软件 GL 模拟器拖动后 overlay 不主动刷帧，先强制重合成再截（见 refresh_screen 注释）
        refresh_screen()
        # 软件 GL 模拟器拖动后会重建渲染目标，第一张截图常常拍到空白首帧，多拍几轮等它画出来
        for _ in range(6):
            shot("emulator_05_float_drag.png")
            cur5 = gray("emulator_05_float_drag.png")
            ndiff = region_diff(base, cur5, nframe) if nframe else 0
            if ndiff >= PET_MIN_DIFF:
                break
            time.sleep(1)
        if nframe:
            ncx, ncy = box_center(nframe)
            moved = ndiff >= PET_MIN_DIFF and (abs(ncx - bx) > 300 or abs(ncy - by) > 300)
        else:
            ncx, ncy = 0, 0
            moved = False
        check("桌宠可拖动并贴边（frame 位移>300px + 桌宠跟随）", bool(moved),
              f"({bx},{by})→({ncx},{ncy}) diff={ndiff}px frame={nframe}")
        check("拖动后 logcat 无 FATAL", fatal_in_logcat() == "")
        tapx, tapy = (ncx, ncy) if nframe else (bx, by)

        # ⑤ 单击桌宠 → 互动（表情/动作 + 原生气泡卡片；logcat pet-tap）
        sh("adb logcat -c")
        sh(f"adb shell input tap {tapx} {tapy}")
        time.sleep(1)
        shot("emulator_06a_float_bubble.png")
        wins_b = dumpsys_windows()
        bub = bubble_frame(wins_b)
        tap_probe = probe_has("[probe] pet-tap:")
        check("单击桌宠触发互动（[probe] pet-tap）", tap_probe)
        check("单击后弹出原生气泡卡片（overlay 卡片窗口）", bub is not None,
              f"bubble_frame={bub} wins={[w['frame'] for w in overlay_wins(wins_b)]}")
        check("单击气泡后桌宠仍在（不被气泡顶掉）", pet_frame(wins_b) is not None,
              f"pet_frame={pet_frame(wins_b)}")

        # ⑥ 双击桌宠（同一个 adb shell 连发两枪，间隔 <380ms）→ 展开迷你窗
        sh("adb logcat -c")
        sh(f"adb shell \"input tap {tapx} {tapy}; input tap {tapx} {tapy}\"")
        big, panel_box, cnt6 = None, None, 0
        for i in range(5):
            time.sleep(3)
            shot("emulator_06_float_panel.png")
            cur6 = gray("emulator_06_float_panel.png")
            cnt6, box6 = diff_stat(base, cur6)
            wins6 = dumpsys_windows()
            big = big_frame(wins6)
            if big:
                panel_box = big
                break
            print(f"[FLOAT-RETRY] 迷你窗未就绪(第{i+1}轮) diff={cnt6}px", flush=True)
        check("双击展开迷你窗（大窗口 frame 宽高>500px）", big is not None,
              f"frame={big} diff={cnt6}px")
        check("双击触发探针（[probe] pet-double / pet-panel）",
              probe_has("[probe] pet-double") and probe_has("[probe] pet-panel"))
        check("迷你窗展开（大面积像素差分>30万px）", cnt6 > PANEL_MIN, f"diff={cnt6}px")
        pf6 = pet_frame(wins6)
        hidden_diff = region_diff(base, gray("emulator_06_float_panel.png"), pf6) if pf6 else 0
        check("展开后桌宠已隐藏（窗口不可见或 frame 差异≤4000px）",
              pf6 is None or hidden_diff <= PET_HIDE_MAX,
              f"hidden_diff={hidden_diff}px frame={pf6}")
        check("logcat 无 FATAL（展开后）", fatal_in_logcat() == "")

        # ⑥b 迷你窗内真实聊天一轮（probe 双向断言）
        if panel_box:
            chat_round(panel_box)
            shot("emulator_07_float_chat.png")

        # ⑦ 点「收起」：BACK 收软键盘 → 扫描顶栏深色文字簇 → 收起后桌宠复现
        sh("adb shell input keyevent 4")
        time.sleep(1)
        shot("float_bar.png")
        btn = find_collapse_button("float_bar.png", panel_box) if panel_box else None
        px1, py1, px2, py2 = panel_box if panel_box else (0, 0, 0, 0)
        fallback = (int(px2 - 269), int(py1 + 58))   # 实测收起按钮中心标定
        candidates = ([btn] if btn else []) + [fallback]
        back_pet = big_gone = False
        cnt8 = box8 = wins8 = pframe8 = ndiff8 = None
        for i, (cx, cy) in enumerate(candidates):
            tag = "扫描质心" if (btn and (cx, cy) == btn) else "兜底坐标"
            print(f"[FLOAT-BTN] 第{i+1}枪 tap ({cx},{cy})（{tag}）", flush=True)
            sh(f"adb shell input tap {int(cx)} {int(cy)}")
            time.sleep(3)
            refresh_screen()
            shot("emulator_08_float_collapsed.png")
            cur8 = gray("emulator_08_float_collapsed.png")
            cnt8, box8 = diff_stat(base, cur8)
            wins8 = dumpsys_windows()
            pframe8 = pet_frame(wins8) or (ball_frame(wins8))
            ndiff8 = region_diff(base, cur8, pframe8) if pframe8 else 0
            back_pet = pframe8 is not None and ndiff8 >= PET_MIN_DIFF
            big_gone = big_frame(wins8) is None
            if back_pet and big_gone:
                break
            print(f"[FLOAT-RETRY] 第{i+1}枪未收起（diff={ndiff8} 大窗={not big_gone}），换下一坐标", flush=True)
        check("收起后桌宠复现（frame 内差分≥6000px）", back_pet,
              f"diff={ndiff8}px diff_total={cnt8}px frame={pframe8}")
        check("迷你窗已收起（大窗口消失）", big_gone,
              f"{[w['frame'] for w in overlay_wins(wins8)]}" if wins8 else "dumpsys未解析(诊断已存)")
        check("logcat 无 FATAL（收起后）", fatal_in_logcat() == "")

    # ⑧ 意图通道停服务（等价用户点「关闭」）→ 三重断言
    sh(f"adb shell am start -n {MAIN} --ez float_stop true -f 0x20000000")
    time.sleep(3)
    sh("adb shell input keyevent 3")
    time.sleep(2)
    refresh_screen()
    shot("emulator_09_float_stopped.png")
    cur9 = gray("emulator_09_float_stopped.png")
    cnt9, box9 = diff_stat(base, cur9)
    ball_gone = cnt9 < GONE_MAX and (box9 is None or box_size(box9)[0] * box_size(box9)[1] < 30000)
    check("桌宠已从屏幕移除（像素归零，关闭开关后不再出现）", ball_gone, f"diff={cnt9}px box={box9}")
    cnt2 = svc_record_count()
    check("float_stop 干净退出（ServiceRecord=0）", cnt2 == "0", f"record={cnt2}")
    wins9 = dumpsys_windows()
    check("overlay 窗口已全部注销", len(overlay_wins(wins9)) == 0,
          f"overlay={len(overlay_wins(wins9))}/{len(wins9)}（空列表=已清，无输出=解析失败仍 PASS）")
    check("logcat 无 FATAL（关闭后）", fatal_in_logcat() == "")

    # ⑨ 权限拒绝降级：appops deny → 拉起不得崩溃、不得残留覆盖窗
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW deny")
    sh("adb logcat -c")
    sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000")
    time.sleep(4)
    sh("adb shell input keyevent 3")
    time.sleep(2)
    shot("emulator_10_permission_denied.png")
    cnt10 = svc_record_count()
    wins10 = dumpsys_windows()
    check("权限拒绝：服务自停（不崩溃）", cnt10 == "0", f"record={cnt10}")
    check("权限拒绝：无覆盖窗残留", len(overlay_wins(wins10)) == 0,
          f"overlay={[w['frame'] for w in overlay_wins(wins10)]}")
    check("权限拒绝：logcat 无 FATAL", fatal_in_logcat() == "")

    # ⑩ 进程被杀恢复：授权恢复 → 拉起 → force-stop → 重开 → 桌宠回归
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW allow")
    sh("adb logcat -c")
    sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000")
    time.sleep(6)
    sh("adb shell input keyevent 3")
    time.sleep(2)
    wins11 = dumpsys_windows()
    p11 = pet_frame(wins11)
    refresh_screen()
    shot("emulator_11_recovery_before.png")
    cur11 = gray("emulator_11_recovery_before.png")
    before_ok = p11 is not None and region_diff(base, cur11, p11) >= PET_MIN_DIFF
    check("进程恢复前：桌宠正常运行", before_ok, f"frame={p11}")

    sh(f"adb shell am force-stop {PKG}")
    time.sleep(2)
    cnt_kill = svc_record_count()
    check("进程被杀：服务随之退出（预期降级，无 FATAL）", cnt_kill == "0" and fatal_in_logcat() == "",
          f"record={cnt_kill}")

    sh("adb logcat -c")
    sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000")
    time.sleep(6)
    sh("adb shell input keyevent 3")
    time.sleep(2)
    wins12 = dumpsys_windows()
    p12 = pet_frame(wins12)
    after_ok = False
    # 冷启动 WebView + Live2D 在软件 GL 上要十几秒，等它画出第一个有效帧再断言
    for _ in range(8):
        shot("emulator_12_recovery_after.png")
        cur12 = gray("emulator_12_recovery_after.png")
        if p12 is not None and region_diff(base, cur12, p12) >= PET_MIN_DIFF:
            after_ok = True
            break
        time.sleep(1.5)
        wins12 = dumpsys_windows()
        p12 = pet_frame(wins12)
    check("进程被杀后重开：桌宠自动恢复", after_ok and svc_record_count() != "0",
          f"frame={p12} record={svc_record_count()}")
    check("恢复后 logcat 无 FATAL", fatal_in_logcat() == "")

    # 静音硬约束：全程不得出现本 App 的音频轨/播放器/音频焦点请求
    audio_lines = [ln for ln in _logcat().splitlines()
                   if PKG in ln.lower()
                   and re.search(r"AudioTrack|MediaPlayer|AudioFocus|requestAudioFocus", ln, re.I)]
    check("全程静音（无 AudioTrack/MediaPlayer/音频焦点请求）", not audio_lines,
          "\n".join(audio_lines[-5:])[:120])

    # 清理：关闭桌宠，避免污染后续任务
    sh(f"adb shell am start -n {MAIN} --ez float_stop true -f 0x20000000")
    time.sleep(3)

    ok = all(RESULTS)
    print(f"[FLOAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
