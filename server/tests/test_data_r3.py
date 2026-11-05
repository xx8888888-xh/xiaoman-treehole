#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_data_r3.py · w19 服务端数据收口（data/sessions 记忆槽）

验收口径与前端同一条：导出 → 真实 JSON 落盘 → 清空（删文件 + 清会话）→
导入 → 逐字段比对，必须 100% 还原；旧版结构（只有 messages/memories）导入不崩。
不 mock 文件层：断言直接读磁盘上的 session-*.json / slots-*.json，
并用**全新** store 实例重读（等价于进程重启后的冷加载）。
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
import mock_api  # noqa: E402

SLOT_FIELDS = ("id", "key", "value", "kind", "emotion", "confidence",
               "source", "created", "updated", "last_used", "uses")

STATE_FIELDS = ("last_topic", "memories", "crisis_count", "depress_count",
                "depress_esc", "turns", "told_v", "memory_hits", "mem_extracted",
                "recall_count", "recent_replies", "mem_aware_ts", "risk_state")


class DataR3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = mock_api._DiskStore(self.root)
        self.slots = ms.SlotStore(self.root)
        self._old_disk, self._old_slots = mock_api._DISK, mock_api._SLOTS
        mock_api._DISK, mock_api._SLOTS = self.store, self.slots
        self.sid = "sess-w19"
        self._reset_state()

    def tearDown(self):
        mock_api._DISK, mock_api._SLOTS = self._old_disk, self._old_slots
        self._reset_state()
        self.tmp.cleanup()

    def _reset_state(self):
        mock_api._STATE._sessions.pop(self.sid, None)
        mock_api._STATE._loaded.discard(self.sid)

    def _session_file(self):
        return self.store._path(self.sid)

    def _slots_file(self):
        return self.slots._path(self.sid)

    def _seed_state(self):
        mock_api._STATE.restore(self.sid, {
            "last_topic": "加班和年糕",
            "memories": {"昵称": "阿秋", "宠物": "年糕", "生日": "5月20日"},
            "crisis_count": 1,
            "depress_count": 2,
            "depress_esc": True,
            "turns": 7,
            "told_v": ["阿秋", "年糕"],
            "memory_hits": {"昵称": 5, "宠物": 2},
            "mem_extracted": ["昵称", "宠物"],
            "recall_count": 6,
            "recent_replies": ["早点睡", "记得吃饭"],
            "mem_aware_ts": 1700000000,
            "risk_state": {"level": "medium", "score": 3},
        })

    def _seed_slots_file(self):
        """直接往磁盘写一份完整槽文件（11 个字段全覆盖），再让 store 冷读。"""
        slots = [
            {"id": "s1", "key": "昵称", "value": "阿秋", "kind": "pin", "emotion": 5,
             "confidence": 1.0, "source": "rule", "created": 1700000000,
             "updated": 1700000100, "last_used": 1700000200, "uses": 8},
            {"id": "s2", "key": "宠物", "value": "年糕", "kind": "fact", "emotion": 4,
             "confidence": 0.9, "source": "model", "created": 1700000001,
             "updated": 1700000101, "last_used": 0, "uses": 0},
            {"id": "s3", "key": "状态", "value": "最近失眠", "kind": "fact", "emotion": 3,
             "confidence": 1.0, "source": "rule", "created": 1700000002,
             "updated": 1700000102, "last_used": 1700000202, "uses": 2},
        ]
        self._slots_file().write_text(
            json.dumps({"version": 1, "updated": 1700000100, "slots": slots}, ensure_ascii=False),
            encoding="utf-8")
        self.slots._mem.pop(self.sid, None)  # 冷读：不命中缓存
        loaded = self.slots.all(self.sid)
        self.assertEqual(len(loaded), 3)
        return loaded

    def _seed(self):
        self._seed_state()
        mock_api.persist_session(self.sid, [
            {"role": "user", "content": "我叫阿秋，我家猫叫年糕", "at": 1000},
            {"role": "assistant", "content": "阿秋，年糕今天乖吗？", "at": 1001},
            {"role": "user", "content": "最近一直失眠", "at": 1002},
        ])
        return self._seed_slots_file()

    def _wipe(self):
        """模拟换机/清容器：删掉磁盘文件 + 清会话缓存"""
        for p in (self._session_file(), self._slots_file()):
            if p.exists():
                p.unlink()
        self._reset_state()
        mock_api._STATE.restore(self.sid, {})
        self.slots._mem.pop(self.sid, None)

    def _assert_state_equal(self, want, got):
        for key in STATE_FIELDS:
            self.assertEqual(key in want, key in got, "state 字段存在性不一致: %s" % key)
            if key in want:
                self.assertEqual(want[key], got[key], "state.%s 不一致" % key)

    def _assert_slots_equal(self, want, got):
        self.assertEqual(len(want), len(got), "槽条数不一致")
        by_id = {s["id"]: s for s in got}
        for w in want:
            g = by_id.get(w["id"])
            self.assertIsNotNone(g, "槽 %s 丢失" % w["id"])
            for field in SLOT_FIELDS:
                self.assertEqual(g[field], w[field], "slot.%s 字段不一致（%s）" % (field, w["id"]))
                self.assertIs(type(g[field]), type(w[field]), "slot.%s 类型不一致（%s）" % (field, w["id"]))

    def test_roundtrip_exact_after_wipe(self):
        seed_slots = self._seed()
        backup = mock_api.export_session(self.sid)
        self.assertTrue(self._session_file().exists(), "会话必须真的落盘")
        self.assertTrue(self._slots_file().exists(), "记忆槽必须真的落盘")
        seed_state = backup["state"]

        self._wipe()
        self.assertFalse(self._session_file().exists())
        self.assertFalse(self._slots_file().exists())

        res = mock_api.import_session(self.sid, backup)
        self.assertTrue(res["ok"])
        self.assertEqual(res["messages"], 3)
        self.assertEqual(res["slots"], 3)

        after = mock_api.export_session(self.sid)
        # 逐字段（不是只比 key/value）
        self._assert_state_equal(seed_state, after["state"])
        self._assert_slots_equal(seed_slots, after["slots"]["slots"])
        self.assertEqual(after["messages"], backup["messages"], "落盘历史必须逐条一致")

        # 冷加载：全新实例从磁盘重读，不能只是内存缓存对得上
        cold_state = mock_api._DiskStore(self.root).load(self.sid)
        cold_slots = ms.SlotStore(self.root).all(self.sid)
        self.assertEqual(cold_state["messages"], backup["messages"])
        for key in ("memories", "turns", "memory_hits", "risk_state", "recent_replies"):
            self.assertEqual(cold_state[key], seed_state[key], "磁盘 state.%s 不一致" % key)
        self._assert_slots_equal(seed_slots, cold_slots)

        # 磁盘上的文件确实是可解析的 JSON
        self.assertEqual(json.loads(self._session_file().read_text(encoding="utf-8"))["messages"],
                         backup["messages"])
        raw_slots = json.loads(self._slots_file().read_text(encoding="utf-8"))
        self.assertEqual(len(raw_slots["slots"]), 3)

    def test_legacy_backup_without_slots(self):
        """旧版备份只有 messages/memories：导入不崩，槽保持空，状态至少还原记忆与轮次"""
        legacy = {
            "session_id": self.sid,
            "messages": [
                {"role": "user", "content": "在吗", "at": 10},
                {"role": "assistant", "content": "在", "at": 11},
            ],
            "state": {"memories": {"昵称": "阿秋"}, "turns": 2},
        }
        res = mock_api.import_session(self.sid, legacy)
        self.assertTrue(res["ok"], "旧版备份不能一导入就崩")
        self.assertEqual(res["messages"], 2)
        self.assertEqual(res["slots"], 0)
        self.assertEqual(mock_api.load_history(self.sid), legacy["messages"])
        self.assertEqual(mock_api._STATE.snapshot(self.sid)["memories"], {"昵称": "阿秋"})

    def test_delete_then_cold_reload_is_consistent(self):
        """删一条消息：历史、内存记忆、记忆槽、磁盘四层同一结果"""
        self._seed()
        out = mock_api.delete_message(
            self.sid, {"role": "user", "content": "我叫阿秋，我家猫叫年糕", "at": 1000})
        self.assertEqual(out["removed"], 1)
        self.assertEqual(sorted(out["memories"]), ["宠物", "昵称"])
        self.assertEqual(len(out["slots"]), 2)

        cold = mock_api._DiskStore(self.root).load(self.sid)
        self.assertEqual(len(cold["messages"]), 2)
        # 只清"由这条消息产生"的记忆；生日与失眠状态与此条无关，必须留着
        self.assertEqual(cold["memories"], {"生日": "5月20日"})
        remaining = ms.SlotStore(self.root).all(self.sid)
        self.assertEqual([s["key"] for s in remaining], ["状态"])
        self.assertEqual([s["key"] for s in mock_api.export_session(self.sid)["slots"]["slots"]],
                         ["状态"])

    def test_update_then_cold_reload_is_consistent(self):
        """改一条消息：旧文案独有的记忆/槽清掉，新文案仍保留的留"""
        self._seed()
        out = mock_api.update_message(
            self.sid, {"role": "user", "content": "我叫阿秋，我家猫叫年糕", "at": 1000},
            "我叫阿秋，猫改名了")
        self.assertEqual(out["updated"], 1)
        self.assertEqual(out["memories"], ["宠物"])

        cold = mock_api._DiskStore(self.root).load(self.sid)
        self.assertEqual(cold["messages"][0]["content"], "我叫阿秋，猫改名了")
        self.assertEqual(cold["memories"], {"昵称": "阿秋", "生日": "5月20日"})
        remaining = ms.SlotStore(self.root).all(self.sid)
        self.assertEqual(sorted(s["key"] for s in remaining), ["昵称", "状态"])

    def test_malformed_slots_file_does_not_crash(self):
        """槽文件损坏（真实坏 JSON）→ 冷读按空库处理，不阻断对话"""
        self._seed()
        self._slots_file().write_text("{not json", encoding="utf-8")
        self.slots._mem.pop(self.sid, None)
        self.assertEqual(self.slots.all(self.sid), [])


if __name__ == "__main__":
    unittest.main()
