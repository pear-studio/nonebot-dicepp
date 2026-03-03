import unittest
import pytest
import sqlite3
import os
import tempfile
from datetime import datetime
from typing import Dict, Any

import log_db


class TestLogDb(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def setup_temp_db(self):
        self.temp_dir = tempfile.mkdtemp()
        original_get_connection = log_db.get_connection
        original_log_dir = log_db.LOG_DIR
        original_db_path = log_db.LOG_DB_PATH

        log_db.LOG_DIR = self.temp_dir
        log_db.LOG_DB_PATH = os.path.join(self.temp_dir, "log.db")

        self.conn = log_db.get_connection()

        yield

        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

        log_db.LOG_DIR = original_log_dir
        log_db.LOG_DB_PATH = original_db_path

    def _make_log_payload(self, log_id: str, group_id: str, name: str, **kwargs) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        return {
            "id": log_id,
            "group_id": group_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
            "recording": 0,
            "record_begin_at": now,
            "last_warn": "",
            **kwargs,
        }

    def test_insert_and_fetch(self):
        payload = self._make_log_payload("log1", "group1", "TestLog")
        log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        log_db.insert_record(
            self.conn,
            "log1",
            time=datetime.now().isoformat(),
            user_id="user1",
            nickname="User",
            content="Hello",
            source="user",
            message_id="msg1",
        )
        self.conn.commit()

        records = log_db.fetch_records(self.conn, "log1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["content"], "Hello")

    def test_delete_by_message_id(self):
        payload = self._make_log_payload("log1", "group1", "TestLog")
        log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        log_db.insert_record(
            self.conn,
            "log1",
            time=datetime.now().isoformat(),
            user_id="user1",
            nickname="User",
            content="Hello",
            source="user",
            message_id="msg1",
        )
        self.conn.commit()

        deleted = log_db.delete_records_by_message_id(self.conn, "log1", "msg1")
        self.assertEqual(deleted, 1)
        records = log_db.fetch_records(self.conn, "log1")
        self.assertEqual(len(records), 0)

    def test_upsert_log(self):
        payload = self._make_log_payload("log1", "group1", "TestLog")
        log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        payload["name"] = "UpdatedName"
        log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        logs = log_db.get_logs_by_group(self.conn, "group1")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["name"], "UpdatedName")

    def test_get_logs_by_group(self):
        for i in range(3):
            payload = self._make_log_payload(f"log{i}", "group1", f"Log{i}")
            log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        logs = log_db.get_logs_by_group(self.conn, "group1")
        self.assertEqual(len(logs), 3)

    def test_set_recording(self):
        payload = self._make_log_payload("log1", "group1", "TestLog")
        log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        log_db.set_recording(self.conn, "log1", True)
        self.conn.commit()

        log = log_db.get_log_by_id(self.conn, "log1")
        self.assertEqual(log["recording"], 1)

        log_db.set_recording(self.conn, "log1", False)
        self.conn.commit()

        log = log_db.get_log_by_id(self.conn, "log1")
        self.assertEqual(log["recording"], 0)

    def test_delete_records_for_log(self):
        payload = self._make_log_payload("log1", "group1", "TestLog")
        log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        log_db.insert_record(
            self.conn,
            "log1",
            time=datetime.now().isoformat(),
            user_id="user1",
            nickname="User",
            content="Hello",
            source="user",
            message_id="msg1",
        )
        self.conn.commit()

        log_db.delete_records_for_log(self.conn, "log1")
        self.conn.commit()

        records = log_db.fetch_records(self.conn, "log1")
        self.assertEqual(len(records), 0)

    def test_delete_log(self):
        payload = self._make_log_payload("log1", "group1", "TestLog")
        log_db.upsert_log(self.conn, payload)
        self.conn.commit()

        log_db.delete_log(self.conn, "log1")
        self.conn.commit()

        log = log_db.get_log_by_id(self.conn, "log1")
        self.assertIsNone(log)
