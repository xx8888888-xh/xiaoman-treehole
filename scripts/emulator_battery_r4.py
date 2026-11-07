#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_battery_r4.py · 30 分钟绝对量耗电实测（云端 AVD）
--------------------------------------------------------------------
回答的是「这个 App 会不会偷偷耗电」，不是百分比：

  1. standby    ：App force-stop（未启动）、灭屏静置 30 min —— 同设备纯待机对照基线；
  2. foreground ：App 前台 + 桌宠浮窗、不操作、亮屏静置 30 min；
  3. background ：App 切后台（Home + 灭屏）静置 30 min —— 重点看唤醒与网络。

每段都记录：
  - `dumpsys batterystats` 的本 uid mAh / cpu mAh / 整机 Computed drain（**模型估算**）；
  - `dumpsys power` 的 wakefulness / wakelock 数量；
  - `/proc/<pid>/stat` 的 App 进程 CPU ticks（跨进程求和，100 tick = 1s）；
  - `/proc/stat` 的整机 busy ticks；
  - per-uid 的 Wakeup alarm / Job / Wake lock 次数（batterystats 明细段）；
  - 整机 Cellular + Wifi 收发字节（batterystats 连通性段，段内增量）；
  - 后台隐藏期的真实 rAF 帧数（pet.html 页内探针的 visibilitychange 快照）。

可靠性（必须如实写在报告里）：
  AVD 没有真实电表。`Estimated battery capacity: 1000 mAh` + `Discharge: 0 mAh`
  + `actual drain: 0` 说明 mAh 来自 Android 的软件电源模型，不是放电实测。
  因此 JSON 里 `mahReliable=false`，判定以 CPU 时间 + 唤醒次数 + 网络字节为主；
  mAh 只作为「同一模型下的回归信号」，并同时给出 %/h 与 4000mAh 续航粗算。

用法（Linux runner / 已连好的 AVD）：
  python3 scripts/emulator_battery_r4.py --minutes 30 \
      --out artifacts/avd/battery_r4.json
退出码：0=三段数据都采到；1=有段采集失败（已拿到的数字仍写入 JSON）。
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from android_cdp import Cdp, PKG   # noqa: E402

MAIN = f"{PKG}/.MainActivity"
PORT = 9223

# 复用耗电探针与浮窗探针的既有实现（importlib 加载，避免复制粘贴漂移）
_spec = importlib.util.spec_from_file_location(
    "epower", os.path.join(HERE, "emulator_power_r4.py"))
ep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ep)
sh = ep.sh

_spec2 = importlib.util.spec_from_file_location(
    "efloat", os.path.join(HERE, "emulator_float_r4.py"))
ef = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ef)

SEGMENTS = ("standby", "foreground", "background")
SAMPLE_SECONDS = 60
_FOREGROUND_CDP = None          # 前台段建立的 pet.html CDP，后台段复用读隐藏期帧数
FLOAT_WAIT_SECONDS = 45
BATTERY_CAPACITY_ASSUMED = 4000.0     # 用户要的「折算成 4000mAh 能用多久」


# ---- 基础解析 ---------------------------------------------------------------
def parse_bytes(text):
    """'13.02KB' / '0B' / '1.5MB' → bytes（batterystats 用 1024 进制）。"""
    if text is None:
        return None
    m = re.match(r"\s*([0-9.]+)\s*(B|KB|MB|GB|TB)?", str(text))
    if not m:
        return None
    mul = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}[
        (m.group(2) or "B").upper()]
    return int(float(m.group(1)) * mul)


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def battery_device():
    """`dumpsys battery` 的 level/status/供电状态（AVD 恒 100，只作诊断）。"""
    _, out = sh("adb shell dumpsys battery", timeout=30)
    level = re.search(r"level: (\d+)", out)
    status = re.search(r"status: (\d+)", out)
    ac = re.search(r"AC powered: (true|false)", out)
    usb = re.search(r"USB powered: (true|false)", out)
    return {
        "level": int(level.group(1)) if level else None,
        "status": int(status.group(1)) if status else None,
        "acPowered": (ac.group(1) == "true") if ac else None,
        "usbPowered": (usb.group(1) == "true") if usb else None,
    }


def power_state(save_path=None):
    """`dumpsys power`：wakefulness + wakelock 数（powermanager 统计）。"""
    _, out = sh("adb shell dumpsys power", timeout=60)
    if save_path:
        try:
            with open(save_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(out)
        except Exception:
            pass
    wake = re.search(r"mWakefulness=([A-Za-z_]+)", out)
    wl = re.search(r"Wake Locks: size=(\d+)", out)
    powered = re.search(r"mIsPowered=(true|false)", out)
    return {
        "wakefulness": wake.group(1) if wake else None,
        "wakeLockCount": int(wl.group(1)) if wl else None,
        "isPowered": (powered.group(1) == "true") if powered else None,
    }


def deviceidle_state():
    _, out = sh("adb shell dumpsys deviceidle", timeout=60)
    st = re.search(r"mState=([A-Za-z_]+)", out)
    return {"state": st.group(1) if st else None}


def proc_stat():
    """`/proc/stat` 首行：整机 ticks（100/s）；busy = 全部 - idle - iowait。"""
    _, out = sh("adb shell cat /proc/stat", timeout=30)
    line = next((ln for ln in out.splitlines() if ln.strip().startswith("cpu ")), "")
    fields = [int(v) for v in re.findall(r"\d+", line)][:8]
    if not fields:
        return {"totalTicks": None, "busyTicks": None}
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return {"totalTicks": sum(fields), "busyTicks": sum(fields) - idle}


def read_batterystats(name):
    """跑一次 `dumpsys batterystats --charged`，原文落盘后返回文本。"""
    _, out = sh("adb shell dumpsys batterystats --charged", timeout=180)
    try:
        with open(f"battery_r4_{name}_batterystats.txt", "w",
                  encoding="utf-8", errors="replace") as f:
            f.write(out or "")
    except Exception:
        pass
    return out or ""


def parse_batterystats(text, uid):
    """抽出段内可比对的数字：整机 mAh 模型、本 uid 明细、连通性、唤醒。"""
    cap = re.search(r"Estimated battery capacity: (\d+) mAh", text)
    computed = re.search(r"Computed drain: ([0-9.]+)", text)
    actual = re.search(r"actual drain: ([0-9.]+)", text)
    discharge = re.search(r"\n  Discharge: (\d+) mAh", text)
    time_on = re.search(r"Time on battery: ([^,]+)realtime", text)
    screen_on = re.search(r"Screen on: ([^,]+?)(?: \([0-9.]+%\)|,)", text)
    screen_off = re.search(r"Time on battery screen off: ([^,]+)realtime", text)

    cell_rx = re.search(r"Cellular data received: ([0-9.]+[BKMGT]?B)", text)
    cell_tx = re.search(r"Cellular data sent: ([0-9.]+[BKMGT]?B)", text)
    wifi_rx = re.search(r"Wifi data received: ([0-9.]+[BKMGT]?B)", text)
    wifi_tx = re.search(r"Wifi data sent: ([0-9.]+[BKMGT]?B)", text)
    data = {
        "capacityMah": int(cap.group(1)) if cap else None,
        "computedDrainMah": _num(computed.group(1)) if computed else None,
        "actualDrainMah": _num(actual.group(1)) if actual else None,
        "dischargeMah": int(discharge.group(1)) if discharge else None,
        "timeOnBatterySec": round(ep.parse_duration(time_on.group(1)), 1) if time_on else None,
        "screenOnSec": round(ep.parse_duration(screen_on.group(1)), 1) if screen_on else None,
        "screenOffSec": round(ep.parse_duration(screen_off.group(1)), 1) if screen_off else None,
        "cellRxBytes": parse_bytes(cell_rx.group(1)) if cell_rx else None,
        "cellTxBytes": parse_bytes(cell_tx.group(1)) if cell_tx else None,
        "wifiRxBytes": parse_bytes(wifi_rx.group(1)) if wifi_rx else None,
        "wifiTxBytes": parse_bytes(wifi_tx.group(1)) if wifi_tx else None,
        "uid": uid,
        "uidPower": None,
        "uidDetail": None,
    }

    if uid:
        m = re.search(
            rf"^    Uid {re.escape(uid)}: ([0-9.eE+-]+) \( ([^)]*) \)(.*)$", text, re.M)
        if m:
            sm = re.search(r"Including smearing: ([0-9.eE+-]+)", m.group(3))
            data["uidPower"] = {
                "totalMah": _num(m.group(1)),
                "components": m.group(2).strip(),
                "smearedMah": _num(sm.group(1)) if sm else None,
            }
        block = _uid_block(text, uid)
        if block:
            cpu = re.search(r"Total cpu time: u=([0-9hms. ]+) s=([0-9hms. ]+)", block)
            alarms = sum(int(v) for v in re.findall(r"Wakeup alarm .*?: (\d+) times", block))
            jobs = sum(int(v) for v in re.findall(r"Job [^\n]*?(\d+) times", block))
            locks = len(re.findall(r"^    Wake lock ", block, re.M))
            net = re.search(r"Mobile network: ([0-9.]+[BKMGT]?B) received, "
                            r"([0-9.]+[BKMGT]?B) sent", block)
            data["uidDetail"] = {
                "cpuSec": round(ep.parse_duration((cpu.group(1) or "") + " " +
                                                  (cpu.group(2) or "")), 3) if cpu else None,
                "wakeupAlarms": alarms,
                "jobs": jobs,
                "wakeLockLines": locks,
                "mobileRxBytes": parse_bytes(net.group(1)) if net else None,
                "mobileTxBytes": parse_bytes(net.group(2)) if net else None,
            }
    return data


def _uid_block(text, uid):
    """batterystats 明细里 `  u0a130:` 之后到下一个 2 空格 uid 行之前的内容。"""
    m = re.search(rf"^  {re.escape(uid)}:\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^  [A-Za-z0-9_]+:\s*$", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def batt_delta(start, end):
    """段首段末差分（reset 失败时也能得到段内增量）。"""
    def sub(a, b):
        if a is None and b is None:
            return None
        return round((b or 0) - (a or 0), 6)

    def sub_int(a, b):
        if a is None and b is None:
            return None
        return int((b or 0) - (a or 0))

    up0 = (start or {}).get("uidPower") or {}
    up1 = (end or {}).get("uidPower") or {}
    det0 = (start or {}).get("uidDetail") or {}
    det1 = (end or {}).get("uidDetail") or {}
    net0 = (start or {})
    net1 = (end or {})

    return {
        "computedDrainMah": sub((start or {}).get("computedDrainMah"),
                               (end or {}).get("computedDrainMah")),
        "actualDrainMah": sub((start or {}).get("actualDrainMah"),
                              (end or {}).get("actualDrainMah")),
        "uidTotalMah": sub(up0.get("totalMah"), up1.get("totalMah")),
        "uidSmearedMah": sub(up0.get("smearedMah"), up1.get("smearedMah")),
        "uidCpuSec": sub(det0.get("cpuSec"), det1.get("cpuSec")),
        "wakeupAlarms": sub_int(det0.get("wakeupAlarms"), det1.get("wakeupAlarms")),
        "jobs": sub_int(det0.get("jobs"), det1.get("jobs")),
        "wakeLockLines": sub_int(det0.get("wakeLockLines"), det1.get("wakeLockLines")),
        "uidMobileRxBytes": sub_int(det0.get("mobileRxBytes"), det1.get("mobileRxBytes")),
        "uidMobileTxBytes": sub_int(det0.get("mobileTxBytes"), det1.get("mobileTxBytes")),
        "cellRxBytes": sub_int(net0.get("cellRxBytes"), net1.get("cellRxBytes")),
        "cellTxBytes": sub_int(net0.get("cellTxBytes"), net1.get("cellTxBytes")),
        "wifiRxBytes": sub_int(net0.get("wifiRxBytes"), net1.get("wifiRxBytes")),
        "wifiTxBytes": sub_int(net0.get("wifiTxBytes"), net1.get("wifiTxBytes")),
    }


def net_total(delta):
    vals = [delta.get("cellRxBytes"), delta.get("cellTxBytes"),
            delta.get("wifiRxBytes"), delta.get("wifiTxBytes")]
    if all(v is None for v in vals):
        return None
    return int(sum(v or 0 for v in vals))


# ---- 绝对量换算 -------------------------------------------------------------
def derive_abs(seg, capacity):
    """mAh/30min、%/h（对 4000mAh 与 AVD 容量各一份）、4000mAh 续航粗算。"""
    minutes = max(0.01, seg.get("wall", 0) / 60.0)
    mah = seg.get("mahMeasured")
    out = {
        "measuredMinutes": round(minutes, 2),
        "mahReliable": False,
        "mahReliability": ("AVD 无真实电表：batterystats 报 Estimated capacity "
                           f"{capacity or 1000} mAh / Discharge 0 / actual drain 0，"
                           "mAh 为 Android 软件电源模型估算，不可当作实放电"),
    }
    if mah is None:
        return out
    rate = mah / minutes                       # mAh/min
    dev = seg.get("deviceMahMeasured")
    out.update({
        "mahPerHour": round(rate * 60, 6),
        "mah30": round(rate * 30, 6),
        "deviceMah30": round(dev / minutes * 30, 6) if dev is not None else None,
        "pctPerHour4000": round(rate * 60 / BATTERY_CAPACITY_ASSUMED * 100, 6),
        "pctPerHourAvd": round(rate * 60 / (capacity or 1000) * 100, 6),
        "hoursOn4000mAh": round(BATTERY_CAPACITY_ASSUMED / (rate * 60), 1)
        if rate > 0 else None,
    })
    return out


# ---- 采样段 ---------------------------------------------------------------
class Segment(object):
    def __init__(self, name, minutes, uid):
        self.name = name
        self.minutes = minutes
        self.uid = uid
        self.start = {}
        self.end = {}
        self.samples = []
        self.t0 = 0.0

    def begin(self):
        ep.battery_reset()
        before = read_batterystats(f"{self.name}_start")
        self.start = {
            "battery": parse_batterystats(before, self.uid),
            "power": power_state(f"battery_r4_{self.name}_power_start.txt"),
            "device": battery_device(),
            "deviceidle": deviceidle_state(),
            "ticks": ep.procs_ticks()[0],
            "pids": ep.pkg_pids(),
            "procStat": proc_stat(),
        }
        self.t0 = time.time()
        print(f"[BATT] ({self.name}) start uid={self.uid} "
              f"pids={self.start['pids']} wake={self.start['power'].get('wakefulness')}",
              flush=True)

    def sample(self):
        t = time.time() - self.t0
        pids = ep.pkg_pids()
        ticks, _ = ep.procs_ticks()
        stat = proc_stat()
        self.samples.append({
            "t": round(t, 1),
            "pids": pids,
            "ticks": ticks,
            "procStatBusy": stat.get("busyTicks"),
            "power": power_state(),
            "deviceidle": deviceidle_state(),
            "battery": battery_device(),
            "cpuinfoApp": ep.cpuinfo().get("app") or [],
        })

    def finish(self, extra=None):
        ticks1, detail1 = ep.procs_ticks()
        stat1 = proc_stat()
        text = read_batterystats(f"{self.name}_end")
        self.end = {
            "battery": parse_batterystats(text, self.uid),
            "power": power_state(f"battery_r4_{self.name}_power_end.txt"),
            "device": battery_device(),
            "deviceidle": deviceidle_state(),
            "ticks": ticks1,
            "pids": ep.pkg_pids(),
            "procStat": stat1,
            "tickDetail": detail1,
        }
        wall = time.time() - self.t0

        d = batt_delta(self.start["battery"], self.end["battery"])
        capacity = (self.end["battery"] or {}).get("capacityMah")
        uid_mah = d.get("uidTotalMah")
        device_mah = d.get("computedDrainMah")
        mah = uid_mah if uid_mah is not None else device_mah
        ticks_delta = (ticks1 - self.start["ticks"]) if ticks1 is not None else None
        busy_delta = None
        b0 = (self.start.get("procStat") or {}).get("busyTicks")
        b1 = (stat1 or {}).get("busyTicks")
        if b0 is not None and b1 is not None:
            busy_delta = b1 - b0

        seg = {
            "name": self.name,
            "wall": round(wall, 1),
            "minutesRequested": self.minutes,
            "mahMeasured": mah,
            "mahBasis": ("uid-power-model" if uid_mah is not None else
                         ("device-power-model" if device_mah is not None else None)),
            "deviceMahMeasured": device_mah,
            "cpuMs": round(ticks_delta / 100.0 * 1000.0, 1) if ticks_delta is not None else None,
            "cpuPct": ep.cpu_pct(ticks_delta, wall) if ticks_delta is not None else None,
            "deviceCpuMs": round(busy_delta / 100.0 * 1000.0, 1) if busy_delta is not None else None,
            "wakeupAlarms": d.get("wakeupAlarms"),
            "jobs": d.get("jobs"),
            "wakeups": (d.get("wakeupAlarms") or 0) + (d.get("jobs") or 0),
            "wakeLockLines": d.get("wakeLockLines"),
            "netBytes": net_total(d),
            "netDetail": {
                "cellRx": d.get("cellRxBytes"), "cellTx": d.get("cellTxBytes"),
                "wifiRx": d.get("wifiRxBytes"), "wifiTx": d.get("wifiTxBytes"),
                "uidMobileRx": d.get("uidMobileRxBytes"),
                "uidMobileTx": d.get("uidMobileTxBytes"),
            },
            "stats": {
                "start": self.start["battery"],
                "end": self.end["battery"],
                "delta": d,
                "resetWindowSec": (self.end["battery"] or {}).get("timeOnBatterySec"),
                "resetOk": ((self.end["battery"] or {}).get("timeOnBatterySec") is not None
                            and (self.end["battery"] or {}).get("timeOnBatterySec", 0)
                            <= wall + 15),
            },
            "power": {"start": self.start["power"], "end": self.end["power"]},
            "deviceidle": {"start": self.start["deviceidle"],
                           "end": self.end["deviceidle"]},
            "batteryDevice": {"start": self.start["device"],
                              "end": self.end["device"]},
            "pids": {"start": self.start["pids"], "end": self.end["pids"],
                     "pidChanged": bool(self.start["pids"] != self.end["pids"])},
            "samples": self.samples,
        }
        seg.update(derive_abs(seg, capacity))
        if extra:
            seg.update(extra)
        self.seg = seg
        print(f"[BATT] ({self.name}) wall={wall:.1f}s mah={mah} cpuMs={seg['cpuMs']} "
              f"deviceCpuMs={seg['deviceCpuMs']} wakeups={seg['wakeups']} "
              f"net={seg['netBytes']} pct/h(4000)={seg.get('pctPerHour4000')}",
              flush=True)
        return seg


def sleep_until(t):
    d = t - time.time()
    if d > 0:
        time.sleep(d)


def run_window(seg, extra_at_end=None):
    """按 SAMPLE_SECONDS 采样到时限，返回 seg 记录。"""
    print(f"\n=== SEGMENT {seg.name} ({seg.minutes} min) ===", flush=True)
    end_at = seg.t0 + seg.minutes * 60
    k = 1
    while True:
        target = seg.t0 + k * SAMPLE_SECONDS
        if target >= end_at:
            sleep_until(end_at)
            break
        sleep_until(target)
        seg.sample()
        k += 1
    extra = extra_at_end() if extra_at_end else None
    return seg.finish(extra)


# ---- 设备状态切换 -----------------------------------------------------------
def screen_on():
    sh("adb shell svc power stayon true", timeout=30)
    sh("adb shell settings put system screen_off_timeout 1800000", timeout=30)
    sh("adb shell dumpsys battery unplug", timeout=30)
    sh("adb shell dumpsys battery set level 100", timeout=30)
    sh("adb shell dumpsys battery set status 3", timeout=30)      # DISCHARGING
    sh("adb shell appops set " + PKG + " SYSTEM_ALERT_WINDOW allow", timeout=30)
    sh("adb shell input keyevent 224", timeout=30)               # WAKEUP
    sh("adb shell wm dismiss-keyguard", timeout=30)
    time.sleep(3)


def screen_off():
    sh("adb shell svc power stayon false", timeout=30)
    sh("adb shell settings put system screen_off_timeout 15000", timeout=30)
    sh("adb shell input keyevent 3", timeout=30)                 # HOME
    time.sleep(3)
    sh("adb shell input keyevent 26", timeout=30)                # POWER
    time.sleep(3)
    return power_state().get("wakefulness")


def wait_pid(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        pids = ep.pkg_pids()
        if pids:
            return pids
        time.sleep(2)
    return []


def ensure_pet_probe(timeout=60):
    """拉起桌宠浮窗 + 连 pet.html + 注入页内探针；返回 (cdp, info)。"""
    rc, out = sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000",
                 timeout=60)
    start = out.strip().splitlines()[-1][:160] if out.strip() else ""
    frame, diag = None, []
    end = time.time() + FLOAT_WAIT_SECONDS
    while time.time() < end:
        wins = ef.dump_windows()
        diag = [w["frame"] for w in ef.overlay_wins(wins)]
        frame = ef.pet_frame(wins)
        if frame:
            break
        time.sleep(2)
    if not frame:
        raise RuntimeError(f"{FLOAT_WAIT_SECONDS}s 内没有桌宠 overlay（dumpsys={diag}）")
    cdp, url = ef.attach_pet(timeout=timeout)
    ok, injected = ep.fast_eval(cdp, ep.PROBE_JS, timeout=15)
    if not ok or injected not in ("installed", "already"):
        raise RuntimeError(f"pet.html 探针注入失败：{injected}")
    ready, st = ef.wait_pet_ready(cdp, timeout=25)
    if not ready:
        print(f"[BATT-WARN] pet 未 ready（{st}），继续采集（浮窗存在即可）", flush=True)
    return cdp, {"start": start, "frame": frame, "overlayFrames": diag,
                 "petReady": ready, "petState": st, "url": url}


def read_pet_snap(cdp, timeout=10):
    if cdp is None:
        return None, "no-cdp"
    ok, val = ep.fast_eval(cdp, ep.SNAP_JS, timeout=timeout)
    return (val, None) if ok and isinstance(val, dict) else (None, str(val)[:140])


def recover_pet_snap(cdp, timeout=10):
    """屏幕熄灭后 WebSocket 可能断；唤醒后重连同一 pet 页（不 reload）。"""
    smp, err = read_pet_snap(cdp, timeout=timeout)
    if smp is not None:
        return cdp, smp, None
    try:
        cdp2, _ = ef.attach_pet(timeout=45)
        ok, inj = ep.fast_eval(cdp2, ep.PROBE_JS, timeout=15)
        if not ok or inj not in ("installed", "already"):
            return cdp2, None, f"reinject:{inj}"
        smp, err = read_pet_snap(cdp2, timeout=timeout)
        return cdp2, smp, err
    except Exception as e:
        return cdp, None, f"recover:{str(e)[:120]}"


# ---- 三段流程 ---------------------------------------------------------------
def run_standby(minutes, uid):
    sh(f"adb shell am force-stop {PKG}", timeout=30)
    time.sleep(2)
    wf = screen_off()
    print(f"[BATT] standby 灭屏后 wakefulness={wf}", flush=True)
    if ep.pkg_pids():
        print(f"[BATT-WARN] standby 时 App 仍有进程：{ep.pkg_pids()}", flush=True)
    seg = Segment("standby", minutes, None)     # 纯待机用整机模型，不取 App uid
    seg.begin()
    return run_window(seg)


def run_foreground(minutes, uid):
    global _FOREGROUND_CDP
    screen_on()
    sh(f"adb shell am start -n {MAIN} -f 0x20000000", timeout=60)
    if not wait_pid(60):
        raise RuntimeError("App 进程未起来")
    # 冷启动时 WebView devtools socket 晚于进程出现：单次 Cdp() 会瞬时失败
    # （run 42380392111 的 foreground 段即因此整体失败）。重试到 60s。
    preset, last = None, None
    end = time.time() + 60
    while time.time() < end:
        try:
            preset = ep.apply_preset(PORT, ep.PRESET_DEFAULT, "default")[0]
            break
        except Exception as e:
            last = e
            time.sleep(2)
    if preset is None:
        raise RuntimeError(f"主界面 CDP 未就绪（60s 内找不到 devtools socket）：{last}")
    preset.close()
    cdp, info = ensure_pet_probe()
    _FOREGROUND_CDP = cdp
    print(f"[BATT] foreground 浮窗：{json.dumps(info.get('frame'), ensure_ascii=False)} "
          f"petReady={info.get('petReady')}", flush=True)
    time.sleep(10)                              # 启动补帧/纹理解码落定

    seg = Segment("foreground", minutes, uid)
    seg.begin()
    ep.fast_eval(cdp, "window.__r4 && window.__r4.reset()", timeout=10)
    s0, _ = read_pet_snap(cdp)
    seg.cdp = cdp

    frames = {"start": s0}

    def _extra():
        smp, err = read_pet_snap(cdp)
        if smp is None:
            return {"frames": {"error": err, "end": None}, "floating": info}
        frames["end"] = smp
        raf = int((smp.get("raf") or 0) - ((s0 or {}).get("raf") or 0))
        px = int((smp.get("px") or 0) - ((s0 or {}).get("px") or 0))
        wall = max(0.01, seg.minutes * 60 - 10)
        return {"frames": {"rafTotal": raf, "rafPerSec": round(raf / wall, 2),
                           "pxTotal": px, "source": "pet.html-rAF",
                           "start": s0, "end": smp},
                "floating": info}

    return run_window(seg, _extra)


def run_background(minutes, uid, cdp):
    """从前景（浮窗仍在）切 Home + 灭屏，30 min；结束时唤醒读隐藏期真实帧数。"""
    # 先在前台可见状态下清零并记 C0，随后 Home+灭屏；visibilitychange 会记 atHidden。
    if cdp is not None:
        ep.fast_eval(cdp, "window.__r4 && window.__r4.reset()", timeout=10)
    c0, _ = read_pet_snap(cdp)
    wf = screen_off()
    print(f"[BATT] background 灭屏后 wakefulness={wf}", flush=True)
    seg = Segment("background", minutes, uid)
    seg.begin()

    def _extra():
        # 先结束统计（在灭屏状态下取 CPU/mAh/唤醒/网络），再唤醒读帧数，
        # 避免把回前台的动作算进后台耗电。
        return {"frames": {"pending": True}, "c0": c0, "wakefulnessBeforeWake": wf}

    seg_rec = run_window(seg, _extra)

    pids_before = seg_rec.get("pids", {}).get("start")
    pids_after = seg_rec.get("pids", {}).get("end")
    sh("adb shell input keyevent 224", timeout=30)              # WAKEUP
    sh("adb shell wm dismiss-keyguard", timeout=30)
    sh(f"adb shell am start -n {MAIN}", timeout=60)
    time.sleep(4)
    cdp, smp, err = recover_pet_snap(cdp)
    vis = (smp or {}).get("visLog") or {}
    at_h, at_v = vis.get("atHidden"), vis.get("atVisible")
    frames_hidden, source = None, None
    if at_h and at_v and int((at_v.get("raf") or 0)) >= int((at_h.get("raf") or 0)):
        frames_hidden = int(at_v.get("raf")) - int(at_h.get("raf"))
        source = "pet-visibilitychange"
    elif c0 and smp:
        frames_hidden = int(smp.get("raf") or 0) - int(c0.get("raf") or 0)
        source = "pet-c0-delta"

    stale = bool((pids_before or []) != (pids_after or []))
    if stale or err:
        frames_hidden = None
        source = f"stale:{err or 'pid-changed'}"
    seg_rec["frames"] = {
        "framesWhileHidden": frames_hidden,
        "source": source,
        "c0": c0,
        "atHidden": at_h,
        "atVisible": at_v,
        "pidsStart": pids_before,
        "pidsEnd": pids_after,
        "pidChanged": stale,
        "returnedSnapError": err,
    }
    print(f"[BATT] background hiddenFrames={frames_hidden} source={source}", flush=True)
    return seg_rec


# ---- 汇总 -------------------------------------------------------------------
def add_net_cost(segs):
    """App 净成本 = App 段 - 纯待机段；mAh 必须同为整机模型，不能拿 uid 估算比整机。"""
    base = next((s for s in segs if s.get("name") == "standby"), None)
    if not base:
        return

    def device_mah(s):
        return s.get("deviceMahMeasured") if s.get("deviceMahMeasured") is not None \
            else s.get("mahMeasured")

    base_mah = device_mah(base)
    for s in segs:
        if s.get("name") == "standby":
            s["appNetCost"] = None
            continue
        s_mah = device_mah(s)
        s["appNetCost"] = {
            "mahOverStandby": round(s_mah - base_mah, 6)
            if s_mah is not None and base_mah is not None else None,
            "cpuMsOverStandby": round((s.get("cpuMs") or 0), 1)
            if s.get("cpuMs") is not None else None,   # 待机无 App 进程，基准为 0
            "deviceCpuMsOverStandby": round((s.get("deviceCpuMs") or 0)
                                            - (base.get("deviceCpuMs") or 0), 1)
            if s.get("deviceCpuMs") is not None and base.get("deviceCpuMs") is not None else None,
            "wakeupsOverStandby": int((s.get("wakeups") or 0)
                                      - (base.get("wakeups") or 0)),
            "netBytesOverStandby": (int((s.get("netBytes") or 0)
                                        - (base.get("netBytes") or 0))
                                    if s.get("netBytes") is not None
                                    and base.get("netBytes") is not None else None),
        }


def write_json(path, payload):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[BATT] JSON 已写入 {path}", flush=True)


def summary(segs):
    print("\n=== SUMMARY (battery r4, 绝对量) ===", flush=True)
    print(f"{'segment':<12}{'min':>6}{'mAh':>12}{'mAh/30m':>10}{'%/h(4000)':>11}"
          f"{'4000mAh':>12}{'cpuMs':>10}{'wakeups':>8}{'netB':>9}{'frames':>7}",
          flush=True)
    for s in segs:
        hours = s.get("hoursOn4000mAh")
        fr = (s.get("frames") or {}).get("framesWhileHidden")
        if fr is None:
            fr = (s.get("frames") or {}).get("rafTotal")
        print(f"{s.get('name', '?'):<12}"
              f"{s.get('measuredMinutes', 'NA'):>6}"
              f"{str(s.get('mahMeasured', 'NA')):>12}"
              f"{str(s.get('mah30', 'NA')):>10}"
              f"{str(s.get('pctPerHour4000', 'NA')):>11}"
              f"{(('%.0fh' % hours) if hours else 'NA'):>12}"
              f"{str(s.get('cpuMs', 'NA')):>10}"
              f"{str(s.get('wakeups', 'NA')):>8}"
              f"{str(s.get('netBytes', 'NA')):>9}"
              f"{str(fr if fr is not None else 'NA'):>7}", flush=True)
        c = s.get("appNetCost") or {}
        if c:
            print(f"     └ 净成本 vs 待机：mAh {c.get('mahOverStandby')}，"
                  f"CPU {c.get('cpuMsOverStandby')}ms，唤醒 {c.get('wakeupsOverStandby')}，"
                  f"网络 {c.get('netBytesOverStandby')}B", flush=True)


def main():
    ap = argparse.ArgumentParser(description="treehole 30 分钟绝对量耗电实测")
    ap.add_argument("--minutes", type=int, default=30, help="每段分钟数（默认 30）")
    ap.add_argument("--out", default=os.path.join("artifacts", "avd", "battery_r4.json"))
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    payload = {
        "meta": {},
        "segments": [],
        "ok": False,
        "mahReliable": False,
        "mahReliability": ("AVD 软件电源模型：Estimated capacity 1000 mAh / Discharge 0；"
                           "mAh 仅作回归信号，判定以 CPU 时间 + 唤醒次数 + 网络字节为主"),
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    rc = 1
    segs = []
    cdp = None
    try:
        rc0, out = sh("adb get-state", timeout=30)
        if "device" not in out:
            raise RuntimeError(f"没有可用设备：{out.strip()[:120]}")
        payload["meta"] = ep.meta_info(argparse.Namespace(
            idle_seconds=args.minutes * 60, bg_seconds=args.minutes * 60,
            port=args.port))
        payload["meta"]["minutesPerSegment"] = args.minutes
        payload["meta"]["sampleSeconds"] = SAMPLE_SECONDS
        print(f"[BATT] meta: {json.dumps(payload['meta'], ensure_ascii=False)}", flush=True)

        ep.device_prepare()
        uid = ep.uid_of()
        print(f"[BATT] app uid={uid}", flush=True)

        def run(name, fn):
            try:
                rec = fn()
                rec["ok"] = True
                segs.append(rec)
            except Exception as e:
                print(f"[BATT-FAIL] {name} 采集失败：{e}", flush=True)
                segs.append({"name": name, "ok": False, "error": repr(e)[:300]})

        run("standby", lambda: run_standby(args.minutes, uid))
        run("foreground", lambda: run_foreground(args.minutes, uid))
        # foreground 失败时 cdp 可能不存在；后台仍可用系统状态采集（帧数记空）。
        cdp = _FOREGROUND_CDP
        run("background", lambda: run_background(args.minutes, uid, cdp))

        add_net_cost(segs)
        payload["segments"] = segs
    except Exception as e:
        print(f"[BATT-FAIL] 探针整体异常：{e}", flush=True)
        payload["error"] = repr(e)[:300]
        payload["segments"] = segs
    finally:
        if cdp is not None:
            try:
                cdp.close()
            except Exception:
                pass

    all_ok = bool(segs) and len(segs) == 3 and all(s.get("ok") for s in segs)
    payload["ok"] = all_ok
    rc = 0 if all_ok else 1
    summary(segs)
    write_json(args.out, payload)
    if rc != 0:
        print("[BATT] 有段采集失败 → exit 1（已拿到的数字仍写入 JSON）", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
