"""Shared payload builders for Linux Manager handoff tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dicepp_manager.linux_handoff import (
    DECISION_COMMIT,
    LINUX_MANAGER_HANDOFF_FORMAT,
    RESULT_TARGET_COMMITTED,
)


def request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "format_version": LINUX_MANAGER_HANDOFF_FORMAT,
        "transaction_id": "a" * 32,
        "operation_id": "b" * 32,
        "source_version": "3.0.0rc20",
        "target_version": "3.0.0rc21",
        "compose_project": "dicepp",
        "manager": {
            "container_id": "c" * 64,
            "name": "dicepp-manager",
            "backup_name": "dicepp-manager.aaaaaaaa",
            "image_id": "sha256:" + "d" * 64,
        },
        "target_manager_image_id": "sha256:" + "e" * 64,
        "bot": {
            "container_id": "f" * 64,
            "image_id": "sha256:" + "10" * 32,
        },
        "dashboard": {
            "container_id": "20" * 32,
            "image_id": "sha256:" + "30" * 32,
        },
        "target_images": {
            "bot": "sha256:" + "40" * 32,
            "dashboard": "sha256:" + "50" * 32,
        },
        "pre_upgrade_archive": "dicepp-pre-upgrade-20260804-abcdef12.zip",
        "dashboard_db": {
            "path": "recovery/aaaaaaaa/dashboard.db",
            "sha256": "0" * 64,
        },
        "original_running": {"bot": True, "dashboard": False},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "startup_deadline_seconds": 300,
        "transaction_deadline_seconds": 3600,
        "current_aliases": {
            "bot": {
                "name": "ghcr.io/pear-studio/nonebot-dicepp:dicepp-current",
                "image_id": "sha256:" + "10" * 32,
            },
            "dashboard_manager": {
                "name": "ghcr.io/pear-studio/dicepp-dashboard:dicepp-current",
                "image_id": "sha256:" + "30" * 32,
            },
        },
        "restart_policies": {
            "manager": "unless-stopped",
            "bot": "unless-stopped",
            "dashboard": "unless-stopped",
        },
        "labels": {
            "transaction": "io.dicepp.upgrade-transaction",
            "role": "io.dicepp.upgrade-role",
        },
    }
    payload.update(overrides)
    return payload


def decision_payload(
    value: str = DECISION_COMMIT, **overrides: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "format_version": LINUX_MANAGER_HANDOFF_FORMAT,
        "transaction_id": "a" * 32,
        "operation_id": "b" * 32,
        "value": value,
        "created_at": "2026-08-04T21:10:00+08:00",
    }
    payload.update(overrides)
    return payload


def result_payload(
    value: str = RESULT_TARGET_COMMITTED, **overrides: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "format_version": LINUX_MANAGER_HANDOFF_FORMAT,
        "transaction_id": "a" * 32,
        "operation_id": "b" * 32,
        "value": value,
        "created_at": "2026-08-04T21:20:00+08:00",
    }
    payload.update(overrides)
    return payload


def tx_dir(root: Any) -> Any:
    directory = root / "manager" / "recovery" / ("a" * 32)
    directory.mkdir(parents=True)
    return directory
