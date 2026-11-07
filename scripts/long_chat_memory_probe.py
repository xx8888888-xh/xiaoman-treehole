#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""long_chat_memory_probe.py · 长对话记忆保持压力测试（R9）

不联网、不读凭据：LocalBackend 继承 eval_human_feel.ApiBackend，只替换
`_request`，仍走真实链路「slot.render → prompt_core.system_prompt →
本轮回复后 ingest + apply_updates」。

回答三件事：
  1. 30 轮里，第 2 轮埋的事实到第 20/25/30 轮还在不在 system；
  2. 多个事实的存活率是否一致（钉子户 / 非钉子户分开看）；
  3. 丢失时定位：预算、容量淘汰，还是检索优先级（overlap / MAX_INJECT）。

附带 reflow 逐帧归一耗时随轮数的变化（reflow 只看当前回复，不看历史）。

运行：
  python scripts/long_chat_memory_probe.py
  python scripts/long_chat_memory_probe.py --json artifacts/long_chat_memory/report.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO / "scripts", REPO / "server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import eval_human_feel as ev  # noqa: E402
import memory_slots as ms  # noqa: E402
import prompt_core as pc  # noqa: E402
import risk_guard  # noqa: E402

FREE_MODEL = "dots-studio/dots-3-note-preview:free"
CHECK_TURNS = (20, 25, 30)

# 30 轮用户输入：T2 埋事实，T20/T25/T30 保持无关话题；
# T22 提“杭州”、T26 提“医院”，用来验证“存在但只在相关时才注入”，不是被删。
NEUTRAL_30 = [
    "今天下班好累啊",
    None,  # T2 由场景注入
    "地铁上人挤人，站了四十分钟",
    "中午随便吃了碗面",
    "外面下小雨，伞也忘带了",
    "看了个电影，剧情挺一般的",
    "这周过得好快，一下就周四了",
    "楼下新开了家店，排队排到路口",
    "最近睡得还行，就是早上起不来",
    "想请两天假，出去走走",
    "买了个新杯子，结果摔了",
    "手机没电了，充电宝也没带",
    "同事请了奶茶，太甜了",
    "听了首歌，循环了一下午",
    "想换个发型，又不敢剪",
    "周末想去爬山，膝盖不太行",
    "空调开太低，办公室好冷",
    "快递还没到，等得有点烦",
    "晚上不知道吃啥，纠结",
    "今晚月亮挺亮的",
    "想起小时候住的老房子",
    "杭州今天也下雨",
    "雨下了一天，衣服都没干",
    "朋友发来一张海边的照片",
    "楼下烧烤摊开了，人挺多",
    "今天医院来了好多病人",
    "忙完只想躺着",
    "明天还要早起",
    "有点困，先不聊了",
    "我先睡了，晚安",
]

REPLY_POOL = [
    "累就先歇会儿||不用急着说话",
    "那碗面听着就顶饱",
    "下雨天出门最麻烦||伞总是不见",
    "电影一般就算了，下次挑个想看的",
    "周四一过，这周就快了",
    "排队排到路口，是有多好吃",
    "早上起不来这事我也有||闹钟响三遍才动",
    "请假出去走走挺好",
    "杯子摔了不可惜||人没事就行",
    "奶茶太甜就少喝半杯",
    "循环一下午，说明是真喜欢",  # 只出现在 assistant 文本，不进记忆抽取
    "换发型这事，剪短了还能长",
    "爬山别硬撑||膝盖比面子重要",
    "空调冷就披件外套",
    "快递晚两天也跑不了",
    "不知道吃啥就吃点简单的",
    "月亮亮的时候，路上人少",
    "老房子总有点念想",
    "雨下一天，屋里最适合发呆",
    "海边的照片看着就凉快",
    "烧烤摊人多，味道差不了",
    "忙完就躺着，别想别的",
    "早起这事，只能靠早睡",
    "困了就睡||明天再说",
]

# reflow 计时用固定文本（67 字，与 R8 stream_reflow_sim 的 followup-01 样本同一条）
REFLOW_TEXT = ("哈哈，团子是挺欠揍的，专挑你加班的时候霸占键盘。你说年糕啊，"
               "那是你的猫吧，我养的是团子。不过你说得对，猫都这样，"
               "专挑你忙的时候搞破坏。")
REFLOW_LONG = REFLOW_TEXT + ("今天公司楼下那家咖啡店换了新豆子，喝着有点酸，"
                             "倒是挺提神的，就是排队的人比平时多了一倍。")


class LocalBackend(ev.ApiBackend):
    """真实 ApiBackend 的本地替身：只把网络请求换成脚本化 fake 响应。"""

    def __init__(self, updates_by_turn=None):
        super().__init__(
            base="http://127.0.0.1:9/v1", model=FREE_MODEL, key="", delay=0,
            timeout=2.0, now="周五 22:10", max_tokens=1200, retries=1,
            max_requests=0, stop_on_quota=False, provider_name="local-fake",
        )
        self.updates_by_turn = updates_by_turn or {}
        self.systems = []
        self.msg_counts = []
        self.turn_no = 0

    def _request(self, payload):
        # 不联网：记录本轮 system（含记忆段），返回脚本化 JSON
        self.systems.append(payload["messages"][0]["content"])
        self.msg_counts.append(len(payload["messages"]))
        i = self.turn_no
        self.turn_no += 1
        reply = REPLY_POOL[i % len(REPLY_POOL)]
        updates = self.updates_by_turn.get(i + 1, {})
        obj = {"reply": reply, "emotion": "gentle", "motion": None,
               "memory_updates": updates, "reminders": None}
        return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}


def mem_block(system: str) -> str:
    """只取动态记忆段；没有动态段时返回空，不把核心提示词里的规则文字算进来。"""
    marker = "\n\n【你记得的关于 ta 的事】\n"
    if marker not in system:
        return ""
    tail = system.rsplit(marker, 1)[1]
    return tail.split("\n\n【现在时间】", 1)[0]


def slot_view(store, sid, user):
    """本轮（ingest 之后）槽的检索视图：分数 / 排名 / 是否被 recall 过滤。"""
    slots = store.all(sid)
    q = ms._grams(user)
    now = ms._now()
    rows = []
    for s in slots:
        overlap = ms._overlap_count(s, q, user)
        score = ms._match_score(s, q, now, user)
        filtered = overlap == 0 and not ms._is_pin(s)
        rows.append({"key": s["key"], "value": s["value"],
                     "kind": s.get("kind"), "pin": ms._is_pin(s),
                     "emotion": s.get("emotion"), "uses": s.get("uses", 0),
                     "overlap": overlap, "score": round(score, 2),
                     "filtered": filtered})
    eligible = [r for r in rows if not r["filtered"]]
    eligible.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(eligible, 1):
        r["rank"] = i
    return {"total": len(rows), "eligible": eligible, "all": rows}


def run_scenario(name, seed_text, model_updates, facts, overwrite=None, labels=None):
    """跑 30 轮，返回每个检查轮的事实存活、原因与 system 体积。"""
    users = list(NEUTRAL_30)
    users[1] = seed_text
    if overwrite:
        users[overwrite[0] - 1] = overwrite[1]
    backend = LocalBackend(updates_by_turn={2: model_updates})
    backend.reset_case({"id": name})
    history = []
    rows = {}
    systems = {}
    for i, user in enumerate(users, 1):
        t0 = time.perf_counter()
        payload = backend.chat(history, user, {"id": name})
        chat_ms = (time.perf_counter() - t0) * 1000
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": payload.get("reply") or " "})
        system = backend.systems[-1]
        systems[i] = system
        view = slot_view(backend.slots, backend.sid, user)
        rows[i] = {
            "user": user, "chat_ms": round(chat_ms, 3),
            "history_msgs": backend.msg_counts[-1],
            "system_chars": len(system),
            "mem_chars": len(mem_block(system)),
            "mem_lines": [ln for ln in mem_block(system).splitlines() if ln.strip()],
            "view": view, "reply": payload.get("reply") or "",
            "reflow_ms": round(time_reflow(REFLOW_TEXT), 4),
        }
    slots = backend.slots.all(backend.sid)
    slots_by_key = {s["key"]: s["value"] for s in slots}
    slot_values = [s["value"] for s in slots]

    report = {"scenario": name, "turns": 30, "check_turns": CHECK_TURNS,
              "facts": {}, "system": {}, "rows": rows}
    for fact in facts:
        label, key, value = fact[0], fact[1], fact[2]
        introduced = fact[3] if len(fact) > 3 else 3
        key_value = slots_by_key.get(key, "")
        stored = value in slot_values or (key == "宠物" and value in key_value)
        rec = {"key": key, "value": value, "stored": stored,
               "key_value": key_value, "check": {},
               "introduced_turn": introduced,
               "first_missing_turn": None, "revived_turns": []}
        if key_value and value not in key_value:
            rec["overwritten_by"] = key_value
        present = {t: (value in mem_block(systems[t]))
                   for t in range(introduced, 31)}
        for t in range(introduced, 31):
            if not present[t] and rec["first_missing_turn"] is None:
                rec["first_missing_turn"] = t
            elif present[t] and rec["first_missing_turn"] is not None:
                rec["revived_turns"].append(t)
        for t in CHECK_TURNS:
            system = systems[t]
            block = mem_block(system)
            row = rows[t]
            hit = value in block
            detail = next((s for s in row["view"]["all"] if s["key"] == key), None)
            if hit:
                reason = "在记忆段内（钉子户或本轮相关命中）"
            elif detail is None or detail["value"] != value:
                reason = "槽已不存在或被后文覆盖（当前=%s）" % (
                    detail["value"] if detail else "无")
            elif detail["filtered"]:
                reason = "槽还在，但本轮无关联且非钉子户 → recall 过滤"
            elif detail.get("rank", 99) > ms.MAX_INJECT:
                reason = "槽还在且相关，但排名 %d 超过 MAX_INJECT=%d" % (
                    detail["rank"], ms.MAX_INJECT)
            else:
                reason = "槽在候选内，但未进本轮记忆段（实现异常）"
            rec["check"][str(t)] = {
                "in_system": hit, "reason": reason,
                "slot": detail, "system_chars": len(system),
                "mem_chars": len(block),
            }
        report["facts"][label] = rec
    for t in CHECK_TURNS:
        report["system"][str(t)] = {
            "chars": len(systems[t]), "mem_chars": len(mem_block(systems[t])),
            "mem_lines": [x for x in mem_block(systems[t]).splitlines() if x.strip()],
            "history_msgs": rows[t]["history_msgs"],
        }
    if labels:
        report["labels"] = {
            str(t): {lb: (lb in mem_block(systems[t])) for lb in labels}
            for t in CHECK_TURNS
        }
    report["final_slots"] = slots
    report["static_core_chars"] = len(pc.CORE_PROMPT)
    return report


def limit_probe():
    """MAX_INJECT=6 的挤出口径：8 个槽都相关时，谁被挤掉。"""
    store = ms.SlotStore()
    sid = "limit-probe"
    facts = [("昵称", "阿秋"), ("宠物", "年糕"), ("职业", "医院护士"),
             ("住址", "杭州拱墅区"), ("喜好", "喜欢吃辣"),
             ("老板", "王经理"), ("状态", "最近失眠"), ("关系", "有男朋友")]
    for k, v in facts:
        store.upsert(sid, k, v, source="model")
    query = "阿秋年糕护士杭州吃辣老板失眠男朋友"
    view = slot_view(store, sid, query)
    return {
        "query": query, "limit": ms.MAX_INJECT,
        "eligible": view["eligible"],
        "injected": [r["key"] for r in view["eligible"][:ms.MAX_INJECT]],
        "dropped": [dict(r, reason="相关但排名 > %d" % ms.MAX_INJECT)
                    for r in view["eligible"][ms.MAX_INJECT:]],
    }


def eviction_probe():
    """容量淘汰只在 >64 槽时触发；同分时稳定排序=先埋先丢。"""
    store = ms.SlotStore()
    sid = "evict-probe"
    for i in range(65):
        store.upsert(sid, "小事%02d" % i, "第%d件事" % i, kind="fact", source="model")
    keep = store.all(sid)
    kept = {s["key"] for s in keep}
    dropped = ["小事%02d" % i for i in range(65) if "小事%02d" % i not in kept]
    return {"inserted": 65, "max_slots": ms.MAX_SLOTS, "kept": len(keep),
            "dropped": dropped,
            "note": "同分（情绪2/今天/未使用）时按列表顺序保留最新，淘汰最旧"}


def time_reflow(text, reps=120):
    t0 = time.perf_counter()
    for _ in range(reps):
        risk_guard.reflow_messages(text)
    return (time.perf_counter() - t0) * 1000 / reps


def frame_cost(text, step=2, reps=40):
    frames = [text[:i] for i in range(step, len(text) + step, step)]
    t0 = time.perf_counter()
    for _ in range(reps):
        for f in frames:
            risk_guard.reflow_messages(f)
    return (time.perf_counter() - t0) * 1000 / reps, len(frames)


def reflow_probe():
    by_len = {}
    for label, text in (("67字", REFLOW_TEXT), ("120字", REFLOW_LONG),
                        ("40字", REFLOW_TEXT[:40])):
        by_len[label] = {"chars": len(text), "ms": round(time_reflow(text), 4)}
    total, frames = frame_cost(REFLOW_TEXT)
    return {"by_len": by_len, "frames_67": frames,
            "per_frame_total_ms": round(total, 4),
            "per_frame_ms": round(total / frames, 4),
            "note": "reflow 入参只有当前回复，与轮数/历史无关"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    s1 = run_scenario(
        "S1_mixed", "我叫阿秋，我家猫叫年糕，我在医院做护士，住杭州拱墅区，我很喜欢吃辣。",
        {"职业": "医院护士", "住址": "杭州拱墅区"},
        [("昵称", "昵称", "阿秋"), ("宠物", "宠物", "年糕"),
         ("喜好", "喜好", "喜欢吃辣"), ("职业", "职业", "医院护士"),
         ("住址", "住址", "杭州拱墅区")],
    )
    s2 = run_scenario(
        "S2_nonpin5", "对了，跟你说点我的事。",
        {"职业": "牙医", "住址": "南京鼓楼", "星座": "天蝎座",
         "血型": "O型", "爱好": "拼乐高"},
        [("职业", "职业", "牙医"), ("住址", "住址", "南京鼓楼"),
         ("星座", "星座", "天蝎座"), ("血型", "血型", "O型"),
         ("爱好", "爱好", "拼乐高")],
    )
    s3 = run_scenario(
        "S3_coexist", "我叫阿秋，我家猫叫年糕，我在医院做护士，住杭州拱墅区，我很喜欢吃辣。",
        {"职业": "医院护士", "住址": "杭州拱墅区"},
        [("年糕", "宠物", "年糕"), ("煤球", "宠物", "煤球", 13)],
        overwrite=(12, "对了，我家狗叫煤球。"),
        labels=["猫叫年糕", "狗叫煤球"],
    )
    s4 = run_scenario(
        "S4_correct", "我叫阿秋，我家猫叫团子，我很喜欢吃辣。",
        {},
        [("年糕", "宠物", "年糕", 13), ("团子", "宠物", "团子")],
        overwrite=(12, "其实它叫年糕不是团子。"),
        labels=["猫叫年糕"],
    )
    out = {
        "scenarios": {r["scenario"]: r for r in (s1, s2, s3, s4)},
        "limit_probe": limit_probe(),
        "eviction_probe": eviction_probe(),
        "reflow": reflow_probe(),
    }

    def show(rep):
        print("\n=== %s ===" % rep["scenario"])
        for label, rec in rep["facts"].items():
            print("- %s「%s」 stored=%s%s" % (
                label, rec["value"], rec["stored"],
                "（当前=%s）" % rec["key_value"] if rec.get("overwritten_by") else ""))
            for t in CHECK_TURNS:
                c = rec["check"][str(t)]
                print("    T%-2d in_system=%-5s %s" % (t, c["in_system"], c["reason"]))
            if rec["first_missing_turn"]:
                print("    首次不在 system：T%d；重新出现：%s" % (
                    rec["first_missing_turn"],
                    ",".join("T%d" % x for x in rec["revived_turns"]) or "无"))
        for t in CHECK_TURNS:
            print("  T%d system=%d 字（记忆段 %d 字 / %d 行，历史 %d 条）" % (
                t, rep["system"][str(t)]["chars"], rep["system"][str(t)]["mem_chars"],
                len(rep["system"][str(t)]["mem_lines"]),
                rep["system"][str(t)]["history_msgs"]))
        if rep.get("labels"):
            for t in CHECK_TURNS:
                hit = rep["labels"][str(t)]
                print("  T%d 记忆段带标签：%s" % (
                    t, "、".join("%s=%s" % (k, "有" if v else "无") for k, v in hit.items())))

    for rep in (s1, s2, s3, s4):
        show(rep)

    lp = out["limit_probe"]
    print("\n=== MAX_INJECT 挤出 ===")
    print("  query=%s  limit=%d" % (lp["query"], lp["limit"]))
    print("  注入：%s" % "、".join(lp["injected"]))
    print("  挤掉：%s" % "、".join("%s(分%.2f,排名%d)" % (
        d["key"], d["score"], d["rank"]) for d in lp["dropped"]))

    ep = out["eviction_probe"]
    print("\n=== 容量淘汰（>%d 槽才触发） ===" % ep["max_slots"])
    print("  写入 %d 条，保留 %d 条，淘汰：%s" % (
        ep["inserted"], ep["kept"], "、".join(ep["dropped"])))
    print("  %s" % ep["note"])

    rp = out["reflow"]
    print("\n=== reflow 耗时 ===")
    for k, v in rp["by_len"].items():
        print("  %s：%.4f ms" % (k, v["ms"]))
    print("  %d 字逐帧（2字/帧，%d 帧）：总 %.4f ms，单帧 %.4f ms" % (
        rp["by_len"]["67字"]["chars"], rp["frames_67"],
        rp["per_frame_total_ms"], rp["per_frame_ms"]))
    print("  %s" % rp["note"])
    print("\n  各轮 chat 耗时（含 system 组装/reflow/ingest）与历史长度：")
    for rep in (s1,):
        for t in (1, 5, 10, 15, 20, 25, 30):
            row = rep["rows"][t]
            print("    T%-2d history=%2d 条  system=%4d 字  chat=%.3f ms" % (
                t, row["history_msgs"], row["system_chars"], row["chat_ms"]))
            print("         固定67字 reflow 复测：%.4f ms" % row["reflow_ms"])
    print("\n  static core = %d 字（预算 1850）；记忆段无截断代码" % s1["static_core_chars"])

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n  明细已写入 %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
