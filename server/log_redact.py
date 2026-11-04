#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log_redact.py · 日志脱敏（纯标准库，零依赖）

审计 P1-5：对话原文/密钥/手机号/超长文本不得进入 docker 日志。
本模块只做「字符串 → 安全字符串」，不改变业务数据，也不读取任何配置。

三层处理（按顺序）：
  1) 抹掉请求行里的查询串与 HTTP 版本（旧 GET /tts?text=… 会整行落日志）
  2) 手机号打码（138****8000）、疑似密钥整体替换
  3) 连续非空白超长串（>=80 字）标成 [长文本N字已脱敏]，并限制单行日志长度

用法：
    from log_redact import redact_request_line, mask_sensitive, redact_log_line
    logger.error("HTTP %d: %s", code, mask_sensitive(message))
"""
import re

_QUERY_RE = re.compile(r"\?\S*")
_HTTP_VER_RE = re.compile(r"\s+HTTP/\d+(?:\.\d+)?")
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_KEY_RES = (
    re.compile(r"sk-[A-Za-z0-9_-]{6,}"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])(?:AIza|ghp_|gho_|xoxb-|xoxp-)[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9_+/=-]{32,})(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}"),
)
_LONG_RUN_RE = re.compile(r"\S{80,}")
MAX_LOG_LEN = 300


def mask_sensitive(value, max_len=MAX_LOG_LEN):
    """把长文本/疑似密钥/手机号替换成不可还原的占位符。"""
    if value is None:
        return "-"
    s = str(value).replace("\r", " ").replace("\n", " ")
    s = _PHONE_RE.sub(lambda m: f"{m.group(1)[:3]}****{m.group(1)[-4:]}", s)
    s = _KEY_RES[0].sub("sk-***", s)
    s = _KEY_RES[1].sub(lambda m: f"{m.group(1)}[REDACTED]", s)
    s = _KEY_RES[2].sub("[REDACTED_TOKEN]", s)
    s = _KEY_RES[3].sub("[REDACTED_TOKEN]", s)
    s = _LONG_RUN_RE.sub(lambda m: f"[长文本{len(m.group(0))}字已脱敏]", s)
    if max_len and len(s) > max_len:
        s = s[:max_len] + "…"
    return s.strip()


def redact_request_line(line):
    """只留「方法 + path」与状态码，查询串（可能含 text=…）整体删除。

    >>> redact_request_line('"GET /tts?text=你好 HTTP/1.1" 200 -')
    '"GET /tts" 200 -'
    """
    if not line:
        return "-"
    s = str(line).replace("\r", " ").replace("\n", " ")
    s = _QUERY_RE.sub("", s)
    s = _HTTP_VER_RE.sub("", s)
    return mask_sensitive(s)


def redact_log_line(line):
    """通用日志行脱敏：先按请求行处理，再打码密钥/手机号/长文本。"""
    if not line:
        return "-"
    s = str(line).replace("\r", " ").replace("\n", " ")
    s = _QUERY_RE.sub("", s)
    return mask_sensitive(s)


if __name__ == "__main__":
    import sys
    print(redact_log_line(" ".join(sys.argv[1:]) or "nothing to redact"))
