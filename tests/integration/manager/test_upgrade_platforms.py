from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from dicepp_data import DATA_CATALOG, InstanceLayout
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from dicepp_manager.models import ManagerOperation
from dicepp_manager.release import RELEASE_CONTRACT_VERSION, ReleaseManager
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.update_guard import UpdateGuardError, run_guard
from dicepp_manager.upgrade import (
    LinuxBundleUpgradeAdapter,
    LINUX_STAGE_RESERVE_BYTES,
    UpgradeCompatibilityError,
    UpgradeCoordinator,
    VerifiedUpgradePackage,
    WindowsVelopackUpgradeAdapter,
)

DEFAULT_COMPOSE = """
services:
  bot:
    image: new-bot
    build: {context: ., dockerfile: Dockerfile}
    networks: [dice-net]
    volumes: ["./config:/app/config:rw"]
  dashboard:
    image: new-dashboard
    build: {context: ., dockerfile: Dockerfile.dashboard}
    networks: [manager-net]
  manager:
    image: manager
    networks: [manager-net]
networks:
  dice-net: {external: true}
  manager-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/16
volumes: {}
"""


class DockerExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def capture_images(self, image_records):
        self.calls.append(
            ("capture", tuple(item["image_id"] for item in image_records))
        )
        return {"images": ["old-bot", "old-dashboard"]}

    async def load_images(self, archive):
        self.calls.append(("load", archive.name))
        return {"loaded": ["new-bot", "new-dashboard"]}

    async def resolve_images(self, image_records):
        self.calls.append(
            ("resolve", tuple(item["image_id"] for item in image_records))
        )
        return {
            item["role"]: {
                "reference": item["reference"],
                "image_id": item["image_id"],
                "defaults": {},
            }
            for item in image_records
        }

    async def switch_images(self, *, target_images, previous):
        self.calls.append(("switch", tuple(target_images), previous))
        return {"status": "switched"}

    async def restore_images(self, previous):
        self.calls.append(("restore", previous))
        return {"status": "restored"}


def _record(path: str, payload: bytes) -> dict:
    return {
        "path": path,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_full_nupkg(root: Path, version: str, name: str | None = None) -> Path:
    package = root / "packages" / (name or f"DicePP-{version}-full.nupkg")
    package.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            f"<package><metadata><version>{version}</version></metadata></package>",
        )
        archive.writestr("lib/net8.0/DicePP.exe", f"program-{version}")
    return package


def _windows_package(tmp_path: Path, version: str = "3.1.0"):
    path = tmp_path / f"DicePP-{version}-full.nupkg"
    path.write_bytes(b"target full package")
    return VerifiedUpgradePackage(
        version=version,
        platform="windows",
        arch="amd64",
        path=path,
        metadata_path=tmp_path / "verified-release.json",
        artifact={
            "purpose": "velopack-full",
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        release={},
    )


def _windows_adapter_layout(tmp_path: Path) -> InstanceLayout:
    layout = InstanceLayout.from_root(tmp_path)
    (tmp_path / "current").mkdir()
    for name in ("Update.exe", "DicePP.exe", "DicePP-UpdateGuard.exe"):
        (tmp_path / name).write_bytes(name.encode())
    return layout


def _linux_package(
    tmp_path: Path,
    *,
    automatic_upgrade: bool = True,
    compose_text: str | None = None,
    change_scope: list[str] | None = None,
    inner_change_scope: list[str] | None = None,
) -> tuple[VerifiedUpgradePackage, Path]:
    outer_scope = change_scope or ["runtime"]
    compose = (
        compose_text
        or DEFAULT_COMPOSE
    ).encode()
    images = b"compressed docker archive"
    manifest = {
        "format_version": 1,
        "version": "3.1.0",
        "platform": "linux",
        "arch": "amd64",
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": MANAGER_VERSION,
        "catalog_version": DATA_CATALOG.to_dict()["format_version"],
        "catalog_digest": DATA_CATALOG.digest,
        "automatic_upgrade": automatic_upgrade,
        "change_scope": inner_change_scope or outer_scope,
        "compose": _record("docker-compose.yml", compose),
        "image_archive": _record("images/dicepp.tar.zst", images),
        "images": [
            {
                "role": "bot",
                "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
                "image_id": "sha256:" + ("1" * 64),
            },
            {
                "role": "dashboard",
                "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
                "image_id": "sha256:" + ("2" * 64),
            },
        ],
    }
    manifest_raw = json.dumps(manifest).encode()
    checksums = {
        "docker-compose.yml": hashlib.sha256(compose).hexdigest(),
        "images/dicepp.tar.zst": hashlib.sha256(images).hexdigest(),
        "dicepp-package.json": hashlib.sha256(manifest_raw).hexdigest(),
    }
    checksum_raw = "".join(
        f"{digest}  {name}\n" for name, digest in checksums.items()
    ).encode()
    package_path = tmp_path / "package.zip"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("docker-compose.yml", compose)
        archive.writestr("images/dicepp.tar.zst", images)
        archive.writestr("dicepp-package.json", manifest_raw)
        archive.writestr("checksums.sha256", checksum_raw)
    artifact = {
        "platform": "linux",
        "arch": "amd64",
        "filename": package_path.name,
        "purpose": "linux-bundle",
        "size": package_path.stat().st_size,
        "sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
    }
    package = VerifiedUpgradePackage(
        version="3.1.0",
        platform="linux",
        arch="amd64",
        path=package_path,
        metadata_path=tmp_path / "verified-release.json",
        artifact=artifact,
        release={
            "change_scope": outer_scope,
            "fallbacks": {
                "linux_ghcr_images": [
                    "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0"
                ]
            },
        },
    )
    current_compose = tmp_path / "matching-current-compose.yml"
    current_compose.write_bytes(compose)
    return package, current_compose


@pytest.mark.asyncio
async def test_linux_upgrade_loads_release_images_without_registry_pull(
    tmp_path: Path,
):
    package, current_compose = _linux_package(tmp_path)
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current_compose,
    )

    preflight = await adapter.preflight(package)
    current = await adapter.capture_current(package)
    staged = await adapter.stage(package, "a" * 32)
    await adapter.switch(
        package,
        current=current,
        staged=staged,
        transaction_id="a" * 32,
    )

    assert preflight["network"] == "not_used"
    assert [call[0] for call in executor.calls] == [
        "capture",
        "load",
        "resolve",
        "switch",
    ]
    assert all("pull" not in str(call).lower() for call in executor.calls)
    stage_dir = Path(staged["stage_dir"])
    assert stage_dir.is_dir()

    await adapter.commit(
        package,
        current=current,
        staged=staged,
        transaction_id="a" * 32,
    )

    assert not stage_dir.exists()


@pytest.mark.asyncio
async def test_linux_upgrade_rejects_manual_release_before_docker_action(
    tmp_path: Path,
):
    package, _ = _linux_package(tmp_path, automatic_upgrade=False)
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
    )

    with pytest.raises(UpgradeCompatibilityError, match="manual deployment"):
        await adapter.preflight(package)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_linux_runtime_rejects_automatic_manager_change_before_docker_action(
    tmp_path: Path,
):
    package, current = _linux_package(
        tmp_path,
        change_scope=["runtime", "manager"],
    )
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )

    with pytest.raises(UpgradeCompatibilityError, match="changes Manager"):
        await adapter.preflight(package)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_linux_runtime_rejects_inner_outer_change_scope_mismatch(
    tmp_path: Path,
):
    package, current = _linux_package(
        tmp_path,
        change_scope=["runtime"],
        inner_change_scope=["runtime", "manager"],
    )
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )

    with pytest.raises(UpgradeCompatibilityError, match="differs"):
        await adapter.preflight(package)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_linux_upgrade_rejects_compose_topology_change(tmp_path: Path):
    package, _ = _linux_package(tmp_path)
    current = tmp_path / "current-compose.yml"
    current.write_text(
        """
services:
  bot:
    image: old-bot
    networks: [dice-net]
    volumes: ["./config:/different-target"]
networks:
  dice-net: {external: true}
""",
        encoding="utf-8",
    )
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )

    with pytest.raises(UpgradeCompatibilityError, match="topology"):
        await adapter.preflight(package)

    assert executor.calls == []


@pytest.mark.parametrize(
    ("label", "target"),
    [
        (
            "mount source",
            DEFAULT_COMPOSE.replace("./config:/app/config:rw", "./other:/app/config:rw"),
        ),
        (
            "mount mode",
            DEFAULT_COMPOSE.replace("./config:/app/config:rw", "./config:/app/config:ro"),
        ),
        (
            "network driver",
            DEFAULT_COMPOSE.replace("driver: bridge", "driver: overlay"),
        ),
        (
            "network external",
            DEFAULT_COMPOSE.replace(
                "manager-net:\n    driver: bridge",
                "manager-net:\n    external: true\n    driver: bridge",
            ),
        ),
        (
            "network IPAM",
            DEFAULT_COMPOSE.replace("172.28.0.0/16", "172.29.0.0/16"),
        ),
        (
            "service dependency",
            DEFAULT_COMPOSE.replace(
                "  manager:\n    image: manager",
                "  manager:\n    depends_on: [dashboard]\n    image: manager",
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_linux_upgrade_rejects_each_deep_compose_change(
    tmp_path: Path, label: str, target: str
):
    package, _ = _linux_package(tmp_path, compose_text=target)
    current = tmp_path / "current-compose.yml"
    current.write_text(DEFAULT_COMPOSE, encoding="utf-8")
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )

    with pytest.raises(UpgradeCompatibilityError, match="topology"):
        await adapter.preflight(package)

    assert executor.calls == [], label


@pytest.mark.asyncio
async def test_linux_upgrade_allows_only_image_build_and_compose_version_changes(
    tmp_path: Path,
):
    target = (
        "version: '3.9'\n"
        + DEFAULT_COMPOSE.replace("image: new-bot", "image: another-bot").replace(
            "dockerfile: Dockerfile}", "dockerfile: Otherfile}", 1
        )
    )
    package, _ = _linux_package(tmp_path, compose_text=target)
    current = tmp_path / "current-compose.yml"
    current.write_text(DEFAULT_COMPOSE, encoding="utf-8")
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=DockerExecutor(),
        current_compose=current,
    )

    result = await adapter.preflight(package)

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_linux_stage_refuses_insufficient_space_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    package, current = _linux_package(tmp_path)
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade.shutil.disk_usage",
        lambda _path: type(
            "DiskUsage", (), {"free": LINUX_STAGE_RESERVE_BYTES}
        )(),
    )

    with pytest.raises(UpgradeCompatibilityError, match="disk space"):
        await adapter.stage(package, "a" * 32)

    assert executor.calls == []
    assert not (
        adapter.layout.manager_state_dir / "upgrade-staging" / ("a" * 32)
    ).exists()


@pytest.mark.asyncio
async def test_linux_stage_cleans_partial_archive_when_image_load_fails(
    tmp_path: Path,
):
    package, current = _linux_package(tmp_path)

    class FailingExecutor(DockerExecutor):
        async def load_images(self, archive):
            self.calls.append(("load", archive.name))
            raise OSError("injected image load failure")

    executor = FailingExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )

    with pytest.raises(OSError, match="image load"):
        await adapter.stage(package, "b" * 32)

    assert not (
        adapter.layout.manager_state_dir / "upgrade-staging" / ("b" * 32)
    ).exists()


@pytest.mark.asyncio
async def test_linux_stage_removes_orphan_and_rollback_removes_active_stage(
    tmp_path: Path,
):
    package, current = _linux_package(tmp_path)
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )
    stale = adapter.layout.manager_state_dir / "upgrade-staging" / ("c" * 32)
    stale.mkdir(parents=True)
    (stale / "partial").write_bytes(b"partial")

    staged = await adapter.stage(package, "d" * 32)

    active = Path(staged["stage_dir"])
    assert not stale.exists()
    assert active.is_dir()

    await adapter.rollback(
        package,
        current={"images": ["old-bot", "old-dashboard"]},
        staged=staged,
        transaction_id="d" * 32,
    )

    assert not active.exists()


@pytest.mark.asyncio
async def test_linux_stage_cleanup_refuses_journal_path_outside_staging_root(
    tmp_path: Path,
):
    package, current = _linux_package(tmp_path)
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=DockerExecutor(),
        current_compose=current,
    )
    outside = adapter.layout.manager_state_dir / "must-remain"
    outside.mkdir(parents=True)
    marker = outside / "marker"
    marker.write_bytes(b"keep")
    disguised = (
        adapter.layout.manager_state_dir
        / "upgrade-staging"
        / ".."
        / outside.name
    )

    with pytest.raises(UpgradeCompatibilityError, match="untrusted"):
        await adapter.cleanup({"stage_dir": str(disguised)})

    assert marker.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_windows_guard_markers_are_scoped_to_one_transaction(tmp_path: Path):
    layout = InstanceLayout.from_root(tmp_path)
    _write_full_nupkg(tmp_path, "3.0.0")
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "--package", "{package}"],
        process_identity_loader=lambda: {
            "pid": 1,
            "started_at": "one",
            "executable": str((tmp_path / "DicePP.exe").resolve()),
        },
        version_loader=lambda: "3.0.0",
    )
    package, _ = _linux_package(tmp_path)

    first = await adapter.stage(package, "tx-one")
    second = await adapter.stage(package, "tx-two")

    assert Path(first["health_marker"]).parent.name == "tx-one"
    assert Path(second["health_marker"]).parent.name == "tx-two"
    assert first["rollback_marker"] != second["rollback_marker"]


def test_windows_default_restart_runs_launcher_in_background(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "--package", "{package}"],
        process_identity_loader=lambda: {},
    )

    assert adapter.restart_command == [str(layout.root / "DicePP.exe"), "--background"]


@pytest.mark.asyncio
async def test_windows_default_background_restart_reaches_target_and_rollback_guard_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    launcher = layout.root / "DicePP.exe"
    launcher.write_bytes(b"launcher")
    package = _windows_package(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["vpk", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 1,
            "started_at": "old",
            "executable": str(launcher.resolve()),
        },
        version_loader=lambda: "3.0.0",
    )
    monkeypatch.setattr(
        adapter,
        "start_guard",
        lambda _request: (SimpleNamespace(pid=1234), tmp_path / "guard.exe"),
    )

    async def create_guard_request(transaction_id: str) -> tuple[Path, dict]:
        transaction_dir = layout.manager_state_dir / "update-guard" / transaction_id
        transaction_dir.mkdir(parents=True)
        rollback_package = transaction_dir / "DicePP-3.0.0-full.nupkg"
        rollback_package.write_bytes(b"rollback")
        staged = {
            "request": str(transaction_dir / "request.json"),
            "guard_marker": str(transaction_dir / "guard.json"),
            "started_marker": str(transaction_dir / "started.json"),
            "health_marker": str(transaction_dir / "health.json"),
            "rollback_marker": str(transaction_dir / "rollback.json"),
            "rollback_package": str(rollback_package),
            "rollback_package_sha256": hashlib.sha256(
                rollback_package.read_bytes()
            ).hexdigest(),
        }
        await adapter.switch(
            package,
            current={
                "source_version": "3.0.0",
                "process_identity": adapter.process_identity_loader(),
            },
            staged=staged,
            transaction_id=transaction_id,
        )
        request_path = Path(staged["request"])
        return request_path, json.loads(request_path.read_text(encoding="utf-8"))

    expected_restart = [str(launcher), "--background"]
    target_path, target_request = await create_guard_request("target")
    target_identity = {
        "pid": 2,
        "started_at": "new",
        "executable": str(launcher.resolve()),
    }
    target_started = {
        "format_version": 2,
        "transaction_id": "target",
        "target_version": "3.1.0",
        "actual_version": "3.1.0",
        "status": "started",
        "manager_identity": target_identity,
    }
    target_health = {
        "format_version": 2,
        "transaction_id": "target",
        "target_version": "3.1.0",
        "status": "healthy",
        "manager_identity": target_identity,
        "health": {"status": "ok"},
    }
    target_starts: list[list[str]] = []

    def start_target(command: list[str]) -> SimpleNamespace:
        target_starts.append(command)
        Path(target_request["started_marker"]).write_text(
            json.dumps(target_started), encoding="utf-8"
        )
        Path(target_request["health_marker"]).write_text(
            json.dumps(target_health), encoding="utf-8"
        )
        return SimpleNamespace(pid=2)

    target_result = run_guard(
        target_path,
        inspect_identity=lambda _pid: None,
        run_command=lambda _command: None,
        start_command=start_target,
        health_probe=lambda _request: {
            "ok": True,
            "dicepp_version": "3.1.0",
            "upgrade_handoff": target_health,
        },
    )

    rollback_path, rollback_request = await create_guard_request("rollback")
    rollback_commands: list[list[str]] = []

    def fail_target_install(command: list[str]) -> None:
        rollback_commands.append(command)
        if command == rollback_request["install_command"]:
            raise OSError("target install failed")

    rollback_result = run_guard(
        rollback_path,
        inspect_identity=lambda _pid: None,
        run_command=fail_target_install,
        start_command=rollback_commands.append,
    )

    assert target_request["restart_command"] == expected_restart
    assert target_result["status"] == "healthy"
    assert target_starts == [expected_restart]
    assert rollback_result["status"] == "program_rolled_back"
    assert rollback_commands == [
        rollback_request["install_command"],
        rollback_request["rollback_command"],
        expected_restart,
    ]


@pytest.mark.asyncio
async def test_windows_guard_spawn_persists_child_output_without_creation_flags(
    monkeypatch,
    tmp_path: Path,
):
    instance_root = tmp_path / "instance"
    transaction_dir = (
        instance_root / "manager" / "state" / "update-guard" / "tx"
    )
    transaction_dir.mkdir(parents=True)
    package = _windows_package(tmp_path)
    rollback_package = tmp_path / "rollback.nupkg"
    rollback_package.write_bytes(b"rollback")
    guard_source = instance_root / "DicePP-UpdateGuard.exe"
    guard_source.write_bytes(b"guard-binary")
    guard_runtime_root = tmp_path / "external-guard"
    staged = {
        "request": str(transaction_dir / "request.json"),
        "guard_marker": str(transaction_dir / "guard.json"),
        "started_marker": str(transaction_dir / "started.json"),
        "health_marker": str(transaction_dir / "health.json"),
        "rollback_marker": str(transaction_dir / "rollback.json"),
        "rollback_package": str(rollback_package),
        "rollback_package_sha256": hashlib.sha256(
            rollback_package.read_bytes()
        ).hexdigest(),
    }
    adapter = WindowsVelopackUpgradeAdapter(
        layout=InstanceLayout.from_root(instance_root),
        guard_command=[str(guard_source), "--diagnostic"],
        install_command=["Update.exe", "apply", "{package}"],
        restart_command=["DicePP.exe"],
        process_identity_loader=lambda: {
            "pid": 1,
            "started_at": "old",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
        version_loader=lambda: "3.0.0",
        guard_runtime_root=guard_runtime_root,
    )
    observed: dict = {}

    def fake_spawn(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        kwargs["stdout"].write(b"guard bootstrap diagnostics\n")

        class Process:
            pid = 4321

        return Process()

    monkeypatch.setattr(
        "dicepp_manager.upgrade.subprocess.Popen",
        fake_spawn,
    )

    result = await adapter.switch(
        package,
        current={
            "source_version": "3.0.0",
            "process_identity": adapter.process_identity_loader(),
        },
        staged=staged,
        transaction_id="tx",
    )

    assert result["guard_pid"] == 4321
    guard_executable = Path(result["guard_executable"])
    assert guard_executable.read_bytes() == b"guard-binary"
    assert guard_executable.is_relative_to(guard_runtime_root)
    assert not guard_executable.is_relative_to(instance_root)
    assert observed["argv"] == [
        str(guard_executable),
        "--diagnostic",
        "--request",
        staged["request"],
    ]
    assert observed["kwargs"]["stdin"] is subprocess.DEVNULL
    assert observed["kwargs"]["stderr"] is subprocess.STDOUT
    assert observed["kwargs"]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "creationflags" not in observed["kwargs"]
    assert (transaction_dir / "guard-output.log").read_bytes() == (
        b"guard bootstrap diagnostics\n"
    )


def test_windows_guard_runtime_must_be_outside_install_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="outside the install root"):
        WindowsVelopackUpgradeAdapter(
            layout=InstanceLayout.from_root(tmp_path),
            guard_command=[str(tmp_path / "DicePP-UpdateGuard.exe")],
            install_command=["Update.exe", "apply", "{package}"],
            process_identity_loader=lambda: {},
            guard_runtime_root=tmp_path / "manager" / "external-guard",
        )


@pytest.mark.asyncio
async def test_windows_preflight_rejects_noop_and_requires_old_full_package(
    tmp_path: Path,
):
    layout = _windows_adapter_layout(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=[str(tmp_path / "Update.exe"), "apply", "-p", "{package}"],
        restart_command=[str(tmp_path / "DicePP.exe")],
        process_identity_loader=lambda: {
            "pid": 1,
            "started_at": "old",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
        version_loader=lambda: "3.1.0",
    )

    # A no-op target is rejected up front, before any rollback material
    # resolution (which may download the current-version full package).
    with pytest.raises(UpgradeCompatibilityError, match="already installed"):
        await adapter.preflight(_windows_package(tmp_path))

    # A real target still requires the current-version full package.
    with pytest.raises(UpgradeCompatibilityError, match="full package"):
        await adapter.preflight(_windows_package(tmp_path, "3.2.0"))


@pytest.mark.asyncio
async def test_windows_preflight_rejects_stable_guard_digest_mismatch(
    tmp_path: Path,
):
    layout = _windows_adapter_layout(tmp_path)
    _write_full_nupkg(tmp_path, "3.0.0")
    bundled = tmp_path / "current" / "DicePP-UpdateGuard.exe"
    bundled.write_bytes(b"guard-from-current-version")
    (tmp_path / "DicePP-UpdateGuard.exe").write_bytes(b"stale-stable-guard")
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(tmp_path / "DicePP-UpdateGuard.exe")],
        install_command=[str(tmp_path / "Update.exe"), "apply", "-p", "{package}"],
        restart_command=[str(tmp_path / "DicePP.exe")],
        process_identity_loader=lambda: {
            "pid": 1,
            "started_at": "old",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
        version_loader=lambda: "3.0.0",
        bundled_guard_path=bundled,
    )

    with pytest.raises(UpgradeCompatibilityError, match="does not match"):
        await adapter.preflight(_windows_package(tmp_path, "3.1.0"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_id", "other"),
        ("target_version", "9.9.9"),
        ("manager_identity", None),
    ],
)
def test_windows_rollback_marker_is_not_authoritative_when_binding_is_wrong(
    tmp_path: Path, field: str, value,
):
    layout = InstanceLayout.from_root(tmp_path)
    marker_dir = layout.manager_state_dir / "update-guard" / "tx"
    marker_dir.mkdir(parents=True)
    marker = {
        "format_version": 2,
        "transaction_id": "tx",
        "target_version": "3.1.0",
        "source_version": "3.0.0",
        "status": "program_rolled_back",
        "manager_identity": {
            "pid": 2,
            "started_at": "new",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
    }
    marker[field] = value
    path = marker_dir / "rollback.json"
    path.write_text(json.dumps(marker), encoding="utf-8")
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: marker["manager_identity"],
    )

    assert adapter.validate_rollback_marker(
        {
            "transaction_id": "tx",
            "target_version": "3.1.0",
            "platform_staged": {
                "source_version": "3.0.0",
                "rollback_marker": str(path),
            },
        }
    ) is None


def test_windows_health_passed_journal_without_matching_marker_is_not_commit(
    tmp_path: Path,
):
    layout = InstanceLayout.from_root(tmp_path)
    marker_dir = layout.manager_state_dir / "update-guard" / "tx"
    marker_dir.mkdir(parents=True)
    path = marker_dir / "health.json"
    path.write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": "other",
                "target_version": "3.1.0",
                "status": "healthy",
                "manager_identity": {
                    "pid": 2,
                    "started_at": "new",
                    "executable": str(
                        (tmp_path / "current" / "DicePP.exe").resolve()
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: {
            "pid": 1,
            "started_at": "old",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
    )

    assert adapter.validate_health_marker(
        {
            "transaction_id": "tx",
            "target_version": "3.1.0",
            "commit_point": "health_passed",
            "platform_staged": {"health_marker": str(path)},
        }
    ) is None


def _guard_request(tmp_path: Path) -> tuple[Path, dict]:
    package = tmp_path / "package.nupkg"
    package.write_bytes(b"package")
    rollback_package = tmp_path / "old-package.nupkg"
    rollback_package.write_bytes(b"old-package")
    guard = tmp_path / "guard.json"
    started = tmp_path / "started.json"
    health = tmp_path / "health.json"
    rollback = tmp_path / "rollback.json"
    current = tmp_path / "current"
    current.mkdir()
    (current / "DicePP.exe").write_bytes(b"old program")
    identity = {
        "pid": 123,
        "started_at": "456",
        "executable": str((tmp_path / "DicePP.exe").resolve()),
    }
    request = {
        "format_version": 2,
        "transaction_id": "tx",
        "target_version": "3.1.0",
        "source_version": "3.0.0",
        "package": str(package.resolve()),
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "rollback_package": str(rollback_package.resolve()),
        "rollback_package_sha256": hashlib.sha256(
            rollback_package.read_bytes()
        ).hexdigest(),
        "manager_identity": identity,
        "guard_marker": str(guard.resolve()),
        "started_marker": str(started.resolve()),
        "health_marker": str(health.resolve()),
        "rollback_marker": str(rollback.resolve()),
        "health_url": "http://127.0.0.1:4091/v1/health",
        "auth_token_path": str((tmp_path / "api-token").resolve()),
        "install_command": ["vpk", "apply", str(package.resolve())],
        "rollback_command": ["vpk", "apply", str(rollback_package.resolve())],
        "restart_command": [str((tmp_path / "DicePP.exe").resolve())],
        "manager_exit_timeout_seconds": 1,
        "health_timeout_seconds": 1,
        "requested_at": "2026-07-23T00:00:00+00:00",
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path, request


def _healthy_guard_markers(request: dict, *, identity: dict | None = None) -> dict:
    identity = identity or {
        "pid": 999,
        "started_at": "new-start",
        "executable": str(
            (Path(request["package"]).parent / "current" / "DicePP.exe").resolve()
        ),
    }
    started = {
        "format_version": 2,
        "transaction_id": request["transaction_id"],
        "target_version": request["target_version"],
        "actual_version": request["target_version"],
        "status": "started",
        "manager_identity": identity,
    }
    health = {
        "format_version": 2,
        "transaction_id": request["transaction_id"],
        "target_version": request["target_version"],
        "status": "healthy",
        "manager_identity": identity,
        "health": {"status": "ok"},
    }
    Path(request["started_marker"]).write_text(json.dumps(started), encoding="utf-8")
    Path(request["health_marker"]).write_text(json.dumps(health), encoding="utf-8")
    return health


def test_update_guard_accepts_only_exact_known_process_identity(tmp_path: Path):
    path, request = _guard_request(tmp_path)
    health = _healthy_guard_markers(request)
    started_payload = Path(request["started_marker"]).read_text(encoding="utf-8")
    Path(request["started_marker"]).unlink()
    inspected = []
    commands = []

    def inspect(pid):
        inspected.append(pid)
        # Reused PID has a different start identity, so the guard treats the
        # known Manager as gone without touching the unrelated process.
        return {**request["manager_identity"], "started_at": "reused"}

    result = run_guard(
        path,
        inspect_identity=inspect,
        run_command=lambda argv: commands.append(argv),
        start_command=lambda _argv: (
            Path(request["started_marker"]).write_text(
                started_payload, encoding="utf-8"
            )
            or object()
        ),
        health_probe=lambda _request: {
            "ok": True,
            "dicepp_version": "3.1.0",
            "upgrade_handoff": health,
        },
    )

    assert result["status"] == "healthy"
    assert inspected == [123]
    assert commands == [request["install_command"]]


def test_update_guard_persists_ordered_process_lifecycle_trace(tmp_path: Path):
    path, request = _guard_request(tmp_path)
    health = _healthy_guard_markers(request)
    started_payload = Path(request["started_marker"]).read_text(encoding="utf-8")
    Path(request["started_marker"]).unlink()

    class StartedProcess:
        pid = 999

    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=lambda _argv: None,
        start_command=lambda _argv: (
            Path(request["started_marker"]).write_text(
                started_payload,
                encoding="utf-8",
            )
            and StartedProcess()
        ),
        health_probe=lambda _request: {
            "ok": True,
            "dicepp_version": request["target_version"],
            "upgrade_handoff": health,
        },
    )

    trace = [
        json.loads(line)
        for line in (tmp_path / "guard-trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert result["status"] == "healthy"
    assert [entry["stage"] for entry in trace] == [
        "run_guard_enter",
        "request_validated",
        "guard_identity_acquired",
        "guard_marker_running",
        "manager_exit_wait_started",
        "manager_exit_confirmed",
        "package_digests_verified",
        "target_apply_started",
        "target_apply_completed",
        "target_manager_restart_requested",
        "started_marker_wait_started",
        "started_marker_received",
        "authenticated_health_wait_started",
        "authenticated_health_received",
        "health_marker_wait_started",
        "health_marker_received",
        "guard_completed_healthy",
    ]
    assert all(entry["format_version"] == 1 for entry in trace)
    assert all(entry["pid"] == trace[0]["pid"] for entry in trace)
    assert all(entry["ppid"] == trace[0]["ppid"] for entry in trace)
    assert all(entry["timestamp"] for entry in trace)


def test_update_guard_rolls_program_back_when_install_or_health_fails(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    commands = []

    def run(argv):
        commands.append(argv)
        if argv == request["install_command"]:
            raise OSError("injected Velopack failure")

    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=run,
        start_command=lambda argv: commands.append(argv),
    )

    assert result["status"] == "program_rolled_back"
    assert commands == [
        request["install_command"],
        request["rollback_command"],
        request["restart_command"],
    ]
    persisted = json.loads(Path(request["rollback_marker"]).read_text())
    assert persisted["status"] == "program_rolled_back"


def test_update_guard_terminates_real_manager_identity_not_launcher_stub(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    new_identity = {
        "pid": 999,
        "started_at": "new-start",
        "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
    }
    health = _healthy_guard_markers(request, identity=new_identity)
    handles = []

    class Handle:
        def __init__(self, identity):
            self.identity = identity
            self.terminated = False
            self.closed = False

        def wait(self, _timeout):
            return self.identity == request["manager_identity"]

        def terminate(self, _timeout):
            self.terminated = True
            return True

        def close(self):
            self.closed = True

    def open_handle(identity):
        handle = Handle(identity)
        handles.append(handle)
        return handle

    class Stub:
        def terminate(self):
            pytest.fail("launcher stub must not be terminated")

    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=lambda _argv: None,
        start_command=lambda _argv: Stub(),
        open_identity_handle=open_handle,
        health_probe=lambda _request: {
            "ok": True,
            "dicepp_version": "wrong-version",
            "upgrade_handoff": health,
        },
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "program_rolled_back"
    assert handles[1].identity == new_identity
    assert handles[1].terminated is True
    assert all(handle.closed for handle in handles)


def test_update_guard_resumes_durable_velpack_rollback_after_crash(tmp_path: Path):
    path, request = _guard_request(tmp_path)
    rollback = {
        "format_version": 2,
        "transaction_id": "tx",
        "target_version": "3.1.0",
        "source_version": "3.0.0",
        "manager_identity": request["manager_identity"],
        "status": "program_rollback_started",
    }
    Path(request["rollback_marker"]).write_text(
        json.dumps(rollback), encoding="utf-8"
    )
    commands = []

    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=lambda argv: commands.append(argv),
        start_command=lambda argv: commands.append(argv),
    )

    assert result["status"] == "program_rolled_back"
    assert commands == [request["rollback_command"], request["restart_command"]]


def test_update_guard_terminal_rollback_survives_crash_before_old_manager_start(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    commands = []

    def run(argv):
        commands.append(argv)
        if argv == request["install_command"]:
            raise OSError("injected target apply failure")

    with pytest.raises(RuntimeError, match="crash before old Manager start"):
        run_guard(
            path,
            inspect_identity=lambda _pid: None,
            run_command=run,
            start_command=lambda _argv: (_ for _ in ()).throw(
                RuntimeError("crash before old Manager start")
            ),
        )

    persisted = json.loads(Path(request["rollback_marker"]).read_text())
    assert persisted["status"] == "program_rolled_back"
    assert commands == [
        request["install_command"],
        request["rollback_command"],
    ]

    Path(request["package"]).unlink()
    Path(request["rollback_package"]).unlink()
    restarted = []
    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=lambda _argv: pytest.fail(
            "terminal rollback must not reapply a package"
        ),
        start_command=lambda argv: restarted.append(argv),
    )

    assert result["status"] == "program_rolled_back"
    assert restarted == [request["restart_command"]]


def test_update_guard_rejects_wrong_target_version_even_with_healthy_marker(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    health = _healthy_guard_markers(request)
    commands = []

    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=lambda argv: commands.append(argv),
        start_command=lambda _argv: object(),
        health_probe=lambda _request: {
            "ok": True,
            "dicepp_version": "3.0.0",
            "upgrade_handoff": health,
        },
    )

    assert result["status"] == "program_rolled_back"
    assert commands == [request["rollback_command"]]


def test_update_guard_rejects_tampered_preserved_rollback_package(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    Path(request["rollback_package"]).write_bytes(b"tampered")
    commands = []

    with pytest.raises(UpdateGuardError, match="digest mismatch"):
        run_guard(
            path,
            inspect_identity=lambda _pid: None,
            run_command=lambda argv: commands.append(argv),
        )

    assert commands == []


def test_update_guard_rejects_stale_started_marker_from_other_target(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    health = _healthy_guard_markers(request)
    started = json.loads(Path(request["started_marker"]).read_text())
    started["target_version"] = "9.9.9"
    Path(request["started_marker"]).write_text(
        json.dumps(started), encoding="utf-8"
    )
    request["health_timeout_seconds"] = 0.01
    path.write_text(json.dumps(request), encoding="utf-8")
    commands = []

    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=lambda argv: commands.append(argv),
        start_command=lambda _argv: object(),
        health_probe=lambda _request: {
            "ok": True,
            "dicepp_version": "3.1.0",
            "upgrade_handoff": health,
        },
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "program_rolled_back"
    assert commands == [request["install_command"], request["rollback_command"]]


def test_update_guard_resumes_after_health_without_reapplying_target(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    health = _healthy_guard_markers(request)
    commands = []

    result = run_guard(
        path,
        inspect_identity=lambda _pid: None,
        run_command=lambda argv: commands.append(argv),
        start_command=lambda _argv: pytest.fail("target must not restart"),
        health_probe=lambda _request: {
            "ok": True,
            "dicepp_version": "3.1.0",
            "upgrade_handoff": health,
        },
    )

    assert result["status"] == "healthy"
    assert commands == []


def test_update_guard_rejects_request_without_full_process_identity(
    tmp_path: Path,
):
    path, request = _guard_request(tmp_path)
    del request["manager_identity"]["started_at"]
    path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(UpgradeCompatibilityError, match="PID/start time"):
        run_guard(path, inspect_identity=lambda _pid: None)


# ── Rollback material: Release fetch + packages directory invariant ──────


class _Response:
    def __init__(self, body: bytes, *, status: int = 200, headers=None) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self.offset
        result = self.body[self.offset : self.offset + size]
        self.offset += len(result)
        return result

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _Transport:
    def __init__(self, routes: dict[str, list[_Response]]) -> None:
        self.routes = routes
        self.requests: list[str] = []

    def open(self, url: str, *, headers=None, timeout=30):
        self.requests.append(url)
        return self.routes[url].pop(0)


def _nupkg_bytes(version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            f"<package><metadata><version>{version}</version></metadata></package>",
        )
        archive.writestr("lib/net8.0/DicePP.exe", f"program-{version}")
    return buffer.getvalue()


def _make_nupkg(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_nupkg_bytes(version))
    return path


def _verified_nupkg_package(path: Path, version: str) -> VerifiedUpgradePackage:
    return VerifiedUpgradePackage(
        version=version,
        platform="windows",
        arch="amd64",
        path=path,
        metadata_path=path.parent / "verified-release.json",
        artifact={
            "purpose": "velopack-full",
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        release={},
    )


def _current_release_routes(version: str, nupkg_body: bytes) -> dict:
    feeds = {
        f"releases.win-x64-stable.json": (b'{"Assets":[]}', "velopack-releases"),
        f"assets.win-x64-stable.json": (b"[]", "velopack-assets"),
    }
    bodies = {f"DicePP-{version}-full.nupkg": (nupkg_body, "velopack-full")}
    bodies.update(feeds)
    artifacts = [
        {
            "platform": "windows",
            "arch": "amd64",
            "filename": filename,
            "purpose": purpose,
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        for filename, (body, purpose) in bodies.items()
    ]
    manifest = {
        "contract_version": RELEASE_CONTRACT_VERSION,
        "version": version,
        "channel": "stable",
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": MANAGER_VERSION,
        "catalog_version": 1,
        "catalog_digest": "1" * 64,
        "change_scope": ["runtime"],
        "automatic_upgrade": True,
        "artifacts": artifacts,
        "fallbacks": {
            "linux_ghcr_images": [
                f"ghcr.io/pear-studio/nonebot-dicepp:v{version}",
                f"ghcr.io/pear-studio/dicepp-dashboard:v{version}",
            ]
        },
    }
    manifest_bytes = json.dumps(manifest).encode()
    release = {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/example/release",
        "published_at": "2026-07-23T00:00:00Z",
        "assets": [
            {
                "name": "dicepp-release.json",
                "size": len(manifest_bytes),
                "digest": f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}",
                "browser_download_url": "https://downloads/manifest",
            }
        ]
        + [
            {
                "name": artifact["filename"],
                "size": artifact["size"],
                "digest": f"sha256:{artifact['sha256']}",
                "browser_download_url": f"https://downloads/{artifact['purpose']}",
            }
            for artifact in artifacts
        ],
    }
    return {
        "https://api/releases?per_page=100&page=1": [
            _Response(json.dumps([release]).encode())
        ],
        "https://downloads/manifest": [_Response(manifest_bytes)],
        "https://downloads/velopack-full": [
            _Response(nupkg_body, headers={"ETag": '"full"'})
        ],
    }


def _rollback_fetch_manager(
    layout: InstanceLayout, transport: _Transport
) -> ReleaseManager:
    return ReleaseManager(
        layout=layout,
        transport=transport,
        github_api="https://api",
        target=("windows", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )


def _rollback_adapter(
    tmp_path: Path,
    layout: InstanceLayout,
    *,
    version: str,
    fetcher=None,
) -> WindowsVelopackUpgradeAdapter:
    return WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(tmp_path / "DicePP-UpdateGuard.exe")],
        install_command=[str(tmp_path / "Update.exe"), "apply", "-p", "{package}"],
        restart_command=[str(tmp_path / "DicePP.exe")],
        process_identity_loader=lambda: {
            "pid": 1,
            "started_at": "old",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
        version_loader=lambda: version,
        bundled_guard_path=tmp_path / "DicePP-UpdateGuard.exe",
        rollback_package_fetcher=fetcher,
    )


@pytest.mark.asyncio
async def test_windows_first_upgrade_fetches_rollback_package_from_release(
    tmp_path: Path,
):
    """Portable first install has no packages/ directory: the rollback
    material for the current version is downloaded from the GitHub
    Release (verified against the contract) instead of refusing."""
    layout = _windows_adapter_layout(tmp_path)
    nupkg_body = _nupkg_bytes("3.0.0")
    transport = _Transport(_current_release_routes("3.0.0", nupkg_body))
    manager = _rollback_fetch_manager(layout, transport)
    adapter = _rollback_adapter(
        tmp_path,
        layout,
        version="3.0.0",
        fetcher=manager.fetch_rollback_package,
    )
    package = _windows_package(tmp_path, "3.1.0")
    assert not (tmp_path / "packages").exists()

    preflight = await adapter.preflight(package)
    staged = await adapter.stage(package, "f" * 32)

    digest = hashlib.sha256(nupkg_body).hexdigest()
    assert preflight["rollback_package_sha256"] == digest
    downloaded = Path(preflight["rollback_package"])
    assert downloaded.is_file()
    assert downloaded.read_bytes() == nupkg_body
    assert Path(staged["rollback_package"]).read_bytes() == nupkg_body
    assert staged["rollback_package_sha256"] == digest
    assert "https://downloads/velopack-full" in transport.requests


@pytest.mark.asyncio
async def test_windows_commit_maintains_packages_dir_for_next_upgrade(
    tmp_path: Path,
):
    """After a healthy commit the target nupkg is the only full package in
    packages/, so the next upgrade finds its rollback material locally and
    never downloads."""
    layout = _windows_adapter_layout(tmp_path)
    _write_full_nupkg(tmp_path, "3.0.0")
    target = _make_nupkg(
        layout.manager_packages_dir / "3.1.0" / "DicePP-3.1.0-full.nupkg",
        "3.1.0",
    )
    package = _verified_nupkg_package(target, "3.1.0")
    marker_dir = layout.manager_state_dir / "update-guard" / ("c" * 32)
    marker_dir.mkdir(parents=True)
    health = marker_dir / "health.json"
    health.write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": "c" * 32,
                "target_version": "3.1.0",
                "status": "healthy",
                "manager_identity": {
                    "pid": 2,
                    "started_at": "new",
                    "executable": str(
                        (tmp_path / "current" / "DicePP.exe").resolve()
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    adapter = _rollback_adapter(tmp_path, layout, version="3.0.0")

    result = await adapter.commit(
        package,
        current={},
        staged={"health_marker": str(health)},
        transaction_id="c" * 32,
    )

    assert "packages_maintenance_error" not in result
    packages_dir = tmp_path / "packages"
    remaining = list(packages_dir.glob("*-full.nupkg"))
    assert [item.name for item in remaining] == ["DicePP-3.1.0-full.nupkg"]
    assert remaining[0].read_bytes() == target.read_bytes()

    def forbidden_fetch(_version: str):
        pytest.fail("rollback material must come from packages/, not the Release")

    second = _rollback_adapter(
        tmp_path, layout, version="3.1.0", fetcher=forbidden_fetch
    )
    preflight = await second.preflight(_windows_package(tmp_path, "3.2.0"))

    assert Path(preflight["rollback_package"]).resolve() == remaining[0].resolve()


@pytest.mark.asyncio
async def test_windows_commit_reports_packages_maintenance_failure_without_failing(
    tmp_path: Path,
):
    """Maintaining packages/ is best-effort: a failure there is reported
    on the commit result, never raised, so an already healthy commit is
    not adjudicated failed."""
    layout = _windows_adapter_layout(tmp_path)
    target = _make_nupkg(
        layout.manager_packages_dir / "3.1.0" / "DicePP-3.1.0-full.nupkg",
        "3.1.0",
    )
    package = _verified_nupkg_package(target, "3.1.0")
    marker_dir = layout.manager_state_dir / "update-guard" / ("c" * 32)
    marker_dir.mkdir(parents=True)
    health = marker_dir / "health.json"
    health.write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": "c" * 32,
                "target_version": "3.1.0",
                "status": "healthy",
                "manager_identity": {
                    "pid": 2,
                    "started_at": "new",
                    "executable": str(
                        (tmp_path / "current" / "DicePP.exe").resolve()
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    # A symlinked packages/ drives _maintain_packages_dir onto its first
    # failure return path without raising.
    (tmp_path / "packages").symlink_to(tmp_path / "elsewhere")
    adapter = _rollback_adapter(tmp_path, layout, version="3.0.0")

    result = await adapter.commit(
        package,
        current={},
        staged={"health_marker": str(health)},
        transaction_id="c" * 32,
    )

    assert result["status"] == "healthy"
    assert result["transaction_id"] == "c" * 32
    assert isinstance(result["packages_maintenance_error"], str)
    assert result["packages_maintenance_error"]


@pytest.mark.asyncio
async def test_windows_unavailable_rollback_material_requires_manual_update(
    tmp_path: Path,
):
    """When the current version's Release/artifact cannot be fetched, the
    upgrade is rejected with a manual-update hint and no half state."""
    layout = _windows_adapter_layout(tmp_path)
    transport = _Transport(
        {
            "https://api/releases?per_page=100&page=1": [
                _Response(json.dumps([]).encode())
            ]
        }
    )
    manager = _rollback_fetch_manager(layout, transport)
    adapter = _rollback_adapter(
        tmp_path,
        layout,
        version="3.0.0",
        fetcher=manager.fetch_rollback_package,
    )

    with pytest.raises(UpgradeCompatibilityError, match="manual"):
        await adapter.preflight(_windows_package(tmp_path, "3.1.0"))

    assert not (tmp_path / "packages").exists()
    assert list(tmp_path.rglob("*.part")) == []
    assert not (layout.manager_state_dir / "update-guard").exists()


def _guard_cache_adapter(tmp_path: Path) -> WindowsVelopackUpgradeAdapter:
    instance_root = tmp_path / "instance"
    instance_root.mkdir()
    guard_source = instance_root / "DicePP-UpdateGuard.exe"
    guard_source.write_bytes(b"guard-binary")
    return WindowsVelopackUpgradeAdapter(
        layout=InstanceLayout.from_root(instance_root),
        guard_command=[str(guard_source)],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {},
        guard_runtime_root=tmp_path / "external-guard",
    )


def _seed_guard_cache(
    adapter: WindowsVelopackUpgradeAdapter, *digests: str
) -> None:
    for digest in digests:
        version_dir = adapter.guard_runtime_dir / digest
        version_dir.mkdir(parents=True)
        (version_dir / "DicePP-UpdateGuard.exe").write_bytes(b"guard")


def _prune_coordinator(platform_adapter, store) -> SimpleNamespace:
    """Minimal coordinator stand-in wiring the real _journal write-back."""
    coordinator = SimpleNamespace(
        platform_adapter=platform_adapter, store=store
    )
    coordinator._journal = MethodType(UpgradeCoordinator._journal, coordinator)
    return coordinator


def test_windows_guard_cache_prune_keeps_current_digest(tmp_path: Path):
    adapter = _guard_cache_adapter(tmp_path)
    _seed_guard_cache(adapter, "a" * 64, "b" * 64, "c" * 64)

    removed = adapter.prune_external_guard_cache("b" * 64)

    assert sorted(removed) == ["a" * 64, "c" * 64]
    assert [p.name for p in adapter.guard_runtime_dir.iterdir()] == ["b" * 64]
    kept = adapter.guard_runtime_dir / ("b" * 64) / "DicePP-UpdateGuard.exe"
    assert kept.read_bytes() == b"guard"


def test_windows_guard_cache_prune_missing_dir_is_noop(tmp_path: Path):
    adapter = _guard_cache_adapter(tmp_path)

    assert adapter.prune_external_guard_cache("a" * 64) == []
    assert not adapter.guard_runtime_dir.exists()


@pytest.mark.parametrize("keep_digest", ["", "not-hex", "a" * 63, "A" * 64])
def test_windows_guard_cache_prune_rejects_invalid_keep_digest(
    tmp_path: Path, keep_digest: str
):
    adapter = _guard_cache_adapter(tmp_path)
    _seed_guard_cache(adapter, "a" * 64)

    with pytest.raises(ValueError, match="keep digest"):
        adapter.prune_external_guard_cache(keep_digest)

    assert (adapter.guard_runtime_dir / ("a" * 64)).is_dir()


def test_windows_guard_cache_prune_unlinks_symlink_without_following(
    tmp_path: Path,
):
    adapter = _guard_cache_adapter(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("safe", encoding="utf-8")
    _seed_guard_cache(adapter, "a" * 64)
    link = adapter.guard_runtime_dir / ("b" * 64)
    link.symlink_to(outside, target_is_directory=True)

    removed = adapter.prune_external_guard_cache("a" * 64)

    assert removed == ["b" * 64]
    assert not link.is_symlink()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "safe"
    assert (adapter.guard_runtime_dir / ("a" * 64)).is_dir()


def test_guard_cache_prune_waits_for_recoverable_journals(tmp_path: Path):
    adapter = _guard_cache_adapter(tmp_path)
    keep = hashlib.sha256(b"guard-binary").hexdigest()
    _seed_guard_cache(adapter, "a" * 64, keep)
    layout = InstanceLayout.from_root(tmp_path / "instance")
    store = ManagerOperationStore(layout.manager_db)
    coordinator = _prune_coordinator(adapter, store)
    operation = ManagerOperation.create_system("upgrade.install")
    store.save(operation)
    detail = {"transaction_id": "d" * 32}

    store.write_journal(
        "d" * 32,
        kind="upgrade",
        phase="healthy",
        status="running",
        detail=detail,
    )
    skipped = UpgradeCoordinator._prune_external_guard_cache(
        coordinator, operation, detail
    )
    assert skipped == {"guard_cache_prune_skipped": "recoverable_journals"}
    assert (adapter.guard_runtime_dir / ("a" * 64)).is_dir()

    store.write_journal(
        "d" * 32,
        kind="upgrade",
        phase="committed",
        status="committed",
        detail=detail,
    )
    pruned = UpgradeCoordinator._prune_external_guard_cache(
        coordinator, operation, detail
    )
    assert pruned == {"guard_cache_pruned": ["a" * 64]}
    assert [p.name for p in adapter.guard_runtime_dir.iterdir()] == [keep]
    journal = store.get_journal("d" * 32)
    assert journal["phase"] == "committed"
    assert journal["status"] == "committed"
    assert journal["detail"]["guard_cache_pruned"] == ["a" * 64]


def test_guard_cache_prune_failure_degrades_to_warning(tmp_path: Path):
    class FailingAdapter:
        def stable_guard_digest(self):
            raise OSError("locked")

        def prune_external_guard_cache(self, keep_digest):
            raise AssertionError("must not run after digest failure")

    layout = InstanceLayout.from_root(tmp_path / "instance")
    store = ManagerOperationStore(layout.manager_db)
    coordinator = _prune_coordinator(FailingAdapter(), store)
    operation = ManagerOperation.create_system("upgrade.install")
    store.save(operation)

    result = UpgradeCoordinator._prune_external_guard_cache(
        coordinator, operation, {"transaction_id": "e" * 32}
    )

    assert result == {"guard_cache_prune_error": "locked"}


def test_guard_cache_prune_prune_error_degrades_to_warning(tmp_path: Path):
    class FailingPruneAdapter:
        def stable_guard_digest(self):
            return "a" * 64

        def prune_external_guard_cache(self, keep_digest):
            raise OSError("access denied")

    layout = InstanceLayout.from_root(tmp_path / "instance")
    store = ManagerOperationStore(layout.manager_db)
    coordinator = _prune_coordinator(FailingPruneAdapter(), store)
    operation = ManagerOperation.create_system("upgrade.install")
    store.save(operation)

    result = UpgradeCoordinator._prune_external_guard_cache(
        coordinator, operation, {"transaction_id": "f" * 32}
    )

    assert result == {"guard_cache_prune_error": "access denied"}
    journal = store.get_journal("f" * 32)
    assert journal["detail"]["guard_cache_prune_error"] == "access denied"


def test_guard_cache_prune_never_raises_on_store_failures(tmp_path: Path):
    class ExplodingStore:
        def list_recoverable_journals(self):
            raise sqlite3.OperationalError("database is locked")

        def write_journal(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    adapter = _guard_cache_adapter(tmp_path)
    coordinator = _prune_coordinator(adapter, ExplodingStore())
    operation = SimpleNamespace(operation_id="op-1")

    result = UpgradeCoordinator._prune_external_guard_cache(
        coordinator, operation, {"transaction_id": "g" * 32}
    )

    assert result == {"guard_cache_prune_error": "database is locked"}


def test_guard_cache_prune_journal_writeback_failure_is_swallowed(
    tmp_path: Path,
):
    adapter = _guard_cache_adapter(tmp_path)
    keep = hashlib.sha256(b"guard-binary").hexdigest()
    _seed_guard_cache(adapter, "a" * 64, keep)

    class ReadonlyStore:
        def list_recoverable_journals(self):
            return []

        def write_journal(self, *args, **kwargs):
            raise sqlite3.OperationalError("readonly database")

    coordinator = _prune_coordinator(adapter, ReadonlyStore())
    operation = SimpleNamespace(operation_id="op-1")

    result = UpgradeCoordinator._prune_external_guard_cache(
        coordinator, operation, {"transaction_id": "h" * 32}
    )

    assert result == {"guard_cache_pruned": ["a" * 64]}
    assert [p.name for p in adapter.guard_runtime_dir.iterdir()] == [keep]
