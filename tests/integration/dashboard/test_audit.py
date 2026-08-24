"""Tests for the ``/api/audit`` endpoint and audit-logging behaviour."""

import json

import pytest
from fastapi.testclient import TestClient

from dashboard.src.audit import log as audit_log
from tests.support.dashboard.app import setup_auth
class TestAuditLogCreated:
    def test_audit_log_created_on_set(self, test_client, tmp_dashboard_paths):
        """Config set creates an audit log entry."""
        setup_auth(test_client)
        test_client.post(
            "/api/config/set", json={"path": "app.name", "value": "new_name"}
        )

        resp = test_client.get("/api/audit")
        entries = resp.json()["entries"]
        assert len(entries) >= 1
        entry = entries[0]
        assert entry["action"] == "config.set"
        assert entry["target"] == "app.name"

    def test_audit_log_created_on_reset(self, test_client, tmp_dashboard_paths):
        """Config reset creates an audit log entry."""
        setup_auth(test_client)
        test_client.post("/api/config/reset", json={"path": "app.nonexistent"})

        resp = test_client.get("/api/audit")
        entries = resp.json()["entries"]
        assert any(e["action"] == "config.reset" for e in entries)

    def test_audit_log_created_on_login(self, test_client):
        """Login creates an audit log entry."""
        # Set up password first
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        # Logout so we can login again
        test_client.post("/api/auth/logout")
        test_client.post("/api/auth/login", json={"password": "test_password"})

        resp = test_client.get("/api/audit")
        entries = resp.json()["entries"]
        login_entries = [e for e in entries if e["action"] == "auth.login"]
        assert len(login_entries) >= 1


class TestAuditList:
    def test_audit_list(self, test_client):
        """``GET /api/audit`` returns recent entries ordered by event time."""
        setup_auth(test_client)
        # Perform a few actions
        test_client.post(
            "/api/config/set", json={"path": "a", "value": 1}
        )
        test_client.post(
            "/api/config/set", json={"path": "b", "value": 2}
        )

        resp = test_client.get("/api/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        entries = data["entries"]
        assert len(entries) >= 2

        # Entries should be ordered by actual event time (newest first).
        timestamps = [e["ts"] for e in entries]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_audit_default_limit(self, test_client):
        """Default limit is 200 entries."""
        setup_auth(test_client)
        resp = test_client.get("/api/audit")
        assert resp.status_code == 200
        # With default limit 200, we should get all entries
        entries = resp.json()["entries"]
        assert len(entries) <= 200

    def test_audit_custom_limit(self, test_client):
        """A custom limit returns at most that many entries."""
        setup_auth(test_client)
        # Generate a few entries
        for i in range(5):
            test_client.post(
                "/api/config/set", json={"path": f"key.{i}", "value": i}
            )

        resp = test_client.get("/api/audit", params={"limit": 3})
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 3

    def test_known_actions_share_one_localized_presentation_contract(
        self,
        test_client: TestClient,
    ) -> None:
        setup_auth(test_client)
        db_path = test_client.app.state.dashboard_db
        samples = [
            ("auth.setup", "auth", "Initial password set"),
            ("auth.login", "auth", "Login"),
            ("auth.change_password", "auth", "Password changed"),
            ("config.set", "app.name", json.dumps({"value": "DicePP"})),
            ("config.reset", "app.name", "reset to default"),
            ("config.bot.save", "bots/demo", ""),
            ("config.user.save", "user.json", ""),
            ("persona.character.save", "调查员", ""),
            ("bot.start", "bot", json.dumps({"status": "running"})),
            ("bot.stop", "bot", json.dumps({"status": "stopped"})),
            ("bot.restart", "bot", json.dumps({"status": "running"})),
            ("content.query.enable", "rules", ""),
            ("content.query.disable", "rules", ""),
            (
                "custom.event",
                "demo",
                json.dumps({"status": "succeeded", "message": "完成"}),
            ),
        ]
        for action, target, detail in samples:
            audit_log(db_path, action, target, detail)

        entries = test_client.get("/api/audit").json()["entries"]
        presented = {entry["action"]: entry for entry in entries}

        assert {
            action: presented[action]["action_label"]
            for action, _, _ in samples
        } == {
            "auth.setup": "初始化管理员密码",
            "auth.login": "管理员登录",
            "auth.change_password": "修改管理员密码",
            "config.set": "修改配置项",
            "config.reset": "重置配置项",
            "config.bot.save": "保存 Bot 配置",
            "config.user.save": "保存全局配置",
            "persona.character.save": "保存角色配置",
            "bot.start": "启动 Bot 进行中",
            "bot.stop": "停止 Bot",
            "bot.restart": "重启 Bot 进行中",
            "content.query.enable": "启用查询库",
            "content.query.disable": "停用查询库",
            "custom.event": "custom.event",
        }
        assert presented["config.set"]["summary"] == "新值：DicePP"
        assert presented["auth.login"]["target_label"] == "管理员账户"
        assert presented["config.bot.save"]["target_label"] == "demo"
        assert presented["config.user.save"]["target_label"] == "全局配置"
        assert presented["custom.event"]["action_label"] == "custom.event"
        assert presented["custom.event"]["summary"] == "成功 · 完成"
        assert presented["custom.event"]["tone"] == "success"
