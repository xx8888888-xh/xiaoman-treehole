#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_power_r4.py · 第四轮耗电验收（AVD 探针：零渲染 / 动画基线 / 后台归零）
--------------------------------------------------------------------
与 emulator_power_probe.py 的三段耗电口径一脉相承（batterystats / /proc ticks /
dumpsys cpuinfo 全部复用），但这一轮要回答的是**渲染侧**的三问：

  1. idle_minimal：极简预设（motion=false）静止 60s —— 页面是否真的零 rAF、零画布填充；
  2. idle_default ：默认预设（motion=true）静止 60s —— rAF 是否持续（约 55~62/s），
     并给出同一窗口的 CPU 数字，作为「动画开销」的可比较基线；
  3. background   ：默认预设 home 后 300s —— 计数增量应为 0（PageLifecycle.onStop →
     web.onPause()，加 stage.js 的 visibilitychange 暂停），回前台「立即」再读一次计数。

页内探针（CDP Runtime.evaluate 注入，不改产品代码）：
  - 包装 window.requestAnimationFrame，只在回调**真正执行**时 +1（排队的回调不计数）；
  - 包装 CanvasRenderingContext2D.prototype.drawImage/fillRect，累计**目的矩形**像素面积
    （4 参取 a[2]*a[3]、8 参取 a[6]*a[7]、2 参取图片原始尺寸；各自的 3/5/9 参形式一并覆盖）；
  - window.__r4 = { raf, frames, px, drawCalls, snap(), reset() }，每秒经 CDP 读一次快照。

数据源（真数字，不估算）：
  - /proc/<pid>/stat utime+stime 增量（跨进程求和，含 WebView 沙箱子进程）
  - dumpsys cpuinfo 本包行
  - dumpsys batterystats --charged 本 uid 的 mAh / cpu 秒 / 整机 Computed drain
  - dumpsys gfxinfo <pkg> 的 Total frames rendered（WebView 下可能不可靠 → JSON 标 reliable=false）
  - logcat -s XiaoManWeb:V 的 `[probe] stage-pause=hidden`

用法（Linux runner / 已连好的 AVD）：
  python3 scripts/emulator_power_r4.py --idle-seconds 60 --bg-seconds 300 \
      --out artifacts/avd/power_r4.json
退出码：0=三段判据全部成立；1=有判据不成立/采集失败（已拿到的数字仍写入 JSON）。

依赖：requests、websockets（android_cdp）；本脚本自身只用标准库。
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from android_cdp import Cdp, PKG   # noqa: E402

MAIN = f"{PKG}/.MainActivity"
PORT = 9223

# 复用 emulator_float_check.py 的 sh()（与 power_probe 同一先例：importlib 加载，避免复制粘贴漂移）
_spec = importlib.util.spec_from_file_location(
    "efc", os.path.join(HERE, "emulator_float_check.py"))
efc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(efc)
sh = efc.sh

PRESET_MINIMAL = {"preset": "minimal", "motion": False, "font": "large", "iconsGray": True, "flat": True}
PRESET_DEFAULT = {"preset": "default", "motion": True, "font": "normal", "iconsGray": True, "flat": False}

# ---- 判据阈值 ---------------------------------------------------------------
RAF_ZERO_MAX = 10        # 零渲染窗口（首秒除外）允许的 rAF 回调数
PX_ZERO_MAX = 20000      # 同上：目的矩形像素面积上限（一次 720p 满屏 ≈ 1.5M）
RAF_ANIM_MIN = 4.0       # 默认预设：rAF 均值下限（1fps 降档混合均值，与 R6 基线 4–7 同源）
RAF_ANIM_MAX = 7.0       # 默认预设：rAF 均值上限，对应 docs/perf_baseline_r6.json 的 range
BG_FRAME_MAX = 10        # 后台 300s 允许的帧增量上限（对照 60fps×300s=18000）

# ---- 页内探针 ---------------------------------------------------------------
PROBE_JS = r"""
(function () {
  if (window.__r4 && window.__r4.__installed) return "already";
  var st = { raf: 0, frames: 0, px: 0, drawCalls: 0 };

  // rAF：只统计实际执行的回调（未执行就被取消的不计数）
  if (window.requestAnimationFrame) {
    var rafNative = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = function (cb) {
      return rafNative(function (t) {
        st.raf += 1;
        st.frames += 1;
        return cb(t);
      });
    };
  }

  function imgArea(im) {
    if (!im) return 0;
    var w = im.naturalWidth || im.videoWidth || im.width || 0;
    var h = im.naturalHeight || im.videoHeight || im.height || 0;
    return w * h;
  }

  if (typeof CanvasRenderingContext2D !== "undefined" && CanvasRenderingContext2D.prototype) {
    var proto = CanvasRenderingContext2D.prototype;
    var origDraw = proto.drawImage;
    var origFill = proto.fillRect;

    // drawImage：目的矩形面积（9 参取 a[7]*a[8]，8 参取 a[6]*a[7]，
    // 5 参取 a[3]*a[4]，4 参取 a[2]*a[3]，3/2 参取图片原始尺寸）
    proto.drawImage = function () {
      var a = arguments, n = a.length, area = 0;
      try {
        if (n >= 9) area = a[7] * a[8];
        else if (n === 8) area = a[6] * a[7];
        else if (n === 5) area = a[3] * a[4];
        else if (n === 4) area = a[2] * a[3];
        else area = imgArea(a[0]);
        if (!(area > 0)) area = imgArea(a[0]);
        area = Math.max(0, area);
      } catch (e) { area = 0; }
      st.px += area;
      st.drawCalls += 1;
      return origDraw.apply(this, a);
    };

    // fillRect：4 参取 a[2]*a[3]
    proto.fillRect = function (x, y, w, h) {
      try {
        if (arguments.length >= 4) st.px += Math.max(0, arguments[2] * arguments[3]);
      } catch (e) { /* 忽略 */ }
      st.drawCalls += 1;
      return origFill.apply(this, arguments);
    };
  }

  window.__r4 = {
    __installed: true,
    get raf() { return st.raf; },
    get frames() { return st.frames; },
    get px() { return Math.round(st.px); },
    get drawCalls() { return st.drawCalls; },
    vis: { atHidden: null, atVisible: null },
    snap: function () {
      return {
        raf: st.raf,
        frames: st.frames,
        px: Math.round(st.px),
        drawCalls: st.drawCalls,
        hidden: document.hidden,
        vis: document.visibilityState,
        stageMode: (typeof window.__stageMode === "string" ? window.__stageMode : null),
        stagePaused: !!(window.Stage && window.Stage._envPaused && window.Stage._envPaused()),
        visLog: { atHidden: window.__r4.vis.atHidden, atVisible: window.__r4.vis.atVisible },
        now: (performance && performance.now) ? performance.now() : 0
      };
    },
    reset: function () {
      st.raf = 0; st.frames = 0; st.px = 0; st.drawCalls = 0;
      window.__r4.vis.atHidden = null;
      window.__r4.vis.atVisible = null;
      return true;
    }
  };

  // 可见性瞬时快照：atHidden/atVisible 记录的是**事件派发那一刻**的累计计数，
  // 因此 atVisible.raf - atHidden.raf = 真正在「看不见」期间执行过的 rAF 数，
  // 不受回前台后 CDP 轮询的时延影响（这是后台归零判据的可信来源）。
  document.addEventListener("visibilitychange", function () {
    var rec = { raf: st.raf, px: Math.round(st.px),
                t: (performance && performance.now) ? performance.now() : 0 };
    if (document.hidden) window.__r4.vis.atHidden = rec;
    else window.__r4.vis.atVisible = rec;
  });

  return "installed";
})()
"""

READY_JS = r"""
(() => {
  var sb = document.getElementById('sendBtn');
  return {
    ready: !!(sb && typeof window.ChatPager !== 'undefined'),
    sendBtn: !!sb,
    chatPager: (typeof window.ChatPager !== 'undefined'),
    stageMode: (typeof window.__stageMode === "string" ? window.__stageMode : null),
    preset: (window.Appearance && window.Appearance.get) ? window.Appearance.get() : null,
    probe: !!(window.__r4 && window.__r4.snap)
  };
})()
"""

SNAP_JS = "(window.__r4 && window.__r4.snap) ? window.__r4.snap() : null"


# ---- 基础命令 ---------------------------------------------------------------
def device_prepare():
    """常亮 + 灭屏超时拉长 + 拔电（电量模型才会计入 mAh）。"""
    sh("adb shell svc power stayon true", timeout=30)
    sh("adb shell settings put system screen_off_timeout 1800000", timeout=30)
    sh("adb shell dumpsys battery unplug", timeout=30)
    sh("adb shell dumpsys battery set level 100", timeout=30)
    sh("adb shell dumpsys battery set status 3", timeout=30)      # 3=DISCHARGING
    sh("adb shell input keyevent 224", timeout=30)                # 224=WAKEUP，确保屏幕亮着


def uid_of():
    """`pm list packages -U` 拿真实 uid（API34 的 dumpsys package 已不打印 userId=）。"""
    _, out = sh("adb shell pm list packages -U", timeout=60)
    m = re.search(rf"package:{re.escape(PKG)} uid:(\d+)", out)
    if not m:
        _, out2 = sh(f"adb shell dumpsys package {PKG}", timeout=60)
        m = re.search(r"appId=(\d+)", out2)
    if not m:
        return None
    uid = int(m.group(1))
    return f"u0a{uid - 10000}" if uid >= 10000 else str(uid)


def battery_reset():
    sh("adb shell dumpsys batterystats --reset", timeout=60)


def parse_duration(s):
    """'2m 24s 87ms' / '45s 985ms' → 秒（float）。"""
    total = 0.0
    for val, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", s):
        total += {"ms": float(val) / 1000.0, "s": float(val),
                  "m": float(val) * 60, "h": float(val) * 3600}[unit]
    return total


def battery_power(name, uid):
    """段末 batterystats：本 uid 的 total_mah / cpu_mah / cpu_sec / device_mah。"""
    if not uid:
        return None
    _, out = sh("adb shell dumpsys batterystats --charged", timeout=180)
    try:
        with open(f"power_r4_{name}_batterystats.txt", "w", encoding="utf-8", errors="replace") as f:
            f.write(out)
    except Exception:
        pass
    # API 30 的 batterystats 行首是 "Uid"（不是全大写 UID），这里统一大小写匹配。
    m = re.search(rf"^    [Uu]id {re.escape(uid)}:.*$", out, re.M)
    if not m:
        return None
    line = m.group(0)
    try:
        tot = float(re.search(r"[Uu]id \S+: (\d+\.\d+)", line).group(1))
        cpu_mah = float(re.search(r"cpu=(\d+\.\d+)", line).group(1))
        cpu_m = re.search(r"cpu=\d+\.\d+ \((.+?)\)", line)
        cpu_sec = parse_duration(cpu_m.group(1)) if cpu_m else 0.0
    except Exception:
        return None
    gm = re.search(r"Computed drain: (\d+(?:\.\d+)?)", out)
    return {
        "total_mah": tot,
        "cpu_mah": cpu_mah,
        "cpu_sec": cpu_sec,
        "device_mah": float(gm.group(1)) if gm else None,
        "raw": line.strip(),
    }


def pkg_pids():
    """本包所有存活进程（含 com.xiaoman.treehole:sandboxed_process* 沙箱）。"""
    _, out = sh("adb shell ps -A", timeout=60)
    pids = []
    for ln in out.splitlines():
        if PKG not in ln or "ps -A" in ln:
            continue
        toks = ln.split()
        pid = next((t for t in toks[:3] if t.isdigit()), None)
        if pid:
            pids.append(pid)
    return pids


def procs_ticks():
    """跨进程 utime+stime 求和（100 tick = 1s）。"""
    total, detail = 0, []
    for pid in pkg_pids():
        _, stat = sh(f"adb shell cat /proc/{pid}/stat", timeout=30)
        if ")" not in stat:
            continue
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
    return {"total": total, "app": ours}


def gfx_total(name=None, save=False):
    """dumpsys gfxinfo 的 Total frames rendered（WebView 下可能恒 0，仅作参考）。"""
    _, out = sh(f"adb shell dumpsys gfxinfo {PKG}", timeout=60)
    if save and name:
        try:
            with open(f"power_r4_{name}_gfxinfo.txt", "w", encoding="utf-8", errors="replace") as f:
                f.write(out)
        except Exception:
            pass
    m = re.search(r"Total frames rendered: (\d+)", out)
    return (int(m.group(1)) if m else None), out


def gfx_reset():
    sh(f"adb shell dumpsys gfxinfo {PKG} reset", timeout=60)


def logcat_probe():
    _, out = sh("adb shell logcat -d -s XiaoManWeb:V", timeout=60)
    return out


def logcat_stage():
    """全量 logcat 里 stage.js 的关键打点（清空后只覆盖本段窗口）。

    `[probe] stage-repaint src=...` 用来定位空闲期那次重绘的来源。"""
    _, out = sh("adb logcat -d -v brief -t 4000", timeout=60)
    lines = [ln.strip()[:200] for ln in (out or "").splitlines()
             if "[probe] stage-" in ln]
    return lines[-30:]


def fast_eval(cdp, js, timeout=10):
    """不触发自动重连的 eval（后台/回前台轮询用）。返回 (ok, value|err)。"""
    if cdp is None or getattr(cdp, "ws", None) is None:
        return False, "no-ws"
    try:
        return True, cdp._eval(js, timeout=timeout)
    except Exception as e:
        return False, str(e)[:140]


def read_snap(cdp, timeout=10):
    ok, val = fast_eval(cdp, SNAP_JS, timeout=timeout)
    if not ok:
        return None, val
    if not isinstance(val, dict):
        return None, "snap-null"
    return val, None


def read_snap_recover(cdp, timeout=10):
    """前台/段首用：掉线时重连并重新注入探针，尽量不中断 60s 序列。
    页面若真的 reload 过，计数会从 0 重来（判据里按 stale 处理）。"""
    smp, err = read_snap(cdp, timeout=timeout)
    if smp is not None:
        return smp, None
    try:
        cdp.attach(url_filter="index.html", timeout=20)
        ok2, inj = fast_eval(cdp, PROBE_JS, timeout=12)
        if not ok2 or inj not in ("installed", "already"):
            return None, f"reinject-fail:{inj}"
        smp, err = read_snap(cdp, timeout=timeout)
        return (smp, None) if smp is not None else (None, f"recover-read-fail:{err}")
    except Exception as e:
        return None, f"recover-fail:{str(e)[:110]}"


def ints(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


CPU_CORES = 1


def detect_cores():
    """设备 CPU 核数（nproc 优先，退化为 /proc/cpuinfo 计数）。"""
    rc, out = sh("adb shell nproc", timeout=30)
    m = re.search(r"\d+", out or "")
    if rc == 0 and m:
        return max(1, int(m.group(0)))
    rc, out = sh("adb shell cat /proc/cpuinfo", timeout=30)
    n = len(re.findall(r"^processor\s*:", out or "", re.M))
    return max(1, n) if n else 1


def cpu_pct(ticks_delta, wall):
    """ticks 增量（100 tick=1s，已跨进程求和）/ 实际秒数 / 核数 → 单机 CPU 百分比。"""
    if not wall or wall <= 0:
        return None
    return round((ticks_delta / 100.0) / wall / max(1, CPU_CORES) * 100.0, 2)


# ---- 预设置 / 页面就绪 -------------------------------------------------------
def attach_ready(port, timeout=90, stage_wait=45):
    """attach index.html → 等 sendBtn/ChatPager 就绪 → 注入页内探针。
    舞台（__stageMode）额外等 stage_wait 秒，超时只告警不致命（记录在段数据里）。"""
    cdp = Cdp(port)
    cdp.attach(url_filter="index.html", timeout=timeout)
    end = time.time() + timeout
    last = None
    stage_deadline = time.time() + stage_wait
    while time.time() < end:
        ok, val = fast_eval(cdp, READY_JS, timeout=8)
        last = val
        if ok and isinstance(val, dict) and val.get("ready"):
            if (val.get("stageMode") in ("layers", "static", "silhouette", "hidden")) or time.time() > stage_deadline:
                if val.get("stageMode") is None or val.get("stageMode") == "idle":
                    print(f"[R4-WARN] 舞台未就绪（__stageMode={val.get('stageMode')}），"
                          f"零渲染判据的可信度下降", flush=True)
                ok2, injected = fast_eval(cdp, PROBE_JS, timeout=12)
                # reload 后 reinject 会返回 installed；同一页面二次进入返回 already，
                # 两者都表示探针可用（不要把它当失败）。
                if not ok2 or injected not in ("installed", "already"):
                    raise RuntimeError(f"页内探针注入失败：{injected}")
                return cdp, val
        time.sleep(1)
    raise RuntimeError(f"页面 {timeout}s 内未就绪（sendBtn/ChatPager）：{last}")


def apply_preset(port, preset, label, prev_cdp=None):
    """设 localStorage 预设置 → reload → 重连 → 注入探针。返回 (cdp, ready_info)。"""
    print(f"\n[PRESET] -> {label} {json.dumps(preset, ensure_ascii=False)}", flush=True)
    if prev_cdp is not None:
        src = prev_cdp
    else:
        src = Cdp(port)
        src.attach(url_filter="index.html", timeout=90)
    js = ("try { localStorage.setItem('xiaoman_appearance_v1', JSON.stringify(%s)); } catch (e) {};"
          " localStorage.getItem('xiaoman_appearance_v1')" % json.dumps(preset, ensure_ascii=False))
    ok, val = fast_eval(src, js, timeout=10)
    if not ok:
        print(f"[R4-WARN] 写入预设置失败：{val}", flush=True)
    try:
        src._eval("location.reload(); 'reload'", timeout=5)
    except Exception:
        pass                                    # reload 会掐断回包，正常现象
    try:
        src.close()
    except Exception:
        pass
    time.sleep(5)
    cdp, info = attach_ready(port)

    # 预设置没生效（写 localStorage 时连接恰好断了、或 reload 前被覆盖）→ 用新连接重试一次
    cur = (info or {}).get("preset") or {}
    if cur.get("preset") != preset.get("preset") or bool(cur.get("motion")) != bool(preset.get("motion")):
        print(f"[R4-WARN] 预设置未生效（当前 {cur}），重试一次", flush=True)
        ok, val = fast_eval(cdp, js, timeout=10)
        try:
            cdp._eval("location.reload(); 'reload'", timeout=5)
        except Exception:
            pass
        try:
            cdp.close()
        except Exception:
            pass
        time.sleep(5)
        cdp, info = attach_ready(port)
        cur = (info or {}).get("preset") or {}
        if cur.get("preset") != preset.get("preset") or bool(cur.get("motion")) != bool(preset.get("motion")):
            raise RuntimeError(f"预设置 {label} 应用失败，当前外观={cur}")
    print(f"[PRESET] {label} 生效：{json.dumps(cur, ensure_ascii=False)}", flush=True)
    return cdp, info


# ---- 序列工具 ---------------------------------------------------------------
def per_second(samples, key, start_val):
    """累计计数 → 每秒增量（不可用样本记 None）。"""
    prev = start_val
    out = []
    for s in samples:
        if "cdp" in s or key not in s:
            out.append(None)
            continue
        v = ints(s.get(key))
        out.append(v - prev if prev is not None else None)
        prev = v
    return out


def total_of(series):
    return sum(v for v in series if v is not None)


def sleep_until(target):
    d = target - time.time()
    if d > 0:
        time.sleep(d)


# ---- 段 1/2：前台静止 -------------------------------------------------------
def measure_foreground(cdp, name, seconds, preset, expectation):
    print(f"\n=== SEGMENT {name} ({seconds}s, preset={preset}) ===", flush=True)
    # 静置：页面可能还在补首帧、解码纹理或播开场白；先等 10s，再清零探针计数，
    # 这样 idle_minimal 的「零渲染」不会被启动补帧误判。
    time.sleep(10)
    r_ok, r_val = fast_eval(cdp, "(window.__r4 && window.__r4.reset) ? window.__r4.reset() : false",
                            timeout=10)
    if not r_ok or r_val is not True:
        print(f"[R4-WARN] {name} 探针 reset 失败：{r_val}", flush=True)
        raise RuntimeError(f"页内探针 reset 失败：{r_val}")

    gfx_before, _ = gfx_total()
    gfx_reset()
    battery_reset()
    sh("adb logcat -c", timeout=30)
    uid = uid_of()
    ticks0, tick_detail0 = procs_ticks()
    snap0, err0 = read_snap_recover(cdp)
    if snap0 is None:
        raise RuntimeError(f"段首读不到页内探针：{err0}")

    t0 = time.time()
    samples = []
    for i in range(seconds):
        sleep_until(t0 + i + 1)          # 每秒一拍，interval≈1s
        smp, err = read_snap_recover(cdp, timeout=10)
        if smp is None:
            samples.append({"t": round(time.time() - t0, 2), "cdp": "cdp-unavailable",
                            "err": str(err)[:120]})
            continue
        samples.append({
            "t": round(time.time() - t0, 2),
            "raf": ints(smp.get("raf")),
            "frames": ints(smp.get("frames")),
            "px": ints(smp.get("px")),
            "drawCalls": ints(smp.get("drawCalls")),
            "hidden": smp.get("hidden"),
            "vis": smp.get("vis"),
            "stageMode": smp.get("stageMode"),
            "stagePaused": smp.get("stagePaused"),
        })
    wall = time.time() - t0

    ticks1, tick_detail1 = procs_ticks()
    cpu = cpuinfo()
    batt = battery_power(name, uid)
    gfx_after, raw = gfx_total(name=name, save=True)

    s0 = {"raf": ints(snap0.get("raf")), "px": ints(snap0.get("px")),
          "frames": ints(snap0.get("frames")), "drawCalls": ints(snap0.get("drawCalls"))}
    raf_series = per_second(samples, "raf", s0["raf"])
    px_series = per_second(samples, "px", s0["px"])
    raf_total = total_of(raf_series)
    px_total = total_of(px_series)
    raf_ex_first = total_of(raf_series[1:])
    px_ex_first = total_of(px_series[1:])

    raf_avg = None
    valid = [v for v in raf_series[1:] if v is not None]
    if valid:
        raf_avg = round(sum(valid) / float(len(valid)), 2)
    elif raf_series:
        valid_all = [v for v in raf_series if v is not None]
        raf_avg = round(sum(valid_all) / float(len(valid_all)), 2) if valid_all else None

    zero_observed = (raf_ex_first <= RAF_ZERO_MAX and px_ex_first <= PX_ZERO_MAX)
    reasons = []
    ok = False
    if expectation == "zero":
        ok = zero_observed
        if not zero_observed:
            if raf_ex_first > RAF_ZERO_MAX:
                reasons.append(f"rAF 回调 {raf_ex_first} 次（>{RAF_ZERO_MAX}），零动画未生效")
            if px_ex_first > PX_ZERO_MAX:
                reasons.append(f"画布目的矩形 {px_ex_first}px（>{PX_ZERO_MAX}），仍在绘制")
        verdict = "zero-render-ok" if ok else "NOT-zero-render"
    else:
        ok = bool(raf_avg is not None and RAF_ANIM_MIN <= raf_avg <= RAF_ANIM_MAX)
        verdict = "NOT-zero-render"                       # 默认预设本就该持续出帧
        if not ok:
            reasons.append(f"默认预设期望 rAF 均值 {RAF_ANIM_MIN}~{RAF_ANIM_MAX}/s"
                           f"（1fps 降档混合均值），实测 {raf_avg}/s 超出区间")
        else:
            reasons.append(f"默认预设 rAF 均值 {raf_avg}/s 在带内（{RAF_ANIM_MIN}~{RAF_ANIM_MAX}/s，"
                           f"1fps 降档混合均值）")
        if zero_observed:
            reasons.append("注意：本段实际观测到零渲染，动画可能没起来")

    seg = {
        "name": name,
        "preset": preset,
        "expectation": expectation,
        "wall": round(wall, 1),
        "ticksDelta": ticks1 - ticks0,
        "cpuPct": cpu_pct(ticks1 - ticks0, wall),
        "tickDetail": [{"phase": "start", "pids": tick_detail0},
                       {"phase": "end", "pids": tick_detail1}],
        "cpuinfo": cpu,
        "battery": batt,
        "gfxFrames": {
            "source": "gfxinfo",
            "reliable": False,
            "note": "Android WebView 的合成帧不一定计入 app gfxinfo，仅作参考、不参与判定；"
                    "已先 reset，因此 delta=段内累计（after），beforeReset 仅作诊断",
            "beforeReset": gfx_before,
            "after": gfx_after,
            "delta": gfx_after,
        },
        "rafTotal": raf_total,
        "rafPerSecond": raf_series,
        "rafAvgPerSec": raf_avg,
        "pxTotal": px_total,
        "pxPerSecond": px_series,
        "drawCalls": ints(samples[-1].get("drawCalls")) - s0["drawCalls"] if samples and "cdp" not in samples[-1] else None,
        "samples": samples,
        "stageProbes": logcat_stage(),
        "verdict": verdict,
        "ok": ok,
        "reasons": reasons or ["零渲染：窗口内无 rAF、无画布填充"],
    }
    print(f"[SEG] {name} wall={wall:.1f}s ticksΔ={seg['ticksDelta']} "
          f"rafTotal={raf_total} rafAvg={raf_avg}/s pxTotal={px_total} "
          f"gfxΔ={seg['gfxFrames']['delta']} verdict={verdict}", flush=True)
    if batt:
        print(f"[SEG] batterystats total={batt['total_mah']}mAh cpu={batt['cpu_mah']}mAh "
              f"cpu_sec={batt['cpu_sec']:.1f}s device={batt['device_mah']}mAh", flush=True)
    else:
        print(f"[R4-WARN] {name} 未取到 uid {uid} 的 batterystats 行（仅告警，/proc ticks 为主口径）",
              flush=True)
        seg["reasons"].append("warning: batterystats 未取到本 uid 行（不影响渲染判据）")
    for ln in cpu.get("app") or []:
        print(f"[SEG] cpuinfo app: {ln}", flush=True)
    return seg


# ---- 段 3：后台 ---------------------------------------------------------------
def measure_background(cdp, seconds):
    print(f"\n=== SEGMENT background ({seconds}s, preset=default) ===", flush=True)

    # ① 前台基线 5s（同一预设、同一进程）
    s_start, err = read_snap_recover(cdp)
    if s_start is None:
        raise RuntimeError(f"后台段前台基线读不到页内探针：{err}")
    t_fg = time.time()
    time.sleep(5)
    s_base, err = read_snap_recover(cdp)
    if s_base is None:
        raise RuntimeError(f"前台基线 5s 后读不到探针：{err}")
    fg_wall = time.time() - t_fg
    raf_baseline = round((ints(s_base.get("raf")) - ints(s_start.get("raf")))) / fg_wall
    c0 = {"raf": ints(s_base.get("raf")), "frames": ints(s_base.get("frames")),
          "px": ints(s_base.get("px"))}
    print(f"[SEG] 前台基线 {fg_wall:.1f}s：rAF {raf_baseline:.1f}/s，C0 raf={c0['raf']} px={c0['px']}",
          flush=True)

    # ② 回桌面（PageLifecycle.onStop → web.onPause()）
    sh("adb logcat -c", timeout=30)
    sh("adb shell input keyevent 3", timeout=30)
    t_bg0 = time.time()
    battery_reset()
    gfx_reset()
    uid = uid_of()
    ticks0, tick_detail0 = procs_ticks()

    time.sleep(5)
    pause_log_5s = "[probe] stage-pause=hidden" in logcat_probe()

    # ③ 每 30s 尝试一次 CDP（页面被暂停 → 可能 cdp-unavailable）
    samples = []
    n_windows = max(1, int(seconds // 30))
    for k in range(1, n_windows + 1):
        target = t_bg0 + min(30 * k, seconds)
        d = target - time.time()
        if d > 0:
            time.sleep(d)
        smp, err = read_snap(cdp, timeout=12)
        if smp is None:
            samples.append({"t": round(time.time() - t_bg0, 1), "cdp": "cdp-unavailable",
                            "err": str(err)[:120]})
        else:
            samples.append({
                "t": round(time.time() - t_bg0, 1),
                "hidden": smp.get("hidden"),
                "vis": smp.get("vis"),
                "raf": ints(smp.get("raf")),
                "frames": ints(smp.get("frames")),
                "px": ints(smp.get("px")),
                "stagePaused": smp.get("stagePaused"),
                "stageMode": smp.get("stageMode"),
                "visLog": smp.get("visLog"),
            })
    bg_wall = time.time() - t_bg0

    # ④ 段末读数
    ticks1, tick_detail1 = procs_ticks()
    cpu = cpuinfo()
    batt = battery_power("background", uid)
    gfx_after, _ = gfx_total(name="background", save=True)
    last_ok = next((s for s in reversed(samples) if "cdp" not in s), None)
    bg_raf_delta = (last_ok["raf"] - c0["raf"]) if last_ok else None

    # 后台期间每秒 rAF / 像素（30s 窗口均值）
    bg_series, bg_px_series = [], []
    prev, prev_px, prev_t = c0["raf"], c0["px"], 0.0
    for s in samples:
        if "cdp" in s:
            bg_series.append(None)
            bg_px_series.append(None)
            continue
        dt = max(0.1, s["t"] - prev_t)
        bg_series.append(round((s["raf"] - prev) / dt, 1))
        bg_px_series.append(round((s["px"] - prev_px) / dt, 1))
        prev, prev_px, prev_t = s["raf"], s["px"], s["t"]

    # ⑤ 回前台：立刻（与 am start 并发）读 C1
    ret = return_foreground_immediately(cdp)
    c1 = ret.get("c1")
    frames_hidden = ret.get("framesWhileHidden")
    pause_log_end = "[probe] stage-pause=hidden" in logcat_probe()
    pause_ok = pause_log_5s or pause_log_end

    c1_c0 = (c1["raf"] - c0["raf"]) if c1 else None
    stale = c1_c0 is not None and c1_c0 < 0        # 计数倒退 = 事务快照过期，不可作为判据
    if stale:
        c1_c0 = None
        frames_hidden = None
    # 后台归零判据：优先用可见性瞬时快照（隐藏期间真实执行的 rAF 数），
    # 取不到时退回 C1-C0（含 Home/回前台的过渡帧，阈值同为 10）。
    bg_zero_frames = frames_hidden if frames_hidden is not None else c1_c0
    zero_observed = bool(bg_zero_frames is not None and bg_zero_frames < BG_FRAME_MAX
                         and (bg_raf_delta is None or bg_raf_delta < BG_FRAME_MAX))
    reasons = []
    if bg_zero_frames is None:
        reasons.append("回前台读到的计数小于 C0（快照过期），判据不可用" if stale
                       else "回前台后没能读到 C1（CDP 不可用），无法证明计数归零")
    elif bg_zero_frames >= BG_FRAME_MAX:
        reasons.append(f"后台期间实际执行 rAF {bg_zero_frames} 帧（应 <{BG_FRAME_MAX}，"
                       f"对照 60fps×{seconds}s={60 * seconds}）")
    if bg_raf_delta is not None and bg_raf_delta >= BG_FRAME_MAX:
        reasons.append(f"后台采样期间 rAF 已增 {bg_raf_delta} 帧，页面没有真正冻结")
    if c1_c0 is not None and c1_c0 >= BG_FRAME_MAX and (bg_zero_frames is None or bg_zero_frames < BG_FRAME_MAX):
        reasons.append(f"注：C1-C0={c1_c0} 含 Home/回前台过渡帧；隐藏期实帧={bg_zero_frames}")
    if not pause_ok:
        reasons.append("warning: logcat 缺 `[probe] stage-pause=hidden`（仅告警，不做判据；"
                       "可能被 Chromium 日志挤掉）")
    if not reasons:
        reasons.append(f"隐藏期实帧 {bg_zero_frames} / C1-C0 {c1_c0}，页面已冻结")

    seg = {
        "name": "background",
        "preset": "default",
        "expectation": "zero",
        "wall": round(bg_wall + fg_wall, 1),
        "bgWall": round(bg_wall, 1),
        "fgBaselineWall": round(fg_wall, 1),
        "fgRafPerSec": round(raf_baseline, 2),
        "c0": c0,
        "c1": c1,
        "c1Source": ret.get("c1Source"),
        "c1MinusC0": c1_c0,
        "framesWhileHidden": frames_hidden,
        "bgZeroFrames": bg_zero_frames,
        "bgRafDelta": bg_raf_delta,
        "ticksDelta": ticks1 - ticks0,
        "cpuPct": cpu_pct(ticks1 - ticks0, bg_wall),
        "tickDetail": [{"phase": "start", "pids": tick_detail0},
                       {"phase": "end", "pids": tick_detail1}],
        "cpuinfo": cpu,
        "battery": batt,
        "gfxFrames": {
            "source": "gfxinfo",
            "reliable": False,
            "note": "Android WebView 的合成帧不一定计入 app gfxinfo，仅作参考、不参与判定；"
                    "已先 reset，delta=段内累计",
            "after": gfx_after,
            "delta": gfx_after,
        },
        "rafTotal": c1_c0,
        "rafPerSecond": bg_series,
        "pxTotal": (c1["px"] - c0["px"]) if (c1 and c1.get("px") is not None) else None,
        "pxPerSecond": bg_px_series,
        "samples": samples,
        "logcatStagePause": {"at5s": pause_log_5s, "atEnd": pause_log_end},
        "returnRead": ret,
        "verdict": "zero-render-ok" if zero_observed else "NOT-zero-render",
        "ok": bool(zero_observed),
        "reasons": reasons,
    }
    print(f"[SEG] background bgWall={bg_wall:.1f}s C1-C0={c1_c0} "
          f"hiddenFrames={frames_hidden} bgRafΔ={bg_raf_delta} "
          f"c1Src={ret.get('c1Source')} cpu%={seg['cpuPct']} "
          f"pauseLog={pause_ok} verdict={seg['verdict']}", flush=True)
    if batt:
        print(f"[SEG] batterystats total={batt['total_mah']}mAh cpu={batt['cpu_mah']}mAh "
              f"cpu_sec={batt['cpu_sec']:.1f}s device={batt['device_mah']}mAh", flush=True)
    else:
        print(f"[R4-WARN] background 未取到 uid {uid} 的 batterystats 行"
              f"（仅告警，/proc ticks 为主口径）", flush=True)
        seg["reasons"].append("warning: batterystats 未取到本 uid 行（不影响渲染判据）")
    return seg


def return_foreground_immediately(cdp):
    """`am start` 与 CDP 轮询并发，只为把「回前台」这件事本身控制到最短时延。

    C1 优先取页面 visibilitychange 那一刻的计数（__r4.vis.atVisible）：
    它由页面自己在事件派发时同步记录，不含 CDP 往返时延；同时
    atVisible.raf - atHidden.raf 就是「看不见」期间真正执行过的 rAF 数。
    页面没记到可见性事件时，退回第一条 hidden=false 的快照（cdp-poll）。"""
    res = {"c1": None, "c1Source": None, "framesWhileHidden": None,
           "visLog": None, "polls": [], "error": None,
           "startCmd": f"adb shell am start -n {MAIN}"}
    t0 = time.time()

    def _go():
        sh(res["startCmd"], timeout=60)

    th = threading.Thread(target=_go, daemon=True)
    th.start()
    polled = None
    end = time.time() + 60
    while time.time() < end:
        smp, err = read_snap(cdp, timeout=8)
        if smp is None:
            if len(res["polls"]) < 40:
                res["polls"].append({"t": round(time.time() - t0, 2),
                                     "cdp": "cdp-unavailable", "err": str(err)[:100]})
            time.sleep(0.25)
            continue
        rec = {"t": round(time.time() - t0, 2), "hidden": smp.get("hidden"),
               "raf": ints(smp.get("raf")), "px": ints(smp.get("px"))}
        if len(res["polls"]) < 40:
            res["polls"].append(rec)
        if smp.get("visLog"):
            res["visLog"] = smp.get("visLog")
        if smp.get("hidden") is False:
            polled = {"raf": rec["raf"], "px": rec["px"],
                      "frames": ints(smp.get("frames")), "t": rec["t"]}
            break
        time.sleep(0.1)
    th.join(timeout=60)

    vis = res.get("visLog") or {}
    at_h, at_v = vis.get("atHidden"), vis.get("atVisible")
    if at_h and at_v and ints(at_v.get("raf")) >= ints(at_h.get("raf")):
        res["framesWhileHidden"] = ints(at_v.get("raf")) - ints(at_h.get("raf"))
        res["c1"] = {"raf": ints(at_v.get("raf")), "px": ints(at_v.get("px")),
                     "source": "page-visibilitychange"}
        res["c1Source"] = "page-visibilitychange"
    elif polled:
        res["c1"] = polled
        res["c1Source"] = "cdp-poll"
    res["c1Polled"] = polled
    if res["c1"] is None:
        res["error"] = "回前台 60s 内 visibilityState 未变 visible（或 CDP 全程不可用）"
    return res


# ---- 输出 -------------------------------------------------------------------
def write_json(path, payload):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[R4] JSON 已写入 {path}", flush=True)


def summary_table(segments):
    print("\n=== SUMMARY (power r4) ===", flush=True)
    print(f"{'segment':<16}{'wall':>8}{'rafTot':>9}{'raf/s':>8}{'pxTot':>14}"
          f"{'ticksΔ':>9}{'cpu%':>7}{'cpu_mAh':>10}{'cpu_s':>8}{'gfxΔ':>8}  verdict", flush=True)
    for s in segments:
        b = s.get("battery") or {}
        print(f"{s.get('name', '?'):<16}"
              f"{str(s.get('wall', 'NA')):>8}"
              f"{str(s.get('rafTotal', 'NA')):>9}"
              f"{str(s.get('rafAvgPerSec', s.get('fgRafPerSec', 'NA'))):>8}"
              f"{str(s.get('pxTotal', 'NA')):>14}"
              f"{str(s.get('ticksDelta', 'NA')):>9}"
              f"{str(s.get('cpuPct', 'NA')):>7}"
              f"{str(b.get('cpu_mah', 'NA')):>10}"
              f"{(('%.1f' % b['cpu_sec']) if b.get('cpu_sec') is not None else 'NA'):>8}"
              f"{str((s.get('gfxFrames') or {}).get('delta', 'NA')):>8}"
              f"  {s.get('verdict', 'NA')}", flush=True)
        for r in (s.get("reasons") or [])[:3]:
            print(f"    - {r}", flush=True)


def meta_info(args):
    global CPU_CORES
    _, model = sh("adb shell getprop ro.product.model", timeout=30)
    _, sdk = sh("adb shell getprop ro.build.version.sdk", timeout=30)
    _, size = sh("adb shell wm size", timeout=30)
    CPU_CORES = detect_cores()
    return {
        "pkg": PKG,
        "activity": MAIN,
        "deviceModel": model.strip(),
        "sdk": sdk.strip(),
        "screen": size.strip(),
        "cpuCores": CPU_CORES,
        "idleSeconds": args.idle_seconds,
        "bgSeconds": args.bg_seconds,
        "cdpPort": args.port,
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main():
    ap = argparse.ArgumentParser(description="treehole R4 耗电/零渲染 AVD 探针")
    ap.add_argument("--idle-seconds", type=int, default=60)
    ap.add_argument("--bg-seconds", type=int, default=300)
    ap.add_argument("--out", default=os.path.join("artifacts", "avd", "power_r4.json"))
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    payload = {"meta": {}, "segments": [], "ok": False}
    rc = 1
    cdp = None
    try:
        rc0, out = sh("adb get-state", timeout=30)
        if "device" not in out:
            raise RuntimeError(f"没有可用设备：{out.strip()[:120]}")
        payload["meta"] = meta_info(args)
        print(f"[R4] meta: {json.dumps(payload['meta'], ensure_ascii=False)}", flush=True)

        device_prepare()

        def run(name, fn):
            try:
                seg = fn()
            except Exception as e:
                print(f"[R4-FAIL] {name} 采集失败：{e}", flush=True)
                seg = {"name": name, "ok": False, "verdict": "NOT-zero-render",
                       "reasons": [f"探针异常：{e}"], "error": repr(e)[:300]}
            payload["segments"].append(seg)

        cdp, _ = apply_preset(args.port, PRESET_MINIMAL, "minimal")
        run("idle_minimal", lambda: measure_foreground(
            cdp, "idle_minimal", args.idle_seconds, "minimal", "zero"))

        cdp, _ = apply_preset(args.port, PRESET_DEFAULT, "default", prev_cdp=cdp)
        run("idle_default", lambda: measure_foreground(
            cdp, "idle_default", args.idle_seconds, "default", "animating"))

        # background：沿用默认预设（不 reload，保持同一进程/同一页）
        run("background", lambda: measure_background(cdp, args.bg_seconds))
    except Exception as e:
        print(f"[R4-FAIL] 探针整体异常：{e}", flush=True)
        payload["error"] = repr(e)[:300]
        rc = 1
    finally:
        if cdp is not None:
            try:
                cdp.close()
            except Exception:
                pass

    segs = payload["segments"]
    exit_ok = bool(segs) and all(bool(s.get("ok")) for s in segs) and len(segs) == 3
    payload["ok"] = exit_ok
    rc = 0 if exit_ok else 1
    summary_table(segs)
    write_json(args.out, payload)
    if rc != 0:
        print("[R4] 关键判据未全部成立 → exit 1（数字已写进 JSON）", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
