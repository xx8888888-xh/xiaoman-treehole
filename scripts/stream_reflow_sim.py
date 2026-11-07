#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟“流式逐帧 + 客户端分条渲染”，对照收口整段归一与逐帧归一。

不联网、不碰 TTS：把 R7 真实回复按 2 字一帧喂进去，用与
web/js/app.js 相同的规则（split("||") -> 每条 updateRow/addMsg）重放画面，
统计每个替换帧“已经显示的字被改写多少、新增几条气泡”。

用法：
  python scripts/stream_reflow_sim.py            # 打印对照表
  python scripts/stream_reflow_sim.py --step 1   # 逐字喂
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import risk_guard as rg  # noqa: E402

# R7 真实复测原文（detail-pet-01 轮1 / followup-01 轮4）
REAL_PET = ("团子前阵子也这样，饿了一天给我吓坏了。你先别急，它精神怎么样？\n\n"
            "不吃超过一天的话，猫粮里掺点罐头试试，不行就带去看医生。"
            "我上次就是这么好的。")
REAL_FU = ("哈哈，团子是挺欠揍的，专挑你加班的时候霸占键盘。你说年糕啊，"
           "那是你的猫吧，我养的是团子。不过你说得对，猫都这样，"
           "专挑你忙的时候搞破坏。")

MAX_SLOTS = 5


def client_apply(slots, full):
    """与 createStreamRenderer().apply/replace 同规则。

    返回 (真正的改写事件, 新增条数)。纯追加（新文本以旧文本为前缀）不算改写，
    那是正常的逐字打字；只有旧字被删/被替换才算“跳”。
    """
    raw = [seg.strip() for seg in str(full or "").split("||")][:MAX_SLOTS]
    rewrites, added = [], 0
    for i, seg in enumerate(raw):
        if i < len(slots):
            old = slots[i]
            if old != seg and not seg.startswith(old):
                keep = 0
                for a, b in zip(old, seg):
                    if a != b:
                        break
                    keep += 1
                rewrites.append({"slot": i, "old": old, "new": seg,
                                 "rewritten": (len(old) - keep) + (len(seg) - keep)})
            slots[i] = seg
        elif seg:
            slots.append(seg)
            added += 1
    return rewrites, added


def frames_of(text, step):
    return [text[:i + step] for i in range(0, len(text), step)]


def run(text, step, progressive):
    """返回 {frames, rewrite_frames, max_rewrite, total_rewrite, added, finish}."""
    slots, emitted, acc = [], "", ""
    rewrite_frames, total_rewrite, max_rewrite, added = 0, 0, 0, 0
    bubble_counts = []
    cost = 0.0

    def feed(full):
        nonlocal rewrite_frames, total_rewrite, max_rewrite, added
        rew, add = client_apply(slots, full)
        if rew or add:
            if rew:
                rewrite_frames += 1
            for r in rew:
                total_rewrite += r["rewritten"]
                max_rewrite = max(max_rewrite, r["rewritten"])
            added += add
        bubble_counts.append(len(slots))

    for i, prefix in enumerate(frames_of(text, step), 1):
        clean = rg.sanitize_output(prefix, "none")
        # 会话为空时名字兜底是空操作；逐帧策略 B 只多跑一次 reflow
        safe = clean
        if progressive:
            t0 = time.perf_counter()
            target = rg.reflow_messages(safe[:-2] if len(safe) > 2 else "")
            cost += time.perf_counter() - t0
        else:
            target = safe[:-2] if len(safe) > 2 else ""
        if target and target.startswith(emitted):
            acc += target[len(emitted):]
            emitted = target
            feed(acc)
        elif target and not target.startswith(emitted):
            acc = target
            emitted = target
            feed(acc)

    # 收尾：整段过名字兜底 + reflow，与 _stream_finish 一致
    t0 = time.perf_counter()
    final = rg.reflow_messages(text)
    cost += time.perf_counter() - t0
    if final.startswith(emitted):
        acc += final[len(emitted):]
    else:
        acc = final
    fin_rew, fin_add = client_apply(slots, acc)
    return {"bubble_counts": bubble_counts, "rewrite_frames": rewrite_frames,
            "total_rewrite": total_rewrite, "max_rewrite": max_rewrite,
            "added": added, "cost_ms": cost * 1000,
            "final_bubbles": len([s for s in slots if s]),
            "final_text": "||".join(slots),
            "finish_rewrite": sum(r["rewritten"] for r in fin_rew),
            "finish_added": fin_add}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=2, help="每帧喂几个字（默认 2，近似 token）")
    args = ap.parse_args()
    print("每帧 %d 字；画面规则 = split('||') 逐条渲染（同 web/js/app.js）\n" % args.step)
    for name, text in (("detail-pet-01 轮1", REAL_PET),
                       ("followup-01 轮4", REAL_FU)):
        print("== %s（%d 字） ==" % (name, len(text)))
        outs = {}
        for label, prog in (("A 收口才归一", False), ("B 逐帧归一", True)):
            r = run(text, args.step, prog)
            outs[label] = r
            counts = r["bubble_counts"]
            jumps = [(i + 1, counts[i]) for i in range(1, len(counts))
                     if counts[i] != counts[i - 1]]
            print("  %s：气泡 %d 条，变条帧 %s"
                  % (label, r["final_bubbles"],
                     jumps if len(jumps) <= 8 else jumps[:8] + ["…"]))
            print("     流中改写 %d 次/%d 字（单帧最多 %d 字），收尾替换改写 %d 字 + 新增 %d 条，"
                  "新增气泡合计 %d 条，reflow 耗时 %.2fms"
                  % (r["rewrite_frames"], r["total_rewrite"], r["max_rewrite"],
                     r["finish_rewrite"], r["finish_added"], r["added"], r["cost_ms"]))
        same = outs["A 收口才归一"]["final_text"] == outs["B 逐帧归一"]["final_text"]
        print("  最终画面是否一致：%s" % ("是" if same else "否"))
        print()

    # 逐帧归一下“半句会不会被切坏”的抽样（正例应切、反例不该切）
    print("== 逐帧归一：半句/特殊串抽样 ==")
    samples = [
        ("反例·未说完半句", "我先把订单号", False),
        ("反例·数字中间的句点", "版本 1.2.3 已经发了，你看看还有没有问题，有的话直接说，别客气，我这边好改。", False),
        ("反例·引号里的句号", "他说“我先睡一觉。明天再说。”我听完也没接话，就让他先休息了，别硬撑，他最近真的太累了。", False),
        ("反例·省略号停顿", "我也不知道该怎么说……你先别急着给建议，我就想有个人听我说完这一整段话，不用急着回应。", False),
        ("正例·真正的句末", "先别急着回他。等明天冷静了，再把想说的话一条条写下来，别在气头上发消息，发出去就真的收不回来了。", True),
    ]
    for label, s, want_split in samples:
        out = rg.reflow_messages(s)
        got = "||" in out
        print("  %-12s %s（期望%s）" % (label, "切成 %d 条" % len([p for p in out.split("||") if p])
                                       if got else "不切", "切" if want_split else "不切"))
        if got != want_split:
            print("     ！不符合期望：%s" % out.replace("\n", "\\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
