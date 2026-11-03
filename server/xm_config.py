#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xm_config.py · 服务端共享运行时配置（端口 / 绑定地址 / CORS 白名单）

设计约定：
  - 默认只监听 127.0.0.1（本机回环）；容器内由 compose 注入 XIAOMAN_BIND_HOST=0.0.0.0，
    再通过端口映射回到宿主机回环，避免默认把服务暴露到局域网。
  - CORS 默认只放行前端站点 http://127.0.0.1:8901；
    安卓 WebView（file:// 源）等额外来源请用 XIAOMAN_ALLOW_ORIGINS 显式追加。
"""
import os


def _port(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


BIND_HOST = (os.environ.get("XIAOMAN_BIND_HOST") or "127.0.0.1").strip()
MOCK_PORT = _port("XIAOMAN_MOCK_PORT", 8902)
TTS_PORT = _port("XIAOMAN_TTS_PORT", 8903)

# 逗号分隔的 Origin 白名单。
# 默认放行：本地前端站点、安卓 WebView（file:// 页面的 Origin 是字面量 "null"）、
# 安卓 WebViewAssetLoader 同源域（https://appassets.androidplatform.net）。
# 注意：安卓端已改为 https 同源加载，正常情况下不再产生 "null"；保留它是为了兼容
# 旧 APK / 其他 file:// 壳（例如悬浮窗 WebView）与本地调试。
_DEFAULT_ORIGINS = ",".join([
    "http://127.0.0.1:8901",
    "null",
    "https://appassets.androidplatform.net",
])
ALLOW_ORIGINS = tuple(
    o.strip() for o in (os.environ.get("XIAOMAN_ALLOW_ORIGINS") or _DEFAULT_ORIGINS).split(",")
    if o.strip()
)


def cors_headers(origin):
    """按白名单返回 CORS 响应头；未命中返回空 dict（浏览器会拦截跨源读取）。"""
    if origin and origin in ALLOW_ORIGINS:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}


def is_origin_allowed(origin):
    return bool(origin) and origin in ALLOW_ORIGINS
