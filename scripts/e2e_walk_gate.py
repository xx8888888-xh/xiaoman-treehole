#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""走查硬门：回复没到达 → 判红，绝不允许继续采样。

上一轮的 0/143 就是"消息没发出去、流程继续跑、对静音采样还判过"造成的。
本模块把"回复到达"从观察值升级成断言：
  - require_reply()：replyArrived=false 直接 GateFail；
  - require_voice()：没有新的 speakSafe 事件（回复没开始播）直接 GateFail。
collector（emulator_e2e_walk.py）捕获后写 metrics 并 exit 1；
workflow 不再吞掉退出码，所以该步骤在 CI 里是红的。
"""


class GateFail(AssertionError):
    """走查步骤的硬断言失败（区别于脚本自身崩溃）。"""


def require_reply(step, arrived, detail=None):
    """该步骤必须收到回复；否则判红。"""
    if not arrived:
        raise GateFail(
            "%s: replyArrived=false（消息/回复没到达，后续口型采样无意义）%s"
            % (step, "" if detail is None else " detail=%r" % (detail,))
        )


def require_voice(step, event):
    """该步骤必须出现新的语音事件；否则判红（不能对着静音采样）。"""
    if not event:
        raise GateFail("%s: 没有新的 speakSafe 事件（回复没开始播，不能开口型采样）" % step)


def require_true(step, ok, why):
    """通用硬断言：ok 为假即判红，why 写清实测值。"""
    if not ok:
        raise GateFail("%s: %s" % (step, why))
