"""Tests for the ``/api/audit`` endpoint and audit-logging behaviour."""

import json

import pytest
from fastapi.testclient import TestClient

from dashboard.src.audit import log as audit_log
from tests.support.dashboard.app import setup_auth
from tests.support.dashboard.manager import PersistingConfigManager


@pytest.fixture(autouse=True)
def _install_config_manager(test_client: TestClient) -> None:
    test_client.app.state.manager_client = PersistingConfigManager()


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
            (
                "manager.start",
                "dicepp-runtime",
                json.dumps({"operation_id": "start-1", "status": "queued"}),
            ),
            (
                "manager.stop",
                "dicepp-runtime",
                json.dumps({"operation_id": "stop-1", "status": "succeeded"}),
            ),
            (
                "manager.restart",
                "dicepp-runtime",
                json.dumps({
                    "operation_id": "restart-1",
                    "status": "failed",
                    "message": "无法启动",
                }),
            ),
            ("content.query.enable", "rules", ""),
            ("content.query.disable", "rules", ""),
            (
                "content.query.normalize.dry_run",
                "rules",
                json.dumps({
                    "status": "succeeded",
                    "report": {
                        "counts": {
                            "data_invalid": 1,
                            "data_duplicates": 1,
                            "directives_deleted": 1,
                            "redirect_invalid": 0,
                        },
                        "impact_counts": {"behavior_change": 1},
                    },
                }),
            ),
            ("content.query.normalize.start", "rules", "normalize-1"),
            (
                "content.query.normalize.result",
                "rules",
                json.dumps({
                    "status": "failed",
                    "message": "拒绝访问",
                    "detail": {"stage": "verify_runtime"},
                }),
            ),
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
            "manager.start": "启动 Bot 已提交",
            "manager.stop": "停止 Bot 成功",
            "manager.restart": "重启 Bot 失败",
            "content.query.enable": "启用查询库",
            "content.query.disable": "停用查询库",
            "content.query.normalize.dry_run": "查询库修复预检",
            "content.query.normalize.start": "查询库修复已提交",
            "content.query.normalize.result": "查询库修复失败",
            "custom.event": "custom.event",
        }
        assert presented["config.set"]["summary"] == "新值：DicePP"
        assert presented["auth.login"]["target_label"] == "管理员账户"
        assert presented["config.bot.save"]["target_label"] == "demo"
        assert presented["config.user.save"]["target_label"] == "全局配置"
        assert presented["manager.restart"]["summary"] == "无法启动 · 操作 ID：restart-1"
        assert presented["content.query.normalize.dry_run"]["summary"] == (
            "删除 2 个词条 · 1 行内容 · 0 个重定向 · 1 项行为变化"
        )
        assert presented["content.query.normalize.result"]["summary"] == (
            "阶段：检查 Bot 运行状态 · 文件被占用或拒绝访问"
        )
        assert presented["custom.event"]["action_label"] == "custom.event"
        assert presented["custom.event"]["summary"] == "成功 · 完成"
        assert presented["custom.event"]["tone"] == "success"
