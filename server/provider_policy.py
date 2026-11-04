#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""provider_policy.py · 上游 provider 识别 + 倾诉内容隐私策略

背景（用户明确要求 / 审计 P0-1）：
  Kilo 的免 key trial 条款明确禁止把个人数据（对话内容）用于其服务，
  而默认配置恰好指向 api.kilo.ai。倾诉内容属于个人数据，不能"默认"发过去。

策略：
  - 命中 persona_data_blocked 的 provider 时，除非运维显式设置
    XIAOMAN_ALLOW_FREE_PERSONAL=1，否则服务端拒绝代理（kind="policy"）。
  - 客户端 api.js 的同名策略默认 false，会直接从用户自填 key → mock → offline
    降级，mock 仍是安全兜底。
"""
import os
import urllib.parse

# 明确禁止个人数据 / 仅限试用用途、且默认免 key 的 provider
PERSONAL_DATA_BLOCKED = {"kilo"}

# 允许覆盖（.env 里显式声明已获得合规授权时）
ALLOW_FREE_PERSONAL = (os.environ.get("XIAOMAN_ALLOW_FREE_PERSONAL") or "").strip().lower() in (
    "1", "true", "yes", "on")


def provider_for(base: str) -> str:
    """按 base_url 自动适配 provider：Kilo 免 key，其余走 Bearer key。"""
    b = (base or "").lower()
    if "api.kilo.ai" in b:
        return "kilo"
    if "openrouter.ai" in b:
        return "openrouter"
    return "openai"


def host_of(base: str) -> str:
    try:
        return urllib.parse.urlparse(base or "").hostname or ""
    except ValueError:
        return ""


def blocks_personal_data(base: str) -> bool:
    """该上游是否被策略禁止承载用户倾诉内容（默认禁止，显式授权后放行）。"""
    if ALLOW_FREE_PERSONAL:
        return False
    return provider_for(base) in PERSONAL_DATA_BLOCKED


def policy_payload(base: str) -> dict:
    """统一的 403 响应体：客户端据此降级到 mock（不重试、不换人格）。"""
    return {
        "error": "该免费端点的条款禁止承载个人倾诉数据；请自填 key 或使用本地 mock",
        "kind": "policy",
        "status": 403,
        "mode": "mock",
        "provider": provider_for(base),
        "host": host_of(base),
    }
