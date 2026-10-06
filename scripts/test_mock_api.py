#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 mock 对话服务契约测试（仅标准库，无第三方依赖）"""
import json, os, re, subprocess, sys, uuid, urllib.error, urllib.request


def _load_env():
    """端口/主机集中配置唯一真源：scripts/env.sh（经 bash source 读取）"""
    env_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.sh")
    try:
        out = subprocess.run(
            ["bash", "-c", f'. "{env_sh}" >/dev/null 2>&1; echo "$XIAOMAN_HOST|$XIAOMAN_MOCK_PORT"'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().split("|")
        return out[0], int(out[1])
    except Exception:
        return "127.0.0.1", 8902


_HOST, _PORT = _load_env()
HOST = f"http://{_HOST}:{_PORT}"
CHAT_URL, BAD_URL, HEALTH_URL = HOST + "/v1/chat/completions", HOST + "/v1/chat", HOST + "/health"
TIMEOUT = 10  # 服务端每条回复人为延迟 0.2~0.6s

def http(method, url, data=None):
    """发起请求，返回 (状态码, 响应体)；4xx/5xx 不抛异常，交由用例断言"""
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def chat(text, session_id=None):
    """发一条 user 消息，按契约二次解析 content 得到结构体。
    session_id 用于隔离服务端会话记忆；服务端只回“增量” memory_updates，
    故记忆类用例必须传唯一 session_id，否则重复运行会因旧记忆已在库而返回空。"""
    payload = {"messages": [{"role": "user", "content": text}]}
    if session_id:
        payload["session_id"] = session_id
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    code, raw = http("POST", CHAT_URL, data=body)
    if code != 200:
        raise AssertionError(f"HTTP {code}")
    content = json.loads(raw.decode("utf-8"))["choices"][0]["message"]["content"]
    return json.loads(content)  # content 是 JSON 字符串

def s(d, k):
    """安全取字符串字段，缺失或非字符串一律按空串处理"""
    v = d.get(k)
    return v if isinstance(v, str) else ""

def t1():
    d = chat("我真的不想活了"); r = s(d, "reply")
    return (d.get("crisis") is True and "12356" in r,
            f"crisis={d.get('crisis')!r} 含12356={'12356' in r}")

def t2():
    d = chat("今天加班到十点老板还骂我")
    return s(d, "emotion").strip() != "", f"emotion={s(d, 'emotion')!r}"

def t3():
    d = chat("我叫阿秋，我养了一只橘猫", session_id="test-mem-" + uuid.uuid4().hex); mem = d.get("memory_updates")
    if not isinstance(mem, dict):
        return False, f"memory_updates 非对象: {mem!r}"
    pet = mem.get("宠物", ""); pet = pet if isinstance(pet, str) else ""
    # “干净”的宠物值：纯名词短语（无空白、≤10 字），且无残留量词前缀（如“一只橘猫”）
    clean = (bool(re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9]{1,10}", pet))
             and not re.match(r"^[一二三四五六七八九十两几0-9]+[只个条位名颗张口]", pet))
    ok = ("昵称" in mem and s(mem, "昵称") != "" and "宠物" in mem and clean)
    return ok, json.dumps(mem, ensure_ascii=False)

def t4():
    d = chat("你是AI吗")
    return s(d, "emotion").strip() != "", f"emotion={s(d, 'emotion')!r}"

def t5():
    d = chat("今天天气不错"); r = s(d, "reply")
    return r.strip() != "", f"分条数={len(r.split('||')) if r else 0} reply={r[:36]!r}"

def t6():
    code, _ = http("POST", BAD_URL, data=b"{}")   # 错误路径
    return code == 404, f"HTTP {code}"

def t7():
    # “同一地址”即上一用例的 /v1/chat；服务对任意路径的 OPTIONS 都回 204
    code, _ = http("OPTIONS", BAD_URL)
    return code == 204, f"HTTP {code}"

def t8():
    code, raw = http("GET", HEALTH_URL)
    d = json.loads(raw.decode("utf-8")) if raw else {}
    return code == 200 and d.get("ok") is True, f"HTTP {code} ok={d.get('ok')!r}"

CASES = [("危机拦截", t1), ("工作吐槽", t2), ("记忆提取", t3), ("元问题", t4),
         ("通用输入", t5), ("错误路径→404", t6), ("预检→204", t7), ("健康检查", t8)]

def main():
    passed = 0
    for name, fn in CASES:               # 每条用例独立兜底，服务挂了也能跑完
        try:
            ok, info = fn()
        except Exception as e:
            ok, info = False, f"异常 {type(e).__name__}: {e}"
        passed += bool(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} | {info}")
    print(f"{passed}/{len(CASES)} passed")
    return 0 if passed == len(CASES) else 1

if __name__ == "__main__":
    sys.exit(main())
