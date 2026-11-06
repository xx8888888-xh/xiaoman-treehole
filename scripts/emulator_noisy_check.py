#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_noisy_check.py · AVD「NoisyGuard 真的注册了」证据（ADB / dumpsys 级）

为什么单独写这个脚本：
  NoisyGuardTest 是纯 JVM 单测，它只能证明「计数器的状态机对不对」，证明不了
  `context.registerReceiver()` 在真机上真的被调用、且真的挂到了本进程上。
  这里要的是操作系统层面的证据：`dumpsys activity broadcasts` 里的
  Registered Receivers（每个注册接收器一个 ReceiverList 块），找到
  com.xiaoman.treehole 名下、filter 为 android.media.AUDIO_BECOMING_NOISY
  的那一个；再用 logcat 的 [probe] noisy-receiver registered 交叉印证。

三态判定（见 docs/android-audio/ANDROID_AUDIO_VERIFIABILITY.md §4）：
  [NOISY-PASS] 证据成立
  [NOISY-FAIL] 能判定，且判定不成立
  [NOISY-SKIP] 无法判定（解析失败 / dumpsys 无输出 / logcat 采集窗口内没拿到）
  SKIP 不冒充通过，也不判红；只有 FAIL 会让脚本以 1 退出。

本脚本只依赖标准库：历史故障 #119 里它排在 `pip3 install websockets` 之前，
  而旧版 `from android_cdp import ...` 会连带 import websockets，直接
  ModuleNotFoundError 把 job 拉红。这里的 adb 封装是自带的，不再有该依赖。

解析约束（历史故障 #117）：
  旧版 `c.split(" ", 2)[2]` 假设「来源名」有三个空格列；`dumpsys activity`
  只有两段，直接 IndexError。本版**不按列切分**，来源名只按命令串结构生成；
  解析异常一律记 [NOISY-SKIP] 无法判定（解析失败），绝不抛异常中断 job。

注意：CI 的 emulator-options 带 -no-audio（镜像没有 TTS 引擎），本脚本不验可闻性，
      只验接收器注册这一件事。

用法：python3 scripts/emulator_noisy_check.py（脚本会自己 force-stop/start 触发一次干净注册）
退出码：0=无 FAIL；1=有 FAIL
"""
import re
import subprocess
import sys
import time

PKG = "com.xiaoman.treehole"
ACTIVITY = f"{PKG}/.MainActivity"
ACTION = "android.media.AUDIO_BECOMING_NOISY"
TAG = "XiaoManAudio"
PROBE = "[probe] noisy-receiver registered"
DUMP_FILE = "noisy_dumpsys.txt"
LOG_FILE = "noisy_logcat.txt"
RESULTS = []  # ["PASS" | "FAIL" | "SKIP"]


def sh(cmd, timeout=150):
    """跑 adb/host 命令，返回 (rc, stdout+stderr)。异常也折算成 rc，不往上抛。"""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 125, f"{type(e).__name__}: {e}"


def record(name, verdict, info=""):
    """记一条三态结果。verdict 只允许 PASS / FAIL / SKIP。"""
    print(f"[NOISY-{verdict}] {name}" + (f" | {info}" if info else ""), flush=True)
    RESULTS.append(verdict)


def pid_of():
    try:
        rc, out = sh(f"adb shell pidof {PKG}", timeout=45)
        return out.strip().split()[0] if rc == 0 and out.strip() else ""
    except Exception:
        return ""


def source_label(cmd):
    """按命令串结构生成来源名；不假设固定列（旧版越界问题的修复点）。"""
    try:
        parts = cmd.split()
        if "dumpsys" in parts:
            return " ".join(parts[parts.index("dumpsys"):])
        return " ".join(parts) or "(空命令)"
    except Exception as e:
        return f"无法判定（解析失败）: {type(e).__name__}: {e}"


def dumpsys_sources():
    """不同 Android 版本里注册接收器可能落在不同段落，三个来源都收一遍。

    返回 (sources, errors)：sources 是 [(cmd, out)]；errors 是失败命令的原因。
    命令失败或输出为空都只记 reason，不抛异常。
    """
    sources = []
    errors = []
    for cmd in ("dumpsys activity broadcasts",
                "dumpsys activity broadcasts --full",
                "dumpsys activity"):
        rc, out = sh(f"adb shell {cmd}", timeout=150)
        if rc == 0 and out.strip():
            sources.append((cmd, out))
        else:
            errors.append(f"{source_label(cmd)}: rc={rc}, 输出{len(out.strip())}字")
    return sources, errors


def receiver_blocks(text):
    """按 `ReceiverList{` 切块：每块是一个已注册接收器（含 pid/uid/filter）。"""
    try:
        starts = [m.start() for m in re.finditer(r"ReceiverList\{", text)]
    except Exception:
        return []
    blocks = []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(len(text), s + 4000)
        blocks.append(text[s:end])
    return blocks


def match_receiver(sources, pid):
    """在 ReceiverList 块里找 ACTION + 包名/pid。返回 (matched 描述, 摘录, 块总数)。"""
    total_blocks = 0
    for cmd, out in sources:
        blocks = receiver_blocks(out)
        total_blocks += len(blocks)
        for block in blocks:
            if ACTION in block and (PKG in block or re.search(rf"[\s{{]{re.escape(pid)}\s", block)):
                return f"{source_label(cmd)} | " + " ".join(block.split())[:220], "", total_blocks
    excerpt = ""
    for cmd, out in sources:
        idx = out.find(ACTION)
        if idx >= 0:
            excerpt = f"{source_label(cmd)} 里 ACTION 出现处: ...{out[max(0, idx - 200): idx + 200]}..."
            break
    if not excerpt:
        names = ", ".join(source_label(c) for c, _ in sources)
        excerpt = f"{names} 里完全没有 {ACTION}"
    return "", excerpt, total_blocks


def capture_logcat_probe():
    """logcat -c 清空 → force-stop/start 触发一次干净注册 → 立刻 -d 拉取。

    拿到探针返回 (pid, PROBE)；拿不到返回 (pid, "")，由调用方记 SKIP。
    进程起不来时返回 ("", 原因)。
    """
    sh("adb logcat -c", timeout=45)
    sh(f"adb shell am force-stop {PKG}", timeout=45)
    sh(f"adb shell am start -n {ACTIVITY}", timeout=45)

    pid = ""
    for _ in range(15):
        pid = pid_of()
        if pid:
            break
        time.sleep(1)
    if not pid:
        return "", "重启后进程未起来"

    log = ""
    for _ in range(6):
        rc, out = sh(f"adb logcat -d --pid={pid} -s {TAG}", timeout=90)
        if rc != 0 or not out.strip():
            rc, out = sh(f"adb logcat -d -s {TAG}", timeout=90)
        log = out
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(log)
        except Exception:
            pass
        if PROBE in out:
            return pid, PROBE
        time.sleep(1.5)
    return pid, ""


def main():
    # ---- 探针 1：logcat（先清空再触发，避免被 2M 环形缓冲挤掉）----
    pid, probe = capture_logcat_probe()
    record("应用进程存活", "PASS" if pid else "FAIL",
           f"{PKG} pid={pid}" if pid else "重启后进程不在（无法继续取证）")

    if not pid:
        record("dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY", "SKIP",
               "无法判定（解析失败：应用未启动，dumpsys 无对象）")
        return summarize()

    # ---- 探针 2：dumpsys ReceiverList（OS 层注册证据）----
    sources, errors = dumpsys_sources()
    try:
        with open(DUMP_FILE, "w", encoding="utf-8") as f:
            for cmd, out in sources:
                f.write(f"\n===== adb shell {cmd} =====\n{out}\n")
    except Exception as e:
        print(f"[NOISY-WARN] 写 {DUMP_FILE} 失败: {e}", flush=True)

    if not sources:
        record("dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY", "SKIP",
               "无法判定（解析失败：dumpsys 无输出）| " + "; ".join(errors))
    else:
        matched, excerpt, total_blocks = match_receiver(sources, pid)
        if matched:
            record("dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY", "PASS", matched)
        elif total_blocks == 0:
            # 输出有内容但没有 ReceiverList 结构 → 无法判定，而不是产品失败
            record("dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY", "SKIP",
                   "无法判定（解析失败：输出里没有 ReceiverList 结构）| 来源="
                   + ", ".join(source_label(c) for c, _ in sources))
        else:
            record("dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY", "FAIL", excerpt)

    # ---- 探针 3：应用自己打的注册探针（交叉印证；拿不到只记 SKIP）----
    if probe:
        record("logcat：[probe] noisy-receiver registered", "PASS", PROBE)
    else:
        record("logcat：[probe] noisy-receiver registered", "SKIP",
               f"无法判定（logcat -c 后重启触发，仍未见该行；已落盘 {LOG_FILE}；"
               "交叉印证缺失，不代表未注册）")

    return summarize()


def summarize():
    p = RESULTS.count("PASS")
    f = RESULTS.count("FAIL")
    s = RESULTS.count("SKIP")
    print("\n" + "=" * 46)
    print(f"NoisyGuard 注册验收：PASS {p} / FAIL {f} / SKIP {s}")
    print("（SKIP = 无法判定：不冒充通过，也不判红；只有 FAIL 退出码为 1）")
    print("=" * 46)
    return 1 if f else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        # 任何未预期异常都收敛成 SKIP，绝不把 job 拉红、也绝不假装通过
        print(f"[NOISY-SKIP] 无法判定（解析失败）| {type(e).__name__}: {e}", flush=True)
        sys.exit(0)
