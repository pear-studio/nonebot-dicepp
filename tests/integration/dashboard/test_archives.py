"""Dashboard archive routes are a pure proxy to the private Manager API."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient

from dicepp_manager.client import ManagerClientError
from tests.support.dashboard.app import setup_auth


class ArchiveManagerClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: ManagerClientError | None = None

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def list_archives(self):
        self._raise()
        self.calls.append(("list",))
        return [{"filename": "save.zip", "valid": True}]

    async def estimate_archive(self, profile):
        self._raise()
        self.calls.append(("estimate", profile))
        return {"profile": profile, "input_bytes": 42}

    async def create_archive(self, *, description=None, profile="regular"):
        self._raise()
        self.calls.append(("create", description, profile))
        return {"operation_id": "op-create", "status": "queued"}

    async def verify_archive(self, filename):
        self._raise()
        self.calls.append(("verify", filename))
        return {"verification": {"verified": True}}

    async def plan_archive_restore(self, filename):
        self._raise()
        self.calls.append(("plan", filename))
        return {"plan": {"profile": "regular", "entries": []}}

    async def restore_archive(self, filename, *, confirm_restore, description=None):
        self._raise()
        self.calls.append(("restore", filename, confirm_restore, description))
        return {"operation_id": "op-restore", "status": "queued"}

    async def archive_detail(self, filename):
        self._raise()
        self.calls.append(("detail", filename))
        return {"archive": {"filename": filename}, "manifest": {"format_version": 2}}

    async def delete_archive(self, filename):
        self._raise()
        self.calls.append(("delete", filename))
        return {"deleted": filename}

    async def export_archive(self, filename):
        self._raise()
        self.calls.append(("export", filename))
        return b"PK-test"

    async def import_archive(self, filename, source):
        self._raise()
        payload = source.read()
        self.calls.append(("import", filename, payload))
        return {"import": {"archive": {"filename": filename}, "restored": False}}


class RecordingUpload:
    """Small in-memory stand-in that exposes exactly what Dashboard writes."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self._stream = io.BytesIO()

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info) -> None:
        self._stream.close()

    def write(self, chunk: bytes) -> int:
        self.writes.append(chunk)
        return self._stream.write(chunk)

    def seek(self, *args) -> int:
        return self._stream.seek(*args)

    def read(self, *args) -> bytes:
        return self._stream.read(*args)


def _install(test_client: TestClient) -> ArchiveManagerClient:
    client = ArchiveManagerClient()
    test_client.app.state.manager_client = client
    setup_auth(test_client)
    return client


def test_list_estimate_and_create_are_manager_operations(test_client: TestClient) -> None:
    manager = _install(test_client)

    listed = test_client.get("/api/archives")
    estimated = test_client.post("/api/archives/estimate", json={"profile": "full"})
    created = test_client.post(
        "/api/archives",
        json={"description": "before migration", "profile": "full"},
    )

    assert listed.json()["archives"][0]["filename"] == "save.zip"
    assert estimated.json()["estimate"] == {"profile": "full", "input_bytes": 42}
    assert created.status_code == 202
    assert created.json()["operation"]["operation_id"] == "op-create"
    assert manager.calls == [
        ("list",),
        ("estimate", "full"),
        ("create", "before migration", "full"),
    ]


def test_verify_plan_restore_detail_and_delete_proxy_manager(
    test_client: TestClient,
) -> None:
    manager = _install(test_client)

    assert test_client.post("/api/archives/a.zip/verify").json()["verification"]["verified"]
    assert test_client.post("/api/archives/a.zip/restore-plan").json()["plan"]["profile"] == "regular"
    restored = test_client.post(
        "/api/archives/a.zip/restore",
        json={"confirm_restore": True, "description": "safety"},
    )
    assert restored.status_code == 202
    assert restored.json()["operation"]["operation_id"] == "op-restore"
    assert test_client.get("/api/archives/a.zip").json()["manifest"]["format_version"] == 2
    assert test_client.delete("/api/archives/a.zip").json()["deleted"] == "a.zip"
    assert manager.calls == [
        ("verify", "a.zip"),
        ("plan", "a.zip"),
        ("restore", "a.zip", True, "safety"),
        ("detail", "a.zip"),
        ("delete", "a.zip"),
    ]


def test_restore_requires_explicit_confirmation(test_client: TestClient) -> None:
    manager = _install(test_client)

    response = test_client.post("/api/archives/a.zip/restore", json={})

    assert response.status_code == 400
    assert manager.calls == []


def test_export_and_import_stream_through_manager(test_client: TestClient) -> None:
    manager = _install(test_client)

    exported = test_client.get("/api/archives/a.zip/export")
    imported = test_client.post(
        "/api/archives/import",
        headers={"X-Archive-Filename": "from-linux.zip"},
        content=b"PK-upload",
    )

    assert exported.status_code == 200
    assert exported.content == b"PK-test"
    assert exported.headers["content-type"] == "application/zip"
    assert imported.json()["import"]["restored"] is False
    assert manager.calls == [
        ("export", "a.zip"),
        ("import", "from-linux.zip", b"PK-upload"),
    ]


def test_import_rejects_oversize_payload_before_temp_or_manager(
    test_client: TestClient,
    monkeypatch,
) -> None:
    """Dashboard enforces the same cap before a request can consume temp disk."""
    import dashboard.src.app as app_module

    manager = _install(test_client)
    upload = RecordingUpload()
    monkeypatch.setattr(app_module, "MAX_ARCHIVE_BYTES", 4, raising=False)
    monkeypatch.setattr(
        app_module.tempfile,
        "SpooledTemporaryFile",
        lambda **_kwargs: upload,
    )

    response = test_client.post(
        "/api/archives/import",
        headers={"X-Archive-Filename": "too-large.zip"},
        content=b"12345",
    )

    assert response.status_code == 413
    assert manager.calls == []
    assert sum(len(chunk) for chunk in upload.writes) <= 4


def test_import_stream_cap_catches_a_smaller_declared_length(
    test_client: TestClient,
    monkeypatch,
) -> None:
    """A lying or absent request length cannot bypass per-chunk accounting."""
    import dashboard.src.app as app_module

    manager = _install(test_client)
    upload = RecordingUpload()
    monkeypatch.setattr(app_module, "MAX_ARCHIVE_BYTES", 4, raising=False)
    monkeypatch.setattr(
        app_module.tempfile,
        "SpooledTemporaryFile",
        lambda **_kwargs: upload,
    )

    response = test_client.post(
        "/api/archives/import",
        headers={
            "X-Archive-Filename": "lying-length.zip",
            "content-length": "4",
        },
        content=b"12345",
    )

    assert response.status_code == 413
    assert manager.calls == []
    assert sum(len(chunk) for chunk in upload.writes) <= 4


def test_import_at_shared_cap_still_reaches_manager(
    test_client: TestClient,
    monkeypatch,
) -> None:
    """The hard cap is inclusive, matching Manager's own upload contract."""
    import dashboard.src.app as app_module

    manager = _install(test_client)
    monkeypatch.setattr(app_module, "MAX_ARCHIVE_BYTES", 4, raising=False)

    response = test_client.post(
        "/api/archives/import",
        headers={"X-Archive-Filename": "at-cap.zip"},
        content=b"1234",
    )

    assert response.status_code == 200
    assert manager.calls == [("import", "at-cap.zip", b"1234")]


def test_manager_error_status_and_payload_are_preserved(test_client: TestClient) -> None:
    manager = _install(test_client)
    manager.error = ManagerClientError(
        "verification failed",
        status_code=409,
        payload={"verification": {"verified": False, "problems": ["bad"]}},
    )

    response = test_client.post("/api/archives/a.zip/restore-plan")

    assert response.status_code == 409
    assert response.json() == {
        "ok": False,
        "message": "verification failed",
        "verification": {"verified": False, "problems": ["bad"]},
    }


def test_dashboard_archive_module_has_no_local_storage_implementation() -> None:
    source = Path("dashboard/src/archives.py").read_text(encoding="utf-8")

    assert "zipfile" not in source
    assert "os.replace" not in source
    assert "DATA_CATALOG" not in source
    assert "create_archive" not in source
    assert "restore_archive" not in source


def test_dashboard_health_is_independent_and_archive_ui_reconnects_operations(
    test_client: TestClient,
) -> None:
    health = test_client.get("/api/health")
    source = Path("dashboard/src/static/dashboard.html").read_text(encoding="utf-8")

    assert health.status_code == 200
    assert health.json()["component"] == "dashboard"
    assert health.json()["status"] == "ok"
    assert health.json()["control"]["latest_heartbeat"] is None
    assert "reconnectArchiveOperation()" in source
    assert "item.action.startsWith('archive.')" in source
    assert "dicepp_archive_operation" in source
    assert "persistedArchiveOperation()" in source
    assert "applyCompletedArchiveOperation(completed)" in source
    assert "archiveRestoreResultFromOperation" in source
    assert "restore.rolled_back = Boolean(operation.detail?.rolled_back)" in source
    assert "clearPersistedArchiveOperation(completed.operation_id)" in source
    assert 'x-model="archiveRestoreQuiesceRuntime"' not in source
