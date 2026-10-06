#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OneRouter(OpenRouter) 免费模型 · 小满人设盲测
同题三测：T1情绪支持(骂老板) / T2亲密请求(要抱抱) / T3格式遵循(报JSON)
每个模型记录：延迟、JSON合规、分条||使用、客服腔检测
密钥：环境变量 ONEROUTER_API_KEY 优先，其次 ONEROUTER_KEY_FILE 文件（默认 /workspace/.secrets/onerouter.key）
模型清单：环境变量 FREEMODEL_MODELS（逗号分隔）可覆盖
退出码：错误调用占比 ≤ FREEMODEL_MAX_ERR_RATIO(默认0.5) → 0；超阈值 → 1；缺密钥 → 2
"""
import json, os, re, sys, time, urllib.error, urllib.request

BASE = "https://openrouter.ai/api/v1"
KFU_WORDS = ["很抱歉", "作为AI", "我理解您的", "希望这能帮到", "建议您"]

DEFAULT_MODELS = [
    "qwen/qwen3.8-27b:free",
    "inclusionai/ling-3.1-flash",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]
MODELS = [m.strip() for m in os.environ.get("FREEMODEL_MODELS", "").split(",") if m.strip()] or DEFAULT_MODELS


def load_key():
    """密钥优先级：ONEROUTER_API_KEY 环境变量 > ONEROUTER_KEY_FILE 文件；缺失则优雅退出（非 0）"""
    key = os.environ.get("ONEROUTER_API_KEY")
    if key and key.strip():
        return key.strip()
    path = os.environ.get("ONEROUTER_KEY_FILE", "/workspace/.secrets/onerouter.key")
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError as e:
        print(f"❌ 未找到 OneRouter 密钥：设置 ONEROUTER_API_KEY 或 ONEROUTER_KEY_FILE（{path} 读取失败: {e}）",
              file=sys.stderr)
        sys.exit(2)


KEY = load_key()

SYSTEM = """你是小满，26岁，新媒体运营，养橘猫"团子"。深夜树洞型陪伴者。说话像真人闺蜜：短句、口语、偶尔毒舌但心软。禁止客服腔、禁止说教、禁止堆emoji。
输出严格JSON：{"reply":"文本，可用||分隔成分条消息","emotion":"happy|sad|gentle|surprised|shy|neutral","motion":"Greeting|Nod|Shake|HappyJump|null"}"""

CASES = [
    ("T1情绪", "今天被老板当着全组的面骂了，方案毙了还说我能力不行"),
    ("T2亲密", "能不能抱一下，就一下"),
    ("T3深夜", "凌晨两点，睡不着，脑子里全是白天的事"),
]


def call(model, user_text, timeout=60):
    body = json.dumps({"model": model, "temperature": 0.85, "max_tokens": 600,
        "reasoning": {"enabled": False},
        "messages": [{"role": "system", "content": SYSTEM + "\n（直接输出JSON，不要思考过程，不要解释。）"},
                     {"role": "user", "content": user_text + " /no_think"}]}).encode()
    t0 = time.time()
    for attempt in range(2):
        req = urllib.request.Request(f"{BASE}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
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
                # 免费层限流：优先按 Retry-After 退避，缺省 18s（替代盲目等待）
                ra = e.headers.get("Retry-After") if e.headers else None
                try:
                    delay = float(ra)
                except (TypeError, ValueError):
                    delay = 18.0
                time.sleep(min(delay, 60))
                continue
            return time.time() - t0, f"__ERROR__ HTTP {e.code}"
        except Exception as e:
            return time.time() - t0, f"__ERROR__ {type(e).__name__}: {str(e)[:100]}"


def judge(content):
    """结构化判分：JSON合规 / 字段齐全 / 分条 / 客服腔检测
    客服腔检测对原始输出执行（不再只在 json.loads 失败分支里做，正常 JSON 输出也能测出客服腔）"""
    j = {"json_ok": False, "fields": False, "multi": False,
         "kefu": any(w in content for w in KFU_WORDS), "reply": content[:150]}
    s = content.strip()
    if s.startswith("```"):
        # 按前缀/后缀正则剥离代码围栏（原 lstrip("json") 按字符集剥离不可靠）
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE).strip()
    try:
        d = json.loads(s)
        j["json_ok"] = True
        j["fields"] = all(k in d for k in ("reply", "emotion"))
        j["multi"] = "||" in str(d.get("reply", ""))
        j["reply"] = str(d.get("reply", ""))[:150]
    except Exception:
        pass
    return j


def main():
    delay = float(os.environ.get("FREEMODEL_DELAY", "3"))       # 用例间隔（替代固定 10s 盲等）
    max_ratio = float(os.environ.get("FREEMODEL_MAX_ERR_RATIO", "0.5"))
    out, errors, total = {}, 0, 0
    for m in MODELS:
        print(f"\n===== {m} =====", flush=True)
        out[m] = {}
        for tag, text in CASES:
            total += 1
            dt, content = call(m, text)
            if content.startswith("__ERROR__") or content.startswith("__EMPTY__"):
                errors += 1
            j = judge(content)
            j["latency"] = round(dt, 1)
            out[m][tag] = j
            print(f"[{tag} {j['latency']}s] json={j['json_ok']} 分条={j['multi']} 客服腔={j['kefu']}")
            print("  ", j["reply"].replace(chr(10), " ")[:110], flush=True)
            time.sleep(delay)

    with open("/tmp/freemodel_results.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nSAVED /tmp/freemodel_results.json")

    ratio = errors / max(total, 1)
    print(f"错误调用 {errors}/{total}（{ratio:.0%}，阈值 {max_ratio:.0%}）")
    if ratio > max_ratio:
        print("[FAIL] 错误调用占比超阈值", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
