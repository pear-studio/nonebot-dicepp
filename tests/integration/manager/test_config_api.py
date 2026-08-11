"""Manager configuration routes validate candidates before atomically saving."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dicepp_data import InstanceLayout
from dicepp_manager.api import create_manager_app
from dicepp_manager.config import ManagerSettings
from dicepp_manager.service import ManagerService
from dicepp_manager.store import ManagerOperationStore


class _IdleRuntime:
    async def status(self, _ids):
        return {}

    async def operate(self, _runtime_unit_id, _action):
        raise AssertionError("Config tests do not operate the runtime")

    async def logs(self, _runtime_unit_id, _lines):
        raise AssertionError("Config tests do not read logs")

    async def runtime_logs(self, _lines):
        raise AssertionError("Config tests do not read logs")


def _app(layout: InstanceLayout):
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=_IdleRuntime(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    return create_manager_app(
        ManagerSettings(layout=layout, release_scheduler_enabled=False),
        service=service,
        api_token="manager-secret",
    )


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer manager-secret"}


def _query_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE data (名称 TEXT, 内容 TEXT)")
        connection.execute("INSERT INTO data VALUES ('规则', '正文')")
        connection.commit()


def test_config_get_routes_return_documents_and_missing_bot_is_404(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.config_user.parent.mkdir(parents=True)
    layout.config_user.write_text('{"update":{"channel":"stable"}}', encoding="utf-8")
    bot_path = layout.bot_config_path("10001")
    bot_path.parent.mkdir(parents=True)
    bot_path.write_text('{"master":["owner"]}', encoding="utf-8")

    with TestClient(_app(layout)) as client:
        user = client.get("/v1/config/user", headers=_auth())
        bot = client.get("/v1/config/bots/10001", headers=_auth())
        missing = client.get("/v1/config/bots/missing", headers=_auth())

    assert user.json() == {"ok": True, "config": {"update": {"channel": "stable"}}}
    assert bot.json() == {"ok": True, "config": {"master": ["owner"]}}
    assert missing.status_code == 404
    assert missing.json()["message"] == "Bot configuration not found"


def test_query_database_normalize_route_runs_as_durable_operation(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    source = layout.content_dir / "queries" / "rules.db"
    _query_database(source)
    app = _app(layout)

    with TestClient(app) as client:
        response = client.post(
            "/v1/content/query-databases/rules/normalize",
            headers=_auth(),
        )
        operation_id = response.json()["operation"]["operation_id"]

    operation = app.state.manager_service.get_operation(operation_id)
    assert response.status_code == 202
    assert operation is not None
    assert operation.status == "succeeded"
    assert operation.detail["backup_database"] == "rules_backup"
    assert source.with_name("rules_backup.db").exists()


def test_query_database_normalize_dry_run_reports_effects_without_writing(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    source = layout.content_dir / "queries" / "rules.db"
    source.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE data (名称 TEXT, 来源 TEXT, 内容 TEXT)")
        connection.executemany(
            "INSERT INTO data VALUES (?, ?, ?)",
            [("规则", "TEST", "第一行"), ("规则", "TEST", "冲突的第二行")],
        )
        connection.commit()
    before = source.read_bytes()

    with TestClient(_app(layout)) as client:
        response = client.post(
            "/v1/content/query-databases/rules/normalize/dry-run",
            headers=_auth(),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["requires_confirmation"] is True
    assert payload["report"]["counts"]["data_duplicates"] == 1
    assert payload["report"]["issues"][0]["code"] == "duplicate_content_conflict"
    assert payload["report"]["issues"][0]["subject"] == "规则"
    assert source.read_bytes() == before
    assert not source.with_name("rules_backup.db").exists()


def test_clean_query_database_dry_run_still_requires_confirmation(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    source = layout.content_dir / "queries" / "rules.db"
    _query_database(source)

    with TestClient(_app(layout)) as client:
        response = client.post(
            "/v1/content/query-databases/rules/normalize/dry-run",
            headers=_auth(),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["requires_confirmation"] is True
    assert payload["report"]["impact_counts"] == {
        "deletion": 0,
        "behavior_change": 0,
    }
    assert payload["report"]["issues"] == []


def test_invalid_update_is_rejected_without_replacing_user_document(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.config_user.parent.mkdir(parents=True)
    before = b'{"app":{"name":"keep"}}\n'
    layout.config_user.write_bytes(before)

    with TestClient(_app(layout)) as client:
        response = client.put(
            "/v1/config/user",
            headers=_auth(),
            json={"update": {"cache_versions": True}},
        )

    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "invalid_configuration"
    assert payload["message"] == "Configuration validation failed"
    assert any(error["field"].endswith("update.cache_versions") for error in payload["errors"])
    assert {error["message"] for error in payload["errors"]} == {
        "Invalid configuration value"
    }
    assert layout.config_user.read_bytes() == before


def test_user_candidate_is_checked_against_each_existing_bot(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.config_user.parent.mkdir(parents=True)
    before = b'{"app":{"name":"keep"}}\n'
    layout.config_user.write_bytes(before)
    invalid_bot = layout.bot_config_path("broken")
    invalid_bot.parent.mkdir(parents=True)
    invalid_bot.write_text(
        json.dumps({"update": {"cache_versions": True}}),
        encoding="utf-8",
    )

    with TestClient(_app(layout)) as client:
        response = client.put(
            "/v1/config/user",
            headers=_auth(),
            json={"update": {"channel": "stable"}},
        )

    assert response.status_code == 422
    assert any(
        error["field"].startswith("bots.broken.update.cache_versions")
        for error in response.json()["errors"]
    )
    assert layout.config_user.read_bytes() == before


def test_user_candidate_is_checked_against_future_bot_fallback(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    existing_bot = layout.bot_config_path("existing")
    existing_bot.parent.mkdir(parents=True)
    existing_bot.write_text(
        json.dumps({"persona_ai": {"segment_hard_limit": 200}}),
        encoding="utf-8",
    )

    with TestClient(_app(layout)) as client:
        response = client.put(
            "/v1/config/user",
            headers=_auth(),
            json={"persona_ai": {"segment_soft_limit": 130}},
        )

    assert response.status_code == 422
    assert any(
        error["field"].startswith("bots.fallback.persona_ai")
        for error in response.json()["errors"]
    )
    assert not layout.config_user.exists()


def test_invalid_full_bot_configuration_is_rejected_without_replacing_document(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    path = layout.bot_config_path("10001")
    path.parent.mkdir(parents=True)
    before = b'{"master":["keep"]}\n'
    path.write_bytes(before)

    with TestClient(_app(layout)) as client:
        response = client.put(
            "/v1/config/bots/10001",
            headers=_auth(),
            json={
                "persona_ai": {
                    "segment_soft_limit": 101,
                    "segment_hard_limit": 100,
                }
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_configuration"
    assert any(error["field"].startswith("bots.10001.persona_ai") for error in response.json()["errors"])
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("route", "path_getter", "candidate"),
    [
        ("/v1/config/user", lambda layout: layout.config_user, {"unknown_api_key": "secret"}),
        (
            "/v1/config/user",
            lambda layout: layout.config_user,
            {"persona_ai": {"unknown_token": "secret"}},
        ),
        (
            "/v1/config/bots/10001",
            lambda layout: layout.bot_config_path("10001"),
            {"unknown_api_key": "secret"},
        ),
        (
            "/v1/config/bots/10001",
            lambda layout: layout.bot_config_path("10001"),
            {"persona_ai": {"unknown_token": "secret"}},
        ),
    ],
    ids=("user-top-level", "user-nested", "bot-top-level", "bot-nested"),
)
def test_manager_rejects_runtime_critical_unknown_fields_without_replacing_document(
    tmp_path: Path,
    route: str,
    path_getter,
    candidate: dict,
) -> None:
    """A Manager save must not create a file the Bot runtime would reject."""
    layout = InstanceLayout.from_root(tmp_path)
    path = path_getter(layout)
    path.parent.mkdir(parents=True)
    before = b'{"app":{"name":"keep"}}\n'
    path.write_bytes(before)

    with TestClient(_app(layout)) as client:
        response = client.put(route, headers=_auth(), json=candidate)

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "invalid_configuration"
    assert payload["errors"]
    assert {error["message"] for error in payload["errors"]} == {
        "Invalid configuration value"
    }
    assert path.read_bytes() == before


def test_valid_config_save_reports_deferred_application(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)

    with TestClient(_app(layout)) as client:
        response = client.put(
            "/v1/config/user",
            headers=_auth(),
            json={"update": {"check_interval_hours": 12.0}},
        )

    assert response.json() == {
        "ok": True,
        "saved": True,
        "application": "deferred",
        "restart_required": True,
    }
    assert json.loads(layout.config_user.read_text(encoding="utf-8")) == {
        "update": {"check_interval_hours": 12.0}
    }


def test_query_database_enablement_is_manager_owned_and_defaults_enabled(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    queries = layout.content_dir / "queries"
    queries.mkdir(parents=True)
    (queries / "rules.db").write_bytes(b"database-placeholder")

    with TestClient(_app(layout)) as client:
        listed = client.get("/v1/content/query-databases", headers=_auth())
        disabled = client.put(
            "/v1/content/query-databases/rules/enabled",
            headers=_auth(),
            json={"enabled": False},
        )
        listed_after = client.get("/v1/content/query-databases", headers=_auth())

    assert listed.json()["databases"][0]["enabled"] is True
    assert disabled.json() == {
        "ok": True,
        "database": "rules",
        "enabled": False,
        "application": "immediate",
        "restart_required": False,
    }
    assert listed_after.json()["databases"][0]["enabled"] is False
    state = json.loads((queries / ".dicepp-query-databases.json").read_text("utf-8"))
    assert state == {"version": 1, "disabled": ["rules"]}


def test_query_database_enablement_rejects_unknown_or_unsafe_names(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    (layout.content_dir / "queries").mkdir(parents=True)

    with TestClient(_app(layout)) as client:
        missing = client.put(
            "/v1/content/query-databases/missing/enabled",
            headers=_auth(),
            json={"enabled": False},
        )
        unsafe = client.put(
            "/v1/content/query-databases/..%2Fsecret/enabled",
            headers=_auth(),
            json={"enabled": False},
        )

    assert missing.status_code == 404
    assert unsafe.status_code == 404


def test_validator_uses_the_runtime_canonical_bot_config_model() -> None:
    """A packaged Manager must not drift to a Dashboard-local schema copy."""
    import dicepp_manager.config_validation as validation
    from plugins.DicePP.core.config.pydantic_models import BotConfig

    assert validation.BotConfig is BotConfig
