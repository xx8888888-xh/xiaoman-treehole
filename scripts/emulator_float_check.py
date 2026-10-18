#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_float_check.py · CI 模拟器悬浮窗验收 v7（P2-4b）
--------------------------------------------------------------------
v7 变更（run 39107576044 实测 19/21 flake——连绿 4 轮后的第 5 轮翻车）：
  实测证据链：收起按钮定位 (703,628)——既非收起(738,541)也非关闭(910,540)，
  tap 落空 → 面板没收起（大窗仍在 diff=115 万px）→ 球未复现（白字 0）。
  根因：扫描区 y2=py1+0.18*panel_h（≈710px）侵入 WebView 内容区，聊天
  内容暗像素与「收起」簇 x 间距 <40px 被并簇，质心拉偏 35px/87px。
  （间歇性：取决于当轮聊天内容是否滚入扫描带——绿 4 轮纯走运）
  修法双保险：
  a) 扫描区 0.18→0.10（y2≈609px，按钮文字行 523~563 仍在带内，
     WebView 内容 ≥620 全部排除）
  b) tap 后验证「球复现+大窗消失」，未收起自动换兜底坐标补一枪
     （定位漂移的通用安全网，不再赌单发命中）
v6 变更（run 38884396667 实测 20/21，收起按钮错点根因）：
  实测证据链（/tmp/v5art 真实截图工件标定，emulator_06 顶栏 4 簇）：
  a) 簇扫描实测：标题(112~400) | 收起(705~771, 763点) | 关闭(875~940,
     753点) | 右缘阴影杂点(999~1006, 仅16点/7px宽)。
     v5"右数第二簇"被 7px 杂点簇顶位——错取「关闭」(910,540)：
     点击后球窗口注销+白字0+像素归零正是"关闭"特征（大窗口消失 PASS
     掩盖了球消失这一旁证）。
     ⇒ 微簇过滤（≥100点且宽≥30px）后右数第二=收起(738,541)。
  b) 兜底坐标按实测收起中心标定：(px2-269, py1+58)。
  c) 顶栏文字行实测 y 523~563（此前 640 的标定作废）。
v5 变更（run 38877847899 实测 9/10，球身份判别器换代 + 面板重试）：
  实测证据链（真实截图工件标定，/tmp/v4art）：
  a) 模拟器壁纸本身是粉色系（球区旁对照 24915 px 命中品牌粉容差）——
     v4 的品牌粉像素判别器失效；粉球灰度(~193) vs 粉壁纸(~193) 几乎同色
     → 灰度差分仅 4365px（v4 按"深色壁纸~18000px"标定 BALL_MIN=8000 必挂），
     且与 launcher 时钟跳变(~4400px)完全同量级，计数不可判别。
     ⇒ 球身份/可见主证据改「满」字白像素：球 frame 内 >245 白像素
       实测 04(有球)=3412 vs 09(球已移,同壁纸)=0，完美分离；
       壁纸全域白密度 3.5% → WHITE_MIN=1500 留 2.3x 余量。
  b) 球/面板窗口 frame 是权威坐标（mFrame 与截图实测吻合）：
     拖动位移断言改 dumpsys 球窗口 frame 前后对比（不再依赖差分计数）。
  c) expandPanel() 会 ball.setVisibility(GONE)（窗口仍注册但隐藏）——
     展开=球frame白字0、收起=白字回来，天然判别；v3 轮 dumpsys 双窗口共存
     之谜由此解开。
  d) 面板渲染时机容错：tap 后 WebView 冷加载可能 >7s，v5 双条件
     (差分>PANEL_MIN 且 dumpsys 大窗口) 最多重试 3 次 x4s。
  e) 收起按钮定位修 y 偏差：v4 公式 cy=+0.55*bar_h 实测偏下 74px（按钮
     中心~640 vs 公式 714，714 已落在 WebView 区）；v5 改暗点质心（x,y 都
     用真实暗像素均值），兜底 (px2-209, py1+51) 按真实布局标定（收起按钮
     中心 ~(800,640)：2字13sp+padding≈127px 宽/枚，两枚+margins≈735..999）。
v4 遗产（坐标系/解析修复，run 38865512010 验证）：
  - diff_stat bbox 还原全屏坐标（y+TOP_CROP）——v3 tap/swipe 打偏 120px 根因
  - dumpsys ty= 双写法兼容（APPLICATION_OVERLAY 文字 / 2038 数字）
  - 收起按钮像素扫描（overlay 原生控件不进 uiautomator dump）
v4→v5 复盘（run 38877847899）：9/10——4 项基础+4 项停服务全绿，
唯一红点=球可见阈值（本版 a 项修复）；交互链 04 截图 bbox=(40,343,205,508)
与 dumpsys mFrame 完全一致，坐标链路 v4 已修好，v5 只换判别器。
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
WHITE_MIN = 1500                         # 球frame内「满」字白像素(>245)下限：实测3412 vs 移除后0
BALL_MAX = 150000                        # 差分计数上限仅诊断（v5起判据=白字/窗口frame）
PINK_MIN = 3000                          # 品牌粉像素仅诊断（实测壁纸粉色系，无判别力）
PANEL_MIN = 300000                       # 面板量级（≈935x1265≈1.18M px）
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
    sh(f"adb exec-out screencap -p > {name}", timeout=60)


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


def ball_frame(wins):
    """从窗口列表取悬浮球小窗口 frame（宽 100~300px 的 overlay）。
    面板展开时球窗口仍注册（setVisibility(GONE) 隐藏）——frame 依旧权威，
    渲染与否由白字像素判别。"""
    for w in overlay_wins(wins):
        fw = w["frame"][2] - w["frame"][0]
        if 100 < fw < 300:
            return w["frame"]
    return None


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
        fm = re.search(r"mFrame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", blk)
        if fm:
            x1, y1, x2, y2 = map(int, fm.groups())
            wins.append({"ty": tm.group(1) if tm else "?",
                         "frame": (x1, y1, x2, y2)})
    return wins


def overlay_wins(wins):
    return [w for w in wins if w["ty"] in ("APPLICATION_OVERLAY", "2038")]


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

    # ③ 球可见 —— 主证据：球窗口 frame 内「满」字白像素（v5 判别器）；
    #    副证据：dumpsys overlay 窗口注册 + 像素差分球簇（bbox 应≈球 frame）
    shot("emulator_04_float_ball.png")
    cur = gray("emulator_04_float_ball.png")
    cnt4, box4 = diff_stat(base, cur)
    wins = dumpsys_windows()
    ov = overlay_wins(wins)
    check("overlay 窗口已注册（dumpsys ty=APPLICATION_OVERLAY）", len(ov) >= 1,
          f"overlay={len(ov)}/{len(wins)} frames={[w['frame'] for w in ov][:3]}" if wins
          else "dumpsys未解析出窗口(诊断已存)")
    bframe = ball_frame(wins)
    region = bframe or box4        # dumpsys 球窗口 frame 权威坐标优先，差分 bbox 兜底
    n_white = white_glyph_px("emulator_04_float_ball.png", region)
    n_pink = pink_px("emulator_04_float_ball.png", region)
    ball_visible = region is not None and n_white >= WHITE_MIN
    check("悬浮球可见（球窗口frame内「满」字白像素≥1500）", ball_visible,
          f"white={n_white}px pink={n_pink}px diff={cnt4}px box={box4} region={region}")

    if not ball_visible:
        # 球不可见时后续交互无从谈起，直接跳到停服务清理段
        print("[FLOAT-SKIP] 球不可见，跳过交互链（后续步骤验证停服务兜底）", flush=True)

    ball_center = box_center(region) if ball_visible else None

    if ball_center:
        bx, by = ball_center
        # ④ 拖动（>SLOP 位移 600px；松手贴边吸附）→ dumpsys 球窗口 frame 位移断言
        sh(f"adb shell input swipe {bx} {by} {bx + 600} {by + 700} 600")
        time.sleep(2)
        shot("emulator_05_float_drag.png")
        cur5 = gray("emulator_05_float_drag.png")
        cnt5, box5 = diff_stat(base, cur5)
        wins5 = dumpsys_windows()
        nframe = ball_frame(wins5) or box5     # 拖动后球窗口 frame（吸附后新位置）
        n_white5 = white_glyph_px("emulator_05_float_drag.png", nframe) if nframe else 0
        ball5 = nframe is not None and n_white5 >= WHITE_MIN
        if nframe:
            ncx, ncy = box_center(nframe)
            moved = ball5 and (abs(ncx - bx) > 300 or abs(ncy - by) > 300)
        else:
            ncx, ncy = 0, 0
            moved = False
        check("悬浮球可拖动（球窗口frame位移>300px+白字跟随）", bool(moved),
              f"({bx},{by})→({ncx},{ncy}) white={n_white5}px diff={cnt5}px")

        # ⑤ 单击球心（拖动吸附后位置）→ 展开迷你窗：
        #    双条件（差分>PANEL_MIN 且 dumpsys 大窗口）重试（WebView 冷加载容错）
        SCREEN_W = 1080
        tapx, tapy = (ncx, ncy) if (nframe and ball5) else (SCREEN_W - 82, by + 700)
        sh(f"adb shell input tap {tapx} {tapy}")
        cnt6, box6, big = 0, None, []
        for i in range(3):
            time.sleep(4)
            shot("emulator_06_float_panel.png")
            cur6 = gray("emulator_06_float_panel.png")
            cnt6, box6 = diff_stat(base, cur6)
            wins6 = dumpsys_windows()
            big = [w for w in overlay_wins(wins6) if (w["frame"][2] - w["frame"][0]) > 500]
            if cnt6 > PANEL_MIN and big:
                break
            print(f"[FLOAT-RETRY] 面板未就绪(第{i+1}轮) diff={cnt6}px big={len(big)}", flush=True)
        check("迷你窗展开（大面积像素差分>30万px）", cnt6 > PANEL_MIN, f"diff={cnt6}px")
        check("迷你窗大窗口已注册（dumpsys 宽>500px）", len(big) >= 1,
              f"{[w['frame'] for w in overlay_wins(wins6)]}" if wins6 else "dumpsys未解析(诊断已存)")
        # 展开后球必须隐藏（expandPanel→setVisibility(GONE)，窗口仍注册但白字=0）
        hframe = ball_frame(wins6)
        n_white_h = white_glyph_px("emulator_06_float_panel.png", hframe) if hframe else -1
        check("展开后球已隐藏（球窗口frame白字=0）", hframe is not None and n_white_h == 0,
              f"hidden_white={n_white_h}px frame={hframe}")
        check("logcat 无 FATAL（展开后）", fatal_in_logcat() == "")

        # 面板交互区：dumpsys 大窗口 frame 优先，像素 bbox 兜底
        panel_box = big[0]["frame"] if big else (box6 if box6 else (tapx - 400, tapy - 500, tapx + 400, tapy + 600))
        shot_6 = ("emulator_07_float_chat.png")

        # ⑥ 迷你窗内真实聊天一轮（probe 双向断言）
        chat_round(panel_box)
        shot(shot_6)

        # ⑦ 点「收起」：BACK 收软键盘（input focus 时只收键盘不 goBack，无历史页）→
        #    实时截图后像素扫描顶栏右侧深色文字簇（关闭最右/收起在其左）。
        #    v7 安全网：tap 后验证「球复现+大窗消失」，未收起则换兜底坐标补一枪
        #    （run 39107576044 实测：定位偶发被 WebView 内容并簇拉偏→点空→面板没关）
        sh("adb shell input keyevent 4")
        time.sleep(1)
        shot("float_bar.png")
        btn = find_collapse_button("float_bar.png", panel_box)
        px1, py1, px2, py2 = panel_box
        fallback = (int(px2 - 269), int(py1 + 58))   # 实测收起按钮中心标定
        candidates = ([btn] if btn else []) + [fallback]
        back_ball = big_gone = False
        cnt8 = box8 = wins8 = bframe8 = n_white8 = None
        for i, (cx, cy) in enumerate(candidates):
            tag = "扫描质心" if (btn and (cx, cy) == btn) else "兜底坐标"
            print(f"[FLOAT-BTN] 第{i+1}枪 tap ({cx},{cy})（{tag}）", flush=True)
            sh(f"adb shell input tap {int(cx)} {int(cy)}")
            time.sleep(3)
            shot("emulator_08_float_collapsed.png")
            cur8 = gray("emulator_08_float_collapsed.png")
            cnt8, box8 = diff_stat(base, cur8)
            wins8 = dumpsys_windows()
            bframe8 = ball_frame(wins8) or box8   # 收起后球在拖动吸附位复现
            n_white8 = white_glyph_px("emulator_08_float_collapsed.png", bframe8) if bframe8 else 0
            back_ball = bframe8 is not None and n_white8 >= WHITE_MIN
            big_gone = not [w for w in overlay_wins(wins8) if (w["frame"][2] - w["frame"][0]) > 500]
            if back_ball and big_gone:
                break
            print(f"[FLOAT-RETRY] 第{i+1}枪未收起（white={n_white8} 大窗仍在），换下一坐标", flush=True)
        check("收起后回悬浮球（球窗口frame白字复现）", back_ball,
              f"white={n_white8}px diff={cnt8}px frame={bframe8}")
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
          f"overlay={len(overlay_wins(wins9))}/{len(wins9)}（空列表=已清，无输出=解析失败仍 PASS）")
    check("logcat 无 FATAL（全程）", fatal_in_logcat() == "")

    ok = all(RESULTS)
    print(f"[FLOAT-SUMMARY] {sum(RESULTS)}/{len(RESULTS)} 通过", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
