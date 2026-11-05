#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
android_cdp.py · WebView DevTools 极简客户端（AVD 脚本共用）
--------------------------------------------------------------------
为什么需要它：uiautomator dump 对 WebView DOM 是盲区（本仓库实测 dump 只有
2KB 空壳），点到输入框、断言 textInput / sendBtn / #messages 只能走 WebView
自己的 DevTools 协议。debug 包在 MainActivity 里开了 setWebContentsDebuggingEnabled，
所以 `adb forward` 到 webview_devtools_remote_<pid> 后就能 Runtime.evaluate。

供 emulator_adapt_sweep.py / emulator_chat_check.py 复用，避免各写一份。
依赖：requests、websockets（CI 里 pip3 install requests websockets）。
"""
import json
import re
import subprocess
import time

import requests
from websockets.sync.client import connect

PKG = "com.xiaoman.treehole"


def sh(cmd, timeout=90):
    """跑 adb/host 命令，返回 (rc, stdout+stderr)。"""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


class Cdp:
    """最小实现：只保留 attach() / eval() / close()。"""

    def __init__(self, port=9223):
        self.port = port
        self.ws = None
        self.n = 0
        self.url_filter = "index.html"

    def attach(self, url_filter="index.html"):
        self.url_filter = url_filter
        _, out = sh("adb shell cat /proc/net/unix")
        sockets = sorted(set(re.findall(r"webview_devtools_remote_(\d+)", out)))
        _, ps = sh("adb shell ps -A -o PID,NAME")
        mypids = {ln.split()[0] for ln in ps.splitlines() if PKG in ln}
        sockets = [s for s in sockets if s in mypids] or sockets
        if not sockets:
            raise RuntimeError("找不到 WebView devtools socket")
        sh(f"adb forward tcp:{self.port} localabstract:webview_devtools_remote_{sockets[0]}")
        lst = None
        for _ in range(20):
            try:
                lst = requests.get(f"http://127.0.0.1:{self.port}/json/list", timeout=5).json()
                break
            except Exception:
                time.sleep(0.5)
        if lst is None:
            raise RuntimeError("devtools /json/list 不可达")
        cand = [t for t in lst if url_filter in (t.get("url") or "")]
        if not cand:
            raise RuntimeError(f"没有匹配 {url_filter} 的 WebView target: "
                               f"{[t.get('url') for t in lst]}")
        target = cand[0]
        self.ws = connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024)
        return target["url"]

    def eval(self, js, timeout=20):
        """掉线自动重连（配置变更/键盘弹出可能让 devtools 连接断开）。"""
        try:
            return self._eval(js, timeout)
        except Exception:
            try:
                self.ws.close()
            except Exception:
                pass
            self.attach(self.url_filter)
            return self._eval(js, timeout)

    def _eval(self, js, timeout=20):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                                 "params": {"expression": js, "returnByValue": True,
                                            "awaitPromise": True}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv(timeout=timeout))
            if msg.get("id") == mid:
                r = msg.get("result", {})
                if "exceptionDetails" in r:
                    raise RuntimeError(str(r["exceptionDetails"])[:300])
                return r.get("result", {}).get("value")
        raise RuntimeError("CDP 超时")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
