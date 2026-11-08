#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_memory_slots.py · W11 长期记忆槽（服务端）

覆盖：规则提取（昵称/宠物/老板/大事/喜好/生日/显式记住）、垃圾不落库、
同键覆盖、泛指不覆盖具体、相关检索、注入格式、容量淘汰、
用户否定删除、落盘往返与损坏容错。
运行：python -m pytest server/tests -q
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import memory_slots as ms  # noqa: E402
import prompt_core as pc  # noqa: E402


class ExtractTest(unittest.TestCase):
    def test_core_fields(self):
        self.assertEqual(ms.extract_slots("你可以叫我阿秋就行")["昵称"], "阿秋")
        self.assertEqual(ms.extract_slots("我家猫叫年糕，三岁")["宠物"], "猫：年糕")
        self.assertEqual(ms.extract_slots("我家狗叫煤球")["宠物"], "狗：煤球")
        self.assertEqual(ms.extract_slots("我养了一只橘猫")["宠物"], "橘猫")
        self.assertEqual(ms.extract_slots("我们老板叫张三")["老板"], "张三")
        self.assertEqual(ms.extract_slots("我下周要考试了")["大事"], "下周要考试")
        self.assertEqual(ms.extract_slots("我很喜欢吃辣")["喜好"], "喜欢吃辣")
        self.assertEqual(ms.extract_slots("我的生日是5月20日")["生日"], "5月20日")

    def test_explicit_remember(self):
        out = ms.extract_slots("帮我记住，下周三给我妈打电话")
        self.assertEqual(out.get("记事"), "下周三给我妈打电话")

    def test_scenery_not_stored_as_pet(self):
        # 路上看到的猫不是 ta 的宠物，不能落库
        self.assertEqual(ms.extract_slots("今天看到一只特别胖的橘猫"), {})

    def test_emotion_weight(self):
        self.assertEqual(ms.infer_emotion("昵称", "阿秋"), 5)
        self.assertGreater(ms.infer_emotion("大事", "下周要面试"),
                           ms.infer_emotion("喜好", "喜欢吃辣"))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = ms.SlotStore()
        self.sid = "s-memory-unit"

    def test_upsert_replaces_same_key(self):
        self.store.upsert(self.sid, "昵称", "阿秋")
        self.store.upsert(self.sid, "昵称", "小秋")
        slots = [s for s in self.store.all(self.sid) if s["key"] == "昵称"]
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["value"], "小秋")

    def test_model_updates_contract(self):
        applied = self.store.apply_updates(self.sid, {"昵称": "阿秋", "宠物": "年糕"})
        self.assertEqual(applied, {"昵称": "阿秋", "宠物": "年糕"})
        self.assertEqual(self.store.get(self.sid, "昵称")["source"], "model")

    def test_generic_pet_does_not_overwrite_specific(self):
        self.store.upsert(self.sid, "宠物", "年糕")
        self.store.upsert(self.sid, "宠物", "猫")
        self.assertEqual(self.store.get(self.sid, "宠物")["value"], "年糕")

    def test_multi_pet_coexist_with_species(self):
        """R10：猫叫年糕 + 狗叫煤球 两条并存，注入时都标明物种。"""
        self.store.ingest(self.sid, "我家猫叫年糕")
        self.store.ingest(self.sid, "我家狗叫煤球")
        self.assertEqual(sorted(self.store.pet_names(self.sid)), sorted(["年糕", "煤球"]))
        block = self.store.render(self.sid, "我家猫和狗今天都在闹")
        self.assertIn("猫叫年糕", block)
        self.assertIn("狗叫煤球", block)

    def test_multi_pet_correction_stays_separate(self):
        """纠正同一只（同物种）以新说法为准，但不影响另一只。"""
        self.store.ingest(self.sid, "我家猫叫团子")
        self.store.ingest(self.sid, "我家狗叫煤球")
        self.store.ingest(self.sid, "我家猫其实叫年糕不是团子")
        names = sorted(self.store.pet_names(self.sid))
        self.assertEqual(names, sorted(["年糕", "煤球"]))
        value = self.store.get(self.sid, "宠物")["value"]
        self.assertIn("猫：年糕", value)
        self.assertIn("狗：煤球", value)

    def test_pronoun_pet_correction(self):
        """'其实它叫年糕不是团子'：保留物种，只换名字。"""
        self.store.ingest(self.sid, "我家猫叫团子")
        self.store.ingest(self.sid, "其实它叫年糕不是团子")
        self.assertEqual(self.store.pet_names(self.sid), ["年糕"])
        self.assertEqual(self.store.get(self.sid, "宠物")["value"], "猫：年糕")

    def test_model_cannot_overwrite_multi_pet(self):
        self.store.ingest(self.sid, "我家猫叫年糕")
        self.store.ingest(self.sid, "我家狗叫煤球")
        self.store.apply_updates(self.sid, {"宠物": "团子"}, source="model")
        self.assertEqual(sorted(self.store.pet_names(self.sid)), sorted(["年糕", "煤球"]))

    def test_delete_one_of_two_pets(self):
        self.store.ingest(self.sid, "我家猫叫年糕")
        self.store.ingest(self.sid, "我家狗叫煤球")
        removed = self.store.delete_from_text(self.sid, "把煤球删了")
        self.assertEqual([s["value"] for s in removed], ["煤球"])
        self.assertEqual(self.store.pet_names(self.sid), ["年糕"])
        self.assertNotIn("煤球", self.store.render(self.sid, "我家猫和狗"))

    def test_recall_is_topic_related(self):
        self.store.upsert(self.sid, "大事", "下周要考试")
        self.store.upsert(self.sid, "喜好", "喜欢吃辣")
        hits = self.store.recall(self.sid, "一想到考试就紧张")
        self.assertIn("大事", [h["key"] for h in hits])
        self.assertNotIn("喜好", [h["key"] for h in hits])
        # 中文别名：说"我家猫"应召回宠物槽
        self.store.upsert(self.sid, "宠物", "年糕")
        hits = self.store.recall(self.sid, "我家猫今天又把杯子推了")
        self.assertIn("宠物", [h["key"] for h in hits])

    def test_render_format(self):
        self.assertEqual(self.store.inject_block(self.sid, ""), "")
        self.store.upsert(self.sid, "大事", "下周要考试")
        text = self.store.render(self.sid, "考试好紧张")
        self.assertIn("- 大事：下周要考试（今天）", text)
        self.store.upsert(self.sid, "昵称", "阿秋")
        block = self.store.inject_block(self.sid, "")
        self.assertTrue(block.startswith("【你记得的关于 ta 的事】"))
        self.assertIn("阿秋", block)

    def test_empty_query_returns_pins(self):
        self.store.upsert(self.sid, "昵称", "阿秋")
        self.store.upsert(self.sid, "喜好", "喜欢吃辣")
        keys = [s["key"] for s in self.store.recall(self.sid, "")]
        self.assertEqual(keys[0], "昵称")

    def test_capacity_eviction_keeps_pins(self):
        self.store.upsert(self.sid, "昵称", "阿秋")
        for i in range(80):
            self.store.upsert(self.sid, "话题%d" % i, "内容%d" % i,
                              kind="fact", emotion=1)
        slots = self.store.all(self.sid)
        self.assertEqual(len(slots), ms.MAX_SLOTS)
        self.assertIsNotNone(self.store.get(self.sid, "昵称"))

    def test_delete_by_user_text(self):
        self.store.upsert(self.sid, "生日", "5月20日")
        self.store.upsert(self.sid, "宠物", "年糕")
        self.store.upsert(self.sid, "老板", "张三")
        self.store.upsert(self.sid, "喜好", "喜欢吃辣")
        removed = self.store.delete_from_text(self.sid, "忘了我的生日吧")
        self.assertEqual([s["key"] for s in removed], ["生日"])
        removed = self.store.delete_from_text(self.sid, "把年糕删了")
        self.assertEqual([s["value"] for s in removed], ["年糕"])
        # "别记我老板的事"：类别指代命中
        removed = self.store.delete_from_text(self.sid, "别记我老板的事了")
        self.assertEqual([s["key"] for s in removed], ["老板"])
        # "删掉这个"：删除最近更新的那条
        removed = self.store.delete_from_text(self.sid, "算了，删掉这个")
        self.assertEqual([s["key"] for s in removed], ["喜好"])

    def test_no_delete_on_common_phrase(self):
        self.store.upsert(self.sid, "记事", "带钥匙")
        self.assertEqual(self.store.delete_from_text(self.sid, "我自己会记得，别忘了带钥匙"), [])

    def test_ingest_reports_updates_and_deletions(self):
        self.store.ingest(self.sid, "我家猫叫年糕")
        out = self.store.ingest(self.sid, "把年糕删了")
        self.assertEqual(out["updates"], {})
        self.assertEqual(out["deleted"], [{"key": "宠物", "value": "年糕"}])

    def test_inject_into_next_turn_system_prompt(self):
        """完整链路：提取 → 槽 → 检索 → 注入下一轮 system（动态段只在末尾）。"""
        self.store.ingest(self.sid, "我下周要考试")
        self.store.ingest(self.sid, "复习不进去")
        lines = self.store.render(self.sid, "一想到考试就紧张")
        self.assertIn("下周要考试", lines)
        prompt = pc.system_prompt(memory_section=lines)
        self.assertTrue(prompt.startswith(pc.CORE_PROMPT))
        # 核心段里的“记忆规则”也含同名字样，用 rfind 定位真正追加在末尾的注入块
        self.assertGreater(prompt.rfind("【你记得的关于 ta 的事】"), len(pc.CORE_PROMPT))

    def test_export_import(self):
        self.store.upsert(self.sid, "昵称", "阿秋")
        data = self.store.export(self.sid)
        self.assertEqual(data["version"], ms.SlotStore.VERSION)
        other = ms.SlotStore()
        self.assertEqual(other.import_(self.sid, data), 1)
        self.assertEqual(other.get(self.sid, "昵称")["value"], "阿秋")


class NameGuardTest(unittest.TestCase):
    """W22：ta 的宠物名零容忍——输出侧兜底 + 存储侧防模型回写。"""

    def test_guard_removes_own_pet_name(self):
        self.assertEqual(
            ms.guard_own_pet_name("年糕啊，听起来就很好rua||"
                                  "我养了只橘猫叫团子，天天跟我抢沙发", ["年糕"]),
            "年糕啊，听起来就很好rua||我养了只橘猫，天天跟我抢沙发")
        self.assertNotIn("团子", ms.guard_own_pet_name("我家团子前阵子也这样", ["年糕"]))

    def test_guard_keeps_ta_pet_named_tuanzi(self):
        self.assertEqual(ms.guard_own_pet_name("团子今天不吃饭", ["团子"]),
                         "团子今天不吃饭")

    def test_guard_noop_without_user_pet_name(self):
        text = "我家团子最爱睡沙发"
        self.assertEqual(ms.guard_own_pet_name(text, []), text)
        self.assertEqual(ms.guard_own_pet_name(text, ["猫"]), text)

    def test_model_cannot_rename_user_pet(self):
        store = ms.SlotStore()
        store.ingest("s", "我家猫叫年糕")
        store.apply_updates("s", {"宠物": "团子"}, source="model")
        self.assertEqual(store.pet_names("s"), ["年糕"])
        # ta 自己纠正仍然以新说法为准
        store.ingest("s", "我家猫叫汤圆")
        self.assertEqual(store.pet_names("s"), ["汤圆"])


class PersistenceTest(unittest.TestCase):
    def test_roundtrip_and_corrupt_tolerance(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = ms.SlotStore(d)
            s1.upsert("u1", "昵称", "阿秋")
            s2 = ms.SlotStore(d)
            self.assertEqual(s2.get("u1", "昵称")["value"], "阿秋")

            p = Path(d) / ("slots-%s.json" % ms._sid_hash("u2"))
            p.write_text("{not json", encoding="utf-8")
            s3 = ms.SlotStore(d)
            self.assertEqual(s3.all("u2"), [], "损坏文件按空库处理，不阻断对话")
            s3.upsert("u2", "昵称", "小秋")
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["version"], 1)


class TombstoneTest(unittest.TestCase):
    """墓碑：删槽 → 独立落盘；候选过滤 + 回复分句扫描；只封值与唯一别名。"""

    def test_forget_persists_separately_and_filters_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            sid = "u-tomb"
            s1 = ms.SlotStore(d)
            s1.ingest(sid, "我家猫叫年糕")
            removed = s1.delete_from_text(sid, "把年糕忘了")
            self.assertEqual([s["value"] for s in removed], ["年糕"])
            tomb = Path(d) / ("suppress-%s.json" % ms._sid_hash(sid))
            self.assertTrue(tomb.exists())
            raw = tomb.read_text(encoding="utf-8")
            self.assertIn("年糕", raw)
            self.assertNotIn("把年糕忘了", raw, "墓碑不许存原句")
            items = s1.forgotten_items(sid)
            self.assertEqual([(x["key"], x["value"]) for x in items],
                             [("宠物", "年糕")])
            # 新实例复载：墓碑仍在，候选被过滤
            s2 = ms.SlotStore(d)
            self.assertEqual(s2.forgotten_items(sid)[0]["value"], "年糕")
            self.assertNotIn("宠物", [h["key"] for h in s2.recall(sid, "我家猫呢")])

    def test_reingest_same_value_stays_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.upsert("u", "宠物", "年糕")
            s.delete_from_text("u", "把年糕删了")
            s.upsert("u", "宠物", "年糕")  # 又被提取回来
            self.assertNotIn("年糕", [h["value"] for h in s.recall("u", "猫")])

    def test_partial_pet_forget_keeps_other(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.ingest("u", "我家猫叫年糕")
            s.ingest("u", "我家狗叫煤球")
            s.delete_from_text("u", "把年糕删了")
            hit = s.recall("u", "我家猫和狗")
            self.assertEqual([h["value"] for h in hit if h["key"] == "宠物"],
                             ["狗：煤球"])

    def test_scrub_reply_drops_only_matching_clause(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.upsert("u", "宠物", "年糕")
            s.delete_from_text("u", "把年糕忘了")
            out = s.scrub_reply("u", "年糕又拆家了吧？||今天下雨了，你带伞没")
            self.assertEqual(out, "今天下雨了，你带伞没")
            # 泛称“猫”不封：不含墓碑值的句子原样保留
            self.assertEqual(s.scrub_reply("u", "猫都爱拆家"),
                             "猫都爱拆家")

    def test_scrub_returns_empty_when_all_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.upsert("u", "宠物", "年糕")
            s.delete_from_text("u", "把年糕忘了")
            self.assertEqual(s.scrub_reply("u", "年糕今天特别乖。"), "")

    def test_revive_only_by_user_input(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.upsert("u", "宠物", "年糕")
            s.delete_from_text("u", "把年糕忘了")
            # 系统/模型路径（upsert / apply_updates）绝不复活
            s.upsert("u", "宠物", "年糕")
            s.apply_updates("u", {"宠物": "年糕"}, source="model")
            self.assertTrue(s.forgotten_items("u"))
            # 负面评价不算复活（同一组对照）
            s.ingest("u", "我不喜欢年糕了")
            self.assertTrue(s.forgotten_items("u"))
            # 用户亲口打出该值 + 确认意图 → 撤销墓碑并允许重新入槽
            s.ingest("u", "我又养了只猫叫年糕")
            self.assertEqual(s.forgotten_items("u"), [])
            self.assertTrue(any("年糕" in h["value"]
                                for h in s.recall("u", "猫")))

    def test_forget_phrase_does_not_revive(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.upsert("u", "宠物", "年糕")
            s.delete_from_text("u", "把年糕忘了")
            s.ingest("u", "把年糕忘了吧")
            self.assertTrue(s.forgotten_items("u"))

    def test_correction_tombstone_expires_after_20_turns(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.ingest("u", "我家猫叫团子")
            s.tick("u")
            s.ingest("u", "其实它叫年糕不是团子")
            items = [x for x in s.forgotten_items("u")
                     if x["reason"] == "correction"]
            self.assertEqual([(x["key"], x["value"]) for x in items],
                             [("宠物", "团子")])
            self.assertEqual(items[0]["expires_turn"], 23)
            self.assertEqual(s.scrub_reply("u", "团子今天很乖。"), "")
            for _ in range(20):
                s.tick("u")
            self.assertEqual(s.scrub_reply("u", "团子今天很乖。"),
                             "团子今天很乖。")

    def test_alias_minimal_new_pet_not_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.ingest("u", "我家猫叫年糕")
            s.delete_from_text("u", "把年糕忘了")
            item = s.forgotten_items("u")[0]
            self.assertEqual(item["aliases"], [])
            self.assertNotIn("猫", item["value"])
            s.upsert("u", "宠物", "兔子")
            self.assertIn("兔子", [h["value"] for h in s.recall("u", "兔子")])

    def test_tombstone_schema_has_no_raw_text(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            s.upsert("u", "宠物", "年糕")
            s.delete_from_text("u", "把年糕忘了")
            raw = json.loads((Path(d) / ("suppress-%s.json" % ms._sid_hash("u")))
                             .read_text(encoding="utf-8"))
            self.assertEqual(set(raw), {"version", "updated", "turn", "items"})
            for it in raw["items"]:
                self.assertEqual(set(it), {"key", "value", "aliases",
                                           "ts", "reason", "expires_turn"})
            body = json.dumps(raw, ensure_ascii=False)
            self.assertNotIn("把年糕忘了", body)
            self.assertNotIn("message", body)
            self.assertNotIn("user_id", body)

    def test_tomb_buffer_follows_longest_alias(self):
        with tempfile.TemporaryDirectory() as d:
            s = ms.SlotStore(d)
            self.assertEqual(s.tomb_buffer("u"), 2)  # 无墓碑默认 2
            s.upsert("u", "老板", "张三")
            s.delete_from_text("u", "把张三忘了")
            self.assertEqual(s.tomb_buffer("u"), 2)  # 2 字名字
            s.upsert("u", "老板", "克里斯蒂娜")
            s.delete_from_text("u", "把克里斯蒂娜忘了")
            self.assertEqual(s.tomb_buffer("u"), 5)
            s.upsert("u", "老板", "阿卜杜拉买买提")
            s.delete_from_text("u", "把阿卜杜拉买买提忘了")
            self.assertEqual(s.tomb_buffer("u"), 6)  # 上限 6


class SensitiveGateTest(unittest.TestCase):
    """W22 敏感槽门控：状态类默认不回带，本轮受邀才提；非敏感槽不受影响。"""

    def setUp(self):
        self.store = ms.SlotStore()
        self.sid = "s-sensitive-unit"

    def _keys(self, query=""):
        return [s["key"] for s in self.store.recall(self.sid, query)]

    def test_uninvited_sensitive_not_recalled(self):
        self.store.ingest(self.sid, "最近总是失眠")
        # 不点名但语义触及（考试压力）不是邀请：门控必须挡住
        self.assertNotIn("状态", self._keys("考试好难"))
        self.assertNotIn("失眠", self.store.render(self.sid, "考试好难"))
        # 无 query 的兜底注入同样不许漏出敏感槽
        self.assertNotIn("状态", self._keys())

    def test_invited_sensitive_recalled(self):
        self.store.ingest(self.sid, "最近总是失眠")
        self.assertIn("状态", self._keys("最近总觉得累"))
        self.assertIn("失眠", self.store.render(self.sid, "最近总觉得累"))

    def test_non_sensitive_recalled_as_before(self):
        self.store.ingest(self.sid, "我们老板叫张三")
        self.store.ingest(self.sid, "我家猫叫年糕")
        self.assertIn("老板", self._keys("今天上班被领导怼了"))
        self.assertIn("宠物", self._keys("年糕又在拆家"))

    def test_mentioned_in_text(self):
        """W23：本轮用户已说过的值不再回带；宠物按名字判，泛指不算。"""
        self.assertTrue(ms.mentioned_in_text("老板", "张三", "张三今天又发火了"))
        self.assertFalse(ms.mentioned_in_text("老板", "张三", "老板今天又发火了"))
        self.assertTrue(ms.mentioned_in_text("宠物", "猫：年糕；狗：煤球", "年糕又在拆家"))
        self.assertFalse(ms.mentioned_in_text("宠物", "猫：年糕", "猫又拆家了"))

    def test_predicate_is_shared(self):
        self.assertTrue(ms.is_sensitive("状态", "失眠"))
        self.assertFalse(ms.is_sensitive("老板", "张三"))
        self.assertTrue(ms.is_invited("状态", "失眠", "最近总是失眠"))
        self.assertFalse(ms.is_invited("状态", "失眠", "考试好难"))
        self.assertTrue(ms.is_invited("老板", "张三", "考试好难"))


class PetSpeciesTest(unittest.TestCase):
    """W24 宠物物种：从槽位结构取物种（带名字取「物种：名字」的物种段；泛称值本身即物种）。"""

    def test_species_from_slot_structure(self):
        self.assertEqual(ms.pet_species("猫：年糕"), {"猫"})
        self.assertEqual(ms.pet_species("狗：煤球"), {"狗"})
        self.assertEqual(ms.pet_species("养只狗"), {"狗"})
        self.assertEqual(ms.pet_species("年糕"), set(),
                         "具体名字但没有物种段 → 结构里没有物种信息")

    def test_mismatch_only_when_both_sides_known(self):
        self.assertTrue(ms.pet_species_mismatch("猫：年糕", "想养只狗"))
        self.assertFalse(ms.pet_species_mismatch("猫：年糕", "猫粮还没买"))
        self.assertFalse(ms.pet_species_mismatch("年糕", "想养只狗"),
                         "结构里没有物种 → 不误杀，也不凭空补一个")

    def test_hint_supplies_missing_species(self):
        """扁平结构（mock）：物种单独存在会话字段，补上后才判。"""
        self.assertTrue(ms.pet_species_mismatch("年糕", "想养只狗", "猫"))
        self.assertFalse(ms.pet_species_mismatch("年糕", "猫粮还没买", "猫"))

    def test_specific_only(self):
        self.assertTrue(ms.pet_specific("年糕"))
        self.assertFalse(ms.pet_specific("养只狗"))
        self.assertFalse(ms.pet_specific("猫"))

    def test_recall_filters_mismatched_pet(self):
        store = ms.SlotStore()
        sid = "pet-species-recall"
        store.ingest(sid, "我家猫叫年糕")
        self.assertNotIn("宠物",
                         [s["key"] for s in store.recall(sid, "狗粮还没买")])
        self.assertIn("宠物",
                      [s["key"] for s in store.recall(sid, "猫粮还没买")])


class NamedValueTest(unittest.TestCase):
    """W24 点名判定：只认结构里真实存在的具体值；泛指/基词不算点名。"""

    def test_pet_name_counts_only_by_name(self):
        self.assertEqual(ms.named_values([("宠物", "猫：年糕")], "你说年糕是不是欠揍"),
                         [("宠物", "年糕")])
        self.assertEqual(ms.named_values([("宠物", "养只狗")], "想养只狗"), [])
        self.assertEqual(ms.named_values([("宠物", "猫：年糕")], "它今天干嘛了"), [])

    def test_base_word_is_not_a_name(self):
        self.assertEqual(ms.named_values([("老板", "老板")], "老板今天又发火"), [])
        self.assertEqual(ms.named_values([("老板", "张三")], "张三今天又发火"),
                         [("老板", "张三")])

    def test_non_entity_keys_ignored(self):
        self.assertEqual(ms.named_values([("状态", "失眠")], "失眠好难受"), [])


if __name__ == "__main__":
    unittest.main()
