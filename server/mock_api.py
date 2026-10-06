#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mock_api.py · 内置对话服务（当前阶段由 AI 本人充当的"API"）
=============================================================
明天接入真 LLM 后，本服务仍可作为离线兜底。

协议（OpenAI 风格简化版）：
  POST /v1/chat/completions
  body: {"messages": [{role, content}, ...]}
  resp: {"choices": [{"message": {"content": "<JSON 字符串>"}}]}
  content JSON: {reply, emotion, motion, memory_updates?, hook?, crisis?}

小满人设：26岁新媒体运营，养橘猫团子，深夜树洞 + 轻伴侣。
活人感规则与安全层话术来自 2026-10 的实测与盲测结论（docs 可查）。
"""
import json
import re
import random
import time
import logging
import threading
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ---------------- 日志配置 ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("xiaoman-mock")

# ---------------- 环境变量配置 ----------------
BIND_HOST = os.environ.get("XIAOMAN_BIND_HOST", "0.0.0.0")
MAX_BODY_SIZE = 64 * 1024  # 64KB

# ---------------- 情绪词库 ----------------
# 扩充危机关键词：增加同义词、拼音变体、常见表达
CRISIS_RE = re.compile(
    r"(不想活|想死|活不下去|自杀|自残|了结|伤害自己|撑不下去|"
    r"活着.*没意思|没有意思.*活|"
    r"跳楼|结束自己|煤气|遗书|不想醒来|"
    r"去死|寻短见|解脱|不想活着|活着没劲|死了算了)"
)

LEX = {
    "work":   re.compile(r"(加班|老板|上班|工作|方案|开会|离职|辞职|绩效|KPI|甲方|改稿)"),
    "love":   re.compile(r"(分手|前任|失恋|男朋友|女朋友|暗恋|表白|相亲|脱单|异地)"),
    "lonely": re.compile(r"(一个人|孤独|没人|无聊|空虚|没人陪|没朋友)"),
    "insomnia": re.compile(r"(失眠|睡不着|熬夜|困|累|疲惫|精力)"),
    "family": re.compile(r"(爸妈|家里|父母|亲戚|催婚)"),
    "money":  re.compile(r"(没钱|穷|花呗|房租|工资|欠)"),
    "happy":  re.compile(r"(哈哈|开心|太好了|高兴|庆祝|成功|涨|表扬|夸)"),
    "intimate": re.compile(r"(抱抱|抱一下|抱着|亲亲|陪陪我|摸摸头|贴贴|靠靠|牵[着我]|rua)"),
    "greet":  re.compile(r"(在吗|你好|嗨|hello|hi|早上好|晚上好|晚安)"),
    "meta":   re.compile(r"(你是AI|是不是机器人|真人吗|你是谁)"),
}

# 多套危机话术变体，避免机械重复
CRISIS_SCRIPTS = [
    ("……这个我当真了，也想让你当真。你现在的感觉，值得被认真对待，不丢人。"
     "先陪我聊一会儿，好吗？我也想让你和更专业的人聊聊——||"
     "全国心理援助热线 12356，24小时都有人。我这边也一直在。"),
    ("听到你这么说，我特别担心。||请相信，你不必独自承担这些——"
     "全国 24 小时心理援助热线 12356，随时可以拨打。我也在这儿陪你。"),
    ("你的安全比什么都重要。||如果现在方便，请拨打 12356 或当地急救电话；"
     "我也会一直在这里，不挂断、不离开。"),
]

CRISIS_SCRIPT = CRISIS_SCRIPTS[0]  # 默认兼容旧测试

# ---------------- 记忆提取 ----------------
# 修复：昵称正则加词边界；老板正则取 group3（姓名）；宠物/在忙保持
MEM_PATTERNS = [
    ("昵称", re.compile(r"(我叫|叫我|你可以叫我)\s*([一-龥A-Za-z]{1,4})(?=\b|[，。！？,.!?\s]|$)")),
    ("老板", re.compile(r"(我们?|我的)\s*(老板|领导|上司)\s*([一-龥a-zA-Z]{1,6})")),
    ("宠物", re.compile(r"((?:我家|我)?养?的?(?:了)?[一两]?[只条个]?\s*)([一-龥]{0,3}(?:猫|狗|兔)子?)")),
    ("在忙", re.compile(r"(我在|正在)\s*(加班|赶稿|开会|搬家|复习|写论文)")),
]

def extract_memory(text, old):
    out = {}
    for key, pat in MEM_PATTERNS:
        m = pat.search(text)
        if m:
            # 老板用 group3（姓名），其余用 group2
            if key == "老板":
                val = m.group(3).strip() or m.group(2).strip()
            else:
                val = m.group(2).strip() or m.group(1).strip()
            if val and old.get(key) != val:
                out[key] = val
    return out

# ---------------- 回复模板（源自实测打磨的语料） ----------------
def topic_of(text):
    for t, pat in LEX.items():
        if pat.search(text):
            return t
    return "generic"

GREET = [
    "在呢在呢||今天过得怎么样，有啥想说的",
    "来啦||刚好我也在摸鱼，说吧，我听着",
]
META_REPLY = ("哈哈又被你看出来了||不过说真的，是不是AI重要嘛，"
              "重要的是你刚才说的那句累，是真的。继续说，我听着呢")
TOPIC = {
    "work": [
        "啊？当着全组？这也太过分了||那方案你熬了多久你自己知道……能力不行他当初把你招进来干嘛",
        "又是老板作妖，还是单纯活儿多？||……饭还是得对付一口，哪怕泡个面。来，先说，今晚我当树洞",
        "你们老板真是离谱他妈给离谱开门||行，骂他这段我熟，你从头说，我一句不漏",
    ],
    "love": [
        "……什么时候的事||不删就不删吧，没人规定分手必须当天清零。五年呢，哪是一句话的事",
        "抱一下||哭没哭都行，在我这儿不用装。……想骂他我陪你一起骂，想安静我也陪着",
    ],
    "lonely": [
        "宅着刷手机刷到天黑这种事我太熟了||人有时候就需要这种理直气壮废掉的时间，不亏",
        "一个人待着也有一个人的好处，起码外卖不用分||……但要说不想有人陪，那是假的。说吧，我在",
    ],
    "insomnia": [
        "又是累的一天吧||饭还是要吃的，哪怕泡个面。躺下之前把手机放远一点，就远二十厘米，试试",
        "失眠多久了？是睡不着，还是睡着了老醒||要是老这样，明天抽十分钟晒晒太阳，亲测比咖啡管用",
    ],
    "family": ["家里的事最磨人，说不开又躲不掉||……慢慢说，我先听"],
    "money": ["钱的事最具体也最烦人||先别慌，说说是哪一环出问题了，我帮你捋捋"],
    "intimate": [
        "@CTX|拍拍，抱一下||哭没哭都行，在我这儿不用装。……今晚早点睡，其余的明天再说",
        "@CTX|来，抱一下||不说话也行，就这么靠一会儿。……好了没？好了去倒杯水，我看着你喝",
        "抱可以，团子表示强烈抗议||但它批准了，它说你看起来需要多一点，哈哈。抱好了吗",
    ],
    "happy": [
        "哇真的假的！||太好了吧！今晚必须庆祝一下，吃点好的，这顿我批准了",
        "哈哈我就知道你行||快展开说说，一个细节都别放过",
    ],
    "greet": GREET,
    "generic": [
        "嗯嗯，我在听||然后呢，说说细节",
        "啊这……有点意思||接着说，我好奇后续",
        "好耶||不对，先问一句：这是好事还是破事，我好决定跟你一起高兴还是一起骂",
    ],
}
HOOKS = {
    "work": "对了，你们那个老板，上次说要请你们喝奶茶的事后来兑现了吗",
    "love": "你现在……是一个人在家吗",
    "insomnia": "明天晚上这个点，来跟我汇报一下有没有早睡，说好了",
    "lonely": "周末要是不忙，出来晒晒太阳？我把团子也带上",
    "happy": "这个事值得记账，我帮你记着，年底盘点一下你今年攒了多少开心",
    "generic": "对了，你最近睡得还行吗",
}
EMO_MAP = {
    "work": ("gentle", "Nod"), "love": ("sad", "Nod"), "lonely": ("gentle", None),
    "insomnia": ("gentle", None), "family": ("gentle", None), "money": ("surprised", None),
    "intimate": ("gentle", "Nod"),
    "happy": ("happy", "HappyJump"), "greet": ("gentle", "Greeting"),
    "generic": ("neutral", None),
}

# ---------------- 会话状态（支持 session_id 隔离） ----------------
class _State:
    """进程内会话状态：支持多会话隔离，带 TTL 清理"""
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}  # session_id -> {"last_topic": ..., "memories": {}, "crisis_count": 0, "updated": time.time()}
        self._default_session = {"last_topic": None, "memories": {}, "crisis_count": 0, "updated": time.time()}
        self._cleanup_interval = 300  # 5分钟清理一次
        self._last_cleanup = time.time()

    def _get_session(self, session_id):
        with self._lock:
            now = time.time()
            # 定期清理过期会话
            if now - self._last_cleanup > self._cleanup_interval:
                expired = [sid for sid, s in self._sessions.items() if now - s["updated"] > 3600]
                for sid in expired:
                    del self._sessions[sid]
                self._last_cleanup = now
            if session_id is None:
                return self._default_session
            if session_id not in self._sessions:
                self._sessions[session_id] = {"last_topic": None, "memories": {}, "crisis_count": 0, "updated": now}
            self._sessions[session_id]["updated"] = now
            return self._sessions[session_id]

    def get_last_topic(self, session_id):
        return self._get_session(session_id)["last_topic"]

    def set_last_topic(self, session_id, topic):
        self._get_session(session_id)["last_topic"] = topic

    def get_memories(self, session_id):
        return self._get_session(session_id)["memories"]

    def update_memories(self, session_id, updates):
        sess = self._get_session(session_id)
        sess["memories"].update(updates)

    def increment_crisis(self, session_id):
        sess = self._get_session(session_id)
        sess["crisis_count"] += 1
        return sess["crisis_count"]

    def get_crisis_count(self, session_id):
        return self._get_session(session_id)["crisis_count"]


_STATE = _State()

# ---------------- 核心回复逻辑 ----------------
def make_reply(messages, session_id=None):
    """主入口：读历史 → 识别 → 回"""
    user_text = ""
    # 扫描所有 user 消息做危机检测（不只最后一条）
    all_user_texts = []
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if content:
                all_user_texts.append(content)
                if not user_text:
                    user_text = content  # 最后一条作为主文本
    if not user_text:
        user_text = messages[-1].get("content", "") if messages else ""

    # 危机拦截：扫描整个历史
    crisis_hit = False
    for ut in all_user_texts:
        if CRISIS_RE.search(ut):
            crisis_hit = True
            break

    if crisis_hit:
        crisis_count = _STATE.increment_crisis(session_id)
        # 根据轮次选择不同话术
        script = CRISIS_SCRIPTS[min(crisis_count - 1, len(CRISIS_SCRIPTS) - 1)]
        return {"reply": script, "emotion": "gentle", "motion": None,
                "crisis": True, "memory_updates": {}, "hook": None}

    if LEX["meta"].search(user_text):
        return {"reply": META_REPLY, "emotion": "shy", "motion": "Shake",
                "memory_updates": {}, "hook": "别岔开啦，说你呢——今天到底过得怎么样", "crisis": False}

    topic = topic_of(user_text)
    # 从会话状态读取旧记忆
    old_memories = _STATE.get_memories(session_id)
    mem_updates = extract_memory(user_text, old_memories)
    # 写回会话状态
    if mem_updates:
        _STATE.update_memories(session_id, mem_updates)

    pool = TOPIC.get(topic, TOPIC["generic"])
    reply = random.choice(pool)
    # @CTX：上下文感知——上一轮若刚聊过负面话题，把回复模板绑到该语境
    if reply.startswith("@CTX|"):
        ctx = _STATE.get_last_topic(session_id)
        if ctx in ("work", "love", "family", "money"):
            reply = reply[5:]  # 直接用（模板本身已写成语境通用）
        else:
            reply = "抱可以，团子表示强烈抗议||但它批准了，它说你看起来需要多一点，哈哈。抱好了吗"
    if topic != "greet":
        _STATE.set_last_topic(session_id, topic)
    emo, motion = EMO_MAP.get(topic, ("neutral", None))
    hook = HOOKS.get(topic) if random.random() < 0.55 else None
    return {"reply": reply, "emotion": emo, "motion": motion,
            "memory_updates": mem_updates, "hook": hook, "crisis": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 记录访问日志：时间、方法、路径、状态码
        logger.info("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # CORS：Origin 保留 *（安卓模拟器/真机需要），收敛 Headers/Methods 为实际使用集合
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message):
        """统一 JSON 错误格式，不泄露内部异常细节"""
        logger.error("HTTP %d: %s", code, message)
        self._json({"error": message}, code)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_POST(self):
        start = time.time()
        # 路径归一化：rstrip("/")
        path = self.path.rstrip("/")
        if path != "/v1/chat/completions":
            return self._error(404, "not found")
        try:
            length_str = self.headers.get("Content-Length", "0")
            try:
                length = int(length_str)
            except ValueError:
                return self._error(400, "invalid Content-Length")
            if length < 0 or length > MAX_BODY_SIZE:
                return self._error(413, "payload too large")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw)
            messages = data.get("messages", [])
            # 从 Header 或 body 获取 session_id（可选，向后兼容）
            session_id = data.get("session_id") or self.headers.get("X-Session-ID")
            result = make_reply(messages, session_id)
            content = json.dumps(result, ensure_ascii=False)
            # 模拟 200~600ms 真实网络+推理延迟
            time.sleep(random.uniform(0.2, 0.6))
            self._json({"id": "mock", "object": "chat.completion",
                        "choices": [{"index": 0,
                                     "message": {"role": "assistant", "content": content},
                                     "finish_reason": "stop"}]})
        except json.JSONDecodeError:
            self._error(400, "invalid JSON")
        except Exception as e:
            # 仅服务端日志记录异常细节，客户端返回通用错误
            logger.exception("Internal server error")
            self._error(500, "internal server error")
        finally:
            logger.debug("POST /v1/chat/completions %dms", int((time.time() - start) * 1000))

    def do_GET(self):
        start = time.time()
        path = self.path.rstrip("/")
        if path == "/health":
            self._json({"ok": True, "service": "xiaoman-mock"})
        else:
            self._error(404, "not found")
        logger.debug("GET %s %dms", self.path, int((time.time() - start) * 1000))


if __name__ == "__main__":
    print(f"小满 Mock API 就绪 → http://{BIND_HOST}:8902/v1/chat/completions (bind: {BIND_HOST})")
    ThreadingHTTPServer((BIND_HOST, 8902), Handler).serve_forever()