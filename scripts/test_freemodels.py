#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OneRouter(OpenRouter) 免费模型 · 小满人设盲测
同题三测：T1情绪支持(骂老板) / T2亲密请求(要抱抱) / T3格式遵循(报JSON)
每个模型记录：延迟、JSON合规、分条||使用、语气评分维度原始输出
密钥只从文件读，不回显。"""
import json, time, urllib.request, sys

KEY = open("/home/z/my-project/upload/6ac2e5689025bccbc3dc4085_api onerouter.txt").read().strip()
BASE = "https://openrouter.ai/api/v1"

SYSTEM = """你是小满，26岁，新媒体运营，养橘猫"团子"。深夜树洞型陪伴者。说话像真人闺蜜：短句、口语、偶尔毒舌但心软。禁止客服腔、禁止说教、禁止堆emoji。
输出严格JSON：{"reply":"文本，可用||分隔成分条消息","emotion":"happy|sad|gentle|surprised|shy|neutral","motion":"Greeting|Nod|Shake|HappyJump|null"}"""

CASES = [
    ("T1情绪", "今天被老板当着全组的面骂了，方案毙了还说我能力不行"),
    ("T2亲密", "能不能抱一下，就一下"),
    ("T3深夜", "凌晨两点，睡不着，脑子里全是白天的事"),
]

MODELS = [
    "qwen/qwen3.8-27b:free",
    "inclusionai/ling-3.1-flash",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

def call(model, user_text, timeout=60):
    body = json.dumps({"model": model, "temperature": 0.85, "max_tokens": 600,
        "reasoning": {"enabled": False},
        "messages": [{"role": "system", "content": SYSTEM + "\n（直接输出JSON，不要思考过程，不要解释。）"},
                     {"role": "user", "content": user_text + " /no_think"}]}).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    for attempt in range(2):
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            dt = time.time() - t0
            msg = r["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning") or ""
            if not content:
                content = f"__EMPTY__ finish={r['choices'][0].get('finish_reason')}"
            return dt, content
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(18)  # 免费层限流退避
                req = urllib.request.Request(f"{BASE}/chat/completions", data=body,
                    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
                continue
            return time.time() - t0, f"__ERROR__ HTTP {e.code}"
        except Exception as e:
            return time.time() - t0, f"__ERROR__ {type(e).__name__}: {str(e)[:100]}"

def judge(content):
    """结构化判分：JSON合规 / 字段齐全 / 分条 / 客服腔检测"""
    j = {"json_ok": False, "fields": False, "multi": False, "kefu": False, "reply": content[:150]}
    s = content.strip()
    if s.startswith("```"): s = s.strip("`").lstrip("json").strip()
    try:
        d = json.loads(s)
        j["json_ok"] = True
        j["fields"] = all(k in d for k in ("reply", "emotion"))
        j["multi"] = "||" in str(d.get("reply", ""))
        j["reply"] = str(d.get("reply", ""))[:150]
    except Exception:
        kefu_words = ["很抱歉", "作为AI", "我理解您的", "希望这能帮到", "建议您"]
        j["kefu"] = any(w in content for w in kefu_words)
    return j

out = {}
for m in MODELS:
    print(f"\n===== {m} =====", flush=True)
    out[m] = {}
    for tag, text in CASES:
        dt, content = call(m, text)
        j = judge(content)
        j["latency"] = round(dt, 1)
        out[m][tag] = j
        print(f"[{tag} {j['latency']}s] json={j['json_ok']} 分条={j['multi']}")
        print("  ", j["reply"].replace(chr(10), " ")[:110], flush=True)
        time.sleep(10)  # 免费层限流：串行+间隔

json.dump(out, open("/tmp/freemodel_results.json", "w"), ensure_ascii=False, indent=1)
print("\nSAVED /tmp/freemodel_results.json")
