#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_power_probe.py · AVD 三段耗电实测（P2 能耗收口）
--------------------------------------------------------------------
三场景各 5 分钟，同一台 AVD、同一屏幕亮度/常亮设置：
  A 静止前台  App 主界面停留，不交互（心跳 + Live2D 待机动画照常）
  B 后台      Home 后停留（页面冻结，心跳/轮询/打字机停表）
  C 连续对话  悬浮迷你窗内每 20s 一轮真实发消息（发送+回复+渲染）

数据源（全部真数字，不估算）：
  1. `dumpsys batterystats --reset` 分段清零 → 段末 `--charged` 里本 uid 的
     "Estimated power use (mAh)" 与该 uid 的 cpu/fgs 细分（Android 电量模型输出）
  2. `dumpsys cpuinfo` 段末快照（进程级 CPU%，含 WebView 沙箱子进程）
  3. /proc/<pid>/stat utime+stime 增量（100 tick = 1s，跨进程求和）
  4. 场景事件计数（logcat [probe] msg me/msg them、心跳心跳日志）

用法：python scripts/emulator_power_probe.py [--minutes 5]
退出码：0=三段全部拿到数据；1=有场景缺数据（逐项打印缺失原因）
"""
import importlib.util
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = "com.xiaoman.treehole"
MAIN = f"{PKG}/.MainActivity"

_spec = importlib.util.spec_from_file_location(
    "efc", os.path.join(HERE, "emulator_float_check.py"))
efc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(efc)


def sh(cmd, timeout=120):
    return efc.sh(cmd, timeout=timeout)


def uid_of():
    """`dumpsys package` 在 API34 只给 appId（不再打印 userId=），用 pm -U 拿真实 uid。"""
    _, out = sh(f"adb shell pm list packages -U")
    m = re.search(rf"package:{PKG} uid:(\d+)", out) or re.search(r"appId=(\d+)", out)
    if not m:
        _, out2 = sh(f"adb shell dumpsys package {PKG}")
        m = re.search(r"appId=(\d+)", out2)
    if not m:
        return None
    uid = int(m.group(1))
    return f"u0a{uid - 10000}" if uid >= 10000 else str(uid)


def reset_stats():
    sh("adb shell dumpsys batterystats --reset", timeout=60)


def battery_unplug():
    sh("adb shell dumpsys battery unplug", timeout=30)
    sh("adb shell dumpsys battery set level 100", timeout=30)
    sh("adb shell dumpsys battery set status 3", timeout=30)   # 3=DISCHARGING


def battery_power(result):
    """段末 batterystats：返回 (uid总mAh, cpu秒, fg秒, bg秒, fgs秒, screen秒)。"""
    _, out = sh("adb shell dumpsys batterystats --charged", timeout=180)
    try:
        with open(f"power_{result['name']}_batterystats.txt", "w", encoding="utf-8") as f:
            f.write(out)
    except Exception:
        pass
    global_total = None
    gm = re.search(r"Computed drain: (\d+(?:\.\d+)?)", out)
    if gm:
        global_total = float(gm.group(1))
    m = re.search(rf"^    UID {re.escape(result['uid'])}:.*$", out, re.M)
    if not m:
        return None
    line = m.group(0)
    tot = float(re.search(r"UID \S+: (\d+\.\d+)", line).group(1))
    def secs(key):
        mm = re.search(rf"{key}=(\d+\.\d+) \((\S+ \S+)\)", line)
        if not mm:
            return 0.0
        val = float(mm.group(1))
        return val
    def cpu_secs():
        mm = re.search(r"cpu=(\d+\.\d+) \((.+?)\)", line)
        if not mm:
            return 0.0
        return parse_duration(mm.group(2))
    return {
        "total_mah": tot,
        "cpu_mah": float(re.search(r"cpu=(\d+\.\d+)", line).group(1)),
        "cpu_sec": cpu_secs(),
        "device_mah": global_total,
        "screen_mah": float((re.search(r"screen=(\d+\.\d+)", line) or [0, 0])[1]) if re.search(r"screen=(\d+\.\d+)", line) else 0.0,
        "raw": line.strip(),
    }


def parse_duration(s):
    """'2m 24s 87ms' / '45s 985ms' → 秒（float）。"""
    total = 0.0
    for val, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", s):
        v = float(val)
        total += {"ms": v / 1000.0, "s": v, "m": v * 60, "h": v * 3600}[unit]
    return total


def procs_ticks():
    """本包所有存活进程的 utime+stime 求和（含 webview 沙箱）。"""
    _, out = sh("adb shell ps -A -o PID,UID,NAME,ARGS", timeout=60)
    pids = []
    for ln in out.splitlines():
        if PKG in ln and "ps -A" not in ln:
            parts = ln.split()
            if parts and parts[0].isdigit():
                pids.append(parts[0])
    total = 0
    detail = []
    for pid in pids:
        _, stat = sh(f"adb shell cat /proc/{pid}/stat", timeout=30)
        if stat.count(")") >= 1:
            tail = stat[stat.rfind(")") + 2:].split()
            if len(tail) > 14:
                u, s = int(tail[11]), int(tail[12])
                total += u + s
                detail.append(f"{pid}:{u + s}")
    return total, detail


def cpuinfo():
    _, out = sh("adb shell dumpsys cpuinfo", timeout=60)
    ours = [ln.strip() for ln in out.splitlines() if PKG in ln]
    total = next((ln.strip() for ln in out.splitlines() if "TOTAL" in ln), "")
    return total, ours


def probe_counts(seconds):
    _, out = sh("adb logcat -d -s XiaoManWeb:V", timeout=60)
    return {
        "msg_me": out.count("[probe] msg me"),
        "msg_them": out.count("[probe] msg them"),
        "pet_ready": out.count("[probe] pet-ready"),
    }


def scenario_foreground(minutes):
    sh(f"adb shell am force-stop {PKG}", timeout=60)
    time.sleep(1)
    sh(f"adb shell am start -n {MAIN} -f 0x20000000", timeout=60)
    time.sleep(10)                                    # 首屏 + Live2D 就绪


def scenario_background(minutes, with_float=False):
    sh(f"adb shell am force-stop {PKG}", timeout=60)
    time.sleep(1)
    extra = "--ez autostart_float true" if with_float else ""
    sh(f"adb shell am start -n {MAIN} {extra} -f 0x20000000", timeout=60)
    time.sleep(8)
    if with_float:
        for _ in range(8):
            wins = efc.dumpsys_windows()
            if efc.pet_frame(wins):
                break
            time.sleep(2)
    sh("adb shell input keyevent 3", timeout=30)
    time.sleep(3)


def ensure_panel():
    """悬浮桌宠 → 双击展开迷你窗，返回 panel frame（复用验收脚本的权威坐标解析）。"""
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW allow", timeout=30)
    sh(f"adb shell am force-stop {PKG}", timeout=60)   # 清掉旧 Activity，extras 才会被处理
    time.sleep(1)
    sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000", timeout=60)
    time.sleep(8)
    sh("adb shell input keyevent 3", timeout=30)
    time.sleep(2)
    efc.refresh_screen()
    pframe = None
    for _ in range(10):
        wins = efc.dumpsys_windows()
        pframe = efc.pet_frame(wins)
        if pframe:
            break
        time.sleep(2)
    if not pframe:
        return None
    x, y = efc.box_center(pframe)
    sh(f"adb shell \"input tap {x} {y}; input tap {x} {y}\"", timeout=60)
    for _ in range(6):
        time.sleep(3)
        wins = efc.dumpsys_windows()
        big = efc.big_frame(wins)
        if big:
            return big
    return None


def panel_now():
    return efc.big_frame(efc.dumpsys_windows())


def count_me():
    _, out = sh("adb logcat -d -s XiaoManWeb:V", timeout=30)
    return out.count("[probe] msg me")


def setup_chat(minutes):
    panel = ensure_panel()
    if not panel:
        return False, "迷你窗未展开"
    return True, "panel ready"


def chat_rounds(seconds):
    """在采样窗口内持续真实对话（每轮：点输入框→输入→回车→校验 probe）。
    返回 (rounds, sent_ok)；供 run_segment 在计时窗口内调用。"""
    rounds = 0
    ok = 0
    deadline = time.time() + seconds
    phrases = ["jin tian xin qing bu hao", "gong zuo hao lei a",
               "xiang he ni liao liao", "wan shang chi shen me",
               "wo you dian xiang jia le", "zhou mo xiang qu san bu"]
    mid_shot = False
    while time.time() < deadline:
        panel = panel_now()
        if not panel:
            panel = ensure_panel()
            if not panel:
                time.sleep(2)
                continue
        x1, y1, x2, y2 = panel
        wcx = (x1 + x2) // 2
        icy = y2 - int((y2 - y1) * 0.07)
        before = count_me()
        sh(f"adb shell input tap {wcx} {icy}", timeout=30)
        time.sleep(0.7)
        sh(f'adb shell input text "{phrases[rounds % len(phrases)]}"', timeout=30)
        time.sleep(0.7)
        sh("adb shell input keyevent 66", timeout=30)
        rounds += 1
        for _ in range(5):
            time.sleep(0.6)
            if count_me() > before:
                ok += 1
                break
        if not mid_shot and rounds >= 6:
            efc.shot("chat_mid.png")
            mid_shot = True
        time.sleep(max(0.5, min(8, deadline - time.time())))
    print(f"[SEG] chat rounds={rounds} sent_ok={ok}", flush=True)
    return rounds, ok


def run_segment(name, setup, minutes, during=None):
    print(f"\n=== SEGMENT {name} ({minutes}min) ===", flush=True)
    ok = setup(minutes)
    if isinstance(ok, tuple) and ok and ok[0] is False:
        print(f"[POWER-FAIL] {name} 场景准备失败：{ok[1]}", flush=True)
        return None
    time.sleep(5)                 # 场景稳定后再清零，避免把启动/展开开销算进稳态
    reset_stats()
    sh("adb logcat -c", timeout=30)
    uid = uid_of()
    ticks0, _ = procs_ticks()
    t0 = time.time()
    if during is not None:
        during(minutes * 60)      # 采样窗口内持续施压（如连续对话）
    else:
        time.sleep(minutes * 60)
    dur = time.time() - t0
    ticks1, detail = procs_ticks()
    ticks = ticks1 - ticks0
    cpu_total, cpu_ours = cpuinfo()
    data = battery_power({"uid": uid, "name": name})
    counts = probe_counts(dur)
    print(f"[SEG] {name} wall={dur:.0f}s uid={uid} ticksΔ={ticks} ({','.join(detail)})", flush=True)
    print(f"[SEG] cpuinfo total: {cpu_total}", flush=True)
    for ln in cpu_ours:
        print(f"[SEG] cpuinfo app: {ln}", flush=True)
    print(f"[SEG] probes: {counts}", flush=True)
    if data:
        print(f"[SEG] batterystats: total={data['total_mah']}mAh cpu={data['cpu_mah']}mAh "
              f"cpu_sec={data['cpu_sec']:.1f}s device_drain={data.get('device_mah')}mAh", flush=True)
        print(f"[SEG] raw: {data['raw']}", flush=True)
    else:
        print(f"[POWER-FAIL] {name} 未取到 uid {uid} 的 batterystats 行", flush=True)
    return {"name": name, "minutes": minutes, "uid": uid, "ticks": ticks,
            "tick_detail": detail, "cpu_total": cpu_total, "cpu_ours": cpu_ours,
            "probes": counts, "battery": data}


def main():
    minutes = 5
    if "--minutes" in sys.argv:
        minutes = int(sys.argv[sys.argv.index("--minutes") + 1])
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    sh("adb shell svc power stayon true", timeout=30)
    sh("adb shell settings put system screen_off_timeout 1800000", timeout=30)
    battery_unplug()
    phases = [
        ("A_fg_nofloat", scenario_foreground, None),
        ("B_bg_nofloat", lambda m: scenario_background(m, False), None),
        ("B_bg_float", lambda m: scenario_background(m, True), None),
        ("C_chat", setup_chat, chat_rounds),
    ]
    results = []
    for name, fn, during in phases:
        if only and only != name:
            continue
        results.append(run_segment(name, fn, minutes, during))

    print("\n=== SUMMARY ===", flush=True)
    for r in results:
        if not r:
            continue
        b = r["battery"] or {}
        print(f"{r['name']}: {b.get('total_mah','NA')}mAh / cpu {b.get('cpu_mah','NA')}mAh"
              f" / cpuΔ {b.get('cpu_sec','NA')}s / ticksΔ={r['ticks']} / probes={r['probes']}"
              f" / device={b.get('device_mah','NA')}mAh", flush=True)
    ok = all(r and r.get("battery") for r in results)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

