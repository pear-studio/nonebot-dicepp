import unittest
import pytest

import sys
from pathlib import Path
dicepp_path = Path(__file__).parent.parent
if str(dicepp_path) not in sys.path:
    sys.path.insert(0, str(dicepp_path))

from module.common.log_db import (
    upsert_log, get_logs_by_group, get_log_by_id, set_recording, delete_log,
    insert_record, fetch_records, delete_records_by_message_id
)
from datetime import datetime


@pytest.mark.integration
class TestLogCommand(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def setup_bot(self):
        from core.bot import Bot
        from core.config import ConfigItem, CFG_MASTER

        self.bot = Bot("test_log_bot")
        self.bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
        self.bot.cfg_helper.save_config()
        self.bot.delay_init_debug()

        yield

        self.bot.shutdown_debug()
        import os
        test_path = self.bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(test_path)

    def _create_log(self, log_id: str, group_id: str, name: str, recording: bool = False):
        from module.common import log_db
        from module.common.log_command import _ensure_log_dir, _init_log_db

        _ensure_log_dir()
        _init_log_db()

        now = datetime.now().isoformat()
        payload = {
            "id": log_id,
            "group_id": group_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
            "recording": 1 if recording else 0,
            "record_begin_at": now,
            "last_warn": "",
        }
        conn = log_db.get_connection()
        upsert_log(conn, payload)
        conn.commit()
        conn.close()
        return payload

    def test_new_log(self):
        from module.common.log_db import get_log_id_by_name
        from module.common import log_db
        from module.common.log_command import _ensure_log_dir, _init_log_db

        _ensure_log_dir()
        _init_log_db()

        log_id = "test_log_1"
        group_id = "test_group"
        self._create_log(log_id, group_id, "MyLog")

        conn = log_db.get_connection()
        found_id = get_log_id_by_name(conn, group_id, "MyLog")
        conn.close()

        self.assertEqual(found_id, log_id)

    def test_on_off_flow(self):
        from module.common import log_db

        log_id = "test_log_2"
        group_id = "test_group"

        self._create_log(log_id, group_id, "FlowLog", recording=False)

        conn = log_db.get_connection()
        set_recording(conn, log_id, True)
        conn.commit()

        log = get_log_by_id(conn, log_id)
        self.assertEqual(log["recording"], 1)

        set_recording(conn, log_id, False)
        conn.commit()

        log = get_log_by_id(conn, log_id)
        self.assertEqual(log["recording"], 0)

        conn.close()

    def test_list_logs(self):
        from module.common import log_db

        group_id = "test_group_list"
        self._create_log("log_a", group_id, "LogA")
        self._create_log("log_b", group_id, "LogB")

        conn = log_db.get_connection()
        logs = get_logs_by_group(conn, group_id)
        conn.close()

        self.assertEqual(len(logs), 2)

    def test_delete_log(self):
        from module.common import log_db

        log_id = "log_to_delete"
        group_id = "test_group_del"
        self._create_log(log_id, group_id, "ToDelete")

        conn = log_db.get_connection()
        delete_log(conn, log_id)
        conn.commit()

        log = get_log_by_id(conn, log_id)
        conn.close()

        self.assertIsNone(log)

    def test_append_record_via_helper(self):
        from module.common.log_command import append_log_record
        from module.common import log_db

        log_id = "log_append"
        group_id = "test_group_append"
        self._create_log(log_id, group_id, "AppendLog", recording=True)

        append_log_record(self.bot, group_id, "user123", "TestUser", "Hello world", is_bot=False)

        conn = log_db.get_connection()
        records = fetch_records(conn, log_id)
        conn.close()

        self.assertTrue(len(records) > 0)
        self.assertEqual(records[-1]["content"], "Hello world")

    def test_delete_by_message_id_via_helper(self):
        from module.common.log_command import append_log_record, delete_log_record_by_message_id
        from module.common import log_db

        log_id = "log_del_msg"
        group_id = "test_group_del_msg"
        self._create_log(log_id, group_id, "DelMsgLog", recording=True)

        append_log_record(self.bot, group_id, "user123", "TestUser", "Hello", message_id="msg_123", is_bot=False)
        append_log_record(self.bot, group_id, "user456", "AnotherUser", "World", message_id="msg_456", is_bot=False)

        delete_log_record_by_message_id(self.bot, group_id, "msg_123")

        conn = log_db.get_connection()
        records = fetch_records(conn, log_id)
        conn.close()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["message_id"], "msg_456")
