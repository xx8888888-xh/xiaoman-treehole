#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_noisy_check.py · AVD「NoisyGuard 真的注册了」证据（ADB / dumpsys 级）
--------------------------------------------------------------------
为什么单独写这个脚本：
  NoisyGuardTest 是纯 JVM 单测，它只能证明「计数器的状态机对不对」，证明不了
  `context.registerReceiver()` 在真机上真的被调用、且真的挂到了本进程上。
  这里要的是操作系统层面的证据：`dumpsys activity broadcasts` 里的
  Registered Receivers（每个注册接收器一个 ReceiverList 块），找到
  com.xiaoman.treehole 名下、filter 为 android.media.AUDIO_BECOMING_NOISY
  的那一个；再用 logcat 的 [probe] noisy-receiver registered 交叉印证。

断言（任一不成立即 exit 1）：
  1) 应用进程活着（pidof 拿得到 pid）；
  2) dumpsys 的 ReceiverList 块里出现 AUDIO_BECOMING_NOISY，且同一块里带包名；
  3) logcat 里有 [probe] noisy-receiver registered。

注意：CI 的 emulator-options 带 -no-audio（镜像没有 TTS 引擎），本脚本不验可闻性，
      只验接收器注册这一件事。

用法：python3 scripts/emulator_noisy_check.py（App 需已启动；CI 里由 workflow 起）
退出码：0=全通过；1=有失败项（逐项 [NOISY-PASS]/[NOISY-FAIL]）
"""
import re
import sys

from android_cdp import PKG, sh

ACTION = "android.media.AUDIO_BECOMING_NOISY"
PROBE = "[probe] noisy-receiver registered"
DUMP_FILE = "noisy_dumpsys.txt"
RESULTS = []


def check(name, ok, info=""):
    tag = "[NOISY-PASS]" if ok else "[NOISY-FAIL]"
    print(f"{tag} {name}" + (f" | {info}" if info else ""), flush=True)
    RESULTS.append(ok)
    return ok


def pid_of():
    rc, out = sh(f"adb shell pidof {PKG}")
    return out.strip().split()[0] if rc == 0 and out.strip() else ""


def dumpsys_sources():
    """不同 Android 版本里注册接收器可能落在不同段落，三个来源都收一遍。"""
    sources = []
    for cmd in ("dumpsys activity broadcasts",
                "dumpsys activity broadcasts --full",
                "dumpsys activity"):
        rc, out = sh(f"adb shell {cmd}", timeout=150)
        if rc == 0 and out:
            sources.append((cmd, out))
    return sources


def receiver_blocks(text):
    """按 `ReceiverList{` 切块：每块是一个已注册接收器（含 pid/uid/filter）。"""
    starts = [m.start() for m in re.finditer(r"ReceiverList\{", text)]
    blocks = []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(len(text), s + 4000)
        blocks.append(text[s:end])
    return blocks


def main():
    pid = pid_of()
    check(f"应用进程存活（pid={pid or '无'}）", bool(pid), f"{PKG} pid={pid}" if pid else "进程不在")

    sources = dumpsys_sources()
    check("dumpsys 有输出", bool(sources), "来源=" + ",".join(c.split(" ", 2)[2] for c, _ in sources))

    try:
        with open(DUMP_FILE, "w", encoding="utf-8") as f:
            for cmd, out in sources:
                f.write(f"\n===== adb shell {cmd} =====\n{out}\n")
    except Exception as e:  # 写盘失败不该挡住判定
        print(f"[NOISY-WARN] 写 {DUMP_FILE} 失败: {e}", flush=True)

    # ---- 证据 1：ReceiverList 块里有这个 action，并且是本进程的接收器 ----
    matched = ""
    for cmd, out in sources:
        for block in receiver_blocks(out):
            if ACTION in block and (PKG in block or (pid and re.search(rf"[\s{{]{re.escape(pid)}\s", block))):
                matched = f"{cmd} | " + " ".join(block.split())[:220]
                break
        if matched:
            break

    # 失败时给出最接近的原文摘录，方便一眼判断是环境（dumpsys 段落缺失）还是产品问题
    excerpt = ""
    if not matched:
        for cmd, out in sources:
            idx = out.find(ACTION)
            if idx >= 0:
                excerpt = f"{cmd} 里 ACTION 出现处: ...{out[max(0, idx - 200): idx + 200]}..."
                break
        if not excerpt:
            names = ", ".join(c.split(" ", 2)[2] for c, _ in sources)
            excerpt = f"{names} 里完全没有 {ACTION}"

    check("dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY",
          bool(matched), matched or excerpt)

    # ---- 证据 2：应用自己打的注册探针（交叉印证，不单独作为通过依据）----
    rc, log = sh("adb logcat -d", timeout=120)
    has_probe = PROBE in log
    check("logcat：[probe] noisy-receiver registered", has_probe,
          PROBE if has_probe else "logcat 里没有该探针（可能缓冲区已滚动）")

    ok = all(RESULTS)
    print("\n" + "=" * 46)
    print("NoisyGuard 注册验收：" + ("全部通过" if ok else f"失败 {RESULTS.count(False)} 项"))
    print("=" * 46)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
