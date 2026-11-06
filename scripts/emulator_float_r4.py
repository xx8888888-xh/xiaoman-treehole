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

CLI：python3 scripts/emulator_float_r4.py --seconds 60 --rounds 5 \
       --out artifacts/avd/float_r4.json
  --rounds N（默认 5）：两预设各重复 N 轮，每轮独立 force-stop / start / 采样；
  每轮都做 ANR 取证（dumpsys activity anr / window / logcat crash+main /
  /data/anr traces pull），结果写进 JSON 的 anr 字段与 roundsSummary，
  末尾汇总 anrOccurrences。窗口若小于 60s，JSON 会显式标注
  「缩短了窗口，判据按 Ns 重算」。
退出码：0=所有轮次都拿到数据且三档/预设判据成立；1=有轮次失败（已得数据仍写 JSON）。
"""
import argparse
import base64
import json
import math
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

# ---- 尺寸 × DPI 几何矩阵（R6 canvas 后备缩到可见区后的回归） ----------------
GEOM_SIZES = ((140, 180), (180, 180), (240, 160))   # 窗口 CSS 尺寸（dp）
GEOM_DPRS = (2.0, 2.75, 3.0)                         # deviceScaleFactor
GEOM_SETTLE_S = 0.35                                 # resize debounce 120ms + 重绘余量
GEOM_ALPHA_MIN = 16                                  # 与 emulator_float_check 同口径
GEOM_BG_DIFF = 6                                     # 截图无 alpha 时的背景差分阈值
GEOM_CONTENT_MIN_RATIO = 0.02                        # 可见区内容占比下限（判整块空白）
GEOM_EDGE_OFFCENTER = 0.20                           # 左右自然透明边：内容偏中心阈值
GEOM_MIN_CONTENT_WIDTH = 0.30                        # 内容 bbox 至少占可见区宽度的比例
GEOM_COVER_TOL_PX = 1.0                              # 后备覆盖可见区的取整容差
GEOM_ASPECT_TOL = 0.02                               # 后备/CSS 宽高比偏差上限（2%）

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

# ---- 尺寸 × DPI 几何矩阵：读画布/后备/可见区/变换，并与 PetCore 的期望值对照 ----
# 期望值按 pet.js logicalBox() + PetCore.backingRect() 的同参数重算：
# cssH=stage.clientHeight*3、dpr 上限 2、canvasTop=8、margin=2。
GEOM_JS = r"""
(function () {
  var stage = document.getElementById("petStage");
  var c = document.getElementById("petCanvas");
  if (!stage || !c) return { ok: false, reason: "no-stage-or-canvas" };
  var mf = window.PUPPET_MANIFEST || {};
  var art = mf.canvas || {};
  var artW = +art.width || 0, artH = +art.height || 0;
  var cssH = (stage.clientHeight * 3) || 720;
  var dprCap = Math.min(2, window.devicePixelRatio || 1);
  var H = Math.max(240, Math.min(1600, Math.round(cssH * dprCap)));
  var W = Math.max(120, Math.round(H * artW / (artH || 1)));
  var cssW = cssH * W / H;
  var k = H / cssH;                       // 后备像素 / CSS 像素
  var exp = null;
  try {
    if (window.PetCore && PetCore.backingRect) {
      exp = PetCore.backingRect({
        fullW: W, fullH: H, canvasCssW: cssW, canvasCssH: cssH,
        windowCssW: stage.clientWidth, windowCssH: stage.clientHeight,
        canvasTop: 8, dpr: k, margin: 2
      });
    }
  } catch (e) { /* 期望值失败由 Python 侧记 error */ }
  var r = c.getBoundingClientRect();
  var t = null;
  try {
    var ctx = c.getContext("2d");
    var m = ctx.getTransform();
    t = { a: m.a, b: m.b, c: m.c, d: m.d, e: m.e, f: m.f };
  } catch (e) { /* 忽略 */ }
  return {
    ok: true,
    innerW: window.innerWidth, innerH: window.innerHeight,
    dpr: window.devicePixelRatio || 0,
    stage: { w: stage.clientWidth, h: stage.clientHeight },
    logical: { W: W, H: H, cssW: cssW, cssH: cssH, k: k, artW: artW, artH: artH },
    expected: exp,
    canvas: { w: c.width, h: c.height, clientW: c.clientWidth, clientH: c.clientHeight },
    css: { left: r.left, top: r.top, width: r.width, height: r.height },
    transform: t
  };
})()
"""

CANVAS_PNG_JS = r"""
(() => {
  const c = document.getElementById('petCanvas');
  if (!c) return '';
  try {
    const url = c.toDataURL('image/png');
    return url.length > 8000000 ? '' : url;
  } catch (e) { return ''; }
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


# ---- 尺寸 × DPI 几何矩阵 -----------------------------------------------------
def geometry_groups():
    """3 窗口尺寸 × 3 deviceScaleFactor = 9 组（尺寸外层、DPI 内层，顺序固定）。"""
    return [{"size": [int(w), int(h)], "dpr": float(d)}
            for w, h in GEOM_SIZES for d in GEOM_DPRS]


def geom_shot_name(w, h, dpr):
    """截图命名：geom_<w>x<h>_dpr<dpr>.png（dpr 保留 Python 浮点字面量）。"""
    return f"geom_{int(w)}x{int(h)}_dpr{float(dpr)}.png"


def _geom_eval(cdp, timeout=10):
    """矩阵专用 eval：断线自动重连（sample() 的 fast_eval 不重连是为了保时间线）。"""
    try:
        val = cdp.eval(GEOM_JS, timeout=timeout)
        return (val, None) if isinstance(val, dict) and val.get("ok") else (None, str(val)[:160])
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def geom_metrics(data, expect_dpr=None):
    """把一次 GEOM_JS 读数换算成断言：后备覆盖可见区 / 宽高比一致 / 无 0 负尺寸 / DPI 生效。"""
    canvas = data.get("canvas") or {}
    css = data.get("css") or {}
    exp = data.get("expected") or {}
    tr = data.get("transform") or {}
    vis = exp.get("visible") or {}
    cw, ch = _num(canvas.get("w")), _num(canvas.get("h"))
    css_w, css_h = _num(css.get("width")), _num(css.get("height"))
    kx, ky = _num(tr.get("a")), _num(tr.get("d"))
    bx = -_num(tr.get("e")) / kx if kx else 0.0
    by = -_num(tr.get("f")) / ky if ky else 0.0
    problems = []

    positive = bool(cw > 0 and ch > 0 and css_w > 0 and css_h > 0
                    and _num(vis.get("w")) > 0 and _num(vis.get("h")) > 0)
    if not positive:
        problems.append(f"尺寸非正：canvas={cw}x{ch} css={css_w}x{css_h} "
                        f"visible={vis.get('w')}x{vis.get('h')}")

    cover = bool(positive
                 and bx <= _num(vis.get("x")) + GEOM_COVER_TOL_PX
                 and by <= _num(vis.get("y")) + GEOM_COVER_TOL_PX
                 and bx + cw >= _num(vis.get("x")) + _num(vis.get("w")) - GEOM_COVER_TOL_PX
                 and by + ch >= _num(vis.get("y")) + _num(vis.get("h")) - GEOM_COVER_TOL_PX)
    if not cover:
        problems.append(f"后备未覆盖可见区：backing=({bx:.1f},{by:.1f},{cw}x{ch}) "
                        f"visible=({vis.get('x')},{vis.get('y')},{vis.get('w')},{vis.get('h')})")

    stretch = None
    aspect_ok = False
    css_scale = {}
    projection_ok = False
    if positive:
        stretch = round((cw / ch) / (css_w / css_h), 4)
        aspect_ok = abs(stretch - 1) <= GEOM_ASPECT_TOL
        css_scale = {"x": round(cw / css_w, 4), "y": round(ch / css_h, 4),
                     "k": _num(exp.get("k"))}
        k = css_scale["k"]
        projection_ok = bool(k > 0
                             and abs(css_scale["x"] / k - 1) <= GEOM_ASPECT_TOL
                             and abs(css_scale["y"] / k - 1) <= GEOM_ASPECT_TOL)
        if not aspect_ok:
            problems.append(f"宽高比不一致（拉伸）：canvas={stretch} css="
                            f"{round((css_w / css_h) / 1.0, 4)}")
        if not projection_ok:
            problems.append(f"CSS 盒未按 1:1 投影后备：scale={css_scale}")
    transform_ok = bool(kx and ky and abs(kx - 1) <= GEOM_ASPECT_TOL
                        and abs(ky - 1) <= GEOM_ASPECT_TOL
                        and abs(_num(tr.get("b"))) <= 0.01 and abs(_num(tr.get("c"))) <= 0.01)
    if not transform_ok:
        problems.append(f"ctx 变换含缩放/错切：{tr}")

    matches = bool(positive and abs(cw - _num(exp.get("w"))) <= 2
                   and abs(ch - _num(exp.get("h"))) <= 2
                   and abs(bx - _num(exp.get("x"))) <= 2
                   and abs(by - _num(exp.get("y"))) <= 2)
    if not matches:
        problems.append(f"后备与 PetCore.backingRect 期望不一致：actual=({bx:.1f},{by:.1f},"
                        f"{cw}x{ch}) expected=({exp.get('x')},{exp.get('y')},"
                        f"{exp.get('w')}x{exp.get('h')})")

    dpr_applied = None
    if expect_dpr is not None:
        dpr_applied = abs(_num(data.get("dpr")) - float(expect_dpr)) <= 0.01
        if not dpr_applied:
            problems.append(f"deviceScaleFactor 未生效：{data.get('dpr')} != {expect_dpr}")

    return {
        "canvas": {"w": int(cw), "h": int(ch),
                   "clientW": _num(canvas.get("clientW")), "clientH": _num(canvas.get("clientH"))},
        "css": {"left": round(_num(css.get("left")), 2), "top": round(_num(css.get("top")), 2),
                 "width": round(css_w, 2), "height": round(css_h, 2),
                 "scale": css_scale},
        "backing": {"x": round(bx, 2), "y": round(by, 2), "w": int(cw), "h": int(ch),
                    "fullW": int(_num(exp.get("fullW"))), "fullH": int(_num(exp.get("fullH"))),
                    "matchesExpected": matches},
        "visible": {"x": round(_num(vis.get("x")), 2), "y": round(_num(vis.get("y")), 2),
                    "width": round(_num(vis.get("w")), 2), "height": round(_num(vis.get("h")), 2),
                    "inBacking": {"x": round(_num(vis.get("x")) - bx, 2),
                                  "y": round(_num(vis.get("y")) - by, 2),
                                  "width": round(_num(vis.get("w")), 2),
                                  "height": round(_num(vis.get("h")), 2)}},
        "transform": {k: (round(v, 4) if isinstance(v, (int, float)) else v)
                      for k, v in tr.items()} if tr else None,
        "stretchRatio": stretch,
        "checks": {"positive": positive, "cover": cover, "aspect": aspect_ok,
                   "cssProjection": projection_ok, "transformUnit": transform_ok,
                   "backingMatches": matches, "dprApplied": dpr_applied},
        "ok": bool(positive and cover and aspect_ok and projection_ok
                   and transform_ok and matches
                   and (dpr_applied is not False)),
        "problems": problems,
        "viewport": {"w": _num(data.get("innerW")), "h": _num(data.get("innerH")),
                     "dpr": data.get("dpr")},
        "logical": data.get("logical") or {},
    }


def _num(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else 0.0
    except (TypeError, ValueError):
        return 0.0


def visible_css_rect(data):
    """画布 CSS 盒 ∩ 视口 = 木偶真正可见的 CSS 区域（截图取景用）。"""
    css = data.get("css") or {}
    iw, ih = _num(data.get("innerW")), _num(data.get("innerH"))
    left = max(0.0, _num(css.get("left")))
    top = max(0.0, _num(css.get("top")))
    right = min(iw, _num(css.get("left")) + _num(css.get("width")))
    bottom = min(ih, _num(css.get("top")) + _num(css.get("height")))
    if right <= left or bottom <= top:
        return None
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def blank_edges_png(path, region):
    """PIL 检查截图可见区四边：整条边 100% 透明（或与背景同色）→ 空白边。

    透明页按 alpha>16 计；Page.captureScreenshot 若出白底/黑底，退回
    「与左上角背景色的灰度差」同一口径（emulator_float_check.content_mask）。
    左右两条边允许自然透明（立绘比可见窗窄），只有内容 bbox 明显偏心/过窄
    时才把该侧计为空白边；上下两条边透明即判红。
    """
    from PIL import Image, ImageChops

    try:
        im = Image.open(path)
    except Exception as e:
        return {"ok": False, "error": f"open: {str(e)[:120]}"}
    W, H = im.size
    x0 = max(0, min(W - 1, int(round(_num(region.get("left"))))))
    y0 = max(0, min(H - 1, int(round(_num(region.get("top"))))))
    x1 = max(0, min(W, int(round(_num(region.get("left")) + _num(region.get("width"))))))
    y1 = max(0, min(H, int(round(_num(region.get("top")) + _num(region.get("height"))))))
    if x1 <= x0 or y1 <= y0:
        return {"ok": False, "error": f"region 非法：{region} image={W}x{H}"}

    rgba = im.convert("RGBA")
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha:
        alpha = rgba.getchannel("A")
        if alpha.getextrema()[0] > GEOM_ALPHA_MIN:
            has_alpha = False                      # 通体不透明 → 没有可用的 alpha 通道
    if has_alpha:
        mask = rgba.getchannel("A").point(lambda v: 255 if v > GEOM_ALPHA_MIN else 0)
        mode = "alpha"
        bg = None
    else:
        rgb = rgba.convert("RGB")
        bg = rgb.getpixel((0, 0))
        diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
        mask = diff.point(lambda v: 255 if v > GEOM_BG_DIFF else 0)
        mode = "bg-diff"

    crop = mask.crop((x0, y0, x1, y1))
    w, h = crop.size
    total = w * h
    content = crop.histogram()[255]
    ratio = round(content / float(total), 4) if total else 0.0
    edge = {
        "top": crop.crop((0, 0, w, 1)).histogram()[255],
        "bottom": crop.crop((0, h - 1, w, h)).histogram()[255],
        "left": crop.crop((0, 0, 1, h)).histogram()[255],
        "right": crop.crop((w - 1, 0, w, h)).histogram()[255],
    }
    edge_len = {"top": w, "bottom": w, "left": h, "right": h}
    raw = [k for k, n in edge.items() if n == 0]
    bbox = crop.getbbox()
    margins = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    fail = []
    if not bbox or ratio < GEOM_CONTENT_MIN_RATIO:
        fail = ["top", "bottom", "left", "right"]
    else:
        bx0, by0, bx1, by1 = bbox
        margins = {"left": bx0, "right": w - bx1, "top": by0, "bottom": h - by1}
        if edge["top"] == 0:
            fail.append("top")
        if edge["bottom"] == 0:
            fail.append("bottom")
        bw = bx1 - bx0
        off = (abs(margins["left"] - margins["right"]) > max(8, GEOM_EDGE_OFFCENTER * w)
               or bw < GEOM_MIN_CONTENT_WIDTH * w)
        if edge["left"] == 0 and off:
            fail.append("left")
        if edge["right"] == 0 and off:
            fail.append("right")
    def px(x, y):
        return 1 if crop.getpixel((x, y)) else 0
    samples = {  # 四角 + 四边中点（诊断用，1=可见区内有内容）
        "tl": px(0, 0), "tr": px(w - 1, 0),
        "bl": px(0, h - 1), "br": px(w - 1, h - 1),
        "topMid": px(w // 2, 0), "bottomMid": px(w // 2, h - 1),
        "leftMid": px(0, h // 2), "rightMid": px(w - 1, h // 2),
    }
    return {
        "ok": not fail,
        "mode": mode,
        "background": list(bg) if bg else None,
        "image": {"w": W, "h": H},
        "region": {"left": x0, "top": y0, "width": w, "height": h},
        "contentPx": content,
        "contentRatio": ratio,
        "contentBBox": list(bbox) if bbox else None,
        "margins": margins,
        "edgeOpaque": edge,
        "edgeLength": edge_len,
        "edgeSamples": samples,
        "blankEdges": fail,
        "blankEdgesRaw": raw,
    }


def capture_geom_png(cdp, path):
    """几何矩阵截图：Page.captureScreenshot 优先，失败退回 canvas.toDataURL。
    返回 (ok, source, detail)；source 决定可见区在图片里的坐标口径。"""
    detail = ""
    try:
        data = cdp.screenshot(path, timeout=20)
        if data and len(data) > 100:
            return True, "Page.captureScreenshot", f"{len(data)}B"
        detail = "Page.captureScreenshot 空"
    except Exception as e:
        detail = f"Page.captureScreenshot 不支持: {str(e)[:90]}"
    try:
        url = cdp.eval(CANVAS_PNG_JS, timeout=20) or ""
        if isinstance(url, str) and url.startswith("data:image/png;base64,"):
            raw = base64.b64decode(url.split(",", 1)[1])
            if len(raw) > 100:
                with open(path, "wb") as f:
                    f.write(raw)
                return True, "canvas.toDataURL", f"{len(raw)}B | {detail}"
        detail += " | canvas.toDataURL 空"
    except Exception as e:
        detail += f" | canvas 读回失败: {str(e)[:80]}"
    return False, "", detail


def _shot_region(data, source, image):
    """截图里「可见区」的像素矩形：页截图按 CSS→图片缩放，canvas 图按后备坐标。"""
    iw, ih = _num(image.get("w")), _num(image.get("h"))
    if source == "canvas.toDataURL":
        vis = ((geom_metrics(data).get("visible") or {}).get("inBacking") or {})
        return {"left": _num(vis.get("x")), "top": _num(vis.get("y")),
                "width": _num(vis.get("width")), "height": _num(vis.get("height"))}
    rect = visible_css_rect(data)
    vw, vh = _num(data.get("innerW")), _num(data.get("innerH"))
    if not rect or vw <= 0 or vh <= 0:
        return None
    sx, sy = iw / vw, ih / vh
    return {"left": rect["left"] * sx, "top": rect["top"] * sy,
            "width": rect["width"] * sx, "height": rect["height"] * sy}


def _restored_equal(before, after):
    """clearDeviceMetricsOverride 后视口/画布是否回到矩阵前。"""
    if not before or not after:
        return False
    bc, ac = before.get("canvas") or {}, after.get("canvas") or {}
    return (abs(_num(before.get("innerW")) - _num(after.get("innerW"))) <= 2
            and abs(_num(before.get("innerH")) - _num(after.get("innerH"))) <= 2
            and abs(_num(before.get("dpr")) - _num(after.get("dpr"))) <= 0.01
            and abs(_num(bc.get("w")) - _num(ac.get("w"))) <= 2
            and abs(_num(bc.get("h")) - _num(ac.get("h"))) <= 2)


def geometry_matrix_summary(groups, supported=True, restored=None, notes=None):
    """矩阵汇总：ok 只由「组是否 ok」和「是否支持/恢复」决定。"""
    failed = [{"size": g.get("size"), "dpr": g.get("dpr"),
               "reasons": g.get("problems") or ([g.get("error")] if g.get("error") else [])}
              for g in groups if not g.get("ok")]
    blank = [{"size": g.get("size"), "dpr": g.get("dpr"), "blankEdges": g.get("blankEdges")}
             for g in groups if g.get("blankEdges")]
    stretch = [{"size": g.get("size"), "dpr": g.get("dpr"),
                "stretchRatio": g.get("stretchRatio")}
               for g in groups if (g.get("checks") or {}).get("aspect") is False]
    shots = sum(1 for g in groups if (g.get("screenshot") or {}).get("ok"))
    planned = len(geometry_groups())
    return {
        "enabled": True,
        "supported": bool(supported),
        "restored": restored,
        "sizes": [list(s) for s in GEOM_SIZES],
        "dprs": [float(d) for d in GEOM_DPRS],
        "groups": planned,
        "groupsExecuted": len(groups),
        "ok": bool(supported and restored is not False and len(groups) == planned
                   and not failed),
        "failed": failed,
        "blankEdgeGroups": blank,
        "stretchFailedGroups": stretch,
        "screenshots": shots,
        "warnings": list(notes or []),
    }


def geometry_matrix(cdp, round_no, out_dir):
    """每轮 minimal 采样后跑 3×3 几何矩阵；结束必须 clear 覆盖，避免污染主采样。"""
    groups, notes = [], []
    supported = True
    before, be = _geom_eval(cdp)
    if before is None:
        notes.append(f"矩阵前读不到几何：{be}")
    for spec in geometry_groups():
        w, h = spec["size"]
        dpr = spec["dpr"]
        g = {"round": round_no, "size": [w, h], "dpr": dpr, "ok": False,
             "blankEdges": [], "stretchRatio": None}
        try:
            cdp._send("Emulation.setDeviceMetricsOverride",
                      {"width": w, "height": h, "deviceScaleFactor": dpr,
                       "mobile": False}, timeout=12)
            time.sleep(GEOM_SETTLE_S)          # 覆盖 resize 的 120ms debounce
            data, err = _geom_eval(cdp)
            if data is None:
                raise RuntimeError(f"几何读数失败：{err}")
            m = geom_metrics(data, expect_dpr=dpr)
            g.update(m)
            png = os.path.join(out_dir, geom_shot_name(w, h, dpr))
            ok_shot, source, detail = capture_geom_png(cdp, png)
            g["screenshot"] = {"path": geom_shot_name(w, h, dpr), "ok": ok_shot,
                               "source": source, "detail": detail}
            blank_ok = None
            if ok_shot:
                try:
                    from PIL import Image
                    iw, ih = Image.open(png).size
                except Exception as e:
                    g["screenshot"]["ok"] = False
                    g["screenshot"]["detail"] = f"PIL 读图失败：{str(e)[:100]}"
                else:
                    g["screenshot"].update({"image": {"w": iw, "h": ih}})
                    region = _shot_region(data, source, {"w": iw, "h": ih})
                    if region:
                        bl = blank_edges_png(png, region)
                        g["blank"] = bl
                        g["blankEdges"] = bl.get("blankEdges") or []
                        blank_ok = bool(bl.get("ok"))
                        if not bl.get("ok"):
                            if bl.get("error"):
                                g["problems"].append(f"空白边取证失败：{bl['error']}")
                            else:
                                g["problems"].append(
                                    f"空白边：{','.join(g['blankEdges'])} "
                                    f"(contentRatio={bl.get('contentRatio')})")
                            blank_ok = False
                    else:
                        g["problems"].append("可见区映射为 0，无法做空白边检查")
                        blank_ok = False
            else:
                g["blankUnverified"] = True
                notes.append(f"{w}x{h}@dpr{dpr} 截图不可用：{detail}")
            g["ok"] = bool(m["ok"] and blank_ok is not False)
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:170]}"
            g["error"] = msg
            g["problems"] = [msg]
            low = msg.lower()
            if "not found" in low or "unknown method" in low or "not supported" in low:
                supported = False
                notes.append("Emulation.setDeviceMetricsOverride 不被当前 WebView 支持")
            groups.append(g)
            if not supported:
                break
            continue
        groups.append(g)
    clear_err = None
    try:
        cdp._send("Emulation.clearDeviceMetricsOverride", {}, timeout=12)
    except Exception as e:
        clear_err = f"{type(e).__name__}: {str(e)[:150]}"
        notes.append(f"恢复原始 metrics 失败：{clear_err}")
    time.sleep(0.3)
    after, ae = _geom_eval(cdp)
    restored = _restored_equal(before, after)
    if clear_err is None and not restored:
        notes.append(f"clearDeviceMetricsOverride 后未回到矩阵前：{ae or ''}")
    summary = geometry_matrix_summary(groups, supported, restored, notes)
    summary["clearError"] = clear_err
    summary["before"] = {"viewport": {"w": _num((before or {}).get("innerW")),
                                      "h": _num((before or {}).get("innerH")),
                                      "dpr": (before or {}).get("dpr")},
                         "canvas": (before or {}).get("canvas")}
    summary["after"] = {"viewport": {"w": _num((after or {}).get("innerW")),
                                     "h": _num((after or {}).get("innerH")),
                                     "dpr": (after or {}).get("dpr")},
                        "canvas": (after or {}).get("canvas")}
    return {"groups": groups, "summary": summary}


def geometry_matrix_overview(rounds_all, enabled):
    """跨轮顶层 JSON：每组附 round，汇总所有轮次的矩阵结论。"""
    if not enabled:
        return {"enabled": False, "ok": True, "groups": 0, "rounds": [],
                "note": "已用 --no-size-matrix 关闭"}
    groups, summaries = [], []
    for rr in rounds_all:
        for n, rec in (rr.get("presets") or {}).items():
            if n != "minimal":
                continue
            for g in rec.get("geometryMatrix") or []:
                groups.append(dict(g, round=rr.get("round"))
                              if "round" not in g else g)
            if rec.get("geometryMatrixSummary"):
                summaries.append(dict(rec["geometryMatrixSummary"],
                                      round=rr.get("round")))
    failed = [{"round": g.get("round"), "size": g.get("size"), "dpr": g.get("dpr"),
               "reasons": g.get("problems") or [g.get("error")]}
              for g in groups if not g.get("ok")]
    ok = bool(groups) and not failed and all(s.get("ok") for s in summaries)
    return {
        "enabled": True,
        "ok": ok,
        "groups": len(geometry_groups()),
        "groupsExecuted": len(groups),
        "failed": failed,
        "blankEdgeGroups": [{"round": g.get("round"), "size": g.get("size"),
                            "dpr": g.get("dpr"), "blankEdges": g.get("blankEdges")}
                           for g in groups if g.get("blankEdges")],
        "stretchFailedGroups": [{"round": g.get("round"), "size": g.get("size"),
                                 "dpr": g.get("dpr"), "stretchRatio": g.get("stretchRatio")}
                                for g in groups
                                if (g.get("checks") or {}).get("aspect") is False],
        "screenshots": sum(1 for g in groups if (g.get("screenshot") or {}).get("ok")),
        "rounds": summaries,
    }


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


ANR_LOG_RE = re.compile(r"ANR in|Input dispatching timed out|am_anr|Application Not Responding")


def _indent_of(line):
    return len(line) - len(line.lstrip())


def anr_process_section(text, max_lines=80):
    """从 `dumpsys activity processes` 里截本包 ProcessRecord 段（最多 max_lines 行）。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if PKG in ln and "ProcessRecord" in ln:
            start = i
            break
    if start is None:
        return []
    base = _indent_of(lines[start])
    out = []
    for i, ln in enumerate(lines[start:start + max_lines], start):
        if ln.strip() and i > start and (_indent_of(ln) < base
                                         or (_indent_of(ln) == base
                                             and ln.lstrip().startswith("ProcessRecord"))):
            break
        out.append(ln.rstrip()[:240])
    return out


def anr_root():
    """尝试 adb root（AOSP/userdebug 镜像可 root；用户版镜像会失败）。

    只影响 /data/anr 的可读性，失败只记录原因、不影响判据。
    """
    out = {"attempted": True, "ok": False, "detail": {}}
    rc, txt = sh("adb root", timeout=30)
    out["detail"]["adbRoot"] = {"rc": rc, "out": (txt or "").strip()[:160]}
    if rc != 0:
        out["detail"]["reason"] = "adb root 命令失败（镜像可能不可 root）"
        return out
    sh("adb wait-for-device", timeout=60)
    time.sleep(1)
    rc2, uid = sh("adb shell id -u", timeout=20)
    uid_s = (uid or "").strip()
    if rc2 == 0 and uid_s.isdigit() and int(uid_s) == 0:
        out["ok"] = True
        out["detail"]["uid"] = uid_s
    else:
        out["detail"]["uid"] = {"rc": rc2, "out": uid_s[:80]}
        out["detail"]["reason"] = "adbd 未取得 root（用户版镜像无法读 /data/anr）"
    return out


def _ls_anr():
    rc, txt = sh("adb shell ls -l -t /data/anr", timeout=30)
    return {"cmd": "adb shell ls -l -t /data/anr", "rc": rc,
            "out": (txt or "").strip()}


def anr_traces(round_no, preset, out_dir):
    """把 /data/anr/traces.txt（或目录里最新文件）拉到 anr_traces_<round>[_preset].txt。

    优先 traces.txt；没有就按 `ls -t` 取最新文件。任何失败都写进 tried/reason。
    """
    tag = str(round_no) + (f"_{preset}" if preset else "")
    name = f"anr_traces_{tag}.txt"
    dst = os.path.join(out_dir, name) if out_dir else name
    rec = {"round": round_no, "preset": preset, "ok": False, "path": dst,
           "source": None, "bytes": 0, "tried": []}
    ls = _ls_anr()
    rec["tried"].append({"cmd": ls["cmd"], "rc": ls["rc"],
                         "out": (ls["out"] or "")[:400]})
    files, err = [], None
    for raw in (ls["out"] or "").splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("total"):
            continue
        if "Permission denied" in ln or "No such file" in ln or "Not a directory" in ln:
            err = ln[:200]
            continue
        parts = ln.split()
        fname = parts[-1] if parts else ""
        if fname in (".", ".."):
            continue
        if fname and ("/" not in fname or fname.startswith("/data/anr/")):
            files.append(fname)
    rec["tried"].append({"files": files[:20], "error": err})
    if not files:
        rec["reason"] = err or "adb shell ls /data/anr 没有可读文件（root 不可用或目录为空）"
        return rec
    src_name = "traces.txt" if "traces.txt" in files else files[0]
    src = f"/data/anr/{src_name}"
    rc, out = sh(f"adb pull {src} \"{dst}\"", timeout=90)
    rec["tried"].append({"cmd": f"adb pull {src}", "rc": rc, "out": (out or "").strip()[:200]})
    rec["source"] = src
    if rc == 0 and os.path.exists(dst):
        size = os.path.getsize(dst)
        rec["bytes"] = size
        rec["ok"] = size > 0
        if not rec["ok"]:
            rec["reason"] = "拉到的 traces 文件为空"
    else:
        rec["reason"] = "adb pull 失败（rc=%s）" % rc
    return rec


def anr_evidence(round_no=None, preset=None, out_dir=None):
    """采集 ANR 证据：系统 ANR 记录 / 窗口状态 / logcat crash+main / dumpsys processes。

    只取证、不改判据；任何一步失败都记进 queries，不抛异常。
    R5 曾在一张浮窗截图里看到系统 ANR 对话框，因此把证据固化进 JSON。
    检测到 ANR（或 -b crash 有本包记录）时追加：
      - dumpsys activity processes 的 com.xiaoman.treehole 段；
      - dumpsys window windows 的 ANR 窗口行（上面的 window 查询已含，另存 anrWindow）；
      - adb root 后的 /data/anr traces pull（见 anr_traces）。
    """
    out = {"round": round_no, "preset": preset, "queries": {}, "matches": {},
           "anrDetected": False}

    def run(label, cmd, timeout=40):
        try:
            rc, txt = sh(cmd, timeout=timeout)
            out["queries"][label] = {"rc": rc, "bytes": len(txt or "")}
            return txt or ""
        except Exception as exc:
            out["queries"][label] = {"error": str(exc)[:120]}
            return ""

    anr_dump = run("dumpsys_activity_anr", "adb shell dumpsys activity anr")
    window = run("dumpsys_window", "adb shell dumpsys window windows")
    crash = run("logcat_crash", "adb logcat -d -b crash -v brief -t 2000")
    log_lines = run("logcat_main", "adb logcat -d -v brief -t 4000")
    matches = {
        "anr_dump": [ln.strip()[:240] for ln in anr_dump.splitlines()
                     if ("ANR" in ln or PKG in ln)][:12],
        "window": [ln.strip()[:240] for ln in window.splitlines()
                   if ("Not Responding" in ln or "ANR" in ln)][:8],
        "crash": [ln.strip()[:240] for ln in crash.splitlines()
                  if ANR_LOG_RE.search(ln) or PKG in ln][:12],
        "logcat": [ln.strip()[:240] for ln in log_lines.splitlines()
                   if ANR_LOG_RE.search(ln)][:12],
    }
    out["matches"] = {k: v for k, v in matches.items() if v}
    out["anrDetected"] = bool(out["matches"])
    out["pattern"] = "ANR in|Input dispatching timed out|am_anr|Application Not Responding"
    if not out["anrDetected"]:
        return out

    # 检测到 ANR：补充进程段与 ANR 窗口行，并尽快抓 traces（堆叠 ANR 会覆盖旧文件）
    procs = run("dumpsys_activity_processes", "adb shell dumpsys activity processes", timeout=60)
    out["processSection"] = anr_process_section(procs) if procs else []
    out["anrWindow"] = [ln.strip()[:240] for ln in window.splitlines()
                        if PKG in ln and ("Not Responding" in ln or "ANR" in ln)][:8]
    out["root"] = anr_root()
    out["traces"] = anr_traces(round_no, preset, out_dir)
    return out


def memory_snapshot():
    """dumpsys meminfo 关键行：TOTAL PSS 与 Graphics/GL mtrack。

    canvas 后备缓冲从 469×1080 裁到可见区后，主要收益在图形内存与后备 clear/合成；
    这里按预设记录进程 TOTAL PSS 与 Graphics 行，供 R6「内存」项对照。
    """
    out = {"lines": []}
    try:
        rc, txt = sh(f"adb shell dumpsys meminfo {PKG}", timeout=40)
        if rc != 0:
            return {"error": f"rc={rc}"}
        for ln in (txt or "").splitlines():
            s = ln.strip()
            if (s.startswith("TOTAL") or s.startswith("Graphics")
                    or "GL mtrack" in s or "EGL mtrack" in s):
                out["lines"].append(s[:160])
        for s in out["lines"]:
            if s.startswith("TOTAL"):
                nums = [int(x) for x in re.findall(r"\d+", s)]
                if nums:
                    out["totalPssKb"] = nums[-1]
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


def run_preset(name, seconds, out_dir, round_no=1, size_matrix=True):
    """单预设全流程：写 localStorage → 拉起桌宠 → HOME → CDP 探针 → 采样 → 判据
    →（minimal 采样后）尺寸×DPI 几何矩阵 → ANR 取证。"""
    rec = {"name": name, "round": round_no, "seconds": seconds,
           "preset": PRESETS[name], "checks": {}, "reasons": [],
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
        # 几何矩阵只放在 minimal 的 60s 采样之后：不进入 fill/raster 计数窗口，
        # 结束前 clear 覆盖，default 预设/下一轮仍按原始窗口采样。
        if size_matrix and name == "minimal":
            try:
                gm = geometry_matrix(cdp, round_no, out_dir)
                rec["geometryMatrix"] = gm["groups"]
                rec["geometryMatrixSummary"] = gm["summary"]
                s = gm["summary"]
                for g in gm["groups"]:
                    if not g.get("ok"):
                        print(f"[FLOAT-R4] geom FAIL round={round_no} "
                              f"{g.get('size')}@dpr{g.get('dpr')} "
                              f"{'; '.join(g.get('problems') or [str(g.get('error'))])}"[:240])
                print(f"[FLOAT-R4] geometry-matrix round={round_no} ok={s.get('ok')} "
                      f"{s.get('groupsExecuted')}/{s.get('groups')} "
                      f"restored={s.get('restored')} shots={s.get('screenshots')}")
                if not s.get("ok"):
                    rec["reasons"].append(
                        "尺寸×DPI 几何矩阵未通过："
                        f"{len(s.get('failed') or [])} 组失败 / {s.get('groups')} 组")
                    rec["ok"] = False
                rec["warnings"] += list(s.get("warnings") or [])
            except Exception as e:
                rec["geometryMatrixSummary"] = {
                    "enabled": True, "ok": False,
                    "error": f"{type(e).__name__}: {str(e)[:180]}"}
                rec["reasons"].append(f"几何矩阵执行异常：{str(e)[:180]}")
                rec["ok"] = False
        rec["memory"] = memory_snapshot()
        rec["logcat"] = logcat_summary()
        rec["anr"] = anr_evidence(round_no, name, out_dir)
        if rec["anr"].get("anrDetected"):
            rec["warnings"].append(
                "检测到 ANR 证据（见 rec.anr.matches / processSection / traces）")

        name_png = f"emulator_float_r4_r{round_no}_{name}.png"
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


def key_metrics(rec):
    """单预设单轮的关键指标（roundsSummary 消费）。"""
    w = rec.get("windows") or {}
    inv = rec.get("invalid_rasterization") or {}
    anr = rec.get("anr") or {}
    gsum = rec.get("geometryMatrixSummary") or {}
    out = {
        "dataSeconds": len([r for r in (rec.get("samples") or []) if not r.get("gap")]),
        "gaps": rec.get("gaps"),
        "probeReinjects": rec.get("probeReinjects"),
        "invalidRasterization": inv.get("verdict"),
        "canvasOverWindow": inv.get("canvasOverWindow"),
        "visibleRatio": inv.get("visibleRatio"),
        "rasterOutsideRatio": inv.get("rasterOutsideRatio"),
        "anrDetected": bool(anr.get("anrDetected")),
        "geometryMatrixOk": gsum.get("ok"),
        "geometryMatrixGroups": gsum.get("groupsExecuted"),
        "geometryMatrixFailed": len(gsum.get("failed") or []),
        "geometryMatrixBlankEdges": len(gsum.get("blankEdgeGroups") or []),
        "geometryMatrixRestored": gsum.get("restored"),
    }
    if w.get("full"):
        out.update({
            "fullRafPerSec": w["full"].get("rafPerSec"),
            "fullFramesPerSec": w["full"].get("framesPerSec"),
            "throttleRafPerSec": w["throttle"].get("rafPerSec"),
            "throttleFramesPerSec": w["throttle"].get("framesPerSec"),
            "stoppedRafPerSec": w["stopped"].get("rafPerSec"),
            "stoppedFramesTotal": w["stopped"].get("framesTotal"),
        })
    elif w.get("zero"):
        out.update({"rafTotal": w["zero"].get("rafTotal"),
                    "framesTotal": w["zero"].get("framesTotal")})
    return out


def rounds_summary(rounds, names):
    """每轮 ok / anrDetected / 关键指标 / 截图 / traces。"""
    rows = []
    for rr in rounds:
        presets = rr.get("presets") or {}
        row = {
            "round": rr.get("round"),
            "ok": rr.get("ok"),
            "anrDetected": rr.get("anrDetected"),
            "durationSec": rr.get("durationSec"),
            "error": rr.get("error"),
            "anrTraces": (rr.get("anrTraces") or {}).get("path"),
            "anrTracesOk": (rr.get("anrTraces") or {}).get("ok"),
            "presets": {},
        }
        for n in names:
            rec = presets.get(n) or {}
            row["presets"][n] = {
                "ok": rec.get("ok"),
                "error": rec.get("error"),
                "checks": rec.get("checks"),
                "reasons": rec.get("reasons"),
                "warnings": rec.get("warnings"),
                "screenshot": (rec.get("screenshot") or {}).get("path"),
                "keys": key_metrics(rec),
            }
        rows.append(row)
    return rows


def anr_occurrences(rounds):
    """跨轮汇总 ANR 命中（每命中带轮次/预设/证据片段/traces 文件名）。"""
    out = []
    for rr in rounds:
        for n, rec in (rr.get("presets") or {}).items():
            anr = rec.get("anr") or {}
            if not anr.get("anrDetected"):
                continue
            tr = anr.get("traces") or {}
            out.append({
                "round": rr.get("round"), "preset": n,
                "matches": anr.get("matches"),
                "processSection": anr.get("processSection"),
                "anrWindow": anr.get("anrWindow"),
                "traces": tr.get("path"), "tracesOk": tr.get("ok"),
                "tracesSource": tr.get("source"), "tracesReason": tr.get("reason"),
                "screenshot": (rec.get("screenshot") or {}).get("path"),
            })
    return out


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
        "seconds": args.seconds, "rounds": args.rounds, "presets": args.presets,
        "cdpPorts": {"main": MAIN_CDP_PORT, "pet": PET_CDP_PORT},
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main():
    ap = argparse.ArgumentParser(description="treehole R4 桌宠浮窗渲染验收（FLOAT_R4）")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--rounds", type=int, default=5,
                    help="两预设各重复轮数（默认 5；小于 1 按 1 处理）")
    ap.add_argument("--out", default=os.path.join("artifacts", "avd", "float_r4.json"))
    ap.add_argument("--presets", default="minimal,default")
    ap.add_argument("--no-size-matrix", action="store_true",
                    help="关闭每轮 minimal 采样后的 3 窗口尺寸 × 3 DPI 几何矩阵（默认开启）")
    args = ap.parse_args()
    rounds_n = max(1, args.rounds)
    args.rounds = rounds_n                     # meta 与实际执行轮数保持一致
    if args.seconds < 12:
        print("[FLOAT-R4] --seconds 小于 12s，>10s 停机档样本不足，仅记录", flush=True)

    window_note = None
    if args.seconds < 60:
        window_note = (f"缩短了窗口：每预设 {args.seconds}s，判据按 {args.seconds}s 重算"
                       f"（停机档仅 {max(0, args.seconds - 10)}s，"
                       f"framesTotal 上限随窗口下调）")
        print(f"[FLOAT-R4] {window_note}", flush=True)

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
            "seconds": args.seconds, "presets": names, "rounds": rounds_n,
            "windowNote": window_note,
            "appearanceKey": APPEARANCE_KEY,
            "sizeMatrix": {
                "enabled": not args.no_size_matrix,
                "sizes": [[w, h] for w, h in GEOM_SIZES],
                "dprs": [float(d) for d in GEOM_DPRS],
                "combinations": len(geometry_groups()),
                "settleMs": int(GEOM_SETTLE_S * 1000),
                "method": "Emulation.setDeviceMetricsOverride(mobile=false)",
                "shot": "geom_<w>x<h>_dpr<dpr>.png",
            },
            "anrPattern": "ANR in|Input dispatching timed out|am_anr|Application Not Responding",
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
        "anrRoot": {},
        "rounds": [],
        "presets": {},
        "errors": [],
    }
    try:
        payload["devicePrep"] = device_prepare()
        print(f"[FLOAT-R4] device ready battery={payload['devicePrep'].get('battery')}", flush=True)
        # /data/anr 只有 root 可读：一次 root 即可，之后每轮只做 ls/pull
        payload["anrRoot"] = anr_root()
        print(f"[FLOAT-R4] ANR 取证 root：ok={payload['anrRoot'].get('ok')} "
              f"detail={json.dumps(payload['anrRoot'].get('detail'), ensure_ascii=False)[:200]}",
              flush=True)

        for r in range(1, rounds_n + 1):
            rrec = {"round": r, "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "presets": {}}
            t_round = time.monotonic()
            print(f"[FLOAT-R4] === 第 {r}/{rounds_n} 轮开始（每预设 {args.seconds}s）===",
                  flush=True)
            for name in names:
                if name not in PRESETS:
                    payload["errors"].append(f"跳过未知预设 {name}")
                    continue
                sh("adb logcat -c", timeout=30)      # logcat 摘要只覆盖本预设窗口
                rec = run_preset(name, args.seconds, out_dir, r,
                                 size_matrix=not args.no_size_matrix)
                rrec["presets"][name] = rec
                payload["presets"][name] = rec        # 顶层保留最后一轮，便于旧消费方
                print(f"[FLOAT-R4] round={r} " + summary_line(rec)[len("[FLOAT-R4] "):],
                      flush=True)
                for why in rec.get("reasons") or []:
                    print(f"[FLOAT-R4]   - {why}", flush=True)
                for why in rec.get("warnings") or []:
                    print(f"[FLOAT-R4]   ! {why}", flush=True)
            # 每轮结束统一抓一次 /data/anr（文件名 anr_traces_<round>.txt）
            rrec["anrTraces"] = anr_traces(r, None, out_dir)
            if rrec["anrTraces"].get("ok"):
                print(f"[FLOAT-R4] round={r} anr_traces -> "
                      f"{rrec['anrTraces']['path']} ({rrec['anrTraces']['bytes']}B, "
                      f"src={rrec['anrTraces']['source']})", flush=True)
            else:
                print(f"[FLOAT-R4] round={r} anr_traces 未取得："
                      f"{rrec['anrTraces'].get('reason')}", flush=True)

            presets = rrec["presets"]
            rrec["ok"] = bool(presets) and all(bool(p.get("ok")) for p in presets.values())
            rrec["anrDetected"] = any(
                bool((p.get("anr") or {}).get("anrDetected")) for p in presets.values())
            rrec["error"] = next((p.get("error") for p in presets.values()
                                  if p.get("error")), None)
            rrec["durationSec"] = round(time.monotonic() - t_round, 1)
            rrec["screenshots"] = [(p.get("screenshot") or {}).get("path")
                                   for p in presets.values()]
            payload["rounds"].append(rrec)
            print(f"[FLOAT-R4] === 第 {r}/{rounds_n} 轮结束 ok={rrec['ok']} "
                  f"anrDetected={rrec['anrDetected']} 用时 {rrec['durationSec']}s ===",
                  flush=True)
    except Exception as e:
        payload["errors"].append(f"{type(e).__name__}: {e}"[:300])
        print(f"[FLOAT-R4] 未预期异常：{payload['errors'][-1]}", flush=True)
    finally:
        # 收尾：停桌宠服务（别把悬浮窗留给后续 job）
        sh(f"adb shell am start -n {MAIN} --ez float_stop true -f 0x20000000", timeout=30)
        time.sleep(1)
        sh(f"adb shell am force-stop {PKG}", timeout=30)

        rounds_all = payload.get("rounds") or []
        presets = payload.get("presets") or {}
        occurrences = anr_occurrences(rounds_all)
        payload["roundsSummary"] = rounds_summary(rounds_all, names)
        payload["anrOccurrences"] = occurrences
        geom_enabled = not args.no_size_matrix
        geom_overview = geometry_matrix_overview(rounds_all, geom_enabled)
        if geom_enabled and "minimal" not in names:
            geom_overview.update({"ok": True, "groupsExecuted": 0,
                                  "note": "preset minimal 未启用，几何矩阵未执行"})
        elif geom_enabled and not geom_overview.get("groupsExecuted"):
            geom_overview["ok"] = False
            geom_overview.setdefault("note", "未拿到任何几何矩阵组数据")
        payload["geometryMatrix"] = [
            g for rr in rounds_all
            for g in ((rr.get("presets") or {}).get("minimal") or {}).get("geometryMatrix") or []
        ]
        payload["geometryMatrixSummary"] = geom_overview

        collected_rounds = [rr.get("round") for rr in rounds_all]
        missing_rounds = [r for r in range(1, rounds_n + 1) if r not in collected_rounds]
        failed_rounds = [rr.get("round") for rr in rounds_all if not rr.get("ok")]
        failed = [n for n in names if n in presets and not presets[n].get("ok")]
        missing = [n for n in names if n not in presets]
        raster = sorted({n for rr in rounds_all
                         for n, rec in (rr.get("presets") or {}).items()
                         if (rec.get("invalid_rasterization") or {}).get("verdict") == "yes"})
        ok_all = (bool(rounds_all) and len(rounds_all) == rounds_n
                  and not failed_rounds and not failed and not missing
                  and not payload["errors"]
                  and (not geom_enabled or geom_overview.get("ok") is True))
        if occurrences:
            anr_conclusion = (f"{rounds_n} 轮 × {args.seconds}s/预设复现 ANR {len(occurrences)} 次："
                              + "、".join(f"第{o['round']}轮/{o['preset']}" for o in occurrences))
        elif len(rounds_all) == rounds_n and not failed_rounds:
            anr_conclusion = (f"{rounds_n} 轮 × {len(names)} 预设 × {args.seconds}s 未复现 ANR"
                              f"（共 {rounds_n * len(names)} 次采样），判为环境偶发")
        else:
            anr_conclusion = (f"轮次未全部完成（{len(rounds_all)}/{rounds_n}），"
                              f"ANR 复现结论不成立")
        payload["verdict"] = {
            "ok": ok_all,
            "rounds": rounds_n,
            "roundsCollected": len(rounds_all),
            "failedRounds": failed_rounds,
            "missingRounds": missing_rounds,
            "anrReproduced": bool(occurrences),
            "anrOccurrences": len(occurrences),
            "anrConclusion": anr_conclusion,
            "perRoundOk": [{"round": rr.get("round"), "ok": rr.get("ok"),
                            "anrDetected": rr.get("anrDetected"),
                            "durationSec": rr.get("durationSec")} for rr in rounds_all],
            "collected": sorted(presets.keys()),
            "failed": failed,
            "missing": missing,
            "invalidRasterization": "yes" if raster else "no",
            "invalidRasterizationPresets": raster,
            "geometryMatrixOk": geom_overview.get("ok"),
            "geometryMatrixFailed": len(geom_overview.get("failed") or []),
            "geometryMatrixNote": geom_overview.get("note"),
            "presetChecks": {n: (presets[n] or {}).get("checks") for n in presets},
        }
        payload["logcat"] = {n: (presets[n] or {}).get("logcat") for n in presets}
        try:
            write_json(args.out, payload)
        except Exception as e:
            print(f"[FLOAT-R4] JSON 写入失败：{e}", flush=True)
        print(f"[FLOAT-R4] verdict={'ok' if ok_all else 'fail'} "
              f"rounds={len(rounds_all)}/{rounds_n} "
              f"failedRounds={failed_rounds or 'none'} "
              f"invalid-rasterization={payload['verdict']['invalidRasterization']} "
              f"anrReproduced={payload['verdict']['anrReproduced']} "
              f"anrOccurrences={len(occurrences)} "
              f"failed={failed or missing or payload['errors'] or 'none'}", flush=True)
        print(f"[FLOAT-R4] ANR 结论：{anr_conclusion}", flush=True)
    return 0 if payload.get("verdict", {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
