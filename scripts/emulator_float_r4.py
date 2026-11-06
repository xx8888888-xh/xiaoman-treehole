#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
emulator_float_r4.py · 第四轮桌宠浮窗渲染验收（FLOAT_R4）
--------------------------------------------------------------------
验收对象是 FloatWindowService 里 140x180dp 透明 WebView 加载的 pet.html
（Live2D 画布），不是迷你聊天窗（float=1 时 #stage 被 display:none）。

要回答的三件事（两个外观预设各跑一遍）：
  1) 预设是否真的生效：index.html 里写 localStorage → force-stop → 带
     autostart_float 重启桌面桌宠 → 在 pet.html 里读 Appearance.get()/__pet.state()；
  2) 三档静止策略是否成立（pet.js）：0–6s 满帧（≈55~62 rAF/s）、6–10s 降到
     15fps 且 rAF≈0（走 setTimeout）、>10s 停循环、每 10s 补一帧（呼吸节拍）。
     minimal 预设 motion=false：首帧后应始终 ≈0 rAF；
  3) 无效光栅化：pet.css 把画布 CSS 高度设成窗口 3 倍（宽也随之溢出）。
     pet.js renderFrame() 已用 save();rect(visibleRect());clip() 取景，Skia 只光栅化
     取景框内，所以「JS 把整层画到窗口外」本身不算无效光栅化。探针维护 clip 栈
     （save/restore/rect/clip，画布像素坐标），逐 draw 算三段面积：
       destArea      = fillPx（目的矩形，getTransform 四角 AABB）
       rasterPx      = dest ∩ clip（真会被 Skia 处理）
       rasterOutsidePx = rasterPx − (dest ∩ clip ∩ 窗口可见区)
     只有 rasterOutsidePx/pageFill > 0.01 才判 invalid-rasterization=yes；
     JS 侧整块出界的调用另记 jsDrawsOutsideClip（已被 clip 丢弃，不构成无效光栅化）。

页内探针（CDP Runtime.evaluate 注入，页面加载后注入也可以，从注入时刻计数）：
  - 包装 window.requestAnimationFrame：只在实际回调执行时 raf++，记录时间戳；
  - 包装 CanvasRenderingContext2D.prototype.drawImage / fillRect：
    drawCalls++，按参数算目的矩形面积（9 参取 a[7]*a[8] 的 dw/dh、5 参取
    a[3]*a[4] 的 dw/dh、3 参取图片原始宽高），累计 fillPx；
  - 包装 save/restore/rect/clip/beginPath 维护 clip 栈：rect 记矩形与当时变换，
    clip 把矩形换算成画布像素并与父 clip 求交后入栈，无 clip 视为整块画布；
  - 包装 clearRect（仅主画布整幅）：frames++ —— 这是真实「帧」计数，
    rAF 驱动与 setTimeout 驱动的帧都能覆盖（rAF 计数在降帧档必然为 0）；
  - window.__r4 = {raf, drawCalls, fillPx, visiblePx, rasterPx, rasterOutsidePx,
    jsDrawsOutsideClip, clipSeen, clipRect, frames, reset(), snap()}，
    snap() 附画布/视口几何、clip 实况与预设实况。

CLI：python3 scripts/emulator_float_r4.py --seconds 60 --out artifacts/avd/float_r4.json
退出码：0=两预设都拿到数据且三档/预设判据成立；1=有项失败（已得数据仍写 JSON）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from android_cdp import Cdp                     # noqa: E402

PKG = "com.xiaoman.treehole"
MAIN = f"{PKG}/.MainActivity"
MAIN_CDP_PORT = 9223        # 主界面 index.html（写 localStorage 用）
PET_CDP_PORT = 9225         # 桌宠 pet.html（沿用 scripts/emulator_float_check.py 的约定）
APPEARANCE_KEY = "xiaoman_appearance_v1"
PET_W_DP, PET_H_DP = 140, 180

PRESETS = {
    "minimal": {"preset": "minimal", "motion": False, "font": "large",
                "iconsGray": True, "flat": True},
    "default": {"preset": "default", "motion": True, "font": "normal",
                "iconsGray": True, "flat": False},
}

# ---- 判据阈值（按 60Hz AVD 标定；带外只记录不判红的部分见 judge()）----------
FULL_RAF_LO, FULL_RAF_HI = 48.0, 72.0        # 0–6s 满帧（实测 55~62/s）
FULL_FRAME_LO = 45.0
THROTTLE_RAF_MAX = 3.0                       # 6–10s 走 setTimeout，rAF 应≈0
THROTTLE_FRAME_LO, THROTTLE_FRAME_HI = 10.0, 20.0
STOPPED_RAF_MAX = 1.0                        # >10s 停循环
STOPPED_FRAME_MAX = 0.5                      # 每 10s 一帧 ≈0.1/s
MINIMAL_RAF_MAX = 10                         # minimal：首帧后全程允许的 rAF 总数
MINIMAL_FRAME_MAX = 6

SNAP_JS = "(window.__r4 && window.__r4.snap) ? window.__r4.snap() : null"
PROBE_PRESENT_JS = "!!(window.__r4 && window.__r4.__installed)"

PROBE_JS = r"""
(function () {
  if (window.__r4 && window.__r4.__installed) return "already";
  var S = {
    raf: 0, drawCalls: 0, fillPx: 0, visiblePx: 0, visibleInClipPx: 0,
    rasterPx: 0, rasterOutsidePx: 0,
    clippedDraws: 0, clippedPx: 0, partialDraws: 0,
    jsDrawsOutsideClip: 0, jsPxOutsideClip: 0,
    frames: 0, drawFrames: 0, offDraws: 0, offPx: 0,
    maxDrawPx: 0, rafLog: [], frameLog: [], t0: performance.now(),
    geo: null, geoAt: 0, resets: 0, clipSeen: false, lastClipRect: null
  };

  function mainCanvas() { return document.getElementById("petCanvas"); }

  /* 画布几何：canvasRect ∩ 视口 → 换算到画布像素坐标（只在主画布上判定） */
  function geom(force) {
    var now = performance.now();
    if (!force && S.geo && (now - S.geoAt) < 250) return S.geo;
    var c = mainCanvas();
    if (!c) return null;
    var r = c.getBoundingClientRect();
    var kx = c.clientWidth ? (c.width / c.clientWidth) : 1;
    var ky = c.clientHeight ? (c.height / c.clientHeight) : 1;
    var vw = window.innerWidth, vh = window.innerHeight;
    var il = Math.max(0, r.left), it = Math.max(0, r.top);
    var ir = Math.min(vw, r.right), ib = Math.min(vh, r.bottom);
    var vis = null;
    if (ir > il && ib > it) {
      vis = { x: (il - r.left) * kx, y: (it - r.top) * ky,
              x2: (ir - r.left) * kx, y2: (ib - r.top) * ky,
              area: ((ir - il) * kx) * ((ib - it) * ky) };
    }
    S.geo = {
      canvasW: c.width, canvasH: c.height,
      clientW: c.clientWidth, clientH: c.clientHeight,
      rect: [r.left, r.top, r.width, r.height],
      kx: kx, ky: ky, innerW: vw, innerH: vh,
      dpr: window.devicePixelRatio || 0,
      canvasArea: c.width * c.height,
      windowArea: vw * vh * kx * ky,
      visible: vis, visibleArea: vis ? ((vis.x2 - vis.x) * (vis.y2 - vis.y)) : 0,
      at: Math.round(now)
    };
    S.geoAt = now;
    return S.geo;
  }

  /* ---- 裁剪栈：pet.js renderFrame() 用 ctx.save();rect(visibleRect);clip() 取景 ---- */
  var CS = new WeakMap();      /* ctx -> {stack:[clipRect|null], last:{m,x,y,w,h}|null} */

  function ctxState(ctx) {
    var st = CS.get(ctx);
    if (!st) { st = { stack: [null], last: null }; CS.set(ctx, st); }
    return st;
  }

  function clipOf(ctx) { var st = ctxState(ctx); return st.stack[st.stack.length - 1]; }

  /* 矩形按当时变换四角映射到画布像素，取轴对齐包围盒 */
  function rectBox(m, x, y, w0, h0) {
    var cs = [
      [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f],
      [m.a * (x + w0) + m.c * y + m.e, m.b * (x + w0) + m.d * y + m.f],
      [m.a * x + m.c * (y + h0) + m.e, m.b * x + m.d * (y + h0) + m.f],
      [m.a * (x + w0) + m.c * (y + h0) + m.e, m.b * (x + w0) + m.d * (y + h0) + m.f]
    ];
    var minx = Math.min(cs[0][0], cs[1][0], cs[2][0], cs[3][0]);
    var maxx = Math.max(cs[0][0], cs[1][0], cs[2][0], cs[3][0]);
    var miny = Math.min(cs[0][1], cs[1][1], cs[2][1], cs[3][1]);
    var maxy = Math.max(cs[0][1], cs[1][1], cs[2][1], cs[3][1]);
    var area = (maxx - minx) * (maxy - miny);
    if (!(area > 0)) return null;
    return { x: minx, y: miny, x2: maxx, y2: maxy, area: area };
  }

  function rectHit(a, b) {
    if (!a || !b) return null;
    var x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
    var x2 = Math.min(a.x2, b.x2), y2 = Math.min(a.y2, b.y2);
    if (!(x2 > x && y2 > y)) return null;
    return { x: x, y: y, x2: x2, y2: y2, area: (x2 - x) * (y2 - y) };
  }

  function areaOf(r) { return r ? r.area : 0; }

  /* 一次 draw 三段面积：
     destArea = fillPx（目的矩形总面积）
     rasterPx = dest ∩ clip（Skia 真正会处理的像素）
     rasterOutsidePx = rasterPx − (dest ∩ clip ∩ 窗口可见区) */
  function track(ctx, x, y, w0, h0) {
    S.drawCalls++;
    var px = Math.abs((w0 || 0) * (h0 || 0));
    if (px > 0) S.fillPx += px;
    if (px > S.maxDrawPx) S.maxDrawPx = px;
    if (!ctx || !ctx.canvas || ctx.canvas !== mainCanvas()) {
      S.offDraws++; S.offPx += px;             /* 离屏缓冲（眼睛裁剪层）不计入判定 */
      return;
    }
    var g = geom(false);
    if (!g) return;
    var m;
    try { m = ctx.getTransform(); } catch (e) { return; }
    var box = rectBox(m, +x, +y, +w0, +h0);
    if (!box) return;
    var canvasRect = { x: 0, y: 0, x2: g.canvasW, y2: g.canvasH, area: g.canvasArea };
    var clip = clipOf(ctx);
    var clipR = rectHit(clip || canvasRect, canvasRect);   /* 无 clip 视为整块画布 */
    var raster = rectHit(box, clipR);
    var vis = rectHit(box, g.visible);
    var rvis = rectHit(raster, g.visible);
    S.rasterPx += areaOf(raster);
    S.rasterOutsidePx += Math.max(0, areaOf(raster) - areaOf(rvis));
    S.visiblePx += areaOf(vis);
    S.visibleInClipPx += areaOf(rvis);
    /* 被 clip 全部丢弃的 draw：只是 JS 侧出界调用，不会光栅化 */
    if (areaOf(raster) <= 0.5) { S.jsDrawsOutsideClip++; S.jsPxOutsideClip += box.area; }
    if (areaOf(vis) <= 0.5) { S.clippedDraws++; S.clippedPx += box.area; }
    else if (areaOf(vis) < box.area * 0.5) S.partialDraws++;
  }

  if (typeof CanvasRenderingContext2D !== "undefined" && CanvasRenderingContext2D.prototype) {
    var proto = CanvasRenderingContext2D.prototype;
    var origDraw = proto.drawImage, origFill = proto.fillRect, origClear = proto.clearRect;
    var origSave = proto.save, origRestore = proto.restore, origRect = proto.rect;
    var origClip = proto.clip, origBegin = proto.beginPath;

    /* save/restore 维护 clip 栈；rect 记最近矩形与当时变换；clip 压入交集；beginPath 清矩形 */
    proto.save = function () {
      try {
        var st = ctxState(this), top = st.stack[st.stack.length - 1];
        st.stack.push(top ? { x: top.x, y: top.y, x2: top.x2, y2: top.y2, area: top.area } : null);
      } catch (e) { /* 忽略 */ }
      return origSave.apply(this, arguments);
    };

    proto.restore = function () {
      try {
        var st = ctxState(this);
        if (st.stack.length > 1) st.stack.pop();
        st.last = null;
      } catch (e) { /* 忽略 */ }
      return origRestore.apply(this, arguments);
    };

    proto.beginPath = function () {
      try { ctxState(this).last = null; } catch (e) { /* 忽略 */ }
      return origBegin.apply(this, arguments);
    };

    proto.rect = function (x, y, w0, h0) {
      try {
        ctxState(this).last = { m: this.getTransform(), x: +x, y: +y, w: +w0, h: +h0 };
      } catch (e) { /* 忽略 */ }
      return origRect.apply(this, arguments);
    };

    proto.clip = function () {
      try {
        var st = ctxState(this), r = null;
        if (st.last) r = rectBox(st.last.m, st.last.x, st.last.y, st.last.w, st.last.h);
        if (r) {
          var top = st.stack[st.stack.length - 1];
          var next = top ? rectHit(top, r) : r;     /* clip 改写当前帧，不新增（save/restore 才是帧） */
          st.stack[st.stack.length - 1] = next || { x: 0, y: 0, x2: 0, y2: 0, area: 0 };
          if (this.canvas === mainCanvas()) {
            S.clipSeen = true;
            S.lastClipRect = [Math.round(r.x), Math.round(r.y),
                              Math.round(r.x2), Math.round(r.y2)];
          }
        }
      } catch (e) { /* 忽略 */ }
      return origClip.apply(this, arguments);
    };

    proto.drawImage = function () {
      try {
        var a = arguments, n = a.length, dx, dy, dw, dh;
        if (n >= 9) { dx = +a[5]; dy = +a[6]; dw = +a[7]; dh = +a[8]; }
        else if (n === 5) { dx = +a[1]; dy = +a[2]; dw = +a[3]; dh = +a[4]; }
        else if (n >= 4) { dx = +a[1]; dy = +a[2]; dw = +a[3]; dh = +a[4]; }
        else {
          var im = a[0] || {};
          dx = +a[1]; dy = +a[2];
          dw = im.naturalWidth || im.videoWidth || im.width || 0;
          dh = im.naturalHeight || im.videoHeight || im.height || 0;
        }
        track(this, dx, dy, dw, dh);
      } catch (e) { /* 探针绝不打断渲染 */ }
      return origDraw.apply(this, arguments);
    };

    proto.fillRect = function (x, y, w0, h0) {
      try { track(this, +x, +y, +w0, +h0); } catch (e) { /* 忽略 */ }
      return origFill.apply(this, arguments);
    };

    /* 帧边界：puppet.render() 每帧先 clearRect(0,0,W,H)。只有主画布整幅清除才算一帧 */
    proto.clearRect = function (x, y, w0, h0) {
      try {
        var c = mainCanvas();
        if (c && this.canvas === c && Math.abs(+w0 - c.width) <= 2 &&
            Math.abs(+h0 - c.height) <= 2) {
          S.frames++; S.drawFrames++;
          S.frameLog.push({ t: Math.round(performance.now() - S.t0), d: S.drawCalls });
          if (S.frameLog.length > 40) S.frameLog.shift();
        }
      } catch (e) { /* 忽略 */ }
      return origClear.apply(this, arguments);
    };
  }

  if (window.requestAnimationFrame) {
    var rafNative = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = function (cb) {
      return rafNative(function (ts) {
        S.raf++;
        S.rafLog.push({ t: Math.round(performance.now() - S.t0), ts: Math.round(ts) });
        if (S.rafLog.length > 30) S.rafLog.shift();
        return cb(ts);
      });
    };
  }

  /* 采样点通常在 renderFrame 之后（clip 已被 restore），故 clipRect 用最近一次观察值 */
  function clipInfo(g) {
    var out = { hasClip: false, clipSeen: S.clipSeen,
                clipRect: null, clipMatchesWindow: null };
    if (!g) return out;
    var c = mainCanvas(), ctx = null, clip = null;
    try { if (c) ctx = c.getContext("2d"); } catch (e) {}
    if (ctx) { try { clip = clipOf(ctx); } catch (e) {} }
    var r = null;
    if (clip) r = rectHit(clip, { x: 0, y: 0, x2: g.canvasW, y2: g.canvasH, area: g.canvasArea });
    if (!r && S.lastClipRect) {
      var lb = S.lastClipRect;
      r = rectBox({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
                  lb[0], lb[1], lb[2] - lb[0], lb[3] - lb[1]);
    }
    out.hasClip = !!clip;
    if (r) {
      out.clipRect = [Math.round(r.x), Math.round(r.y), Math.round(r.x2), Math.round(r.y2)];
      var hit = g.visible ? rectHit(r, g.visible) : null;
      /* 取景框 = 可见区：交集覆盖 98% 以上可见区，且 clip 面积不超过可见区 1.2 倍
         （容忍 pet.js CLIP_MARGIN_PX=6 的外扩） */
      out.clipMatchesWindow = !!(g.visible && r.area > 0
        && areaOf(hit) / g.visible.area > 0.98 && r.area / g.visible.area <= 1.2);
    }
    return out;
  }

  var api = {
    __installed: true,
    reset: function () {
      S.raf = 0; S.drawCalls = 0; S.fillPx = 0; S.visiblePx = 0;
      S.visibleInClipPx = 0; S.rasterPx = 0; S.rasterOutsidePx = 0;
      S.jsDrawsOutsideClip = 0; S.jsPxOutsideClip = 0;
      S.clippedDraws = 0; S.clippedPx = 0; S.partialDraws = 0;
      S.frames = 0; S.drawFrames = 0; S.offDraws = 0; S.offPx = 0;
      S.maxDrawPx = 0; S.rafLog.length = 0; S.frameLog.length = 0;
      S.t0 = performance.now(); S.geoAt = 0; S.resets++;
      return true;
    },
    snap: function () {
      var g = geom(true), ci = clipInfo(g), app = null, pet = null, dom = {}, ls = "";
      try { app = (window.Appearance && Appearance.get) ? Appearance.get() : null; } catch (e) {}
      try { pet = (window.__pet && __pet.state) ? __pet.state() : null; } catch (e) {}
      try {
        var d = document.documentElement.dataset;
        dom = { preset: d.preset, motion: d.motion, font: d.font,
                icons: d.icons, flat: d.flat };
      } catch (e) {}
      try { ls = localStorage.getItem("xiaoman_appearance_v1") || ""; } catch (e) {}
      return {
        t: Math.round(performance.now() - S.t0),
        raf: S.raf, drawCalls: S.drawCalls,
        fillPx: Math.round(S.fillPx), visiblePx: Math.round(S.visiblePx),
        visibleInClipPx: Math.round(S.visibleInClipPx),
        rasterPx: Math.round(S.rasterPx),
        rasterOutsidePx: Math.round(S.rasterOutsidePx),
        jsDrawsOutsideClip: S.jsDrawsOutsideClip,
        jsPxOutsideClip: Math.round(S.jsPxOutsideClip),
        /* clippedDraws/clippedPx 是旧字段，现只表示 JS 侧出界调用（已被 clip 丢弃） */
        clippedDraws: S.clippedDraws, clippedPx: Math.round(S.clippedPx),
        partialDraws: S.partialDraws, frames: S.frames, offDraws: S.offDraws,
        offPx: Math.round(S.offPx), maxDrawPx: Math.round(S.maxDrawPx),
        hasClip: ci.hasClip, clipSeen: ci.clipSeen, clipRect: ci.clipRect,
        clipMatchesWindow: ci.clipMatchesWindow,
        geometry: g,
        visibleShare: (g && g.canvasArea) ? +(g.visibleArea / g.canvasArea).toFixed(4) : null,
        canvasOverWindow: (g && g.windowArea) ? +(g.canvasArea / g.windowArea).toFixed(3) : null,
        app: app, pet: pet, dom: dom, localStorage: ls.slice(0, 200),
        hidden: document.hidden, ready: document.readyState,
        hasFocus: (typeof document.hasFocus === "function") ? document.hasFocus() : null,
        ua: navigator.userAgent,
        frameRecent: S.frameLog.slice(-10), rafRecent: S.rafLog.slice(-10),
        resets: S.resets
      };
    }
  };
  ["raf", "drawCalls", "fillPx", "visiblePx", "visibleInClipPx", "rasterPx",
   "rasterOutsidePx", "jsDrawsOutsideClip", "jsPxOutsideClip",
   "clippedDraws", "clippedPx", "frames"].forEach(function (k) {
    Object.defineProperty(api, k, { get: function () { return S[k]; }, enumerable: true });
  });
  window.__r4 = api;
  return "installed";
})()
"""

# 探针注入后统一把静止计时锚回 0：无条件 tap（interact→noteActivity 重置 6s 满帧窗口，
# 顺带 startLoop；气泡不画在 canvas 上，不影响计数），再 repaint 走原生补帧路径。
ANCHOR_JS = r"""
(function () {
  var p = window.__pet;
  if (!p || !p.state) return { ok: false, reason: "no-__pet" };
  var st = p.state(), tapped = false, repainted = false;
  try { if (p.tap) { p.tap(0.4); tapped = true; } } catch (e) {}
  try { if (p.repaint) { p.repaint("r4"); repainted = true; } } catch (e) {}
  return { ok: tapped || repainted, how: "tap+repaint",
           tapped: tapped, repainted: repainted, stateBefore: st };
})()
"""

# ---- 基础命令（口径同 emulator_float_check.py，此处自带实现避免导入副作用）----
def sh(cmd, timeout=60):
    """跑 adb/host 命令，返回 (rc, stdout+stderr)。"""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def shot(path):
    """adb screencap 落盘（bytes 管道，避免 shell 重定向写坏 PNG）。"""
    try:
        p = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=60)
        data = p.stdout or b""
        if len(data) > 1024 and data[:1] == b"\x89":
            with open(path, "wb") as f:
                f.write(data)
            return True
    except Exception:
        pass
    return False


_SCREEN = None


def screen_size():
    global _SCREEN
    if _SCREEN:
        return _SCREEN
    _, out = sh("adb shell wm size", timeout=30)
    m = re.search(r"(\d+)x(\d+)", out)
    _SCREEN = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
    return _SCREEN


def dump_windows():
    """dumpsys window windows → 本包窗口 [{ty, frame, visible}]。
    解析口径与 emulator_float_check.dumpsys_windows 一致（mFrame 优先，
    没有 mFrame 时用 mAttrs 请求框；gr=CENTER 的请求框换算成屏幕居中）。"""
    _, out = sh("adb shell dumpsys window windows", timeout=60)
    wins = []
    for blk in re.split(r"Window #\d+", out):
        if PKG not in blk:
            continue
        tm = re.search(r"\bty=([A-Z_0-9]+)", blk)
        frame, from_attrs = None, False
        fm = re.search(r"mFrame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", blk)
        if fm:
            frame = tuple(map(int, fm.groups()))
        else:
            am = re.search(r"mAttrs=\{\((-?\d+),(-?\d+)\)\((\d+)\s*x\s*(\d+|wrap)\)", blk)
            if am:
                x, y, w, hs = am.groups()
                h = 160 if hs == "wrap" else int(hs)
                frame = (int(x), int(y), int(x) + int(w), int(y) + int(h))
                from_attrs = True
        if not frame:
            continue
        gm = re.search(r"gr=([A-Z_ ]+?) sim=", blk)
        if from_attrs and gm and gm.group(1).strip() == "CENTER":
            sw, shh = screen_size()
            w, h = frame[2] - frame[0], frame[3] - frame[1]
            frame = ((sw - w) // 2, (shh - h) // 2, (sw + w) // 2, (shh + h) // 2)
        vis = "mViewVisibility=0x8" not in blk and "isVisible=false" not in blk
        wins.append({"ty": tm.group(1) if tm else "?", "frame": frame, "visible": vis})
    return wins


def overlay_wins(wins):
    return [w for w in wins if w["ty"] in ("APPLICATION_OVERLAY", "2038")]


def pet_frame(wins):
    """桌宠窗口：尺寸≈140x180dp 比例的可见 overlay（不写死像素，密度自适应）。
    迷你窗宽>600、气泡卡片高<300，都被比例/宽高区间排除；密度读不到时退化为比例判定。"""
    _, dens = sh("adb shell wm density", timeout=30)
    m = re.search(r"(\d+)", dens)
    den = int(m.group(1)) if m else 420
    ew, eh = PET_W_DP * den / 160.0, PET_H_DP * den / 160.0
    cands = []
    for w in overlay_wins(wins):
        if not w.get("visible", True):
            continue
        fw, fh = w["frame"][2] - w["frame"][0], w["frame"][3] - w["frame"][1]
        if fw <= 0 or fh <= 0:
            continue
        cands.append((w["frame"], fw, fh))
    for frame, fw, fh in cands:
        if (0.6 <= fw / float(fh) <= 0.95 and 150 <= fw <= 600
                and abs(fw - ew) <= max(40, ew * 0.22)
                and abs(fh - eh) <= max(60, eh * 0.22)):
            return frame
    for frame, fw, fh in cands:           # 密度对不上时的兜底：只看比例与量级
        if 0.6 <= fw / float(fh) <= 0.88 and 150 <= fw <= 600 and fh >= 300:
            return frame
    return None


def device_prepare():
    """常亮 + 拔电（电量模型不计充电）+ 悬浮窗权限 + 唤醒。"""
    sh("adb shell svc power stayon true", timeout=30)
    sh("adb shell dumpsys battery unplug", timeout=30)
    sh(f"adb shell appops set {PKG} SYSTEM_ALERT_WINDOW allow", timeout=30)
    sh("adb shell input keyevent 224", timeout=30)      # WAKEUP：上一个探针可能把屏灭了
    sh("adb shell wm dismiss-keyguard", timeout=30)
    _, batt = sh("adb shell dumpsys battery", timeout=30)
    powered = [ln.strip() for ln in batt.splitlines()
               if re.search(r"AC powered|USB powered|Wireless powered", ln)]
    return {"battery": powered}


def fast_eval(cdp, js, timeout=10):
    """不触发自动重连的 eval：返回 (ok, value|err)，采样循环靠它保时间线。"""
    if cdp is None or getattr(cdp, "ws", None) is None:
        return False, "no-ws"
    try:
        return True, cdp._eval(js, timeout=timeout)
    except Exception as e:
        return False, str(e)[:140]


def attach_pet(timeout=45):
    """连桌宠页 pet.html：overlay WebView 刚拉起时 target 可能还是空 url，重试到超时。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        cdp = Cdp(PET_CDP_PORT)
        try:
            return cdp, cdp.attach(url_filter="pet.html", timeout=15)
        except Exception as e:
            last = str(e)[:160]
            try:
                cdp.close()
            except Exception:
                pass
            time.sleep(2)
    raise RuntimeError(f"pet.html CDP 连接失败：{last}")


def attach_main(timeout=45):
    """连主界面 index.html：force-stop 后 devtools socket 可能还没重建，
    单次 attach 可能在「找不到 socket」上瞬时失败，这里重试到超时。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        cdp = Cdp(MAIN_CDP_PORT)
        try:
            return cdp, cdp.attach(url_filter="index.html", timeout=15)
        except Exception as e:
            last = str(e)[:160]
            try:
                cdp.close()
            except Exception:
                pass
            time.sleep(2)
    raise RuntimeError(f"index.html CDP 连接失败：{last}")


def wait_pet_ready(cdp, timeout=15):
    """等 pet.js mount 完成（vendor 加载 + 纹理解码）；未 ready 时开测没有意义。"""
    end = time.time() + timeout
    st = None
    while time.time() < end:
        ok, val = fast_eval(cdp, "(window.__pet && __pet.state) ? __pet.state() : null", timeout=6)
        st = val if ok else None
        if isinstance(st, dict) and st.get("mounted") and st.get("ready"):
            return True, st
        time.sleep(1)
    return False, st


def inject_probe(cdp):
    ok, val = fast_eval(cdp, PROBE_JS, timeout=15)
    return ok, val


def write_preset(name):
    """force-stop → 起主 Activity（不带 autostart）→ index.html CDP 写 localStorage → force-stop。
    回读只证明进程内已写入；落盘持久性最终由 pet.html 里的 Appearance.get() 交叉验证。"""
    want = PRESETS[name]
    sh(f"adb shell am force-stop {PKG}", timeout=30)
    time.sleep(1.5)
    rc, out = sh(f"adb shell am start -n {MAIN} -f 0x20000000", timeout=60)
    cdp = None
    r = None
    try:
        cdp, _ = attach_main(45)
        js = ("(function(){var p=%s;try{localStorage.setItem('%s',JSON.stringify(p));}"
              "catch(e){return {ok:false,err:String(e)};}"
              "var back=null;try{back=JSON.parse(localStorage.getItem('%s')||'null');}catch(e){}"
              "return {ok:!!back&&back.preset===p.preset,back:back};})()"
              % (json.dumps(want, ensure_ascii=False), APPEARANCE_KEY, APPEARANCE_KEY))
        r = cdp.eval(js, timeout=15) or {}
        print(f"[FLOAT-R4] preset-write {name}: ok={r.get('ok')} "
              f"value={json.dumps(r.get('back'), ensure_ascii=False)}", flush=True)
        time.sleep(6.5)        # Chromium LocalStorage 5s 后写盘；force-stop 是 SIGKILL，不能急
    finally:
        try:
            if cdp is not None:
                cdp.close()
        except Exception:
            pass
        sh(f"adb shell am force-stop {PKG}", timeout=30)
        time.sleep(2)
    return {"start": out.strip().splitlines()[-1][:120] if out.strip() else "",
            "write": r}


def start_pet(timeout=20):
    """带 autostart_float 拉起 → 等桌宠 overlay 出现（最多 timeout 秒）→ HOME 回桌面。"""
    rc, out = sh(f"adb shell am start -n {MAIN} --ez autostart_float true -f 0x20000000", timeout=60)
    start = out.strip().splitlines()[-1][:120] if out.strip() else ""
    end = time.time() + timeout
    frame, diag = None, []
    while time.time() < end:
        wins = dump_windows()
        diag = [w["frame"] for w in overlay_wins(wins)]
        frame = pet_frame(wins)
        if frame:
            break
        time.sleep(1.5)
    if not frame:
        return None, {"start": start, "overlayFrames": diag,
                      "error": f"{timeout}s 内没有桌宠 overlay（dumpsys overlay={diag}）"}
    sh("adb shell input keyevent 3", timeout=30)
    time.sleep(2)
    after = pet_frame(dump_windows())      # 回桌面后桌宠应仍然可见
    return frame, {"start": start, "overlayFrames": diag, "afterHome": after}


COUNTERS = ("raf", "drawCalls", "fillPx", "visiblePx", "visibleInClipPx",
            "rasterPx", "rasterOutsidePx", "jsDrawsOutsideClip", "jsPxOutsideClip",
            "clippedDraws", "clippedPx", "partialDraws", "frames", "offDraws", "offPx")


def read_snap(cdp, timeout=8):
    ok, val = fast_eval(cdp, SNAP_JS, timeout=timeout)
    if not ok:
        return None, str(val)[:140]
    if not isinstance(val, dict):
        return None, "snap-null"
    return val, None


def ensure_probe(cdp):
    """探针在就复用，不在（页面重载过）就重新注入。返回 (ok, detail)。"""
    ok, val = fast_eval(cdp, PROBE_PRESENT_JS, timeout=6)
    if ok and val:
        return True, "present"
    ok2, inj = inject_probe(cdp)
    if ok2 and inj in ("installed", "already"):
        return True, "reinjected"
    return False, str(inj if ok2 else val)[:140]


def attach_again(cdp):
    """断线重连：关旧 ws → 重新 attach（页面 reload 会换 target，需要重新找）。"""
    try:
        cdp.close()
    except Exception:
        pass
    return cdp.attach(url_filter="pet.html", timeout=30)


def sample(cdp, seconds):
    """清零计数后每秒读一次 snap → per-second 序列。CDP 断点/重载只记 gap 并尝试重连。
    返回 (rows, first_snap, last_snap, gaps, reinjects, baseline_ok)。"""
    fast_eval(cdp, "window.__r4.reset()", timeout=8)   # 计时锚点与 t0 对齐
    s0, err = read_snap(cdp)
    if s0 is None:
        raise RuntimeError(f"采样前读不到页内探针：{err}")
    prev = dict((k, int(s0.get(k) or 0)) for k in COUNTERS)
    prev_t = 0.0
    rows, gaps, reinj = [], 0, 0
    last, last_ok = s0, None
    t0 = time.monotonic()
    for i in range(1, seconds + 1):
        d = t0 + i - time.monotonic()
        if d > 0:
            time.sleep(d)
        now = round(time.monotonic() - t0, 3)
        smp, err = read_snap(cdp)
        if smp is None:
            gaps += 1
            try:
                attach_again(cdp)
                ok_pr, det = ensure_probe(cdp)
                if ok_pr:
                    smp, err = read_snap(cdp)
                    reinj += 1
                else:
                    err = f"探针重建失败:{det}"
            except Exception as e:
                err = f"重连失败:{str(e)[:110]}"
            if smp is not None:
                prev = None          # 计数已归零，本秒不做跨探针作差
        if smp is None:
            # 保留上一个好样本作差：下一个好样本的 dt 会跨过断点，速率仍然正确
            rows.append({"second": i, "gap": True, "err": str(err)[:140], "dt": 0.0})
            continue
        cur = dict((k, int(smp.get(k) or 0)) for k in COUNTERS)
        dt = max(0.05, now - prev_t)
        if prev is None:
            rows.append({"second": i, "gap": True, "err": "probe-reinjected",
                         "dt": round(dt, 3)})
        else:
            dlt = dict((k, max(0, cur[k] - prev[k])) for k in COUNTERS)
            row = {"second": i, "dt": round(dt, 3), "gap": False}
            row.update(dlt)
            row["rafPerSec"] = round(dlt["raf"] / dt, 2)
            row["framesPerSec"] = round(dlt["frames"] / dt, 2)
            row["fillPxPerSec"] = int(round(dlt["fillPx"] / dt))
            row["visiblePxPerSec"] = int(round(dlt["visiblePx"] / dt))
            row["rasterPxPerSec"] = int(round(dlt["rasterPx"] / dt))
            row["rasterOutsidePxPerSec"] = int(round(dlt["rasterOutsidePx"] / dt))
            row["clippedDrawsPerSec"] = round(dlt["clippedDraws"] / dt, 2)
            row["jsDrawsOutsideClipPerSec"] = round(dlt["jsDrawsOutsideClip"] / dt, 2)
            p = smp.get("pet") or {}
            row["petMode"] = p.get("mode")
            row["petLoop"] = p.get("loop")
            row["hidden"] = smp.get("hidden")
            rows.append(row)
            last_ok = smp
        prev = cur
        prev_t = now
        last = smp
    return rows, s0, (last_ok or last), gaps, reinj


# ---- 统计口径 ---------------------------------------------------------------
def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / float(len(vals)), 2) if vals else None


def total_of(rows, key):
    return sum(int(r.get(key) or 0) for r in rows if not r.get("gap"))


def win_stats(rows, lo, hi):
    """(lo, hi] 秒窗口的均值（第 i 行 = 第 i-1→i 秒的增量）。"""
    sel = [r for r in rows if not r.get("gap") and lo < r.get("second", 0) <= hi]
    return {
        "seconds": len(sel),
        "rafPerSec": _mean([r["rafPerSec"] for r in sel]),
        "framesPerSec": _mean([r["framesPerSec"] for r in sel]),
        "fillPxPerSec": _mean([r["fillPxPerSec"] for r in sel]),
        "visiblePxPerSec": _mean([r["visiblePxPerSec"] for r in sel]),
        "rasterPxPerSec": _mean([r["rasterPxPerSec"] for r in sel]),
        "rasterOutsidePxPerSec": _mean([r["rasterOutsidePxPerSec"] for r in sel]),
        "clippedDrawsPerSec": _mean([r["clippedDrawsPerSec"] for r in sel]),
        "jsDrawsOutsideClipPerSec": _mean([r["jsDrawsOutsideClipPerSec"] for r in sel]),
        "framesTotal": total_of(sel, "frames"),
        "rafTotal": total_of(sel, "raf"),
        "clippedDraws": total_of(sel, "clippedDraws"),
        "jsDrawsOutsideClip": total_of(sel, "jsDrawsOutsideClip"),
        "rasterOutsidePx": total_of(sel, "rasterOutsidePx"),
    }


def frame_intervals(snap, lo_ms=10000):
    """>10s 停机档的真实帧间隔（来自最近帧时间戳，验证「每 10s 补一帧」）。"""
    ts = [f.get("t") for f in (snap or {}).get("frameRecent") or []
          if isinstance(f.get("t"), (int, float)) and f["t"] > lo_ms]
    return [int(ts[i] - ts[i - 1]) for i in range(1, len(ts))]


def judge(name, rows, s0, s1, gaps, reinj, seconds):
    """判预设生效 / 三档静止 / 无效光栅化，返回报告片段（含 ok 与 reasons）。"""
    want = PRESETS[name]
    want_motion = bool(want["motion"])
    app = (s1.get("app") or s0.get("app") or {})
    pet = (s1.get("pet") or s0.get("pet") or {})
    geo = s1.get("geometry") or {}
    checks, reasons, warnings = {}, [], []

    valid = [r for r in rows if not r.get("gap")]
    checks["data"] = len(valid) >= max(5, int(seconds * 0.85)) and gaps <= max(2, seconds // 10)
    if not checks["data"]:
        reasons.append(f"有效样本 {len(valid)}/{seconds} 秒、CDP 断点 {gaps} 次、重注入 {reinj} 次")

    checks["preset_effective"] = bool(
        app.get("preset") == name and app.get("motion") == want_motion
        and pet.get("motion") == want_motion and pet.get("ready") is True)
    if not checks["preset_effective"]:
        reasons.append(f"预设未按预期生效：Appearance={app.get('preset')}/motion={app.get('motion')} "
                       f"__pet.motion={pet.get('motion')} ready={pet.get('ready')} "
                       f"localStorage={str(s1.get('localStorage'))[:90]}")

    checks["layout"] = bool(int(geo.get("clientW") or 0) > 0 and int(geo.get("clientH") or 0) > 0
                            and int(geo.get("visibleArea") or 0) > 0)
    if not checks["layout"]:
        reasons.append(f"画布布局异常：client={geo.get('clientW')}x{geo.get('clientH')} "
                       f"visibleArea={geo.get('visibleArea')}")

    windows = {}
    if want_motion:
        windows["full"] = win_stats(rows, 0, 6)
        windows["throttle"] = win_stats(rows, 6, 10)
        windows["stopped"] = win_stats(rows, 10, seconds)
        f, t, s = windows["full"], windows["throttle"], windows["stopped"]
        f_ok = (f["rafPerSec"] is not None and FULL_RAF_LO <= f["rafPerSec"] <= FULL_RAF_HI
                and f["framesPerSec"] is not None and f["framesPerSec"] >= FULL_FRAME_LO)
        t_ok = (t["rafPerSec"] is not None and t["rafPerSec"] <= THROTTLE_RAF_MAX
                and t["framesPerSec"] is not None
                and THROTTLE_FRAME_LO <= t["framesPerSec"] <= THROTTLE_FRAME_HI)
        stop_frame_cap = max(0, int(s["seconds"] // 10)) + 3 if s["seconds"] else 3
        s_ok = (s["rafPerSec"] is not None and s["rafPerSec"] <= STOPPED_RAF_MAX
                and s["framesPerSec"] is not None and s["framesPerSec"] <= STOPPED_FRAME_MAX
                and s["framesTotal"] <= stop_frame_cap
                and (s["framesTotal"] >= 1 or s["seconds"] < 20))
        checks["tier_full"] = bool(f_ok)
        checks["tier_throttle"] = bool(t_ok)
        checks["tier_stopped"] = bool(s_ok)
        if not f_ok:
            reasons.append(f"0–6s 未达满帧：rAF {f['rafPerSec']}/s、帧 {f['framesPerSec']}/s"
                           f"（期望 {FULL_RAF_LO:.0f}–{FULL_RAF_HI:.0f}；"
                           f"hidden={s1.get('hidden')} loop={pet.get('loop')} "
                           f"mode={pet.get('mode')}）")
        if not t_ok:
            reasons.append(f"6–10s 未降到 15fps：rAF {t['rafPerSec']}/s、帧 {t['framesPerSec']}/s"
                           f"（期望 rAF≤{THROTTLE_RAF_MAX}、帧 10–20）")
        if not s_ok:
            reasons.append(f">10s 未停循环：rAF {s['rafPerSec']}/s、帧 {s['framesTotal']} 帧/"
                           f"{s['seconds']}s（期望 rAF≈0、每 10s 一帧）")
    else:
        raf_total = total_of(rows, "raf")
        frames_total = total_of(rows, "frames")
        checks["tier_zero"] = raf_total <= MINIMAL_RAF_MAX and frames_total <= MINIMAL_FRAME_MAX
        windows["zero"] = {"rafTotal": raf_total, "framesTotal": frames_total,
                           "seconds": len(valid),
                           "rafPerSec": _mean([r["rafPerSec"] for r in valid]),
                           "framesPerSec": _mean([r["framesPerSec"] for r in valid])}
        if not checks["tier_zero"]:
            reasons.append(f"minimal 预设仍在出帧：rAF {raf_total} 次、帧 {frames_total} 次"
                           f"（上限 {MINIMAL_RAF_MAX}/{MINIMAL_FRAME_MAX}；"
                           f"hidden={s1.get('hidden')} motion={pet.get('motion')}）")

    # ---- 无效光栅化：dest ∩ clip ∩ 窗口可见区（三者都在画布像素坐标）----
    fill = total_of(rows, "fillPx")
    vis = total_of(rows, "visiblePx")
    off_px = total_of(rows, "offPx")
    page_fill = max(0, fill - off_px)          # 主画布（上屏）目的矩形面积；离屏眼缓冲另计
    raster_px = total_of(rows, "rasterPx")
    raster_out = total_of(rows, "rasterOutsidePx")
    js_out_draws = total_of(rows, "jsDrawsOutsideClip")
    js_out_px = total_of(rows, "jsPxOutsideClip")
    clipped_px = total_of(rows, "clippedPx")
    clipped_draws = total_of(rows, "clippedDraws")
    clip_ratio = round(clipped_px / float(page_fill), 5) if page_fill else None
    raster_out_ratio = round(raster_out / float(page_fill), 5) if page_fill else None
    clip_active = bool(s1.get("clipSeen") and s1.get("clipRect"))
    invalid = {
        "method": "每次 draw 用 ctx.getTransform() 把目的矩形四角映射到画布像素；"
                  "维护 save/restore/rect/clip 的 clip 栈，算 dest ∩ clip（rasterPx）"
                  "与 dest ∩ clip ∩ 窗口可见区（可见部分），差值即 rasterOutsidePx",
        "canvasOverWindow": s1.get("canvasOverWindow"),
        "visibleShareOfCanvas": s1.get("visibleShare"),
        "fillPxTotal": fill,
        "pageFillPxTotal": page_fill,
        "offDrawPxTotal": off_px,
        "visiblePxTotal": vis,
        "visibleInClipPxTotal": total_of(rows, "visibleInClipPx"),
        "visibleRatio": round(vis / float(page_fill), 5) if page_fill else None,
        "clipActive": clip_active,
        "clipSeen": s1.get("clipSeen"),
        "clipRect": s1.get("clipRect"),
        "clipMatchesWindow": s1.get("clipMatchesWindow"),
        "hasClipAtSnap": s1.get("hasClip"),
        "rasterPxTotal": raster_px,
        "rasterOutsidePxTotal": raster_out,
        "rasterOutsideRatio": raster_out_ratio,
        "jsDrawsOutsideClip": js_out_draws,
        "jsPxOutsideClip": js_out_px,
        "clippedDraws": clipped_draws,
        "clippedPx": clipped_px,
        "clippedRatio": clip_ratio,
        "partialDraws": total_of(rows, "partialDraws"),
        "offDrawCount": total_of(rows, "offDraws"),
        "verdict": "yes" if (raster_out_ratio is not None and raster_out_ratio > 0.01) else "no",
        "note": "pet.js renderFrame 的取景 clip 已把 3 倍画布裁到可见区；JS 侧整层绘制"
                "（clippedDraws/jsDrawsOutsideClip）仍会执行，但被 clip 丢弃、不会光栅化。"
                "只有 rasterOutsidePx/pageFill > 0.01（clip 内、却在窗口可见区之外的"
                "被光栅化像素）才判 invalid-rasterization=yes",
    }
    if invalid["verdict"] == "yes":
        warnings.append(f"clip 内仍有 {raster_out}px 落在窗口外被光栅化"
                        f"（占 pageFill {raster_out_ratio}）")
    elif js_out_draws:
        warnings.append(f"JS 侧有 {js_out_draws} 次 draw 整块出界（被取景 clip 丢弃，"
                        f"不构成无效光栅化）")
    # 光栅化判定只作结论上报，不参与退出码：300% 画布是刻意的取景设计，
    # 真正要看的是「取景 clip 内有没有被光栅化到窗口外的像素」，交给 verdict 字段消费。
    invalid["ok"] = invalid["verdict"] == "no"
    ok = all(bool(v) for v in checks.values())
    return {
        "ok": ok,
        "checks": checks,
        "reasons": reasons,
        "warnings": warnings,
        "windows": windows,
        "invalid_rasterization": invalid,
        "geometry": geo,
        "canvas": {
            "width": geo.get("canvasW"), "height": geo.get("canvasH"),
            "clientWidth": geo.get("clientW"), "clientHeight": geo.get("clientH"),
            "innerWidth": geo.get("innerW"), "innerHeight": geo.get("innerH"),
            "dpr": geo.get("dpr"), "rect": geo.get("rect"), "visibleRect": geo.get("visible"),
            "canvasArea": geo.get("canvasArea"), "windowArea": geo.get("windowArea"),
            "visibleArea": geo.get("visibleArea"),
        },
        "presetLive": {"appearance": app, "pet": pet, "dom": s1.get("dom"),
                       "localStorage": s1.get("localStorage"),
                       "pageHidden": s1.get("hidden")},
        "stoppedFrameIntervalsMs": frame_intervals(s1),
        "samples": rows,
    }


def logcat_summary():
    """logcat 摘要：崩溃行 + pet.js 探针行（清空后只覆盖本预设窗口）。"""
    _, out = sh("adb logcat -d -v brief -t 2000", timeout=60)
    lines = out.splitlines()
    fatal = [ln.strip()[:200] for ln in lines
             if "FATAL EXCEPTION" in ln or ("AndroidRuntime" in ln and PKG in ln)]
    pet = [ln.strip()[:200] for ln in lines if "[probe] pet-" in ln]
    return {"fatal": fatal[-5:], "petProbes": pet[-12:], "scannedLines": len(lines)}


def run_preset(name, seconds, out_dir):
    """单预设全流程：写 localStorage → 拉起桌宠 → HOME → CDP 探针 → 采样 → 判据。"""
    rec = {"name": name, "preset": PRESETS[name], "checks": {}, "reasons": [],
           "warnings": [], "ok": False}
    cdp = None
    try:
        rec["write"] = write_preset(name)
        frame, info = start_pet(20)
        rec["overlay"] = info
        if not frame:
            raise RuntimeError(info.get("error") or "桌宠 overlay 未出现")
        rec["petFrame"] = frame
        if info.get("afterHome") is None:
            rec["warnings"].append("HOME 后 dumpsys 未再识别到桌宠 overlay（继续用 CDP 复核）")

        cdp, url = attach_pet(45)
        rec["url"] = url
        ok_pr, det = ensure_probe(cdp)
        rec["probe"] = det
        if not ok_pr:
            raise RuntimeError(f"页内探针注入失败：{det}")

        ok_rd, st = wait_pet_ready(cdp, 15)
        rec["petReady"] = st
        if not ok_rd:
            raise RuntimeError(f"pet.js 15s 内未 ready（state={st}）")

        ok_a, anchor = fast_eval(cdp, ANCHOR_JS, timeout=10)
        rec["anchor"] = anchor if ok_a else {"ok": False, "err": str(anchor)[:140]}
        time.sleep(0.7)              # 让 repaint 的 120ms 补帧先落地，再清零计数
        rows, s0, s1, gaps, reinj = sample(cdp, seconds)
        verdict = judge(name, rows, s0, s1, gaps, reinj, seconds)
        verdict["warnings"] = rec["warnings"] + list(verdict.get("warnings") or [])
        rec.update(verdict)
        rec["gaps"] = gaps
        rec["probeReinjects"] = reinj
        rec["logcat"] = logcat_summary()

        name_png = f"emulator_float_r4_{name}.png"
        ok_shot = shot(name_png)
        rec["screenshot"] = {"path": name_png, "ok": ok_shot}
        if ok_shot and os.path.abspath(out_dir) != os.getcwd():
            try:
                copy_path = os.path.join(out_dir, name_png)
                shutil.copyfile(name_png, copy_path)
                rec["screenshot"]["copy"] = copy_path
            except Exception:
                pass
        if not ok_shot:
            rec["reasons"].append("截图失败（不影响测量，仅证据缺失）")
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
        rec["reasons"].append(rec["error"])
        rec["ok"] = False
        try:
            rec["logcat"] = logcat_summary()
        except Exception:
            pass
    finally:
        try:
            if cdp is not None:
                cdp.close()
        except Exception:
            pass
    return rec


def summary_line(rec):
    name = rec.get("name")
    if rec.get("error"):
        return f"[FLOAT-R4] preset={name} FAILED err={rec['error'][:120]}"
    w = rec.get("windows") or {}
    inv = rec.get("invalid_rasterization") or {}
    g = rec.get("geometry") or {}
    live = rec.get("presetLive") or {}
    pet = live.get("pet") or {}
    parts = [f"preset={name}",
             f"motion={pet.get('motion')}",
             f"ready={pet.get('ready')}",
             f"canvas={g.get('canvasW')}x{g.get('canvasH')}",
             f"canvasOverWindow={inv.get('canvasOverWindow')}",
             f"fillPx={inv.get('fillPxTotal')}",
             f"visibleRatio={inv.get('visibleRatio')}",
             f"clip={inv.get('clipActive')}/{inv.get('clipMatchesWindow')}",
             f"rasterOutsidePx={inv.get('rasterOutsidePxTotal')}",
             f"rasterOutsideRatio={inv.get('rasterOutsideRatio')}",
             f"jsDrawsOutsideClip={inv.get('jsDrawsOutsideClip')}",
             f"invalid-rasterization={inv.get('verdict')}"]
    if w.get("full"):
        parts += [f"fullRaf={w['full']['rafPerSec']}/s",
                  f"fullFrames={w['full']['framesPerSec']}/s",
                  f"throttleFrames={w['throttle']['framesPerSec']}/s",
                  f"throttleRaf={w['throttle']['rafPerSec']}/s",
                  f"stoppedRaf={w['stopped']['rafPerSec']}/s",
                  f"stoppedFrames={w['stopped']['framesTotal']}"]
    elif w.get("zero"):
        parts += [f"rafTotal={w['zero']['rafTotal']}", f"framesTotal={w['zero']['framesTotal']}"]
    return "[FLOAT-R4] " + " ".join(str(p) for p in parts)


def write_json(path, payload):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[FLOAT-R4] JSON 已写入 {path}", flush=True)


def meta_info(args):
    _, model = sh("adb shell getprop ro.product.model", timeout=30)
    _, sdk = sh("adb shell getprop ro.build.version.sdk", timeout=30)
    _, size = sh("adb shell wm size", timeout=30)
    _, dens = sh("adb shell wm density", timeout=30)
    _, rel = sh("adb shell getprop ro.build.version.release", timeout=30)
    return {
        "pkg": PKG, "activity": MAIN, "petUrl": "https://appassets.androidplatform.net/www/pet.html",
        "deviceModel": model.strip(), "android": f"{rel.strip()} (API {sdk.strip()})",
        "screen": size.strip(), "density": dens.strip(),
        "seconds": args.seconds, "presets": args.presets,
        "cdpPorts": {"main": MAIN_CDP_PORT, "pet": PET_CDP_PORT},
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main():
    ap = argparse.ArgumentParser(description="treehole R4 桌宠浮窗渲染验收（FLOAT_R4）")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--out", default=os.path.join("artifacts", "avd", "float_r4.json"))
    ap.add_argument("--presets", default="minimal,default")
    args = ap.parse_args()
    if args.seconds < 12:
        print("[FLOAT-R4] --seconds 小于 12s，>10s 停机档样本不足，仅记录", flush=True)

    names = [n.strip() for n in args.presets.split(",") if n.strip()]
    bad = [n for n in names if n not in PRESETS]
    if not names:
        names = ["minimal", "default"]
    if bad:
        print(f"[FLOAT-R4] 未知预设 {bad}（可选：{list(PRESETS)}）", flush=True)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "meta": meta_info(args),
        "config": {
            "seconds": args.seconds, "presets": names,
            "appearanceKey": APPEARANCE_KEY,
            "thresholds": {
                "fullRafPerSec": [FULL_RAF_LO, FULL_RAF_HI],
                "fullFramesPerSecMin": FULL_FRAME_LO,
                "throttleRafPerSecMax": THROTTLE_RAF_MAX,
                "throttleFramesPerSec": [THROTTLE_FRAME_LO, THROTTLE_FRAME_HI],
                "stoppedRafPerSecMax": STOPPED_RAF_MAX,
                "stoppedFramesPerSecMax": STOPPED_FRAME_MAX,
                "minimalRafTotalMax": MINIMAL_RAF_MAX,
                "minimalFramesTotalMax": MINIMAL_FRAME_MAX,
                "clippedDrawsMax": 0,
            },
        },
        "devicePrep": {},
        "presets": {},
        "errors": [],
    }
    try:
        payload["devicePrep"] = device_prepare()
        print(f"[FLOAT-R4] device ready battery={payload['devicePrep'].get('battery')}", flush=True)
        for name in names:
            if name not in PRESETS:
                payload["errors"].append(f"跳过未知预设 {name}")
                continue
            print(f"[FLOAT-R4] === 预设 {name} 开始（{args.seconds}s）===", flush=True)
            sh("adb logcat -c", timeout=30)      # logcat 摘要只覆盖本预设窗口
            rec = run_preset(name, args.seconds, out_dir)
            payload["presets"][name] = rec
            print(summary_line(rec), flush=True)
            for why in rec.get("reasons") or []:
                print(f"[FLOAT-R4]   - {why}", flush=True)
            for why in rec.get("warnings") or []:
                print(f"[FLOAT-R4]   ! {why}", flush=True)
    except Exception as e:
        payload["errors"].append(f"{type(e).__name__}: {e}"[:300])
        print(f"[FLOAT-R4] 未预期异常：{payload['errors'][-1]}", flush=True)
    finally:
        # 收尾：停桌宠服务（别把悬浮窗留给后续 job）
        sh(f"adb shell am start -n {MAIN} --ez float_stop true -f 0x20000000", timeout=30)
        time.sleep(1)
        sh(f"adb shell am force-stop {PKG}", timeout=30)

        presets = payload.get("presets") or {}
        failed = [n for n in names if n in presets and not presets[n].get("ok")]
        missing = [n for n in names if n not in presets]
        raster = [n for n in names if (presets.get(n) or {}).get("invalid_rasterization", {}).get("verdict") == "yes"]
        ok_all = bool(presets) and not failed and not missing and not payload["errors"]
        payload["verdict"] = {
            "ok": ok_all,
            "collected": sorted(presets.keys()),
            "failed": failed,
            "missing": missing,
            "invalidRasterization": "yes" if raster else "no",
            "invalidRasterizationPresets": raster,
            "presetChecks": {n: (presets[n] or {}).get("checks") for n in presets},
        }
        payload["logcat"] = {n: (presets[n] or {}).get("logcat") for n in presets}
        try:
            write_json(args.out, payload)
        except Exception as e:
            print(f"[FLOAT-R4] JSON 写入失败：{e}", flush=True)
        print(f"[FLOAT-R4] verdict={'ok' if ok_all else 'fail'} "
              f"invalid-rasterization={payload['verdict']['invalidRasterization']} "
              f"failed={failed or missing or payload['errors']}", flush=True)
    return 0 if payload.get("verdict", {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
