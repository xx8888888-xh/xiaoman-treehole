#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mock_echo.py · W11 第三轮：mock 轻量插值（实体回声）与反过拟合

mock 是没填 key 时的默认通道，固定语料池不能只会"然后呢"。本文件锁定：
1. 保守抽取：动物/食物/身体/天气/时间/工作物/地点 + 追问回指
2. 抽错守卫：一点点/差一点/三点头/猫粮/拒绝建议里的"方案"不得误抽
3. 回声真的把实体填进模板，且不留占位符
4. 反过拟合：同一句输入喂 5 次不同回复 ≥4、句长有方差；50 条批量重复率 <20%
"""
import re
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import mock_api as m  # noqa: E402

BRACE_RE = re.compile(r"\{[a-z]\}")

# 50 条带具体细节的输入（用户日常真会说的话），用于批量反过拟合
DETAIL_INPUTS = [
    # 宠物 8
    "我家橘猫今天不吃饭", "楼下金毛又拆家了", "我家狗昨天吐了", "新养的兔子老咬人",
    "邻居家的狗一直乱叫", "我家猫掉毛掉得厉害", "那只柯基又胖了一圈", "我家狗不让人抱",
    # 食物 10
    "今天吃了火锅", "公司楼下新开了家关东煮", "晚上点了螺蛳粉外卖", "我妈寄来一箱西瓜",
    "刚买了杯奶茶", "夜宵吃了烧烤", "中午吃了牛肉面", "点了份炸鸡外卖",
    "买了盒雪糕", "晚饭是麻辣烫",
    # 身体 8
    "这两天胃疼得厉害", "最近老是头疼", "嗓子疼了三天", "昨天有点低烧",
    "腰疼得直不起来", "牙疼了一整晚", "眼睛疼得睁不开", "好像过敏了",
    # 天气 8
    "今天突然降温", "下班下了好大的雨", "外面刮风刮得厉害", "今天闷热得不行",
    "昨晚下了雪", "雾霾大得看不清", "打了半天雷", "台风要来了",
    # 时间 8
    "昨晚三点才睡", "凌晨两点还在赶稿", "昨天一点才到家", "今天五点就醒了",
    "昨晚十二点才下班", "半夜一点才睡", "上周六点就起了", "今晚十点才吃饭",
    # 工作 8
    "这周的周报改了四遍", "甲方又加了一版需求", "今天连开三场会议",
    "简历投了二十份没回音", "代码写到半夜", "预算又被砍了", "合同卡在法务", "排期全乱了",
]


def fresh(sid):
    m._STATE._sessions.pop(sid, None)
    m._STATE._loaded.discard(sid)
    return sid


class ExtractDetailTest(unittest.TestCase):
    def test_animal_and_action(self):
        d = m.extract_detail("我家橘猫今天不吃饭")
        self.assertEqual(d["kind"], "animal")
        self.assertEqual(d["v"], "橘猫")
        self.assertEqual(d["a"], "不吃饭")
        self.assertEqual(m.extract_detail("楼下金毛又拆家了")["v"], "金毛")

    def test_food_body_weather_time_work_place(self):
        self.assertEqual(m.extract_detail("公司楼下新开了家关东煮")["v"], "关东煮")
        self.assertEqual(m.extract_detail("这两天胃疼得厉害")["v"], "胃疼")
        self.assertEqual(m.extract_detail("今天突然降温")["v"], "降温")
        self.assertEqual(m.extract_detail("昨晚三点才睡")["v"], "三点")
        self.assertEqual(m.extract_detail("这周的周报改了四遍")["v"], "周报")
        self.assertEqual(m.extract_detail("今天地铁上有人让座")["v"], "地铁")

    def test_weather_phrase_normalized(self):
        for text, want in (("下班下了好大的雨", "下雨"), ("昨晚下了雪", "下雪"),
                           ("打了半天雷", "打雷")):
            d = m.extract_detail(text)
            self.assertEqual(d["kind"], "weather")
            self.assertEqual(d["v"], want)

    def test_guess_reference(self):
        d = m.extract_detail("你猜我昨晚几点睡的")
        self.assertEqual(d["kind"], "guess")
        self.assertEqual(d["focus"], "time")
        self.assertEqual(m.extract_detail("你猜我当时什么反应")["focus"], "generic")
        reply = m.make_reply([{"role": "user", "content": "你猜我昨晚几点睡的"}],
                             fresh("echo-guess"))["reply"]
        self.assertTrue("猜" in reply or "几点" in reply, reply)

    def test_conservative_no_false_extraction(self):
        """宁可抽不出，也不能抽错词拼出荒唐回复。"""
        for text in (
            "差一点就迟到了",      # 时间误抽"一点"
            "一点点小事而已",      # 同上
            "三点头表示同意",      # 裸"X点"
            "三点水的字怎么写",    # 同上
            "该买猫粮了",          # 物品当宠物
            "猫砂盆该换了",        # 同上
            "今天心情不好",        # 无实体
            "不用给我方案",        # 拒绝建议里的"方案"不是工作实体
            "别劝我，说句人话就行",
        ):
            self.assertIsNone(m.extract_detail(text), text)

    def test_refusal_guard_keeps_pool_off_advice(self):
        reply = m.make_reply([{"role": "user", "content": "不用给我方案"}],
                             fresh("echo-refuse"))["reply"]
        self.assertNotIn("方案", reply)
        self.assertNotIn("建议", reply)


class EchoRenderTest(unittest.TestCase):
    def test_placeholders_filled(self):
        for text in DETAIL_INPUTS:
            pool = m.detail_pool(text)
            self.assertIsNotNone(pool, text)
            for item in pool:
                self.assertIsNone(BRACE_RE.search(item), "%r 残留占位符：%r" % (text, item))
            d = m.extract_detail(text)
            self.assertTrue(any(d["v"] in item for item in pool),
                            "%r 回声必须包含实体 %r" % (text, d["v"]))

    def test_echo_reply_contains_entity_and_is_short(self):
        sid = fresh("echo-short")
        for text in ("我家橘猫今天不吃饭", "今天突然降温", "昨晚三点才睡"):
            reply = m.make_reply([{"role": "user", "content": text}], sid)["reply"]
            d = m.extract_detail(text)
            self.assertIn(d["v"], reply, reply)
            self.assertLessEqual(len(reply.replace("||", "")), 80)


class AntiOverfitTest(unittest.TestCase):
    def test_same_input_five_times_is_not_a_broken_record(self):
        """同一句输入连喂 5 次：不同回复 ≥4，句长要有方差（≥2 字差）。"""
        for i, text in enumerate(DETAIL_INPUTS[:12]):
            sid = fresh("anti-single-%d" % i)
            outs = [m.make_reply([{"role": "user", "content": text}], sid)["reply"]
                    for _ in range(5)]
            self.assertGreaterEqual(len(set(outs)), 4, "%r 复读：%r" % (text, outs))
            self.assertGreaterEqual(max(map(len, outs)) - min(map(len, outs)), 2,
                                    "%r 句长无方差：%r" % (text, outs))

    def test_fifty_inputs_batch_repetition_rate(self):
        """50 条输入 × 5 次：整体重复率 <20%（unique/total ≥80%）。"""
        all_replies = []
        for i, text in enumerate(DETAIL_INPUTS):
            sid = fresh("anti-batch-%d" % i)
            outs = [m.make_reply([{"role": "user", "content": text}], sid)["reply"]
                    for _ in range(5)]
            self.assertGreaterEqual(len(set(outs)), 4, "%r 复读：%r" % (text, outs))
            all_replies.extend(outs)
        total = len(all_replies)
        unique = len(set(all_replies))
        self.assertLess((total - unique) / total, 0.20,
                        "整体重复率过高：%d/%d" % (total - unique, total))


if __name__ == "__main__":
    unittest.main(verbosity=2)
