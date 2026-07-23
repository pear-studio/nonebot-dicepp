"""Tests for Dashboard save archives."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from dashboard.src import archives
from dashboard.src.config import DashboardPaths
from dicepp_manager.client import ManagerClientError
from dicepp_manager.models import ManagerAction, RuntimeUnitStatus
from tests.support.dashboard.app import setup_auth


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def _try_symlink(link_path: Path, target: Path) -> None:
    try:
        link_path.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable in this environment: {exc}")


def _prepare_archive_fixture(project_root: Path) -> None:
    _write(project_root / "config" / "user.json", '{"persona_ai": {"primary_api_key": "fixture"}}')
    _write(project_root / "data" / "dicepp.db", "instance db")
    _write(project_root / "data" / "bots" / "test_bot" / "log.db", "log db")
    _write(
        project_root / "data" / "bots" / "test_bot" / "personas_data_hero.db",
        "persona db",
    )
    _write(project_root / "data" / "local_images" / "avatar.png", b"\x89PNG\r\n")

    _write(project_root / "data" / "backups" / "older.zip", "old archive")
    _write(project_root / "data" / "runtime" / "local-control.token", "runtime")
    _write(project_root / "data" / "bots" / "test_bot" / "logs" / "bot.log", "logs")
    _write(project_root / "content" / "decks" / "excluded.txt", "content")
    _write(project_root / "data" / "bots" / "test_bot" / "llonebot.db", "llonebot")


def _archive_path(project_root: Path, filename: str) -> Path:
    return project_root / "data" / "backups" / filename


def _api_archive_path(filename: str) -> str:
    return f"/api/archives/{quote(filename, safe='')}"


def _api_archive_verify_path(filename: str) -> str:
    return f"/api/archives/{quote(filename, safe='')}/verify"


def _api_archive_restore_plan_path(filename: str) -> str:
    return f"/api/archives/{quote(filename, safe='')}/restore-plan"


def _api_archive_restore_path(filename: str) -> str:
    return f"/api/archives/{quote(filename, safe='')}/restore"


def _bytes(data: bytes | str) -> bytes:
    return data if isinstance(data, bytes) else data.encode("utf-8")


def _checksums(payloads: dict[str, bytes | str]) -> dict[str, str]:
    return {
        arcname: hashlib.sha256(_bytes(data)).hexdigest()
        for arcname, data in payloads.items()
    }


def _write_verify_zip(
    project_root: Path,
    filename: str,
    *,
    payloads: dict[str, bytes | str],
    manifest_files: dict[str, str] | None = None,
    extra_payloads: dict[str, bytes | str] | None = None,
) -> dict:
    manifest = {
        "format_version": 1,
        "created_at": "2026-01-02T03:04:05Z",
        "dicepp_version": "test",
        "description": "verify fixture",
        "scope": {
            "included": ["config", "data/dicepp.db"],
            "excluded": ["content"],
        },
        "checksum": {
            "algorithm": "sha256",
            "files": manifest_files if manifest_files is not None else _checksums(payloads),
        },
    }
    target = _archive_path(project_root, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as zf:
        for arcname, data in payloads.items():
            zf.writestr(arcname, _bytes(data))
        for arcname, data in (extra_payloads or {}).items():
            zf.writestr(arcname, _bytes(data))
        zf.writestr("manifest.json", json.dumps(manifest))
    return manifest


def _write_manifest_zip(project_root: Path, filename: str, manifest) -> None:
    target = _archive_path(project_root, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))


class ArchiveManagerRuntimeBackend:
    def __init__(
        self,
        *,
        events: list[str] | None = None,
        fail: dict[tuple[str, str], Exception] | None = None,
        runtime_detail: dict | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.fail = fail or {}
        self.runtime_detail = runtime_detail or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    async def status(self, bot_ids: list[str]) -> dict[str, RuntimeUnitStatus]:
        return {
            bot_id: RuntimeUnitStatus(
                runtime_unit_id=bot_id,
                runtime_state="running",
                health="healthy",
                message="archive fake running",
            )
            for bot_id in bot_ids
        }

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> RuntimeUnitStatus:
        detail = dict(request_detail) if request_detail else None
        self.calls.append((bot_id, action, detail))
        self.events.append(f"{action}:{bot_id}")
        failure = self.fail.get((bot_id, action))
        if failure is not None:
            raise failure
        runtime_state = "stopped" if action == "stop" else "running"
        health = "stopped" if action == "stop" else "healthy"
        return RuntimeUnitStatus(
            runtime_unit_id=bot_id,
            runtime_state=runtime_state,
            health=health,
            message=f"archive fake {action}",
            detail=dict(self.runtime_detail),
        )


def _install_archive_manager_service(
    test_client: TestClient,
    backend: ArchiveManagerRuntimeBackend,
) -> None:
    class ArchiveManagerClient:
        def __init__(self) -> None:
            self.operations: dict[str, dict] = {}
            self.counter = 0

        async def status(self):
            units = []
            for unit_id in ("another_bot", "test_bot"):
                runtime = (await backend.status([unit_id]))[unit_id].to_dict()
                units.append({
                    "runtime_unit_id": unit_id,
                    "bot_ids": [unit_id],
                    "shared_process": False,
                    "runtime": runtime,
                    "manager": {"operation_status": "idle"},
                })
            return {"runtime_units": units, "bots": [], "health": {"status": "ok"}}

        async def operate(self, runtime_unit_id: str, action: str):
            self.counter += 1
            operation_id = f"archive-op-{self.counter}"
            try:
                result = await backend.operate(runtime_unit_id, action)
            except Exception as exc:
                operation = {
                    "operation_id": operation_id,
                    "runtime_unit_id": runtime_unit_id,
                    "action": action,
                    "status": "failed",
                    "message": str(exc),
                }
                self.operations[operation_id] = operation
                raise ManagerClientError(str(exc), status_code=500, payload={"operation": operation}) from exc
            operation = {
                "operation_id": operation_id,
                "runtime_unit_id": runtime_unit_id,
                "action": action,
                "status": "succeeded",
                "message": f"archive fake {action}",
                "detail": {"runtime": result.to_dict()},
            }
            self.operations[operation_id] = operation
            return operation

        async def get_operation(self, operation_id: str):
            return self.operations[operation_id]

    test_client.app.state.manager_client = ArchiveManagerClient()


def _assert_archive_runtime_operations_are_sanitized(runtime_quiesce: dict) -> None:
    serialized = json.dumps(runtime_quiesce, ensure_ascii=False)
    for forbidden in (
        "detail",
        '"runtime":',
        "service",
        "stdout",
        "stderr",
        "pid",
        "secret-token",
    ):
        assert forbidden not in serialized
    for operation in (
        runtime_quiesce["stop_operations"] + runtime_quiesce["start_operations"]
    ):
        assert set(operation).issubset({
            "operation_id",
            "runtime_unit_id",
            "action",
            "status",
            "message",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "request",
        })
        if "request" in operation:
            assert set(operation["request"]) == {
                "source",
                "archive_filename",
                "phase",
            }


class TestArchiveApi:
    @pytest.fixture(autouse=True)
    def _archive_user_config(self, tmp_dashboard_paths: Path) -> None:
        _write(tmp_dashboard_paths / "config" / "user.json", '{"archive": "user"}')

    def test_create_archive_contains_manifest_checksums_and_expected_scope(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """POST creates a zip containing only the first save-archive scope."""
        _prepare_archive_fixture(tmp_dashboard_paths)
        outside = tmp_dashboard_paths / "outside-secret.txt"
        outside.write_text("outside", encoding="utf-8")
        _try_symlink(DashboardPaths.CONFIG_DIR / "outside-link.txt", outside)

        setup_auth(test_client)
        resp = test_client.post("/api/archives", json={"description": "Manual Save"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        archive = data["archive"]
        manifest = data["manifest"]
        assert archive["filename"].endswith(".zip")
        assert "manual-save" in archive["filename"]
        assert archive["valid"] is True
        assert archive["file_count"] == len(manifest["checksum"]["files"])

        assert manifest["format_version"] == 1
        assert manifest["description"] == "Manual Save"
        assert isinstance(manifest["dicepp_version"], str)
        assert manifest["checksum"]["algorithm"] == "sha256"
        datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        assert manifest["scope"]["included"] == [
            "config/user.json",
            "config/bots/*.json",
            "data/dicepp.db",
            "data/bots/*/bot_data.db",
            "data/bots/*/log.db",
            "data/bots/*/personas_data_*.db",
            "data/local_images",
        ]
        assert "config/global.json" in manifest["scope"]["excluded"]
        assert "config/bots/_template.json" in manifest["scope"]["excluded"]
        assert "content" in manifest["scope"]["excluded"]
        assert "dashboard/data/dashboard.db" in manifest["scope"]["excluded"]

        with zipfile.ZipFile(_archive_path(tmp_dashboard_paths, archive["filename"])) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            zip_manifest = json.loads(zf.read("manifest.json"))
            assert zip_manifest == manifest

            expected_payloads = {
                "config/user.json",
                "config/bots/another_bot.json",
                "config/bots/test_bot.json",
                "data/dicepp.db",
                "data/bots/another_bot/bot_data.db",
                "data/bots/test_bot/bot_data.db",
                "data/bots/test_bot/log.db",
                "data/bots/test_bot/personas_data_hero.db",
                "data/local_images/avatar.png",
            }
            assert expected_payloads.issubset(names)
            excluded_payloads = {
                "config/global.json",
                "config/bots/_template.json",
                "config/outside-link.txt",
                "data/backups/older.zip",
                "data/runtime/local-control.token",
                "data/bots/test_bot/logs/bot.log",
                "content/decks/excluded.txt",
                "dashboard/data/dashboard.db",
                "data/bots/test_bot/llonebot.db",
            }
            assert names.isdisjoint(excluded_payloads)

            checksum_files = manifest["checksum"]["files"]
            assert set(checksum_files) == names - {"manifest.json"}
            for arcname, expected_digest in checksum_files.items():
                assert not arcname.startswith("/")
                assert ".." not in PurePosixPath(arcname).parts
                actual_digest = hashlib.sha256(zf.read(arcname)).hexdigest()
                assert actual_digest == expected_digest

    def test_archive_detail_returns_summary_and_manifest_from_zip(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """GET detail returns the manifest stored in the archive zip."""
        _prepare_archive_fixture(tmp_dashboard_paths)
        setup_auth(test_client)
        created = test_client.post("/api/archives", json={"description": "detail me"})
        assert created.status_code == 200
        filename = created.json()["archive"]["filename"]

        resp = test_client.get(_api_archive_path(filename))

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["archive"]["filename"] == filename
        assert data["archive"]["valid"] is True
        with zipfile.ZipFile(_archive_path(tmp_dashboard_paths, filename)) as zf:
            zip_manifest = json.loads(zf.read("manifest.json"))
        assert data["manifest"] == zip_manifest
        assert data["manifest"] == created.json()["manifest"]

    def test_archive_verify_created_archive_returns_restorable_preview(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """POST verify reports a created archive as fully restorable."""
        _prepare_archive_fixture(tmp_dashboard_paths)
        setup_auth(test_client)
        created = test_client.post("/api/archives", json={"description": "verify me"})
        assert created.status_code == 200
        filename = created.json()["archive"]["filename"]

        resp = test_client.post(_api_archive_verify_path(filename))

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        verification = data["verification"]
        assert "_archive_file_identity" not in verification
        assert verification["archive"]["filename"] == filename
        assert verification["manifest"] == created.json()["manifest"]
        assert verification["verified"] is True
        assert verification["problems"] == []
        assert verification["warnings"] == []
        assert verification["restorable_files"] == sorted(
            created.json()["manifest"]["checksum"]["files"]
        )
        assert {
            "config/user.json",
            "data/dicepp.db",
            "data/bots/test_bot/log.db",
            "data/local_images/avatar.png",
        }.issubset(set(verification["restorable_files"]))

    def test_archive_restore_plan_returns_create_overwrite_and_display_paths(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Restore plan maps verified payloads to logical targets without absolute paths."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-plan.zip",
            payloads={
                "config/user.json": "{}",
                "data/dicepp.db": "instance db",
                "data/bots/test_bot/bot_data.db": "bot data",
                "data/local_images/avatar.png": b"\x89PNG\r\n",
            },
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_restore_plan_path("restore-plan.zip"))

        assert resp.status_code == 200
        plan = resp.json()["plan"]
        assert "_archive_file_identity" not in plan
        assert plan["archive"]["filename"] == "restore-plan.zip"
        assert plan["verified"] is True
        assert plan["problems"] == []
        entries = {entry["arcname"]: entry for entry in plan["entries"]}
        assert entries["config/user.json"]["target_path"] == "config/user.json"
        assert entries["config/user.json"]["action"] == "overwrite"
        assert entries["data/dicepp.db"]["target_path"] == "data/dicepp.db"
        assert entries["data/dicepp.db"]["action"] == "create"
        assert entries["data/bots/test_bot/bot_data.db"]["target_path"] == (
            "data/bots/test_bot/bot_data.db"
        )
        assert entries["data/bots/test_bot/bot_data.db"]["action"] == "overwrite"
        assert entries["data/local_images/avatar.png"]["target_path"] == (
            "data/local_images/avatar.png"
        )
        assert entries["data/local_images/avatar.png"]["action"] == "create"
        assert all(not Path(entry["target_path"]).is_absolute() for entry in plan["entries"])
        assert all("\\" not in entry["target_path"] for entry in plan["entries"])
        assert entries["data/dicepp.db"]["size"] == len(b"instance db")

    def test_archive_restore_plan_verify_failed_returns_409_with_verification(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Restore planning refuses unverified archives and returns the verify report."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-mismatch.zip",
            payloads={"data/dicepp.db": "tampered bytes"},
            manifest_files={"data/dicepp.db": hashlib.sha256(b"original bytes").hexdigest()},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_restore_plan_path("restore-mismatch.zip"))

        assert resp.status_code == 409
        data = resp.json()
        assert data["ok"] is False
        verification = data["verification"]
        assert verification["archive"]["filename"] == "restore-mismatch.zip"
        assert verification["verified"] is False
        assert verification["restorable_files"] == []
        assert any("Checksum mismatch" in problem for problem in verification["problems"])

    def test_archive_restore_plan_rejects_unknown_prefix(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Payloads outside the restore whitelist fail verification before planning."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "unknown-prefix.zip",
            payloads={"data/untracked.db": "not restorable"},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_restore_plan_path("unknown-prefix.zip"))

        assert resp.status_code == 409
        verification = resp.json()["verification"]
        assert verification["verified"] is False
        assert verification["restorable_files"] == []
        assert any(
            "Unsupported restore path" in problem and "data/untracked.db" in problem
            for problem in verification["problems"]
        )

    @pytest.mark.parametrize(
        "arcname",
        [
            "config/global.json",
            "config/bots/_template.json",
        ],
    )
    def test_archive_verify_rejects_version_supplied_config_payloads(
        self, test_client: TestClient, tmp_dashboard_paths: Path, arcname: str
    ):
        """Version-supplied config files are not part of the restorable archive scope."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "version-config.zip",
            payloads={arcname: "{}"},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("version-config.zip"))

        assert resp.status_code == 200
        verification = resp.json()["verification"]
        assert verification["verified"] is False
        assert verification["restorable_files"] == []
        assert any(
            "Unsupported restore path" in problem and arcname in problem
            for problem in verification["problems"]
        )

    def test_archive_restore_plan_blocks_directory_target_without_writing(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """A target directory is reported as blocked and left untouched."""
        target = tmp_dashboard_paths / "data" / "dicepp.db"
        target.mkdir(parents=True)
        _write_verify_zip(
            tmp_dashboard_paths,
            "directory-target.zip",
            payloads={"data/dicepp.db": "archive db"},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_restore_plan_path("directory-target.zip"))

        assert resp.status_code == 200
        plan = resp.json()["plan"]
        assert plan["entries"] == [
            {
                "arcname": "data/dicepp.db",
                "target_path": "data/dicepp.db",
                "action": "blocked",
                "size": len(b"archive db"),
            }
        ]
        assert any("not a regular file" in problem for problem in plan["problems"])
        assert target.is_dir()
        assert not (target / "archive db").exists()

    def test_archive_restore_plan_blocks_symlink_target_without_writing(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """A target symlink is reported as blocked and its referent is not changed."""
        outside = tmp_dashboard_paths / "outside-config.json"
        outside.write_text("outside stays", encoding="utf-8")
        link = tmp_dashboard_paths / "config" / "user.json"
        link.unlink()
        _try_symlink(link, outside)
        _write_verify_zip(
            tmp_dashboard_paths,
            "symlink-target.zip",
            payloads={"config/user.json": "archive config"},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_restore_plan_path("symlink-target.zip"))

        assert resp.status_code == 200
        plan = resp.json()["plan"]
        assert plan["entries"] == [
            {
                "arcname": "config/user.json",
                "target_path": "config/user.json",
                "action": "blocked",
                "size": len(b"archive config"),
            }
        ]
        assert any("symlink" in problem for problem in plan["problems"])
        assert link.is_symlink()
        assert outside.read_text(encoding="utf-8") == "outside stays"

    def test_archive_restore_overwrites_and_creates_after_pre_restore(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Restore writes whitelisted payloads and archives the previous state first."""
        original_user = (tmp_dashboard_paths / "config" / "user.json").read_bytes()
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-success.zip",
            payloads={
                "config/user.json": '{"restored": true}',
                "data/dicepp.db": "restored db",
                "data/local_images/avatar.png": b"\x89PNG\r\nrestored",
            },
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-success.zip"),
            json={"confirm_restore": True},
        )

        assert resp.status_code == 200
        restore = resp.json()["restore"]
        assert restore["archive"]["filename"] == "restore-success.zip"
        assert restore["failed_entries"] == []
        restored = {entry["arcname"]: entry for entry in restore["restored_entries"]}
        assert set(restored) == {
            "config/user.json",
            "data/dicepp.db",
            "data/local_images/avatar.png",
        }
        assert restored["config/user.json"]["action"] == "overwrite"
        assert restored["data/dicepp.db"]["action"] == "create"
        assert restored["data/local_images/avatar.png"]["action"] == "create"
        assert restored["data/dicepp.db"]["bytes_written"] == len(b"restored db")
        assert restore["pre_restore_archive"]["filename"].endswith(".zip")
        assert "pre-restore-restore-success-zip" in restore["pre_restore_archive"]["filename"]
        assert restore["pre_restore_manifest"]["description"] == "pre-restore restore-success.zip"

        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == '{"restored": true}'
        assert (tmp_dashboard_paths / "data" / "dicepp.db").read_text(
            encoding="utf-8"
        ) == "restored db"
        assert (tmp_dashboard_paths / "data" / "local_images" / "avatar.png").read_bytes() == (
            b"\x89PNG\r\nrestored"
        )

        pre_restore_filename = restore["pre_restore_archive"]["filename"]
        with zipfile.ZipFile(_archive_path(tmp_dashboard_paths, pre_restore_filename)) as zf:
            assert zf.read("config/user.json") == original_user
            assert "data/dicepp.db" not in zf.namelist()
            assert "data/local_images/avatar.png" not in zf.namelist()

    def test_archive_restore_uses_same_opened_zip_after_pre_restore_creation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_dashboard_paths: Path,
    ):
        """Replacing the source zip during pre-restore cannot swap restore bytes."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-held-open.zip",
            payloads={"config/user.json": '{"restored": "verified"}'},
        )
        original_create_archive = archives.create_archive

        def replace_source_after_pre_restore(*args, **kwargs):
            result = original_create_archive(*args, **kwargs)
            _write_verify_zip(
                tmp_dashboard_paths,
                "restore-held-open.zip",
                payloads={"config/user.json": '{"restored": "replaced"}'},
            )
            return result

        monkeypatch.setattr(archives, "create_archive", replace_source_after_pre_restore)

        restore = archives.restore_archive(
            "restore-held-open.zip",
            paths=DashboardPaths,
        )

        assert restore["failed_entries"] == []
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == '{"restored": "verified"}'

    def test_archive_restore_quiesces_runtime_before_restore_and_restarts(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Opt-in runtime quiesce stops all discovered bots before writing restore payloads."""
        events: list[str] = []
        backend = ArchiveManagerRuntimeBackend(
            events=events,
            runtime_detail={
                "service": "dicepp-secret-service",
                "stdout": "secret-token stdout",
                "stderr": "secret-token stderr",
                "pid": 4242,
            },
        )
        _install_archive_manager_service(test_client, backend)
        original_write = archives._write_zip_payload_to_target

        def record_restore_write(archive, arcname, *, target, root):
            events.append(f"restore:{arcname}")
            return original_write(archive, arcname, target=target, root=root)

        monkeypatch.setattr(archives, "_write_zip_payload_to_target", record_restore_write)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-quiesce-success.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-quiesce-success.zip"),
            json={"confirm_restore": True, "quiesce_runtime": True},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["restore"]["failed_entries"] == []
        assert events == [
            "stop:another_bot",
            "stop:test_bot",
            "restore:config/user.json",
            "start:another_bot",
            "start:test_bot",
        ]
        runtime_quiesce = data["runtime_quiesce"]
        assert runtime_quiesce["runtime_units"] == ["another_bot", "test_bot"]
        assert runtime_quiesce["failed_stage"] is None
        assert runtime_quiesce["restore_started"] is True
        assert runtime_quiesce["restart_attempted"] is True
        assert [op["action"] for op in runtime_quiesce["stop_operations"]] == [
            "stop",
            "stop",
        ]
        assert [op["status"] for op in runtime_quiesce["start_operations"]] == [
            "succeeded",
            "succeeded",
        ]
        _assert_archive_runtime_operations_are_sanitized(runtime_quiesce)
        assert backend.calls[0][2] is None
        assert backend.calls[-1][2] is None

    def test_archive_restore_quiesce_stop_failure_does_not_write_or_pre_restore(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
    ):
        """A failed runtime stop aborts before pre-restore archive creation or writes."""
        original_global = (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        )
        backend = ArchiveManagerRuntimeBackend(
            fail={("another_bot", "stop"): RuntimeError("simulated stop failure")}
        )
        _install_archive_manager_service(test_client, backend)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-stop-failure.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-stop-failure.zip"),
            json={"confirm_restore": True, "quiesce_runtime": True},
        )

        assert resp.status_code == 500
        data = resp.json()
        assert data["ok"] is False
        runtime_quiesce = data["runtime_quiesce"]
        assert runtime_quiesce["failed_stage"] == "stop"
        assert runtime_quiesce["restore_started"] is False
        assert runtime_quiesce["restart_attempted"] is False
        assert runtime_quiesce["stop_operations"][0]["runtime_unit_id"] == "another_bot"
        assert runtime_quiesce["stop_operations"][0]["status"] == "failed"
        assert "simulated stop failure" in runtime_quiesce["stop_operations"][0]["message"]
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == original_global
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-stop-failure.zip"]

    def test_archive_restore_quiesce_verify_mismatch_does_not_stop_or_pre_restore(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
    ):
        """Quiesce restore rejects unverified archives before touching Manager/runtime."""
        original_global = (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        )
        backend = ArchiveManagerRuntimeBackend()
        _install_archive_manager_service(test_client, backend)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-quiesce-mismatch.zip",
            payloads={"config/user.json": '{"restored": true}'},
            manifest_files={
                "config/user.json": hashlib.sha256(b'{"expected": true}').hexdigest()
            },
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-quiesce-mismatch.zip"),
            json={"confirm_restore": True, "quiesce_runtime": True},
        )

        assert resp.status_code == 409
        data = resp.json()
        assert data["ok"] is False
        assert data["verification"]["verified"] is False
        runtime_quiesce = data["runtime_quiesce"]
        assert runtime_quiesce["failed_stage"] == "plan"
        assert runtime_quiesce["restore_started"] is False
        assert runtime_quiesce["stop_operations"] == []
        assert runtime_quiesce["start_operations"] == []
        assert backend.calls == []
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == original_global
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-quiesce-mismatch.zip"]

    def test_archive_restore_quiesce_blocked_plan_does_not_stop_or_pre_restore(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
    ):
        """Quiesce restore rejects blocked plans before Manager operations."""
        backend = ArchiveManagerRuntimeBackend()
        _install_archive_manager_service(test_client, backend)
        target = tmp_dashboard_paths / "data" / "dicepp.db"
        target.mkdir(parents=True)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-quiesce-blocked.zip",
            payloads={"data/dicepp.db": "archive db"},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-quiesce-blocked.zip"),
            json={"confirm_restore": True, "quiesce_runtime": True},
        )

        assert resp.status_code == 409
        data = resp.json()
        assert data["ok"] is False
        assert data["plan"]["entries"][0]["action"] == "blocked"
        runtime_quiesce = data["runtime_quiesce"]
        assert runtime_quiesce["failed_stage"] == "plan"
        assert runtime_quiesce["restore_started"] is False
        assert runtime_quiesce["stop_operations"] == []
        assert runtime_quiesce["start_operations"] == []
        assert backend.calls == []
        assert target.is_dir()
        assert not (target / "archive db").exists()
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-quiesce-blocked.zip"]

    def test_archive_restore_quiesce_restarts_only_bots_stopped_before_stop_failure(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
    ):
        """A later stop failure restarts only earlier successful stops and skips restore."""
        original_global = (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        )
        backend = ArchiveManagerRuntimeBackend(
            fail={("test_bot", "stop"): RuntimeError("simulated later stop failure")},
            runtime_detail={
                "service": "dicepp-secret-service",
                "stdout": "secret-token stdout",
                "stderr": "secret-token stderr",
                "pid": 4242,
            },
        )
        _install_archive_manager_service(test_client, backend)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-partial-stop-failure.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-partial-stop-failure.zip"),
            json={"confirm_restore": True, "quiesce_runtime": True},
        )

        assert resp.status_code == 500
        data = resp.json()
        assert data["ok"] is False
        runtime_quiesce = data["runtime_quiesce"]
        assert runtime_quiesce["failed_stage"] == "stop"
        assert runtime_quiesce["restore_started"] is False
        assert runtime_quiesce["restart_attempted"] is True
        assert [op["runtime_unit_id"] for op in runtime_quiesce["stop_operations"]] == [
            "another_bot",
            "test_bot",
        ]
        assert [op["status"] for op in runtime_quiesce["stop_operations"]] == [
            "succeeded",
            "failed",
        ]
        assert [op["runtime_unit_id"] for op in runtime_quiesce["start_operations"]] == [
            "another_bot"
        ]
        assert [call[:2] for call in backend.calls] == [
            ("another_bot", "stop"),
            ("test_bot", "stop"),
            ("another_bot", "start"),
        ]
        _assert_archive_runtime_operations_are_sanitized(runtime_quiesce)
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == original_global
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-partial-stop-failure.zip"]

    def test_archive_restore_quiesce_restore_failure_still_restarts_runtime(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Runtime restart is attempted even when archive payload writing fails."""
        backend = ArchiveManagerRuntimeBackend()
        _install_archive_manager_service(test_client, backend)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-quiesce-write-failure.zip",
            payloads={
                "config/user.json": '{"restored": true}',
                "data/dicepp.db": "restored db",
            },
        )
        original_write = archives._write_zip_payload_to_target

        def fail_on_dicepp_db(archive, arcname, *, target, root):
            if arcname == "data/dicepp.db":
                raise OSError("simulated restore write failure")
            return original_write(archive, arcname, target=target, root=root)

        monkeypatch.setattr(archives, "_write_zip_payload_to_target", fail_on_dicepp_db)
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-quiesce-write-failure.zip"),
            json={"confirm_restore": True, "quiesce_runtime": True},
        )

        assert resp.status_code == 500
        data = resp.json()
        assert data["ok"] is False
        restore = data["restore"]
        assert [entry["arcname"] for entry in restore["restored_entries"]] == [
            "config/user.json"
        ]
        assert restore["failed_entries"][0]["arcname"] == "data/dicepp.db"
        assert "simulated restore write failure" in restore["failed_entries"][0]["error"]
        runtime_quiesce = data["runtime_quiesce"]
        assert runtime_quiesce["failed_stage"] == "restore"
        assert runtime_quiesce["restore_started"] is True
        assert runtime_quiesce["restart_attempted"] is True
        assert [call[:2] for call in backend.calls] == [
            ("another_bot", "stop"),
            ("test_bot", "stop"),
            ("another_bot", "start"),
            ("test_bot", "start"),
        ]

    def test_archive_restore_quiesce_start_failure_is_not_reported_as_success(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
    ):
        """A restart failure makes the response non-200 even after successful restore writes."""
        backend = ArchiveManagerRuntimeBackend(
            fail={("another_bot", "start"): RuntimeError("simulated start failure")}
        )
        _install_archive_manager_service(test_client, backend)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-start-failure.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-start-failure.zip"),
            json={"confirm_restore": True, "quiesce_runtime": True},
        )

        assert resp.status_code == 500
        data = resp.json()
        assert data["ok"] is False
        assert data["message"] == "Archive restore completed but runtime restart failed"
        assert data["restore"]["failed_entries"] == []
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == '{"restored": true}'
        runtime_quiesce = data["runtime_quiesce"]
        assert runtime_quiesce["failed_stage"] == "start"
        assert runtime_quiesce["start_failed"] is True
        assert [op["status"] for op in runtime_quiesce["start_operations"]] == [
            "failed",
            "succeeded",
        ]
        assert "simulated start failure" in runtime_quiesce["start_operations"][0]["message"]

    def test_archive_restore_without_quiesce_runtime_does_not_call_manager(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
    ):
        """Direct restore remains the default when quiesce_runtime is not explicitly true."""
        backend = ArchiveManagerRuntimeBackend()
        _install_archive_manager_service(test_client, backend)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-direct-default.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-direct-default.zip"),
            json={"confirm_restore": True},
        )

        assert resp.status_code == 200
        assert resp.json()["restore"]["failed_entries"] == []
        assert "runtime_quiesce" not in resp.json()
        assert backend.calls == []
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == '{"restored": true}'

    def test_archive_restore_requires_confirmation_without_writing(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Restore is guarded by an explicit confirmation flag."""
        original_global = (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        )
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-needs-confirm.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_restore_path("restore-needs-confirm.zip"))

        assert resp.status_code == 400
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == original_global
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-needs-confirm.zip"]

    def test_archive_restore_rejects_false_confirmation_without_writing(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """confirm_restore must be exactly true before restore can start."""
        original_global = (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        )
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-false-confirm.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-false-confirm.zip"),
            json={"confirm_restore": False},
        )

        assert resp.status_code == 400
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == original_global
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-false-confirm.zip"]

    def test_archive_restore_verify_failed_returns_409_without_pre_restore(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Unverified archives are rejected before pre-restore or writes happen."""
        original_global = (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        )
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-mismatch.zip",
            payloads={"config/user.json": '{"restored": true}'},
            manifest_files={
                "config/user.json": hashlib.sha256(b'{"expected": true}').hexdigest()
            },
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-mismatch.zip"),
            json={"confirm_restore": True},
        )

        assert resp.status_code == 409
        verification = resp.json()["verification"]
        assert verification["verified"] is False
        assert any("Checksum mismatch" in problem for problem in verification["problems"])
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == original_global
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-mismatch.zip"]

    def test_archive_restore_blocked_plan_returns_409_without_pre_restore(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Blocked restore plans are rejected before pre-restore or writes happen."""
        target = tmp_dashboard_paths / "data" / "dicepp.db"
        target.mkdir(parents=True)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-blocked.zip",
            payloads={"data/dicepp.db": "archive db"},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-blocked.zip"),
            json={"confirm_restore": True},
        )

        assert resp.status_code == 409
        plan = resp.json()["plan"]
        assert plan["entries"] == [
            {
                "arcname": "data/dicepp.db",
                "target_path": "data/dicepp.db",
                "action": "blocked",
                "size": len(b"archive db"),
            }
        ]
        assert any("not a regular file" in problem for problem in plan["problems"])
        assert target.is_dir()
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-blocked.zip"]

    def test_archive_restore_rejects_parent_symlink_before_pre_restore(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """A symlink parent is a restore precondition problem, not a write failure."""
        real_parent = tmp_dashboard_paths / "data" / "local_images-real-parent"
        real_parent.mkdir()
        linked_parent = tmp_dashboard_paths / "data" / "local_images" / "linked-parent"
        linked_parent.parent.mkdir(parents=True, exist_ok=True)
        _try_symlink(linked_parent, real_parent)
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-parent-symlink.zip",
            payloads={"data/local_images/linked-parent/restored.png": "restored"},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-parent-symlink.zip"),
            json={"confirm_restore": True},
        )

        assert resp.status_code == 409
        plan = resp.json()["plan"]
        assert plan["entries"] == []
        assert any("parent is a symlink" in problem for problem in plan["problems"])
        assert not (real_parent / "restored.png").exists()
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-parent-symlink.zip"]

    def test_archive_restore_rejects_root_symlink_before_pre_restore(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """A symlink restore root is rejected during planning."""
        config_root = tmp_dashboard_paths / "config"
        real_config = tmp_dashboard_paths / "config-real"
        config_root.rename(real_config)
        _try_symlink(config_root, real_config)
        original_global = (real_config / "user.json").read_text(encoding="utf-8")
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-root-symlink.zip",
            payloads={"config/user.json": '{"restored": true}'},
        )
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-root-symlink.zip"),
            json={"confirm_restore": True},
        )

        assert resp.status_code == 409
        plan = resp.json()["plan"]
        assert plan["entries"] == []
        assert any("root is a symlink" in problem for problem in plan["problems"])
        assert (real_config / "user.json").read_text(encoding="utf-8") == original_global
        assert sorted(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == ["restore-root-symlink.zip"]

    def test_archive_restore_reports_mid_write_failure_and_stops(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A mid-restore write failure keeps pre-restore and does not report success."""
        original_global = (tmp_dashboard_paths / "config" / "user.json").read_bytes()
        _write_verify_zip(
            tmp_dashboard_paths,
            "restore-mid-write-failure.zip",
            payloads={
                "config/user.json": '{"restored": true}',
                "data/dicepp.db": "restored db",
                "data/local_images/avatar.png": b"\x89PNG\r\nrestored",
            },
        )
        original_write = archives._write_zip_payload_to_target

        def fail_on_dicepp_db(archive, arcname, *, target, root):
            if arcname == "data/dicepp.db":
                raise OSError("simulated restore write failure")
            return original_write(archive, arcname, target=target, root=root)

        monkeypatch.setattr(archives, "_write_zip_payload_to_target", fail_on_dicepp_db)
        setup_auth(test_client)

        resp = test_client.post(
            _api_archive_restore_path("restore-mid-write-failure.zip"),
            json={"confirm_restore": True},
        )

        assert resp.status_code == 500
        restore = resp.json()["restore"]
        assert [entry["arcname"] for entry in restore["restored_entries"]] == [
            "config/user.json"
        ]
        assert len(restore["failed_entries"]) == 1
        assert restore["failed_entries"][0]["arcname"] == "data/dicepp.db"
        assert "simulated restore write failure" in restore["failed_entries"][0]["error"]
        assert (tmp_dashboard_paths / "config" / "user.json").read_text(
            encoding="utf-8"
        ) == '{"restored": true}'
        assert not (tmp_dashboard_paths / "data" / "dicepp.db").exists()
        assert not (tmp_dashboard_paths / "data" / "local_images" / "avatar.png").exists()

        pre_restore_filename = restore["pre_restore_archive"]["filename"]
        with zipfile.ZipFile(_archive_path(tmp_dashboard_paths, pre_restore_filename)) as zf:
            assert zf.read("config/user.json") == original_global
        assert set(
            path.name for path in (tmp_dashboard_paths / "data" / "backups").glob("*.zip")
        ) == {
            "restore-mid-write-failure.zip",
            pre_restore_filename,
        }

    def test_archive_verify_checksum_mismatch_returns_problem(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Checksum mismatch keeps the zip readable but marks it unverified."""
        manifest = _write_verify_zip(
            tmp_dashboard_paths,
            "mismatch.zip",
            payloads={"data/dicepp.db": "tampered bytes"},
            manifest_files={"data/dicepp.db": hashlib.sha256(b"original bytes").hexdigest()},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("mismatch.zip"))

        assert resp.status_code == 200
        verification = resp.json()["verification"]
        assert verification["manifest"] == manifest
        assert verification["verified"] is False
        assert verification["restorable_files"] == []
        assert any(
            "Checksum mismatch" in problem and "data/dicepp.db" in problem
            for problem in verification["problems"]
        )
        assert verification["warnings"] == []

    def test_archive_verify_manifest_declared_missing_payload_returns_problem(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """A manifest entry must exist as a payload inside the same zip."""
        manifest = _write_verify_zip(
            tmp_dashboard_paths,
            "missing-payload.zip",
            payloads={"config/user.json": "{}"},
            manifest_files={
                "config/user.json": hashlib.sha256(b"{}").hexdigest(),
                "data/dicepp.db": hashlib.sha256(b"db").hexdigest(),
            },
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("missing-payload.zip"))

        assert resp.status_code == 200
        verification = resp.json()["verification"]
        assert verification["manifest"] == manifest
        assert verification["verified"] is False
        assert verification["restorable_files"] == ["config/user.json"]
        assert any(
            "missing from zip" in problem and "data/dicepp.db" in problem
            for problem in verification["problems"]
        )

    def test_archive_verify_undeclared_zip_payload_returns_warning(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Extra payload files are reported as warnings without blocking verification."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "extra-payload.zip",
            payloads={"config/user.json": "{}"},
            extra_payloads={"data/untracked.db": "not in manifest"},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("extra-payload.zip"))

        assert resp.status_code == 200
        verification = resp.json()["verification"]
        assert verification["verified"] is True
        assert verification["problems"] == []
        assert verification["restorable_files"] == ["config/user.json"]
        assert any(
            "undeclared payload" in warning and "data/untracked.db" in warning
            for warning in verification["warnings"]
        )

    def test_archive_verify_unsafe_manifest_arcname_returns_problem(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Manifest paths are validated before any restore preview trusts them."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "unsafe-arcname.zip",
            payloads={"../evil": "owned"},
            manifest_files={"../evil": hashlib.sha256(b"owned").hexdigest()},
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("unsafe-arcname.zip"))

        assert resp.status_code == 200
        verification = resp.json()["verification"]
        assert verification["verified"] is False
        assert verification["restorable_files"] == []
        assert any(
            "Unsafe manifest archive path" in problem and "../evil" in problem
            for problem in verification["problems"]
        )

    def test_directory_asset_root_cannot_be_verified_planned_or_restored_as_file(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
    ):
        """A v1 zip cannot replace a managed directory root with a file payload."""
        local_images = tmp_dashboard_paths / "data" / "local_images"
        local_images.mkdir(parents=True)
        (local_images / "existing.png").write_bytes(b"existing")
        _write_verify_zip(
            tmp_dashboard_paths,
            "directory-root-as-file.zip",
            payloads={"data/local_images": "not a directory"},
        )
        setup_auth(test_client)

        verification_response = test_client.post(
            _api_archive_verify_path("directory-root-as-file.zip")
        )
        plan_response = test_client.post(
            _api_archive_restore_plan_path("directory-root-as-file.zip")
        )
        restore_response = test_client.post(
            _api_archive_restore_path("directory-root-as-file.zip"),
            json={"confirm_restore": True},
        )

        assert verification_response.status_code == 200
        verification = verification_response.json()["verification"]
        assert verification["verified"] is False
        assert verification["restorable_files"] == []
        assert any(
            "Unsupported restore path: data/local_images" in problem
            for problem in verification["problems"]
        )
        assert plan_response.status_code == 409
        assert restore_response.status_code == 409
        assert local_images.is_dir()
        assert (local_images / "existing.png").read_bytes() == b"existing"
        assert not list((tmp_dashboard_paths / "data" / "backups").glob("*pre-restore*.zip"))

    def test_restore_targets_use_asset_specific_scope_roots(
        self,
        tmp_dashboard_paths: Path,
    ):
        cases = {
            "config/bots/test_bot.json": tmp_dashboard_paths / "config" / "bots",
            "data/bots/test_bot/bot_data.db": tmp_dashboard_paths / "data" / "bots",
            "data/local_images/avatar.png": tmp_dashboard_paths / "data" / "local_images",
        }

        for arcname, expected_root in cases.items():
            mapped = archives._restore_target_for_arcname(
                arcname,
                paths=DashboardPaths,
            )
            assert not isinstance(mapped, str)
            _target, scope_root, _display = mapped
            assert scope_root == expected_root

    def test_archive_verify_manifest_declared_as_payload_returns_problem(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """manifest.json is archive metadata, not a restorable payload."""
        _write_verify_zip(
            tmp_dashboard_paths,
            "manifest-as-payload.zip",
            payloads={"config/user.json": "{}"},
            manifest_files={
                "config/user.json": hashlib.sha256(b"{}").hexdigest(),
                "manifest.json": "0" * 64,
            },
        )
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("manifest-as-payload.zip"))

        assert resp.status_code == 200
        verification = resp.json()["verification"]
        assert verification["verified"] is False
        assert verification["restorable_files"] == ["config/user.json"]
        assert "manifest.json" not in verification["restorable_files"]
        assert any(
            "must not declare itself" in problem and "manifest.json" in problem
            for problem in verification["problems"]
        )

    def test_delete_archive_removes_file_and_list_entry(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """DELETE removes a regular archive zip from backups."""
        _prepare_archive_fixture(tmp_dashboard_paths)
        setup_auth(test_client)
        created = test_client.post("/api/archives", json={"description": "delete me"})
        assert created.status_code == 200
        filename = created.json()["archive"]["filename"]
        archive_path = _archive_path(tmp_dashboard_paths, filename)
        assert archive_path.is_file()

        resp = test_client.delete(_api_archive_path(filename))

        assert resp.status_code == 200
        assert resp.json()["deleted"] == filename
        assert not archive_path.exists()
        listed = test_client.get("/api/archives")
        assert listed.status_code == 200
        assert filename not in {item["filename"] for item in listed.json()["archives"]}

    def test_checksum_matches_zip_bytes_when_source_changes_before_write(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Checksums are based on payload bytes written into the zip."""
        _prepare_archive_fixture(tmp_dashboard_paths)
        target = tmp_dashboard_paths / "data" / "dicepp.db"
        target.write_text("before write", encoding="utf-8")
        original_write = zipfile.ZipFile.write

        def mutate_before_legacy_write(self, filename, arcname=None, *args, **kwargs):
            if Path(filename) == target:
                target.write_text("after write", encoding="utf-8")
            return original_write(self, filename, arcname, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "write", mutate_before_legacy_write)

        setup_auth(test_client)
        resp = test_client.post("/api/archives", json={"description": "changing source"})

        assert resp.status_code == 200
        archive = resp.json()["archive"]
        manifest = resp.json()["manifest"]
        with zipfile.ZipFile(_archive_path(tmp_dashboard_paths, archive["filename"])) as zf:
            payload = zf.read("data/dicepp.db")
        assert payload == b"before write"
        assert manifest["checksum"]["files"]["data/dicepp.db"] == hashlib.sha256(payload).hexdigest()

    def test_list_archives_returns_created_archive_and_survives_bad_zip(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """GET lists valid archive summaries and unreadable zip files."""
        _prepare_archive_fixture(tmp_dashboard_paths)
        bad_zip = tmp_dashboard_paths / "data" / "backups" / "broken.zip"
        _write(bad_zip, "not really a zip")

        setup_auth(test_client)
        created = test_client.post("/api/archives", json={"description": "list me"})
        assert created.status_code == 200
        filename = created.json()["archive"]["filename"]

        resp = test_client.get("/api/archives")

        assert resp.status_code == 200
        archives = resp.json()["archives"]
        created_entry = next(item for item in archives if item["filename"] == filename)
        assert created_entry["valid"] is True
        assert created_entry["description"] == "list me"
        assert created_entry["file_count"] > 0
        broken_entry = next(item for item in archives if item["filename"] == "broken.zip")
        assert broken_entry["valid"] is False
        assert broken_entry["size"] == bad_zip.stat().st_size

    def test_archive_detail_and_delete_missing_archive_returns_404(
        self, test_client: TestClient
    ):
        """Missing archive names are not treated as valid archives."""
        setup_auth(test_client)

        detail = test_client.get(_api_archive_path("missing.zip"))
        plan = test_client.post(_api_archive_restore_plan_path("missing.zip"))
        restore = test_client.post(
            _api_archive_restore_path("missing.zip"),
            json={"confirm_restore": True},
        )
        delete = test_client.delete(_api_archive_path("missing.zip"))

        assert detail.status_code == 404
        assert plan.status_code == 404
        assert restore.status_code == 404
        assert delete.status_code == 404

    def test_bad_zip_detail_returns_error_but_list_keeps_invalid_summary(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Detail rejects broken zips while list still reports them as invalid."""
        bad_zip = tmp_dashboard_paths / "data" / "backups" / "broken.zip"
        _write(bad_zip, "not really a zip")
        setup_auth(test_client)

        detail = test_client.get(_api_archive_path("broken.zip"))

        assert detail.status_code == 422
        listed = test_client.get("/api/archives")
        assert listed.status_code == 200
        broken_entry = next(
            item for item in listed.json()["archives"]
            if item["filename"] == "broken.zip"
        )
        assert broken_entry["valid"] is False

    @pytest.mark.parametrize("filename", ["../evil.zip", "subdir/evil.zip", "notes.txt"])
    def test_archive_detail_and_delete_reject_unsafe_filename(
        self, test_client: TestClient, filename: str
    ):
        """Archive lookup only accepts plain .zip filenames."""
        setup_auth(test_client)

        detail = test_client.get(_api_archive_path(filename))
        plan = test_client.post(_api_archive_restore_plan_path(filename))
        restore = test_client.post(
            _api_archive_restore_path(filename),
            json={"confirm_restore": True},
        )
        delete = test_client.delete(_api_archive_path(filename))

        assert detail.status_code == 400
        assert plan.status_code == 400
        assert restore.status_code == 400
        assert delete.status_code == 400

    def test_symlink_zip_is_not_accepted_for_detail_or_delete(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """A symlink in backups is not treated as a manageable archive."""
        backups = tmp_dashboard_paths / "data" / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        outside_zip = tmp_dashboard_paths / "outside.zip"
        with zipfile.ZipFile(outside_zip, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": 1}))
        link = backups / "linked.zip"
        _try_symlink(link, outside_zip)
        setup_auth(test_client)

        detail = test_client.get(_api_archive_path("linked.zip"))
        plan = test_client.post(_api_archive_restore_plan_path("linked.zip"))
        restore = test_client.post(
            _api_archive_restore_path("linked.zip"),
            json={"confirm_restore": True},
        )
        delete = test_client.delete(_api_archive_path("linked.zip"))

        assert detail.status_code == 404
        assert plan.status_code == 404
        assert restore.status_code == 404
        assert delete.status_code == 404
        assert link.is_symlink()
        assert outside_zip.exists()

    def test_description_is_sanitized_for_filename(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Description text cannot create archive names outside backups."""
        _prepare_archive_fixture(tmp_dashboard_paths)
        setup_auth(test_client)

        resp = test_client.post(
            "/api/archives",
            json={"description": "../../evil\\manual save"},
        )

        assert resp.status_code == 200
        filename = resp.json()["archive"]["filename"]
        assert "/" not in filename
        assert "\\" not in filename
        assert ".." not in filename
        assert "evil-manual-save" in filename
        assert _archive_path(tmp_dashboard_paths, filename).is_file()

    def test_archives_api_requires_auth(self, test_client: TestClient):
        """Archive endpoints are protected by the Dashboard session."""
        get_resp = test_client.get("/api/archives")
        post_resp = test_client.post("/api/archives", json={"description": "nope"})
        detail_resp = test_client.get("/api/archives/example.zip")
        verify_resp = test_client.post(_api_archive_verify_path("example.zip"))
        plan_resp = test_client.post(_api_archive_restore_plan_path("example.zip"))
        restore_resp = test_client.post(
            _api_archive_restore_path("example.zip"),
            json={"confirm_restore": True},
        )
        delete_resp = test_client.delete("/api/archives/example.zip")

        assert get_resp.status_code == 401
        assert post_resp.status_code == 401
        assert detail_resp.status_code == 401
        assert verify_resp.status_code == 401
        assert plan_resp.status_code == 401
        assert restore_resp.status_code == 401
        assert delete_resp.status_code == 401

    def test_archive_verify_plan_and_restore_reject_unsafe_filename_and_bad_zip(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Restore uses the same filename safety and bad zip semantics."""
        bad_zip = tmp_dashboard_paths / "data" / "backups" / "broken.zip"
        _write(bad_zip, "not really a zip")
        setup_auth(test_client)

        unsafe = test_client.post(_api_archive_verify_path("../evil.zip"))
        unsafe_plan = test_client.post(_api_archive_restore_plan_path("../evil.zip"))
        unsafe_restore = test_client.post(
            _api_archive_restore_path("../evil.zip"),
            json={"confirm_restore": True},
        )
        bad = test_client.post(_api_archive_verify_path("broken.zip"))
        bad_plan = test_client.post(_api_archive_restore_plan_path("broken.zip"))
        bad_restore = test_client.post(
            _api_archive_restore_path("broken.zip"),
            json={"confirm_restore": True},
        )

        assert unsafe.status_code == 400
        assert unsafe_plan.status_code == 400
        assert unsafe_restore.status_code == 400
        assert bad.status_code == 422
        assert bad_plan.status_code == 422
        assert bad_restore.status_code == 422
        assert unsafe.json()["ok"] is False
        assert unsafe_plan.json()["ok"] is False
        assert unsafe_restore.json()["ok"] is False
        assert bad.json()["ok"] is False
        assert bad_plan.json()["ok"] is False
        assert bad_restore.json()["ok"] is False

    def test_archive_verify_missing_manifest_returns_422(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Verify cannot produce a report when manifest.json is absent."""
        target = _archive_path(tmp_dashboard_paths, "no-manifest.zip")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w") as zf:
            zf.writestr("config/user.json", "{}")
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("no-manifest.zip"))

        assert resp.status_code == 422
        assert resp.json()["ok"] is False

    @pytest.mark.parametrize(
        ("filename", "manifest"),
        [
            (
                "unsupported-format.zip",
                {
                    "format_version": 999,
                    "checksum": {
                        "algorithm": "sha256",
                        "files": {},
                    },
                },
            ),
            (
                "checksum-files-not-dict.zip",
                {
                    "format_version": 1,
                    "checksum": {
                        "algorithm": "sha256",
                        "files": [],
                    },
                },
            ),
        ],
    )
    def test_archive_verify_invalid_manifest_schema_returns_422(
        self,
        test_client: TestClient,
        tmp_dashboard_paths: Path,
        filename: str,
        manifest: dict,
    ):
        """Unsupported manifest schema is rejected before checksum reporting."""
        _write_manifest_zip(tmp_dashboard_paths, filename, manifest)
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path(filename))

        assert resp.status_code == 422
        assert resp.json()["ok"] is False

    def test_archive_verify_missing_archive_returns_404(self, test_client: TestClient):
        """Missing archive names are reported as not found."""
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("missing.zip"))

        assert resp.status_code == 404
        assert resp.json()["ok"] is False

    def test_archive_verify_symlink_zip_returns_404(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        """Verify refuses backup entries that are symlinks."""
        backups = tmp_dashboard_paths / "data" / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        outside_zip = tmp_dashboard_paths / "outside-verify.zip"
        with zipfile.ZipFile(outside_zip, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": 1}))
        link = backups / "linked-verify.zip"
        _try_symlink(link, outside_zip)
        setup_auth(test_client)

        resp = test_client.post(_api_archive_verify_path("linked-verify.zip"))

        assert resp.status_code == 404
        assert resp.json()["ok"] is False
        assert link.is_symlink()
        assert outside_zip.exists()
