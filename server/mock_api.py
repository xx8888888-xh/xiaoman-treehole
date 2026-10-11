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
    # P0-3 关系连续性：更新查询入口（Replika 式"你变了"温柔接住 → 播报更新记录）
    # 主语收紧防误伤：你(?!们)——"你们老板更新了周报模板"不触发；裸"变"不触发（"你变漂亮了"落 happy）
    "update_query": re.compile(
        r"你(?!们)(?:今天|最近|好像|感觉|有点|怎么|是不是|的|了|变|得)*?(更新|升级|变了|版本|怪)|不像以前|更新记录"),
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

# ---------------- P2-2 抑郁信号层（与 app.js / mock_engine.js 同源同集合） ----------------
# 分级频控=会话内抑郁信号累计次数 n（二道防线 + 直测；在线路径由客户端先行截获）：
# n=1 接住+轻问（不推热线）；n=2 陪伴；n>=3 未转过→温柔转介（AI 边界声明+12356，一次）；再中回陪伴池轮换
DEPRESS_RE = re.compile(
    r"(很没用|没用的(?:人|东西)|就是个?(?:废物|累赘)|没有价值|"
    r"(?:看不到|没有)(?:希望|盼头)|绝望|"
    r"(?:日子|生活|干什么|做什么|干啥|怎么过)(?:都|也|好|真|太)?(?:没意思|没劲)|"
    r"提不起(?:劲|兴趣)|(?:开心|高兴)不起来|"
    r"一直(?:很|特别)?(?:不开心|低落|很高兴不起来)|"
    r"很(?:低落|丧)|特别(?:低落|丧)|"
    r"活着(?:好|真|太)?(?:累|难)|心(?:好|真)?累)"
)
DEPRESS_FIRST = [
    "你这话我听进去了||最近这种没劲的感觉，缠你多久啦？跟我说说，我在听",
]
DEPRESS_MID = [
    "又是这种感觉……||不用急着好起来，今天想具体聊聊吗？我陪着",
    "怎么又难受了||说不清为什么也没关系，就这样待着，我陪你",
]
DEPRESS_ESC = [
    "我一直都在，这句是真的||但有句实话也得跟你说：我是 AI 朋友，有些事我接不住，需要真人的帮助"
    "||如果这种低落一直缠着你，找心理咨询师聊聊，或者拨 12356（24小时）"
    "——这不是敷衍你，是我认真替你想的",
]

# ---------------- 记忆提取 ----------------
# 修复：昵称正则加词边界；老板正则取 group3（姓名）；宠物/在忙保持
# P0-1 新增"大事"：最近的重要事件（考试/搬家/面试…），整段匹配作值
MEM_PATTERNS = [
    ("昵称", re.compile(r"(我叫|叫我|你可以叫我)\s*([一-龥A-Za-z]{1,4})(?=\b|[，。！？,.!?\s]|$)")),
    ("老板", re.compile(r"(我们?|我的)\s*(老板|领导|上司)\s*([一-龥a-zA-Z]{1,6})")),
    ("宠物", re.compile(r"((?:我家|我)?养?的?(?:了)?[一两]?[只条个]?\s*)([一-龥]{0,3}(?:猫|狗|兔)子?)")),
    ("在忙", re.compile(r"(我在|正在)\s*(加班|赶稿|开会|搬家|复习|写论文)")),
    ("大事", re.compile(
        r"((?:下周|下个月|下学期|明天|后天|这?周五|这?周六|这?周日|月底|年底|马上|快)\s*"
        r"(?:要|得|准备|打算)?\s*"
        r"(?:考试|月考|期中考试|期末考试|期中|期末|中考|高考|考研|复试|答辩|"
        r"面试|搬家|入职|报到|交稿|交报告|交方案|比赛|演出|体检|领证)"
        r"(?:啦|了)?"
        r"|"
        r"(?:要|得|准备|打算)\s*"
        r"(?:考试|月考|期中考试|期末考试|期中|期末|中考|高考|考研|复试|答辩|"
        r"面试|搬家|入职|报到|交稿|交报告|交方案|比赛|演出|体检|领证)"
        r"(?:啦|了)?)"
    )),
]

def _clean_name(v):
    """昵称尾部废话词清洗：'阿秋就行'→'阿秋'（P0-1 钉子户内容质量）"""
    for suf in ("就行", "就好", "好了", "可以", "吧", "呀", "啦", "哈", "呢", "哦", "啊", "呗"):
        if v.endswith(suf) and len(v) > len(suf):
            return v[:-len(suf)]
    return v

# ---------------- P0-2 遗留守卫：泛指不覆盖具体（提取层信息劣化防护） ----------------
# 现象：已存"橘猫"后用户说"我家那只猫拆家了"，宠物正则的 group2 会把
# "那只"当名字前缀吃进去提取出"那只猫"——指示词+数量词+基名词=零信息泛指，
# 覆盖旧值属于信息劣化（模板回提会从"你家橘猫"退化成"你家那只猫"）。
# 判定：剥离指示/数量/称谓虚词后只剩基名词（猫/狗/兔(子)）→ 泛指。
# 规则：泛指新值只在旧值缺失或旧值同为泛指时落库；旧值具体 → 跳过（保留）。
PET_GENERIC_STRIP = set("一这那每某该个小条只家我有")
PET_BASE_RE = re.compile(r"^(?:猫|狗|兔子?)$")

def _pet_generic(v):
    """'那只猫/我家猫/一只狗/有只猫'→True；'橘猫/英短猫/金毛狗'→False"""
    core = "".join(c for c in v if c not in PET_GENERIC_STRIP)
    return bool(PET_BASE_RE.match(core))

# ---------------- P0-2 遗留守卫 II：老板姓名可信度（提取层垃圾捕获防护） ----------------
# 现象（23:15 轮探针实测）：老板正则 group3 会把后续谓语当姓名——
#   "我们老板又骂我"→老板="又骂我"、"我们老板今天心情不好"→老板="今天心情"、
#   "我们老板王总今天又作妖"→老板="王总今天"（姓名+谓语连排全吞），
#   回提模板将产出"又骂我今天没又折腾你吧"式乱语。
# 判定（与 mock_engine.js/app.js 三端同源同表）：
#   谓语字截断（时间/副词/动词字起断）→ 姓表/老小+姓/单姓/英文名模式 → 可信姓名。
# 规则：可信姓名永远可落库（用户换老板=信息升级）；不可信（谓语垃圾）→ 转基词
#   （"老板/领导/上司"）占位——仅旧值缺失时生效；基词不覆盖已有可信姓名（防劣化）。
BOSS_SURNAMES = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
    "于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江"
    "尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
# 截断集=时间/副词/动词高频字（避开姓表与称谓字"总哥姐工董师傅"；"周"在姓表但
# 姓名中位置≥1 的"周"多为周报/周会，保留）；"老/小"不进截断集（老王/小王模式）
BOSS_CUT = set(
    "今昨前早上午晚夜凌晨周月年天时分秒就才又再被让叫说问要骂催找发给改加请带"
    "忙活完没不太很太点对跟和像是心情脾气折作搞整烦惹凶")
BOSS_EN_RE = re.compile(r"^[A-Z][a-zA-Z]{1,11}$")

def _boss_cut(v):
    """姓名后谓语截断：'王总今天骂我'→'王总'（第2字起遇谓语字即断，姓首字不动）"""
    for i in range(1, len(v)):
        if v[i] in BOSS_CUT:
            return v[:i]
    return v

def _boss_name_ok(v):
    """'张三/老王/小王/王总/周(单姓)/Jack'→True；'又骂我/今天心情/老折腾我/老板'→False"""
    if not v or len(v) > 4:
        return False
    if BOSS_EN_RE.match(v):
        return True
    if len(v) >= 2 and v[0] in "老小" and v[1] in BOSS_SURNAMES:
        return True
    return bool(v[0] in BOSS_SURNAMES and (len(v) == 1 or re.match(r"^[一-龥]{1,2}$", v[1:])))

def extract_memory(text, old):
    out = {}
    for key, pat in MEM_PATTERNS:
        m = pat.search(text)
        if m:
            # 老板用 group3（姓名）；大事取整段短语；其余用 group2
            if key == "老板":
                val = (m.group(3) or m.group(2) or "").strip()
            elif key == "大事":
                val = (m.group(1) or "").strip()
            else:
                val = (m.group(2) or m.group(1) or "").strip()
            # P0-2 对齐：所有键统一尾缀清洗（与前端 cleanName 同源同表）——
            # 语气尾缀词不进记忆值（"我们老板张三吧"→"张三"，前后端口径一致）
            val = _clean_name(val)
            if key == "老板":
                # P0-2 遗留守卫 II：谓语截断 + 姓名可信度。垃圾（"又骂我"）→
                # 转基词占位（g2 恒为 老板/领导/上司）；可信姓名（"王总"）保持
                val = _boss_cut(val)
                if not _boss_name_ok(val):
                    val = m.group(2) or "老板"
            if val and old.get(key) != val:
                # 泛指不覆盖具体：旧值具体（"橘猫"）时，泛指新值（"那只猫"）不落库
                if (key == "宠物" and old.get(key)
                        and _pet_generic(val) and not _pet_generic(old[key])):
                    continue
                # 基词不覆盖姓名（老板）：旧值可信姓名（"张三"）时，基词新值跳过
                if (key == "老板" and old.get("老板")
                        and not _boss_name_ok(val) and _boss_name_ok(old["老板"])):
                    continue
                out[key] = val
    return out

# ---------------- 首日引导（P0-1：不搞表单，聊着聊着铺钉子户） ----------------
# 前几轮通过 hook 自然带出"称呼→大事"；已有记忆或超轮次则退回常规随机 hook
ONBOARD_NAME_HOOK = "对了，聊了这么久还不知道怎么称呼你——我叫你什么顺口？"
ONBOARD_EVENT_HOOK = "还有呀，你最近有啥大事吗？考试、搬家、换工作那种，说一件，我帮你记着"
ONBOARD_NAME_TURNS = 4   # 称号引导最晚轮次
ONBOARD_EVENT_TURNS = 8  # 大事引导最晚轮次

# ---------------- P0-2 主动回提（"被记住"的核心体验） ----------------
# 回提谁：话题关联映射（当前 LEX 类目 → 候选记忆键，优先级序）；
# insomnia/happy → 大事：熬夜累/开心话题常与备考赶稿等大事相关（设计定稿口径）
RECALL_MAP = {
    "work": ["老板", "在忙"],
    "insomnia": ["大事"],
    "happy": ["大事"],
}
PET_TOPIC_RE = re.compile(r"(猫|狗|兔|团子|宠物|毛孩|铲屎)")
# 怎么提：值零加工直填模板（记忆原文原样嵌入——不张冠李戴的结构性保证）
RECALL_TEMPLATES = {
    "大事": "你上次说{v}——准备得怎么样啦？",
    "宠物": "你家{v}呢？今天乖不乖",
    "老板": "{v}今天没又折腾你吧",
    "在忙": "你之前说在{v}，现在缓过来了吗",
}
RECALL_EXTRACT_GAP = 3  # 闸门0：提取后至少隔 3 轮才可回提（"你上次说"须指向足够久之前，防复读感）
RECALL_COOLDOWN = 6    # 闸门1：同记忆两次回提间隔 ≥6 轮（"从未被引用"视为通过）
RECALL_MAX = 4         # 闸门2：每会话回提总次数上限（含 greet 开场引用；频控哲学与心跳一致）

def _pick_recall(memories, mem_updates, topic, user_text, sess, turn):
    """P0-2 回提决策：返回 (hook 文本, 命中键) 或 (None, None)。确定性触发，不掷骰子。
    昵称不回提（称呼里天然高频使用，回提显得刻意）。"""
    hits = sess.setdefault("memory_hits", {})
    extracted = sess.setdefault("mem_extracted", {})
    # 闸门2：每会话总额（含 greet 开场引用）
    if sess.get("recall_count", 0) >= RECALL_MAX:
        return None, None
    # 候选键：话题关联 → 宠物词表命中 → 兜底大事（最重要钉子户）
    cands = [k for k in RECALL_MAP.get(topic, []) if k in memories]
    if PET_TOPIC_RE.search(user_text) and "宠物" in memories:
        cands.append("宠物")
    if not cands and "大事" in memories:
        cands = ["大事"]
    for k in cands:
        if k in mem_updates:
            continue  # 当轮刚提取：用户正聊这个，回提=复读
        ext = extracted.get(k)
        if ext is not None and turn - ext < RECALL_EXTRACT_GAP:
            continue  # 闸门0：提取后不足 2 轮，"你上次说"指向太近
        hit = hits.get(k)
        if hit is None or turn - hit >= RECALL_COOLDOWN:
            return RECALL_TEMPLATES[k].format(v=memories[k]), k
    return None, None

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

# ---------------- P0-3 关系连续性承诺（Replika 教训） ----------------
# 人格层（模板/话术/记忆逻辑）变更对用户可见可查可反馈：
# ①登记此处（新条目插头部，版本号递增）②greet 首次再访主动告知（老用户一次）
# ③update_query 随时可查。流程详见 docs/PERSONA_CHANGE_PROCESS.md
XIAOMAN_VERSION = 2
XIAOMAN_UPDATES = [   # 倒序（新在前）；note 为用户向语言，登记时三端同源（mock_engine.js/app.js）
    ("2026-10-11", "学会了主动汇报——就现在这样，你一问我就能答"),
    ("2026-10-10", "学会了记事——你跟我说的大事、宠物名字、老板叫啥，我都记着"),
]
UPDATE_TELL = ("对了跟你说个事||我这两天悄悄升级了一下脑子，学的东西有点多，"
               "说不准哪句话的味儿会变。你要是觉得我哪不对劲、不像以前了，"
               "直接告诉我，我听")
def _update_reply():
    """update_query 应答：播报最近 3 条更新（日期转口语"10号"）"""
    items = "；".join(f"{int(d[8:10])}号{note}" for d, note in XIAOMAN_UPDATES[:3])
    return f"被你查到啦||最近更新就这几条：{items}||要是觉得我哪里变了不像以前，尽管说，我改"
UPDATE_REPLY = _update_reply()

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
        self._sessions = {}  # session_id -> {"last_topic": ..., "memories": {}, "crisis_count": 0, "turns": 0, "updated": time.time()}
        self._default_session = {"last_topic": None, "memories": {}, "crisis_count": 0, "turns": 0, "updated": time.time()}
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
                self._sessions[session_id] = {"last_topic": None, "memories": {}, "crisis_count": 0, "turns": 0, "updated": now}
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

    # P2-2 抑郁信号层：会话内累计计数 + 转介标记（先例同 crisis_count；内存态，跨重启清零可接受——
    # 在线路径的频控权威在客户端 history 派生，此处为二道防线）
    def increment_depress(self, session_id):
        sess = self._get_session(session_id)
        sess["depress_count"] = sess.get("depress_count", 0) + 1
        return sess["depress_count"]

    def depress_esc_fired(self, session_id):
        sess = self._get_session(session_id)
        fired = sess.get("depress_esc", False)
        sess["depress_esc"] = True   # 询问即标记（调用方只在选择转介前询问一次）
        return fired

    def next_turn(self, session_id):
        """计一轮正常对话，返回当前轮次（P0-1 首日引导用；危机/元问题不计）"""
        sess = self._get_session(session_id)
        sess["turns"] = sess.get("turns", 0) + 1
        return sess["turns"]

    def get_session(self, session_id):
        """P0-2：暴露会话 dict（memory_hits/mem_extracted/recall_count 读写）"""
        return self._get_session(session_id)

    def mark_recall(self, session_id, key, turn):
        """P0-2：标记记忆回提（引用轮次 + 总次数计数，greet 开场引用同走此口）"""
        sess = self._get_session(session_id)
        sess.setdefault("memory_hits", {})[key] = turn
        sess["recall_count"] = sess.get("recall_count", 0) + 1
        return sess["recall_count"]


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

    # P2-2 抑郁信号层（早退层，优先级 危机 > 抑郁 > meta > 更新查询 > 话题池，与客户端/离线引擎同序；
    # 在线路径客户端先行截获——此处为二道防线与直测入口）
    if DEPRESS_RE.search(user_text):
        n = _STATE.increment_depress(session_id)
        if n >= 3:
            script = DEPRESS_MID[(n - 3) % len(DEPRESS_MID)] if _STATE.depress_esc_fired(session_id) else DEPRESS_ESC[0]
        elif n == 2:
            script = DEPRESS_MID[0]
        else:
            script = DEPRESS_FIRST[0]
        return {"reply": script, "emotion": "gentle", "motion": "Nod",
                "crisis": False, "distress": True, "memory_updates": {}, "hook": None}

    if LEX["meta"].search(user_text):
        return {"reply": META_REPLY, "emotion": "shy", "motion": "Shake",
                "memory_updates": {}, "hook": "别岔开啦，说你呢——今天到底过得怎么样", "crisis": False}

    # P0-3 更新查询：播报更新记录（早退层，优先级 危机 > meta > 更新查询 > 话题池）
    if LEX["update_query"].search(user_text):
        return {"reply": UPDATE_REPLY, "emotion": "shy", "motion": "Shake",
                "memory_updates": {}, "hook": None, "crisis": False}

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
    # P0-1 首日引导：新会话前几轮用确定性 hook 自然铺钉子户（称呼→大事）
    # P0-2 主动回提：引导完成/超轮后接管 hook 通道（优先级：引导 > 回提 > 常规随机）
    turn = _STATE.next_turn(session_id)
    memories = _STATE.get_memories(session_id)
    sess = _STATE.get_session(session_id)
    # P0-2 闸门0数据：记录本轮新提取记忆的提取轮次（防隔 1 轮复读"你上次说"）
    if mem_updates:
        extracted = sess.setdefault("mem_extracted", {})
        for k in mem_updates:
            extracted[k] = turn
    # P0-2 greet 开场引用同步标记：再访首句时前端会拼"上次说{大事/宠物}"，
    # 服务端给未引用过的大事/宠物打标记（hit+总额），防止回提紧跟开场双提
    if topic == "greet":
        # P0-3 升级告知：人格层变更后首次再访主动说（只对有记忆的老用户——
        # 新用户没有"以前"可对比，直接静默登记，避免首面就"我升级了"的诡异感）
        if sess.get("told_v", 0) < XIAOMAN_VERSION:
            if memories:
                reply = UPDATE_TELL
            sess["told_v"] = XIAOMAN_VERSION
        for k in ("大事", "宠物"):
            if k in memories and sess.setdefault("memory_hits", {}).get(k) is None:
                _STATE.mark_recall(session_id, k, turn)
    if "昵称" not in memories and turn <= ONBOARD_NAME_TURNS:
        hook = ONBOARD_NAME_HOOK
    elif "大事" not in memories and turn <= ONBOARD_EVENT_TURNS:
        hook = ONBOARD_EVENT_HOOK
    else:
        hook, hit_key = _pick_recall(memories, mem_updates, topic, user_text, sess, turn)
        if hook is not None:
            _STATE.mark_recall(session_id, hit_key, turn)
        else:
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