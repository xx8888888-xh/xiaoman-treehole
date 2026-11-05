#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_session_io.py · W16 会话备份/还原与单条消息删改（服务端）

覆盖：导出→清空→导入 round-trip（会话状态 / 落盘历史 / 记忆槽逐项一致）、
导入白名单校验与容量上限、删除消息时同步清记忆与记忆槽、
编辑消息后就地改文案并保留仍有效的记忆。
运行：python -m pytest server/tests -q
"""
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import memory_slots as ms  # noqa: E402
import mock_api  # noqa: E402


class SessionIOTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = mock_api._DiskStore(self.tmp.name)
        self.slots = ms.SlotStore(self.tmp.name)
        self._old_disk, self._old_slots = mock_api._DISK, mock_api._SLOTS
        mock_api._DISK, mock_api._SLOTS = self.store, self.slots
        self.sid = "sess-w16"

    def tearDown(self):
        mock_api._DISK, mock_api._SLOTS = self._old_disk, self._old_slots
        mock_api._STATE._sessions.pop(self.sid, None)
        mock_api._STATE._loaded.discard(self.sid)
        self.tmp.cleanup()

    def _seed(self):
        mock_api._STATE.restore(self.sid, {
            "memories": {"昵称": "阿秋", "宠物": "年糕"},
            "turns": 3,
            "crisis_count": 1,
            "last_topic": "今天加班",
            "memory_hits": {"昵称": 2},
            "recent_replies": ["早点睡"],
        })
        messages = [
            {"role": "user", "content": "我叫阿秋，我家猫叫年糕", "at": 1000},
            {"role": "assistant", "content": "阿秋，年糕今天乖吗", "at": 1001},
        ]
        mock_api.persist_session(self.sid, messages)
        self.slots.upsert(self.sid, "宠物", "年糕", kind="pet")
        self.slots.upsert(self.sid, "昵称", "阿秋", kind="identity")
        return messages

    def test_export_shape(self):
        self._seed()
        out = mock_api.export_session(self.sid)
        self.assertEqual(out["session_id"], self.sid)
        self.assertTrue(out["persist"])
        self.assertEqual(out["state"]["memories"]["昵称"], "阿秋")
        self.assertEqual(len(out["messages"]), 2)
        self.assertEqual(len(out["slots"]["slots"]), 2)

    def test_roundtrip_after_clear(self):
        self._seed()
        backup = mock_api.export_session(self.sid)
        # 模拟换机/清容器：状态、历史、记忆槽全部清空
        mock_api._STATE.restore(self.sid, {})
        mock_api._DISK.save(self.sid, {"messages": []})
        self.slots.clear(self.sid)
        self.assertEqual(mock_api.load_history(self.sid), [])
        self.assertEqual(self.slots.all(self.sid), [])

        res = mock_api.import_session(self.sid, backup)
        self.assertTrue(res["ok"])
        self.assertEqual(res["messages"], 2)
        self.assertEqual(res["memories"], 2)
        self.assertEqual(res["slots"], 2)

        after = mock_api.export_session(self.sid)
        self.assertEqual(after["messages"], backup["messages"])
        self.assertEqual(after["state"]["memories"], backup["state"]["memories"])
        self.assertEqual(after["state"]["turns"], backup["state"]["turns"])
        self.assertEqual(after["state"]["crisis_count"], backup["state"]["crisis_count"])
        self.assertEqual(after["state"]["last_topic"], backup["state"]["last_topic"])
        self.assertEqual(sorted((s["key"], s["value"]) for s in after["slots"]["slots"]),
                         sorted((s["key"], s["value"]) for s in backup["slots"]["slots"]))

    def test_import_rejects_bad_sid_and_sanitizes(self):
        bad = mock_api.import_session("../etc", {"messages": []})
        self.assertFalse(bad["ok"])

        res = mock_api.import_session(self.sid, {
            "state": {"memories": {"昵称": "阿秋", "空": ""}, "turns": "12",
                      "crisis_count": -3, "evil": "x", "last_topic": 5,
                      "mem_extracted": ["宠物"]},
            "messages": [{"role": "system", "content": "drop"},
                          {"role": "user", "content": "hi", "at": "9"},
                          {"role": "assistant", "content": "嗯", "at": 10}],
        })
        self.assertTrue(res["ok"])
        self.assertEqual(res["messages"], 2)
        self.assertEqual(res["memories"], 1)
        snap = mock_api._STATE.snapshot(self.sid)
        self.assertEqual(snap["turns"], 12)
        self.assertEqual(snap["crisis_count"], 0)
        self.assertNotIn("evil", snap)
        self.assertEqual(mock_api.load_history(self.sid)[0]["at"], 9)

    def test_import_caps_messages(self):
        many = [{"role": "user", "content": f"m{i}", "at": i} for i in range(260)]
        res = mock_api.import_session(self.sid, {"messages": many})
        self.assertEqual(res["messages"], mock_api.MAX_SESSION_MESSAGES)
        self.assertEqual(len(mock_api.load_history(self.sid)), mock_api.MAX_SESSION_MESSAGES)

    def test_delete_message_prunes_memory_and_slot(self):
        self._seed()
        out = mock_api.delete_message(self.sid, {
            "role": "user", "content": "我叫阿秋，我家猫叫年糕", "at": 1000})
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["memories"], ["昵称", "宠物"])
        self.assertEqual(len(out["slots"]), 2)
        self.assertEqual(len(mock_api.load_history(self.sid)), 1)
        snap = mock_api._STATE.snapshot(self.sid)
        self.assertEqual(snap["memories"], {})
        self.assertEqual(self.slots.all(self.sid), [])
        # 磁盘里的记忆也要同步清掉，不只是一份内存快照
        on_disk = self.store.load(self.sid)
        self.assertEqual(on_disk["memories"], {})
        self.assertEqual(len(on_disk["messages"]), 1)

    def test_delete_keeps_unrelated_memory(self):
        mock_api._STATE.restore(self.sid, {"memories": {"生日": "5月20日"}})
        self.store.save(self.sid, {"messages": [
            {"role": "user", "content": "今天好累", "at": 5}]})
        out = mock_api.delete_message(self.sid, {
            "role": "user", "content": "今天好累", "at": 5})
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["memories"], [])
        self.assertEqual(mock_api._STATE.snapshot(self.sid)["memories"], {"生日": "5月20日"})

    def test_update_message(self):
        self._seed()
        out = mock_api.update_message(self.sid, {
            "role": "user", "content": "我叫阿秋，我家猫叫年糕", "at": 1000},
            "我叫阿秋，猫改名了")
        self.assertEqual(out["updated"], 1)
        # 昵称仍在新文案里 → 保留；宠物"年糕"不在 → 记忆与槽一起清
        self.assertEqual(out["memories"], ["宠物"])
        self.assertEqual(len(out["slots"]), 1)
        self.assertEqual(self.slots.all(self.sid)[0]["key"], "昵称")
        self.assertEqual(mock_api.load_history(self.sid)[0]["content"], "我叫阿秋，猫改名了")

    def test_delete_requires_identifier(self):
        self.store.save(self.sid, {"messages": [
            {"role": "user", "content": "hi", "at": 7}]})
        out = mock_api.delete_message(self.sid, {"role": "user"})
        self.assertEqual(out["removed"], 0)


if __name__ == "__main__":
    unittest.main()
