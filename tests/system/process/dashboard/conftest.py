"""Thin fixture registration for Dashboard process-level system tests."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support.dashboard.project import build_dashboard_project, build_test_client


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
) -> TestClient:
    return build_test_client(tmp_dashboard_paths, monkeypatch)
