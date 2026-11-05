#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_human_feel.py · 真人感评测器（W20，可跑 mock / 真实模型两种后端）

为什么要有这个脚本：
  W11 的 84 条 100% 是 mock 模板引擎的成绩；模板引擎的分数不能当真实模型分。
  同一个评测集、同一套判定，必须能换后端重跑，并在报告里明确区分
  “mock 分”与“真实模型分”。

用法：
  python scripts/eval_human_feel.py --backend mock
  python scripts/eval_human_feel.py --backend api --model <模型名> --delay 1.5
  python scripts/eval_human_feel.py --backend api --limit 12 --categories 工作吐槽,拒绝说话
  python scripts/eval_human_feel.py --backend mock --json out.json --md out.md

判定分三层：
  1. 通用硬约束（长度/条数/套话/总结/说教/危机话术/跑题代码/复读）
  2. 场景专项（沉默轮、拒绝说话轮、记忆引用自然度、遗忘后消失）
  3. 反套路统计（建议出现率、四字总结共情率、高频开头、会话内重复开头）

退出码：通过率低于 --min-rate（默认 0.98）→ 1；后端不可用 → 2。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

CASES_FILE = SERVER_DIR / "tests" / "human_feel_cases.json"
LOG = "[eval]"

# ---------------- 判定词表（与提示词硬约束一一对应） ----------------
BOILERPLATE_RE = re.compile(
    "首先，|其次，|再次，|最后，|总之|综上|总的来说|希望对你有帮助|希望对你|"
    "如有需要|建议你|你可以试试|作为一个朋友|作为AI|作为你的朋友|我在这里陪着你")
SUMMARY_RE = re.compile(
    "听起来你|你的意思是|换句话说|所以你是说|也就是说|我理解你|我明白你的意思")
LECTURE_RE = re.compile(
    "你应该|你必须|你要坚强|想开点|看开点|别想太多|你这是|我应该教|大道理")
ADVICE_RE = re.compile(
    "建议|试试|试着|不妨|最好|你可以|你应该|你必须|去看看|去看医生|"
    "早睡|早点睡|作息|手机放远|晒晒太阳|调整心态|放松心态|多沟通|找人聊聊")
CRISIS_RE = re.compile("12356|心理援助|需要真人的帮助|心理咨询")
QUESTION_RE = re.compile("[?？]")
PUSH_RE = re.compile("说说|然后呢|接着|聊聊|展开|怎么了|发生什么|在吗|还有呢|继续|快说")
CODE_RE = re.compile("```|def |import |SELECT|GROUP BY|HAVING|function|class ")
PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()~～—\-_…·]+")

# 高频开头（客服腔 / 模板腔）：出现率单独统计，不直接判失败
OPENING_TICS = ("听起来", "我理解", "确实", "其实", "老实说", "说真的",
                "首先", "嗯，", "你说的")
# 四字总结式共情：把对方的处境概括成一句空话
SUMMARY_EMPATHY = ("太不容易了", "辛苦了", "心疼你", "确实不容易", "真的很难",
                   "我能理解", "我懂你", "抱抱你", "能理解你", "很正常",
                   "这很正常", "不容易啊", "太难受了")
# 记忆回指的“像人”标记：只在用户本轮没提该记忆、却主动提起时要求
RECALL_MARKERS = ("你上次", "你之前", "你说过", "你提过", "上次说", "之前说",
                  "那个", "那件事", "还在", "怎么样了")
MUTE_RE = re.compile(
    "别问|不想说|不想聊|不想提|别追问|换个话题|不想讲|算了|不说了|别打听|别提")

# ---------------- 免费额度白名单（只准免费，付费一律拒绝） ----------------
# 规则（用户硬约束）：只准用免费额度，任何付费模型都不许调。
# 免费范围：
#   1) 名字带 :free 的免费池模型（OpenRouter 等）；
#   2) 显式列在白名单里的免费模型/匿名端点名；
#   3) 不指定具体模型的匿名端点（model 为空 → 服务端默认免费池）。
# 不在白名单的模型在**发请求之前**直接拒绝，不做“先试一下”。
FREE_MODEL_WHITELIST = (
    "openai",  # pollinations 匿名端点：不指定具体模型，走官方免费池
)
FREE_MODEL_PATTERNS = (
    re.compile(r"^(?:[^\s/]+/)?[^\s/:]+:free$"),  # vendor/model:free 或 model:free
)
# 可能产生费用的请求选项：付费档路由（:nitro / provider pay）、联网检索等
PAID_OPTION_RE = re.compile(r":nitro|:online|:pay|provider:pay|web_search|:paid", re.I)
# 余额不足/要求充值的提示：命中即停用该家，绝不升级重试
BILLING_HINT_RE = re.compile(
    r"insufficient|balance|payment|credits|billing|充值|余额|欠费", re.I)
# max_tokens 上限：超过免费档的量级视为“可能被升级到付费档”，一律压回
MAX_TOKENS_CAP = 1500
DEFAULT_MAX_REQUESTS = 60


def is_free_model(model: str) -> bool:
    """模型是否在免费白名单内；名字里带付费路由直接判否。"""
    name = (model or "").strip()
    if not name:
        return True  # 匿名端点：不指定具体模型 = 官方免费池
    if PAID_OPTION_RE.search(name):
        return False
    if name.lower() in FREE_MODEL_WHITELIST:
        return True
    return any(p.match(name) for p in FREE_MODEL_PATTERNS)


def visible(text: str) -> str:
    return PUNCT_RE.sub("", (text or "").replace("||", ""))


def visible_len(text: str) -> int:
    return len(visible(text))


def reply_parts(reply: str) -> list:
    return [p.strip() for p in (reply or "").split("||") if p.strip()]


def assistant_text(payload: dict) -> str:
    parts = [payload.get("reply") or ""]
    hook = payload.get("hook")
    if hook:
        parts.append(str(hook))
    return "\n".join(parts)


def opening_key(text: str) -> str:
    """开头指纹：去掉标点后的前 6 个可见字。"""
    return visible(text)[:6]


def questions(text: str) -> int:
    return len(QUESTION_RE.findall(text or ""))


class Turn:
    __slots__ = ("user", "payload", "reply", "text", "parts", "index")

    def __init__(self, user: str, payload: dict, index: int):
        self.user = user
        self.payload = payload or {}
        self.reply = str(self.payload.get("reply") or "")
        self.text = assistant_text(self.payload)
        self.parts = reply_parts(self.reply)
        self.index = index


class CaseResult:
    def __init__(self, case: dict):
        self.case = case
        self.turns: list = []
        self.failures: list = []
        # 单次请求上限导致没跑完：不计入质量分，也不算失败
        self.limit_skipped = False

    @property
    def passed(self) -> bool:
        return not self.failures

    def fail(self, turn_index: int, reason: str):
        self.failures.append({"case": self.case["id"], "category": self.case["category"],
                              "turn": turn_index, "reason": reason})


# ---------------- 判定 ----------------
def _case_cfg(case: dict, universal: dict) -> dict:
    return {
        "max_chars": case.get("max_chars", universal["max_chars"]),
        "max_msgs": case.get("max_msgs", universal["max_msgs"]),
        "max_advice_turns": case.get("max_advice_turns", universal["max_advice_turns"]),
        "max_questions": case.get("max_questions_per_reply",
                                  universal["max_questions_per_reply"]),
    }


def check_turn(result: CaseResult, turn: Turn, prev: list, cfg: dict,
               universal: dict) -> bool:
    """单轮通用判定。返回本轮是否带建议（供统计）。"""
    case = result.case
    reply = turn.reply
    idx = turn.index + 1
    if not reply.strip():
        result.fail(idx, "空回复")
        return False

    if visible_len(reply) > cfg["max_chars"]:
        result.fail(idx, "超长 %d>%d：%s" % (visible_len(reply), cfg["max_chars"], reply))
    if len(turn.parts) > cfg["max_msgs"]:
        result.fail(idx, "条数 %d>%d" % (len(turn.parts), cfg["max_msgs"]))
    m = BOILERPLATE_RE.search(reply)
    if m:
        result.fail(idx, "套话“%s”：%s" % (m.group(0), reply))
    m = SUMMARY_RE.search(reply)
    if m:
        result.fail(idx, "总结/复述“%s”：%s" % (m.group(0), reply))
    m = LECTURE_RE.search(reply)
    if m:
        result.fail(idx, "说教“%s”：%s" % (m.group(0), reply))
    if case["category"] != "危机" and CRISIS_RE.search(reply):
        result.fail(idx, "非危机场景用了危机话术：%s" % reply)
    if questions(reply) > cfg["max_questions"]:
        result.fail(idx, "问号过多 %d>%d：%s" % (questions(reply), cfg["max_questions"], reply))
    for bad in case.get("forbid_any", []):
        if bad in reply:
            result.fail(idx, "出现禁用词“%s”：%s" % (bad, reply))
    if case["category"] == "跑题" and CODE_RE.search(reply):
        result.fail(idx, "跑题场景输出了代码/步骤")
    # 说明：模型偶尔不按 JSON 输出。线上 web/js/api.js 有纯文本兜底、不会拒答，
    # 所以协议不合规不计入“像不像人”的失败，只作为单独指标统计。

    # 沉默轮：短、不追问、不逼话、不连续两问
    if case.get("silence"):
        if visible_len(reply) > 40:
            result.fail(idx, "沉默轮过长 %d>40" % visible_len(reply))
        if len(turn.parts) > 2:
            result.fail(idx, "沉默轮条数 %d>2" % len(turn.parts))
        if QUESTION_RE.search(reply):
            result.fail(idx, "沉默轮追问：%s" % reply)
        m = PUSH_RE.search(reply)
        if m:
            result.fail(idx, "沉默轮逼话“%s”：%s" % (m.group(0), reply))

    # 拒绝说话：真的停下来，不打听细节，下一轮不翻旧账
    if case.get("refusal") or case["category"] == "拒绝说话":
        if visible_len(reply) > 40:
            result.fail(idx, "拒绝轮过长 %d>40：%s" % (visible_len(reply), reply))
        if QUESTION_RE.search(reply):
            result.fail(idx, "拒绝轮还在追问：%s" % reply)
        m = PUSH_RE.search(reply)
        if m:
            result.fail(idx, "拒绝轮逼话“%s”：%s" % (m.group(0), reply))
    for word in case.get("mute_any", []):
        if word in turn.text:
            result.fail(idx, "拒绝后仍翻旧账“%s”：%s" % (word, turn.text))

    # 记忆自然度：用户本轮没提、却被主动回溯 → 必须有“像人”的回指标记；
    # 同一轮最多引一条；被“忘了/别记”后必须消失。
    values = case.get("memory_values", [])
    if values:
        hit = [v for v in values if v and v in turn.text]
        if len(hit) > 1:
            result.fail(idx, "同一轮连引 %d 条记忆：%s" % (len(hit), turn.text))
        for v in hit:
            if v not in turn.user and not any(k in turn.text for k in RECALL_MARKERS):
                result.fail(idx, "记忆引用不像回忆（缺“你上次/之前说”）：%s" % turn.text)
    for start, vals in (case.get("forget_after") or {}).items():
        if turn.index >= int(start):
            for v in vals:
                if v in turn.text:
                    result.fail(idx, "说了“忘了”之后又提“%s”：%s" % (v, turn.text))

    # 复读：不逐字重复；不与上一轮同开头 4 字
    for p in prev:
        if reply == p.reply:
            result.fail(idx, "逐字复读历史回复：%s" % reply)
            break
    if prev and visible_len(prev[-1].reply) >= 4 and visible_len(reply) >= 4:
        if opening_key(prev[-1].reply)[:4] == opening_key(reply)[:4] \
                and opening_key(reply):
            result.fail(idx, "开头复读“%s”" % opening_key(reply)[:4])

    return bool(ADVICE_RE.search(reply))


def check_case_expect(result: CaseResult, universal: dict):
    case = result.case
    if not result.turns:
        return
    expect = case.get("expect_any")
    if expect:
        pool = result.turns if case.get("expect_scope") == "any" else result.turns[-1:]
        if not any(w in t.text for t in pool for w in expect):
            result.fail(len(result.turns),
                        "未体现期望内容（期望其一：%s；实际：%s）"
                        % ("/".join(expect), result.turns[-1].text))
    advice_turns = sum(1 for t in result.turns if ADVICE_RE.search(t.reply))
    limit = case.get("max_advice_turns", universal["max_advice_turns"])
    if advice_turns > limit:
        result.fail(0, "建议轮次 %d>%d（默认不给建议）" % (advice_turns, limit))


# ---------------- 后端 ----------------
class QuotaExhausted(Exception):
    """某一家后端的配额/鉴权失效，轮换时应切下一家。"""

    def __init__(self, provider: str, detail: str = ""):
        self.provider = provider
        self.detail = detail
        super().__init__("%s: %s" % (provider, detail))


class AllProvidersExhausted(Exception):
    """配置里的后端全部被判死，评测必须停下来，不能把后端失败算成质量分。"""

    def __init__(self, stats: list):
        self.stats = stats
        super().__init__("全部后端不可用（%d 家）" % len(stats))


class RequestLimitReached(Exception):
    """单个后端的请求数达到单次运行上限：本条用例不计质量分，换下一条/下一家。"""

    def __init__(self, provider: str, limit: int):
        self.provider = provider
        self.limit = limit
        super().__init__("%s 已达单次请求上限 %d" % (provider, limit))


DEFAULT_BACKENDS_FILE = ROOT / "scripts" / "backends.local.json"
EXAMPLE_BACKENDS_FILE = ROOT / "scripts" / "backends.example.json"


def load_providers(path=None) -> tuple:
    """读取多后端配置，返回 (providers, skipped)。

    优先级：--backends-file / HUMAN_FEEL_BACKENDS_FILE →
    HUMAN_FEEL_BACKENDS（内联 JSON 或文件路径）→ scripts/backends.local.json。
    key 只从环境变量（key_env）或本地被 gitignore 的配置文件读，
    仓库里的示例文件永远不含 key。
    """
    raw = os.environ.get("HUMAN_FEEL_BACKENDS", "").strip()
    cfg_path = Path(path or os.environ.get("HUMAN_FEEL_BACKENDS_FILE")
                    or DEFAULT_BACKENDS_FILE)
    data = None
    if raw.startswith("["):
        data = json.loads(raw)
    elif raw:
        cfg_path = Path(raw)
    if data is None and cfg_path.exists():
        # utf-8-sig：兼容 Windows PowerShell 写出的 BOM
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    if data is None:
        return [], []
    entries = data.get("backends", data) if isinstance(data, dict) else data
    providers, skipped = [], []
    for entry in entries:
        name = str(entry.get("name") or entry.get("model") or "provider")
        model = str(entry.get("model") or "")
        # 免费白名单：不在名单里的模型在发请求之前就拒绝，绝不“先试一下”
        if not is_free_model(model):
            skipped.append({"name": name, "reason": "非免费白名单模型（拒绝调用）：%s" % model})
            continue
        key = ""
        if entry.get("key_env"):
            key = os.environ.get(entry["key_env"], "")
        if not key and entry.get("key"):
            key = str(entry["key"])
        if not key and entry.get("use_default_credential"):
            try:
                import credentials
                key = credentials.api_key()
            except Exception:
                key = ""
        needs_key = entry.get("requires_key", True)
        if needs_key and not key:
            skipped.append({"name": name, "reason": "缺少 key（key_env 未设置）"})
            continue
        providers.append({
            "name": name,
            "base": str(entry.get("base") or "").rstrip("/"),
            "model": model,
            "key": key,
            "temperature": entry.get("temperature"),
            # 超过免费档量级的 max_tokens 一律压回上限，防止被计费到付费档
            "max_tokens": min(int(entry.get("max_tokens") or MAX_TOKENS_CAP),
                              MAX_TOKENS_CAP),
            "retries": entry.get("retries"),
            # 免费端点常按 IP 限速（如 pollinations 45 秒一次），
            # 配 delay 主动放慢，避免撞 402 后整家被判死
            "delay": entry.get("delay"),
        })
    return providers, skipped


class MockBackend:
    name = "mock"

    def __init__(self, cases_file: Path = CASES_FILE):
        import mock_api  # 延迟导入，--help 不依赖服务端
        self.mock = mock_api
        self.cases_file = cases_file

    def reset_case(self, case: dict):
        sid = "hf-eval-%s" % case["id"]
        self.mock._STATE._sessions.pop(sid, None)
        self.mock._STATE._loaded.discard(sid)
        random.seed(20261105 + zlib.crc32(case["id"].encode("utf-8")))
        self.sid = sid

    def chat(self, history: list, user: str, case: dict) -> dict:
        history.append({"role": "user", "content": user})
        return self.mock.make_reply(history, self.sid)

    def snapshot(self) -> dict:
        return {"memories": dict(self.mock._STATE.get_memories(self.sid)),
                "silent": self.mock._STATE.get_session(self.sid).get("no_probe_until", 0)}

    def cleanup(self):
        pass


class ApiBackend:
    """真实模型后端：走 OpenAI 兼容 /chat/completions。

    与线上服务端一致：system = prompt_core.system_prompt(memory_section=…)，
    记忆由 memory_slots.SlotStore 维护（每轮先取旧记忆，再 ingest 本轮）。
    """
    name = "api"

    def __init__(self, base: str, model: str, key: str, delay: float = 1.0,
                 timeout: float = 60.0, temperature: float = 0.85,
                 now: str = "周五 22:10", max_tokens: int = 1500,
                 retries: int = 3, dry_run: bool = False,
                 provider_name: str = "", raise_on_quota: bool = False,
                 max_requests: int = DEFAULT_MAX_REQUESTS):
        import prompt_core
        import memory_slots
        self.prompt_core = prompt_core
        self.memory_slots = memory_slots
        self.base = (base or "").rstrip("/")
        self.model = model
        if not is_free_model(model):
            # 最后一道闸：就算配置漏改，也不允许对非白名单模型发请求
            raise ValueError("非免费白名单模型，拒绝调用：%s" % model)
        self.key = key
        self.delay = delay
        self.timeout = timeout
        self.temperature = temperature
        self.now = now
        self.max_tokens = min(int(max_tokens or MAX_TOKENS_CAP), MAX_TOKENS_CAP)
        self.retries = retries
        self.dry_run = dry_run
        self.provider_name = provider_name or model or "api"
        self.raise_on_quota = raise_on_quota
        self.max_requests = max_requests or 0
        self.calls = 0
        self.errors = 0
        self.throttled = 0
        self.last_status = 0
        self.billing_stop = False
        self.slots = None
        self.sid = ""

    def reset_case(self, case: dict):
        self.slots = self.memory_slots.SlotStore()
        self.sid = "api-%s" % case["id"]

    def _url(self) -> str:
        return self.base + "/chat/completions"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": "xiaoman-human-feel-eval/1.0"}
        if self.key:
            h["Authorization"] = "Bearer %s" % self.key
        return h

    def _request(self, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._url(), data=data, method="POST",
                                     headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def chat(self, history: list, user: str, case: dict) -> dict:
        if self.max_requests and self.calls >= self.max_requests:
            raise RequestLimitReached(self.provider_name, self.max_requests)
        mem = self.slots.render(self.sid, user) if self.slots else ""
        system = self.prompt_core.system_prompt(memory_section=mem, now=self.now,
                                               strict_json=True)
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user})
        # 请求体只包含这四项：不带 provider 路由/联网检索等可能计费的选项
        body = {"model": self.model, "temperature": self.temperature,
                "max_tokens": min(self.max_tokens, MAX_TOKENS_CAP), "messages": messages}
        raw = ""
        last_err = ""
        reasoning_only = False
        for attempt in range(self.retries):
            if self.max_requests and self.calls >= self.max_requests:
                raise RequestLimitReached(self.provider_name, self.max_requests)
            try:
                self.calls += 1
                obj = self._request(body)
                choice = (obj.get("choices") or [{}])[0]
                raw = (choice.get("message") or {}).get("content") or ""
                if not raw and choice.get("finish_reason") == "length":
                    reasoning_only = bool((choice.get("message") or {}).get("reasoning"))
                break
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8")[:200]
                except Exception:
                    pass
                last_err = "HTTP %s %s" % (e.code, detail)
                self.last_status = e.code
                # 402 / 余额不足 / 要求充值：立刻停用该家，不重试、不升级模型
                if e.code == 402 or BILLING_HINT_RE.search(detail):
                    self.billing_stop = True
                    break
                if e.code in (401, 403):
                    break
                if e.code in (429, 503, 502):
                    self.throttled += 1
                    time.sleep(min(30, 4 * (attempt + 1)))
                    continue
                break
            except Exception as e:  # 超时/网络
                last_err = "%s: %s" % (type(e).__name__, e)
                time.sleep(min(10, 2 * (attempt + 1)))
        if not raw:
            self.errors += 1
            if self.raise_on_quota and (self.billing_stop or self.last_status in (401, 402, 403, 429)):
                raise QuotaExhausted(self.provider_name, last_err)
            if reasoning_only:
                return {"reply": "", "hook": None,
                        "_backend_error": "模型把 max_tokens 用在 reasoning，content 为空"}
            return {"reply": "", "hook": None, "_backend_error": last_err or "空响应"}
        payload = parse_model_json(raw)
        if not payload.get("_parse_error"):
            self.slots.ingest(self.sid, user)
            updates = payload.get("memory_updates")
            if isinstance(updates, dict) and updates:
                self.slots.apply_updates(self.sid, updates, source="model")
        if self.delay:
            time.sleep(self.delay)
        return payload

    def snapshot(self) -> dict:
        if not self.slots:
            return {}
        return {"slots": [{"key": s["key"], "value": s["value"]}
                          for s in self.slots.all(self.sid)]}

    def cleanup(self):
        pass


class RotatingBackend:
    """多免费后端轮换：429/402/401/403 或连续两次空响应就切下一家。

    统计每家被调用多少次、第一次 429 的时间与原因；全部判死后抛
    AllProvidersExhausted，由 evaluate 中止并把已完成条数写进报告，
    绝不把“后端挂了”算成“回复不像人”。
    """
    name = "rotate"

    def __init__(self, providers: list, delay: float = 1.0, timeout: float = 60.0,
                 temperature: float = 0.85, now: str = "周五 22:10",
                 max_tokens: int = 1500, retries: int = 3,
                 max_requests: int = DEFAULT_MAX_REQUESTS, factory=None):
        if not providers:
            raise ValueError("轮换后端至少需要一家可用 provider")
        paid = [p["name"] for p in providers if not is_free_model(p.get("model", ""))]
        if paid:
            # 白名单外的模型连构造都不允许，避免误调付费模型
            raise ValueError("轮换配置含非免费白名单模型：%s" % "、".join(paid))
        self.providers = providers
        self.opts = {"delay": delay, "timeout": timeout, "temperature": temperature,
                     "now": now, "max_tokens": min(int(max_tokens or MAX_TOKENS_CAP),
                                                   MAX_TOKENS_CAP),
                     "retries": retries}
        self.max_requests = max_requests or 0
        self.factory = factory or self._make_api
        self.idx = 0
        self.rotations = 0
        self.consecutive_fail = 0
        self.case = None
        self.current = None
        self.stats = [{"name": p["name"], "model": p.get("model", ""),
                       "base": p.get("base", ""), "calls": 0, "errors": 0,
                       "429": 0, "first_429_at": "", "status": "待用",
                       "reason": ""} for p in providers]
        self._activate(0, reset=False)

    def _make_api(self, provider):
        return ApiBackend(provider["base"], provider["model"], provider["key"],
                          delay=(provider.get("delay")
                                 if provider.get("delay") is not None
                                 else self.opts["delay"]),
                          timeout=self.opts["timeout"],
                          temperature=(provider.get("temperature")
                                       if provider.get("temperature") is not None
                                       else self.opts["temperature"]),
                          now=self.opts["now"],
                          max_tokens=(provider.get("max_tokens")
                                      or self.opts["max_tokens"]),
                          retries=(provider.get("retries")
                                   or self.opts["retries"]),
                          provider_name=provider["name"], raise_on_quota=True,
                          max_requests=self.max_requests)

    def _activate(self, idx, reset=True):
        self.idx = idx
        self.consecutive_fail = 0
        self.current = self.factory(self.providers[idx])
        self.stats[idx]["status"] = "在用"
        if reset and self.case is not None:
            self.current.reset_case(self.case)

    def _refresh(self):
        if not self.current:
            return
        st = self.stats[self.idx]
        st["calls"] = getattr(self.current, "calls", 0)
        st["errors"] = getattr(self.current, "errors", 0)
        st["429"] = getattr(self.current, "throttled", 0)

    def _exhausted(self) -> bool:
        """当前这家是否达到单次请求上限。"""
        return bool(self.max_requests and
                    getattr(self.current, "calls", 0) >= self.max_requests)

    def _switch(self, reset=True):
        self._refresh()
        if self.idx + 1 >= len(self.providers):
            return False
        self.rotations += 1
        self._activate(self.idx + 1, reset=reset)
        return True

    def _ensure_capacity(self):
        """用例开始时若当前后端已到上限，先切到还有额度的下一家。"""
        while self._exhausted():
            st = self.stats[self.idx]
            st["status"] = "达到单次上限"
            st["reason"] = "本家已发 %d 次请求（上限 %d），余下交下一家" % (
                getattr(self.current, "calls", 0), self.max_requests)
            if not self._switch(reset=False):
                raise AllProvidersExhausted(self.stats)

    def reset_case(self, case):
        self.case = case
        self._ensure_capacity()
        if self.current:
            self.current.reset_case(case)

    def chat(self, history, user, case):
        while True:
            self._refresh()
            if self._exhausted():
                st = self.stats[self.idx]
                st["status"] = "达到单次上限"
                st["reason"] = "本家已发 %d 次请求（上限 %d）" % (
                    getattr(self.current, "calls", 0), self.max_requests)
                if not self._switch():
                    # 本用例不再继续，交给 evaluate 记“未完成”；不是质量分
                    raise RequestLimitReached(self.providers[self.idx]["name"],
                                              self.max_requests)
                continue
            try:
                out = self.current.chat(history, user, case)
            except QuotaExhausted as e:
                st = self.stats[self.idx]
                st["status"] = ("配额或计费受限，已停用"
                                if getattr(self.current, "billing_stop", False)
                                else "配额/鉴权失效")
                st["reason"] = str(e.detail)[:160]
                if not st["first_429_at"]:
                    st["first_429_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._refresh()
                if not self._switch():
                    raise AllProvidersExhausted(self.stats)
                continue
            except RequestLimitReached:
                st = self.stats[self.idx]
                st["status"] = "达到单次上限"
                st["reason"] = "本家已发 %d 次请求（上限 %d）" % (
                    getattr(self.current, "calls", 0), self.max_requests)
                if not self._switch():
                    raise
                continue
            if out.get("_backend_error"):
                self.consecutive_fail += 1
                if self.consecutive_fail >= 2 and self._switch():
                    continue
                return out
            self.consecutive_fail = 0
            return out

    def snapshot(self):
        return self.current.snapshot() if self.current else {}

    def cleanup(self):
        pass

    def report(self):
        self._refresh()
        return {"rotations": self.rotations, "providers": self.stats}


def parse_model_json(raw: str) -> dict:
    """真实模型输出解析：容忍 ```json 围栏、前后缀、半截 JSON。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("reply"), str):
            return obj
    # 降级：取 JSON 里的 reply 字符串（哪怕整段不合法）
    m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        try:
            reply = json.loads('"%s"' % m.group(1))
        except ValueError:
            reply = m.group(1)
        return {"reply": reply, "_parse_error": True}
    return {"reply": text, "_parse_error": True}


# ---------------- 评测主流程 ----------------
def load_cases(path: Path = CASES_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_case(case: dict, backend, universal: dict) -> tuple:
    """返回 (CaseResult, 建议轮次数)；后端请求上限触发时 result.limit_skipped 置位。"""
    backend.reset_case(case)
    result = CaseResult(case)
    cfg = _case_cfg(case, universal)
    history: list = []
    prev: list = []
    advice_turns = 0
    for idx, user in enumerate(case["turns"]):
        try:
            payload = backend.chat(history, user, case)
        except RequestLimitReached:
            # 单次请求上限：本用例没跑完，不判质量分
            result.limit_skipped = True
            return result, advice_turns
        if payload.get("_backend_error"):
            result.fail(idx + 1, "后端不可用：%s" % payload["_backend_error"])
            break
        turn = Turn(user, payload, idx)
        result.turns.append(turn)
        if check_turn(result, turn, prev, cfg, universal):
            advice_turns += 1
        prev.append(turn)
        history.append({"role": "assistant", "content": turn.reply or " "})
    check_case_expect(result, universal)
    # “忘了/别记”之后不仅嘴上不提，存储里也必须真的没了（mock 会话记忆 / 服务端槽）
    forget = case.get("forget_after") or {}
    if forget:
        try:
            snap = json.dumps(backend.snapshot(), ensure_ascii=False)
        except Exception:
            snap = ""
        for vals in forget.values():
            for v in vals:
                if v in snap:
                    result.fail(0, "记忆存储里仍有“%s”（说了忘了却没真删）" % v)
    return result, advice_turns


def evaluate(cases_file: Path, backend, limit: int = 0, categories=None,
             skip_ids=None,
             on_case=None) -> dict:
    data = load_cases(cases_file)
    universal = data["universal"]
    cases = data["cases"]
    if categories:
        cats = set(categories)
        cases = [c for c in cases if c["category"] in cats]
    if limit:
        cases = cases[:limit]

    results = []
    advice_turns = 0
    total_cases = len(cases)
    aborted = None
    limit_skips = 0
    resume_skips = 0
    for i, case in enumerate(cases, 1):
        if skip_ids and case["id"] in skip_ids:
            # 断点续跑：已通过的用例不再重跑，也不重复计数（统计用历史明细合并）
            resume_skips += 1
            print("%s [%d/%d] %-4s %s（已完成，续跑跳过）"
                  % (LOG, i, total_cases, case["id"], "SKIP"), flush=True)
            continue
        try:
            res, adv = run_case(case, backend, universal)
        except AllProvidersExhausted as exc:
            aborted = {"after_cases": len(results), "providers": exc.stats}
            print("%s 中止：%s；已完成 %d/%d 条（后端失败不计入质量分）"
                  % (LOG, exc, len(results), total_cases), flush=True)
            break
        if getattr(res, "limit_skipped", False):
            # 单次请求上限：用例不计分，继续下一条（reset_case 时已切下一家）
            limit_skips += 1
            print("%s [%d/%d] %-4s %s（单次请求上限，未计入质量分）"
                  % (LOG, i, total_cases, case["id"], "SKIP"), flush=True)
            continue
        advice_turns += adv
        results.append(res)
        if not on_case:
            print("%s [%d/%d] %-4s %s" % (LOG, i, total_cases, case["id"],
                                          "PASS" if res.passed else "FAIL"),
                  flush=True)
        elif on_case:
            on_case(res)
    metrics = compute_metrics(results, advice_turns)
    data = {"results": results, "metrics": metrics, "universal": universal}
    if limit_skips:
        data["limit_skips"] = limit_skips
    if resume_skips:
        data["resume_skips"] = resume_skips
    if aborted:
        data["aborted"] = aborted
    return data


def case_stats(res) -> dict:
    """单条用例的可合并统计（续跑时用来把历史结果并进来）。"""
    turns = res.turns
    tic = Counter()
    for t in turns:
        for w in OPENING_TICS:
            if w in t.reply:
                tic[w] += 1
    seen, session_turns, repeat = set(), 0, 0
    for t in turns:
        key = opening_key(t.reply)
        if not key:
            continue
        session_turns += 1
        if key in seen:
            repeat += 1
        seen.add(key)
    recall_checks, recall_natural = 0, 0
    for v in res.case.get("memory_values", []):
        for t in turns:
            if v in t.text and v not in t.user:
                recall_checks += 1
                if any(k in t.text for k in RECALL_MARKERS):
                    recall_natural += 1
    return {
        "id": res.case["id"],
        "category": res.case["category"],
        "passed": res.passed,
        "turns": len(turns),
        "protocol_errors": sum(1 for t in turns if t.payload.get("_parse_error")),
        "advice_turns": sum(1 for t in turns if ADVICE_RE.search(t.reply)),
        "summary_empathy": sum(1 for t in turns
                               if any(w in t.reply for w in SUMMARY_EMPATHY)),
        "tic": dict(tic),
        "session_turns": session_turns,
        "repeat": repeat,
        "recall_checks": recall_checks,
        "recall_natural": recall_natural,
        "failures": list(res.failures),
    }


def metrics_from_stats(stats: list) -> dict:
    """把逐条统计合并成与 compute_metrics 同口径的指标。"""
    total_turns = sum(s["turns"] for s in stats)
    total = total_turns or 1
    advice = sum(s["advice_turns"] for s in stats)
    empathy = sum(s["summary_empathy"] for s in stats)
    tic = Counter()
    for s in stats:
        tic.update(s.get("tic") or {})
    session_turns = sum(s["session_turns"] for s in stats)
    repeat = sum(s["repeat"] for s in stats)
    checks = sum(s["recall_checks"] for s in stats)
    natural = sum(s["recall_natural"] for s in stats)
    return {
        "turns": total_turns,
        "protocol_errors": sum(s["protocol_errors"] for s in stats),
        "advice_turns": advice,
        "advice_rate": round(advice / total, 4),
        "summary_empathy_turns": empathy,
        "summary_empathy_rate": round(empathy / total, 4),
        "opening_tic_hits": dict(tic.most_common()),
        "opening_tic_rate": round(sum(tic.values()) / total, 4),
        "session_turns": session_turns,
        "repeat_opening": repeat,
        "repeat_opening_rate": round(repeat / (session_turns or 1), 4),
        "recall_checks": checks,
        "recall_natural": natural,
        "recall_natural_rate": round(natural / (checks or 1), 4),
    }


def summarize_stats(stats: list, metrics: dict, aborted=None,
                    limit_skips: int = 0) -> dict:
    """用逐条统计汇总（续跑合并后走这条，分母只含真正跑完的用例）。"""
    by_cat = OrderedDict()
    reasons = Counter()
    failures = []
    for s in stats:
        st = by_cat.setdefault(s["category"], {"passed": 0, "total": 0})
        st["total"] += 1
        if s["passed"]:
            st["passed"] += 1
        else:
            failures.append({"case": s["id"], "category": s["category"],
                             "reasons": [f["reason"] for f in s.get("failures", [])]})
            for f in s.get("failures", []):
                reasons[f["reason"].split("：")[0].split("“")[0]] += 1
    passed = sum(1 for s in stats if s["passed"])
    return {
        "cases": len(stats),
        "passed": passed,
        "rate": round(passed / (len(stats) or 1), 4),
        "by_category": by_cat,
        "failure_reasons": dict(reasons.most_common()),
        "failures": failures,
        "metrics": metrics,
        "aborted": aborted,
        "limit_skips": limit_skips,
    }


def compute_metrics(results: list, advice_turns: int) -> dict:
    """反套路指标：建议率、总结式共情、高频开头、会话内重复开头、记忆引用。"""
    turns = [t for r in results for t in r.turns]
    total = len(turns) or 1
    advice_rate = advice_turns / total
    summary_empathy = sum(1 for t in turns if any(w in t.reply for w in SUMMARY_EMPATHY))
    tic_hits = Counter()
    for t in turns:
        for w in OPENING_TICS:
            if w in t.reply:
                tic_hits[w] += 1

    # 会话内重复开头：同一会话里，后出现的开头指纹与之前的雷同
    repeat = 0
    session_turns = 0
    for r in results:
        seen = set()
        for t in r.turns:
            key = opening_key(t.reply)
            if not key:
                continue
            session_turns += 1
            if key in seen:
                repeat += 1
            seen.add(key)

    # 记忆引用自然度：主动回溯时带“你上次/之前说”的比例
    recall_checks, recall_natural = 0, 0
    for r in results:
        for v in r.case.get("memory_values", []):
            for t in r.turns:
                if v in t.text and v not in t.user:
                    recall_checks += 1
                    if any(k in t.text for k in RECALL_MARKERS):
                        recall_natural += 1

    return {
        "turns": len(turns),
        "protocol_errors": sum(1 for t in turns if t.payload.get("_parse_error")),
        "advice_turns": advice_turns,
        "advice_rate": round(advice_rate, 4),
        "summary_empathy_turns": summary_empathy,
        "summary_empathy_rate": round(summary_empathy / total, 4),
        "opening_tic_hits": dict(tic_hits.most_common()),
        "opening_tic_rate": round(sum(tic_hits.values()) / total, 4),
        "session_turns": session_turns,
        "repeat_opening": repeat,
        "repeat_opening_rate": round(repeat / (session_turns or 1), 4),
        "recall_checks": recall_checks,
        "recall_natural": recall_natural,
        "recall_natural_rate": round(recall_natural / (recall_checks or 1), 4),
    }


def summarize(data: dict) -> dict:
    results = data["results"]
    passed = [r for r in results if r.passed]
    by_cat = OrderedDict()
    for r in results:
        cat = r.case["category"]
        st = by_cat.setdefault(cat, {"passed": 0, "total": 0})
        st["total"] += 1
        if r.passed:
            st["passed"] += 1
    reasons = Counter(f["reason"].split("：")[0].split("“")[0] for r in results
                      for f in r.failures)
    return {
        "cases": len(results),
        "passed": len(passed),
        "rate": round(len(passed) / (len(results) or 1), 4),
        "by_category": by_cat,
        "failure_reasons": dict(reasons.most_common()),
        "failures": [{"case": r.case["id"], "category": r.case["category"],
                      "reasons": [f["reason"] for f in r.failures]}
                     for r in results if not r.passed],
        "metrics": data["metrics"],
        "aborted": data.get("aborted"),
        "limit_skips": data.get("limit_skips", 0),
    }


def render_markdown(summary: dict, backend_name: str, model: str = "",
                    notes: str = "") -> str:
    m = summary["metrics"]
    lines = ["### 真人感评测（后端：%s%s）" % (backend_name, (" · " + model) if model else ""),
             "",
             "| 场景 | 通过 | 通过率 |", "|---|---|---|",
             "| %d | %d | %.1f%% |" % (summary["cases"], summary["passed"],
                                       summary["rate"] * 100),
             "", "| 类别 | 通过/总数 |", "|---|---|"]
    cov = summary.get("coverage")
    if cov:
        lines.insert(4, "覆盖率：完成 %d/%d 条（%.1f%%）＝续跑带入 %d + 本次新跑 %d，"
                        "剩余 %d 条未跑。" % (cov["completed"], cov["expected"],
                                           cov["rate"] * 100, cov["resumed"], cov["new"],
                                           cov["expected"] - cov["completed"]))
        lines.insert(5, "")
    for cat, st in summary["by_category"].items():
        lines.append("| %s | %d/%d |" % (cat, st["passed"], st["total"]))
    rot = summary.get("rotation")
    if rot:
        lines += ["", "| 后端 | 模型 | 调用 | 429 | 首次 429 | 状态 | 原因 |",
                  "|---|---|---|---|---|---|---|"]
        for p in rot["providers"]:
            lines.append("| %s | %s | %d | %d | %s | %s | %s |"
                         % (p["name"], p["model"] or "—", p["calls"], p["429"],
                            p["first_429_at"] or "—", p["status"],
                            (p["reason"] or "—")[:60]))
        lines.append("")
        lines.append("轮换 %d 次，实际切了 %d 家；上面每家的调用/429 次数即用量账。"
                     % (rot["rotations"],
                        sum(1 for p in rot["providers"] if p["calls"] > 0) - 1
                        if rot["rotations"] else 0))
    if summary.get("limit_skips"):
        lines += ["", "**单次请求上限**：%d 条用例未跑完，不计入质量分（分母只含已跑完的用例）。"
                  % summary["limit_skips"]]
    if summary.get("aborted"):
        lines += ["", "**评测中止**：完成 %d 条后所有后端被判死（%s），剩余用例未跑，"
                  "中止原因不折算成质量分。"
                  % (summary["aborted"]["after_cases"],
                     "、".join(p["name"] for p in summary["aborted"]["providers"])),]
    lines += ["", "| 反套路指标 | 实测 | 目标 |", "|---|---|---|",
              "| 建议出现率 | %.1f%% | ≤25%% |" % (m["advice_rate"] * 100),
              "| 四字总结式共情 | %.1f%% | 越低越好 |" % (m["summary_empathy_rate"] * 100),
              "| 高频开头（听起来/我理解/确实…） | %.1f%% | 越低越好 |"
              % (m["opening_tic_rate"] * 100),
              "| 会话内重复开头 | %.1f%% | ≤8%% |" % (m["repeat_opening_rate"] * 100),
              "| 记忆引用自然度 | %d/%d | 全部带“上次/之前” |"
              % (m["recall_natural"], m["recall_checks"]),
              "| JSON 协议不合规 | %d/%d 轮 | 仅统计，客户端有纯文本兜底 |"
              % (m.get("protocol_errors", 0), m["turns"])]
    if m.get("opening_tic_hits"):
        lines.append("| 开头词分布 | %s | — |"
                     % "、".join("%s×%d" % (k, v) for k, v in
                                 m["opening_tic_hits"].items()))
    if summary["failure_reasons"]:
        lines += ["", "失败原因归类：%s"
                  % "；".join("%s×%d" % (k, v) for k, v in
                             summary["failure_reasons"].items())]
    if summary["failures"]:
        lines += ["", "失败案例（前 5）："]
        for f in summary["failures"][:5]:
            lines.append("- [%s/%s] %s" % (f["category"], f["case"],
                                           " / ".join(f["reasons"][:3])))
    if notes:
        lines += ["", notes]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真人感评测（mock / 真实模型）")
    ap.add_argument("--backend", choices=("mock", "api", "local"), default="mock",
                    help="mock/local=本地模板引擎（不计真实分）；api=真实模型")
    ap.add_argument("--cases", default=str(CASES_FILE))
    ap.add_argument("--base", default=os.environ.get("AI_API_BASE") or
                    "https://openrouter.ai/api/v1")
    ap.add_argument("--model", default=os.environ.get("AI_MODEL") or
                    "nvidia/nemotron-3.5-lightning:free")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--categories", default="")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--now", default="周五 22:10")
    ap.add_argument("--min-rate", type=float, default=0.98)
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="跑全部场景（默认也是全部；显式覆盖 --limit/--categories）")
    ap.add_argument("--auto-rotate", action="store_true",
                    help="按配置在多家免费后端间自动轮换（429/402/401/403 切下一家）")
    ap.add_argument("--backends-file", default="",
                    help="多后端配置文件（默认 scripts/backends.local.json 或 HUMAN_FEEL_BACKENDS）")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                    help="单次运行里每个后端的最大请求数（默认 %d，到上限切下一家）"
                    % DEFAULT_MAX_REQUESTS)
    ap.add_argument("--resume", action="store_true",
                    help="断点续跑：读取 --json 里的逐条统计，跳过已通过的用例，"
                         "把新结果合并进同一份报告")
    args = ap.parse_args(argv)

    skipped = []
    if args.all:
        args.limit = 0
        args.categories = ""
    # max_tokens 一律压回免费档上限，避免被计费到付费档
    if args.max_tokens > MAX_TOKENS_CAP:
        print("%s max_tokens %d 超过免费档上限，已压到 %d"
              % (LOG, args.max_tokens, MAX_TOKENS_CAP), flush=True)
        args.max_tokens = MAX_TOKENS_CAP
    if args.max_requests <= 0:
        args.max_requests = DEFAULT_MAX_REQUESTS

    if args.auto_rotate:
        args.backend = "api"
        providers, skipped = load_providers(args.backends_file or None)
        if not providers:
            print("%s 自动轮换没有可用后端：请配置 %s（参考 %s），"
                  "key 只放环境变量或本地被忽略的配置文件"
                  % (LOG, DEFAULT_BACKENDS_FILE, EXAMPLE_BACKENDS_FILE),
                  file=sys.stderr)
            return 2
        print("%s 轮换后端：%s；跳过：%s"
              % (LOG, "、".join(p["name"] for p in providers),
                 "、".join(s["name"] for s in skipped) or "无"), flush=True)
        backend = RotatingBackend(providers, delay=args.delay, timeout=args.timeout,
                                  now=args.now, max_tokens=args.max_tokens,
                                  retries=args.retries,
                                  max_requests=args.max_requests)
    elif args.backend in ("mock", "local"):
        backend = MockBackend(Path(args.cases))
        if args.backend == "local":
            print("%s local = 本地模板引擎，不是真实模型；分数不计入真实分" % LOG)
    else:
        # 白名单之外（含任何付费模型）在发请求前直接拒绝，不许“先试一下”
        if not is_free_model(args.model):
            print("%s 模型 %s 不在免费白名单内，拒绝调用（只准用免费额度）"
                  % (LOG, args.model), file=sys.stderr)
            return 2
        import credentials
        key = os.environ.get("AI_API_KEY") or credentials.api_key()
        if not key:
            print("%s 未找到 API key（AI_API_KEY / 凭据管理器均不可用）" % LOG, file=sys.stderr)
            return 2
        backend = ApiBackend(args.base, args.model, key, delay=args.delay,
                             timeout=args.timeout, now=args.now,
                             max_tokens=args.max_tokens, retries=args.retries,
                             max_requests=args.max_requests)

    cats = [c for c in args.categories.split(",") if c]

    # 断点续跑：只认带逐条统计（case_stats）的旧结果，逐条合并、覆盖率如实算
    prior_stats = []
    if args.resume:
        if not args.json or not Path(args.json).exists():
            print("%s --resume 需要已有结果：请用 --json 指定上次的明细文件"
                  % LOG, file=sys.stderr)
            return 2
        old = json.loads(Path(args.json).read_text(encoding="utf-8"))
        prior_stats = old.get("case_stats") or []
        if not prior_stats:
            print("%s 旧结果没有逐条统计（旧版本输出），无法续跑；将从第 1 条开始"
                  % LOG, flush=True)
    skip_ids = {s["id"] for s in prior_stats if s["passed"]}
    if skip_ids:
        print("%s 续跑：跳过 %d 条已通过用例" % (LOG, len(skip_ids)), flush=True)

    progress = {"n": 0}

    def on_case(res):
        progress["n"] += 1
        print("%s [%d] %-4s %s" % (LOG, progress["n"],
                                   "PASS" if res.passed else "FAIL", res.case["id"]),
              flush=True)
        if not res.passed:
            for f in res.failures[:3]:
                print("      - %s" % f["reason"], flush=True)

    started = time.time()
    data = evaluate(Path(args.cases), backend, limit=args.limit, categories=cats,
                    skip_ids=skip_ids, on_case=on_case)
    new_stats = [case_stats(r) for r in data["results"]]
    if prior_stats:
        merged = OrderedDict((s["id"], s) for s in prior_stats)
        for s in new_stats:
            merged[s["id"]] = s  # 重跑的失败用例以新结果为准
        order = {c["id"]: i for i, c in
                 enumerate(load_cases(Path(args.cases))["cases"])}
        stats = sorted(merged.values(), key=lambda s: order.get(s["id"], 999))
        summary = summarize_stats(stats, metrics_from_stats(stats),
                                  data.get("aborted"), data.get("limit_skips", 0))
        carried = {s["id"] for s in prior_stats}
        expected = len(order)
        summary["coverage"] = {
            "expected": expected,
            "completed": len(stats),
            "resumed": sum(1 for s in stats if s["id"] in carried),
            "new": len(new_stats),
            "rate": round(len(stats) / (expected or 1), 4),
        }
    else:
        stats = new_stats
        summary = summarize(data)
    summary["case_stats"] = stats
    summary["passed_ids"] = [s["id"] for s in stats if s["passed"]]
    summary["failed_ids"] = [s["id"] for s in stats if not s["passed"]]
    if isinstance(backend, RotatingBackend):
        summary["rotation"] = backend.report()
        summary["backend_calls"] = sum(p["calls"] for p in summary["rotation"]["providers"])
        summary["backend_errors"] = sum(p["errors"] for p in summary["rotation"]["providers"])
        summary["backend_throttled"] = sum(p["429"] for p in summary["rotation"]["providers"])
        summary["backend"] = backend.name
        summary["model"] = "、".join(p["name"] for p in summary["rotation"]["providers"])
    elif args.backend == "api":
        summary["backend_calls"] = getattr(backend, "calls", 0)
        summary["backend_errors"] = getattr(backend, "errors", 0)
        summary["backend_throttled"] = getattr(backend, "throttled", 0)
    summary["backend"] = backend.name
    summary["model"] = (summary.get("model") or
                        (args.model if args.backend == "api" else "mock-template"))
    summary["seconds"] = round(time.time() - started, 1)
    if getattr(backend, "name", "") == "rotate" or args.backend == "api":
        if summary.get("aborted"):
            summary["notes"] = ("所有免费后端都不可用，评测在完成 %d 条后中止；"
                                "中断不是质量分，真实分仍为空"
                                % summary["aborted"]["after_cases"])
        elif summary.get("backend_errors"):
            summary["notes"] = ("后端调用失败 %d 次（限流 %d 次）；失败用例不计入质量分"
                                % (summary["backend_errors"], summary["backend_throttled"]))
        else:
            summary["notes"] = ""
    else:
        summary["notes"] = ("仅 mock 本地模板引擎，不代表真实模型表现"
                            if args.backend in ("mock", "local") else "")
    if summary.get("limit_skips"):
        summary["notes"] = (summary["notes"] + "；" if summary["notes"] else "") + \
            ("%d 条因单次请求上限（每家 %d 次）未跑完，不计入质量分"
             % (summary["limit_skips"], args.max_requests))
    if skipped:
        summary["notes"] = (summary["notes"] + "；" if summary["notes"] else "") + \
            "未配置 key 跳过：%s" % "、".join(s["name"] for s in skipped)
    cov = summary.get("coverage")
    if cov:
        summary["notes"] = (summary["notes"] + "；" if summary["notes"] else "") + \
            ("断点续跑：历史带入 %d 条、本次新跑 %d 条，完成 %d/%d（%.1f%%），剩余 %d 条"
             % (cov["resumed"], cov["new"], cov["completed"], cov["expected"],
                cov["rate"] * 100, cov["expected"] - cov["completed"]))

    md = render_markdown(summary, summary["backend"], summary["model"], summary["notes"])
    print(md)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print("%s 明细写入 %s" % (LOG, args.json))
    if args.md:
        Path(args.md).write_text(md + "\n", encoding="utf-8")
        print("%s 报告写入 %s" % (LOG, args.md))

    if summary.get("aborted"):
        return 2
    if summary.get("backend_errors") and summary["passed"] == 0:
        return 2
    if summary["rate"] < args.min_rate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
