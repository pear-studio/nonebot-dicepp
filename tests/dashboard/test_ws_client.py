"""Tests for Control Channel client runtime configuration."""

from plugins.DicePP.module.dashboard_reporter import ws_client


def test_source_runtime_requires_explicit_dashboard_host(monkeypatch):
    monkeypatch.delenv("DPP_ADMIN_HOST", raising=False)
    monkeypatch.delenv("DPP_ADMIN_PORT", raising=False)
    monkeypatch.setattr(ws_client, "is_frozen", lambda: False)

    assert ws_client.resolve_dashboard_url() is None


def test_windows_executable_defaults_to_local_dashboard(monkeypatch):
    monkeypatch.delenv("DPP_ADMIN_HOST", raising=False)
    monkeypatch.delenv("DPP_ADMIN_PORT", raising=False)
    monkeypatch.setattr(ws_client, "is_frozen", lambda: True)

    assert ws_client.resolve_dashboard_url() == "ws://127.0.0.1:4090/ws/control"


def test_explicit_dashboard_address_overrides_runtime_default(monkeypatch):
    monkeypatch.setenv("DPP_ADMIN_HOST", "dashboard.internal")
    monkeypatch.setenv("DPP_ADMIN_PORT", "5090")
    monkeypatch.setattr(ws_client, "is_frozen", lambda: True)

    assert ws_client.resolve_dashboard_url() == (
        "ws://dashboard.internal:5090/ws/control"
    )
