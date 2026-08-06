from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import stat
import subprocess
import zipfile
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

import dicepp_manager.upgrade as manager_upgrade
import dicepp_manager._path_security as path_security
from dicepp_data import DATA_CATALOG, InstanceLayout
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from dicepp_manager.models import ManagerOperation
from dicepp_manager.release import RELEASE_CONTRACT_VERSION, ReleaseManager
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.upgrade import (
    LinuxBundleUpgradeAdapter,
    LINUX_STAGE_RESERVE_BYTES,
    UpgradeCompatibilityError,
    UpgradeCoordinator,
    VerifiedUpgradePackage,
)
from tests.support.fs_utils import symlink_or_skip

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


def _bundle_bytes(
    version: str,
    nupkg_body: bytes | None = None,
) -> tuple[bytes, bytes, dict]:
    nupkg_body = nupkg_body or _nupkg_bytes(version)
    nupkg_name = f"DicePP-{version}-full.nupkg"
    inner = {
        "format_version": 1,
        "dicepp_version": version,
        "velopack_version": version,
        "channel": "stable",
        "platform": "windows",
        "arch": "amd64",
        "nupkg": {
            "filename": nupkg_name,
            "size": len(nupkg_body),
            "sha256": hashlib.sha256(nupkg_body).hexdigest(),
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(inner))
        archive.writestr(nupkg_name, nupkg_body)
    return output.getvalue(), nupkg_body, inner


def _write_full_nupkg(root: Path, version: str, name: str | None = None) -> Path:
    del name
    bundle = root / "packages" / "velopack.win-x64.zip"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_bytes(_bundle_bytes(version)[0])
    (bundle.parent / "velopack.win-x64.verified.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "dicepp_version": version,
                "channel": "stable",
                "platform": "windows",
                "arch": "amd64",
                "filename": bundle.name,
                "size": bundle.stat().st_size,
                "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return bundle


def _windows_package(tmp_path: Path, version: str = "3.1.0"):
    version_dir = tmp_path / "manager" / "packages" / version
    version_dir.mkdir(parents=True, exist_ok=True)
    path = version_dir / f"DicePP-{version}-full.nupkg"
    nupkg = _nupkg_bytes(version)
    path.write_bytes(nupkg)
    bundle_body, _payload, inner = _bundle_bytes(version, nupkg)
    bundle_path = version_dir / "velopack.win-x64.zip"
    bundle_path.write_bytes(bundle_body)
    return VerifiedUpgradePackage(
        version=version,
        platform="windows",
        arch="amd64",
        path=path,
        metadata_path=tmp_path / "verified-release.json",
        artifact={
            "platform": "windows",
            "arch": "amd64",
            "purpose": "velopack-bundle",
            "filename": bundle_path.name,
            "size": len(bundle_body),
            "sha256": hashlib.sha256(bundle_body).hexdigest(),
        },
        release={"channel": "stable"},
        bundle_path=bundle_path,
        bundle_manifest=inner,
    )


def _windows_adapter_layout(tmp_path: Path) -> InstanceLayout:
    layout = InstanceLayout.from_root(tmp_path)
    (tmp_path / "current").mkdir()
    for name in ("Update.exe", "DicePP.exe"):
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
