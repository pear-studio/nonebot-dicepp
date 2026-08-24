"""Thin fixture registration for Dashboard integration tests."""

from pathlib import Path
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.support.dashboard.project import (
    build_dashboard_project,
    build_dual_clients,
    build_test_client,
)


@pytest.fixture
def tmp_dashboard_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    return build_dashboard_project(monkeypatch, tmp_path)


@pytest.fixture
def test_client(
    tmp_dashboard_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    client = build_test_client(tmp_dashboard_paths, monkeypatch)
    try:
        yield client
    finally:
        _cleanup_bot_controller(client)


@pytest.fixture
def dual_clients(
    tmp_dashboard_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, TestClient]]:
    clients = build_dual_clients(tmp_dashboard_paths, monkeypatch)
    try:
        yield clients
    finally:
        _cleanup_bot_controller(clients[0])


def _cleanup_bot_controller(client: TestClient) -> None:
    """Reap any controller created by an app route or injected by a test."""
    controller = getattr(client.app.state, "bot_process_controller", None)
    if controller is not None:
        controller.shutdown()
    if hasattr(client.app.state, "bot_process_controller"):
        delattr(client.app.state, "bot_process_controller")
    client.app.state.bot_auto_start = False
