from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import stat
import subprocess
import zipfile
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

import dicepp_manager.upgrade as manager_upgrade
import dicepp_manager._path_security as path_security
from dicepp_data import DATA_CATALOG, InstanceLayout
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from dicepp_manager.docker_handoff import ContainerIdentity
from dicepp_manager.docker_runtime import DockerRuntimeError
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
from tests.support.handoff_fixtures import request_payload

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


_BOT_IMAGE_ID = "sha256:" + "8" * 64
_DASHBOARD_IMAGE_ID = "sha256:" + "9" * 64
_BOT_REFERENCE = "ghcr.io/pear-studio/nonebot-dicepp:v3.0.0rc20"
_DASHBOARD_REFERENCE = "ghcr.io/pear-studio/dicepp-dashboard:v3.0.0rc20"
_BOT_ALIAS = "ghcr.io/pear-studio/nonebot-dicepp:dicepp-current"
_DASHBOARD_ALIAS = "ghcr.io/pear-studio/dicepp-dashboard:dicepp-current"


class DockerExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.tags = {
            _BOT_ALIAS: _BOT_IMAGE_ID,
            _DASHBOARD_ALIAS: _DASHBOARD_IMAGE_ID,
        }

    async def capture_images(self, image_records):
        self.calls.append(
            ("capture", tuple(item["image_id"] for item in image_records))
        )
        return {
            "project": "dicepp",
            "containers": {
                "bot": {
                    "container_id": "1" * 64,
                    "name": "dicepp",
                    "image_id": _BOT_IMAGE_ID,
                    "image_reference": _BOT_REFERENCE,
                    "running": True,
                    "restart_policy": "unless-stopped",
                    "labels": {"com.docker.compose.project": "dicepp"},
                    "config": {
                        "Labels": {
                            "io.dicepp.runtime-unit": "dicepp-runtime",
                        }
                    },
                },
                "dashboard": {
                    "container_id": "2" * 64,
                    "name": "dicepp-dashboard",
                    "image_id": _DASHBOARD_IMAGE_ID,
                    "image_reference": _DASHBOARD_REFERENCE,
                    "running": False,
                    "restart_policy": "unless-stopped",
                    "labels": {"com.docker.compose.project": "dicepp"},
                },
            },
        }

    async def capture_manager(self, project):
        self.calls.append(("capture_manager", project))
        return {
            "container_id": "a" * 64,
            "name": "dicepp-manager",
            "image_id": _DASHBOARD_IMAGE_ID,
            "image_reference": _DASHBOARD_REFERENCE,
            "running": True,
            "restart_policy": "unless-stopped",
            "labels": {"com.docker.compose.project": project},
        }

    async def inspect_tag(self, reference):
        self.calls.append(("inspect_tag", reference))
        return {"Id": self.tags[reference]}

    async def inspect_tag_optional(self, reference):
        self.calls.append(("inspect_tag_optional", reference))
        image_id = self.tags.get(reference)
        return None if image_id is None else {"Id": image_id}

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

    async def restore_images(self, previous, *, transaction_id=None):
        self.calls.append(("restore", previous, transaction_id))
        return {"status": "restored"}


class DockerHandoff:
    def __init__(self, executor: DockerExecutor | None = None) -> None:
        self.calls: list[tuple] = []
        self.executor = executor
        self.images: dict[str, str] = {}
        self.containers: list[str] = []
        self.labels: dict[str, dict[str, str]] = {}
        self.identities: dict[str, ContainerIdentity] = {}
        self.current_identity: ContainerIdentity | None = None

    async def create(self, name, config, *, extra_labels, restart_policy):
        self.calls.append(
            ("create", name, extra_labels, restart_policy, config)
        )
        return "f" * 64

    async def start(self, container_id):
        self.calls.append(("start", container_id))
        if container_id in self.identities:
            self.identities[container_id] = replace(
                self.identities[container_id], running=True
            )

    async def set_restart_policy(self, container_id, policy):
        self.calls.append(("set_restart_policy", container_id, policy))
        if container_id in self.identities:
            self.identities[container_id] = replace(
                self.identities[container_id], restart_policy=policy
            )

    async def tag_image(self, image_id, repo, tag):
        reference = f"{repo}:{tag}"
        self.calls.append(("tag_image", image_id, reference))
        if self.executor is not None:
            self.executor.tags[reference] = image_id

    async def list_by_label(self, key, value):
        self.calls.append(("list_by_label", key, value))
        return list(self.containers)

    async def inspect(self, container_id):
        if container_id in self.identities:
            return self.identities[container_id]
        image_id = self.images.get(container_id, container_id)
        labels = self.labels.get(container_id, {})
        return ContainerIdentity(
            container_id=container_id,
            name=f"container-{container_id[:4]}",
            image_id=image_id,
            image_reference=image_id,
            running=True,
            restart_policy="no",
            labels=labels,
        )

    async def inspect_current(self, hostname):
        self.calls.append(("inspect_current", hostname))
        if self.current_identity is None:
            raise DockerRuntimeError("current identity is unavailable")
        return self.current_identity

    @staticmethod
    def resolve_host_bind_source(
        identity, *, container_root, container_path
    ):
        del identity, container_root
        return f"/srv/dicepp/manager/recovery/{Path(container_path).name}"


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
    handoff_protocol: int | None = 1,
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
    if handoff_protocol is not None:
        manifest["linux_manager_handoff_protocol"] = handoff_protocol
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
async def test_linux_target_manager_identity_is_exactly_bound_before_takeover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = request_payload()
    target_id = "f" * 64
    handoff = DockerHandoff()
    labels = {
        "com.docker.compose.project": request["compose_project"],
        "com.docker.compose.service": "manager",
        request["labels"]["transaction"]: request["transaction_id"],
        request["labels"]["role"]: "manager",
    }
    handoff.current_identity = ContainerIdentity(
        container_id=target_id,
        name=request["manager"]["name"],
        image_id=request["target_manager_image_id"],
        image_reference="ghcr.io/pear-studio/dicepp-dashboard:v3.0.0rc21",
        running=True,
        restart_policy="no",
        labels=labels,
        hostname=target_id[:12],
    )
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path),
        executor=DockerExecutor(),
        handoff_executor=handoff,
    )
    monkeypatch.setattr(manager_upgrade.socket, "gethostname", lambda: target_id[:12])

    await adapter.verify_target_manager_identity(request)

    handoff.current_identity = ContainerIdentity(
        container_id=target_id,
        name=request["manager"]["name"],
        image_id=request["target_manager_image_id"],
        image_reference="ghcr.io/pear-studio/dicepp-dashboard:v3.0.0rc21",
        running=True,
        restart_policy=request["restart_policies"]["manager"],
        labels=labels,
        hostname=target_id[:12],
    )
    with pytest.raises(UpgradeCompatibilityError) as exc_info:
        await adapter.verify_target_manager_identity(request)
    assert exc_info.value.code == "target_manager_identity_invalid"

    await adapter.verify_target_manager_identity(
        request, allow_restored_restart_policy=True
    )

    handoff.current_identity = ContainerIdentity(
        container_id=target_id,
        name=request["manager"]["name"],
        image_id=request["manager"]["image_id"],
        image_reference="ghcr.io/pear-studio/dicepp-dashboard:v3.0.0rc20",
        running=True,
        restart_policy=request["restart_policies"]["manager"],
        labels=labels,
        hostname=target_id[:12],
    )
    with pytest.raises(UpgradeCompatibilityError) as exc_info:
        await adapter.verify_target_manager_identity(
            request, allow_restored_restart_policy=True
        )
    assert exc_info.value.code == "target_manager_identity_invalid"


@pytest.mark.asyncio
async def test_linux_capture_bootstraps_only_fixed_current_aliases(
    tmp_path: Path,
) -> None:
    package, current_compose = _linux_package(tmp_path)
    executor = DockerExecutor()
    executor.tags.clear()
    handoff = DockerHandoff(executor)
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path),
        executor=executor,
        handoff_executor=handoff,
        current_compose=current_compose,
    )

    current = await adapter.capture_current(package)

    assert current["current_aliases"] == {
        "bot": {"name": _BOT_ALIAS, "image_id": _BOT_IMAGE_ID},
        "dashboard_manager": {
            "name": _DASHBOARD_ALIAS,
            "image_id": _DASHBOARD_IMAGE_ID,
        },
    }
    assert executor.tags == {
        _BOT_ALIAS: _BOT_IMAGE_ID,
        _DASHBOARD_ALIAS: _DASHBOARD_IMAGE_ID,
    }
    assert _BOT_REFERENCE not in executor.tags
    assert _DASHBOARD_REFERENCE not in executor.tags
    assert [call for call in handoff.calls if call[0] == "tag_image"] == [
        ("tag_image", _BOT_IMAGE_ID, _BOT_ALIAS),
        ("tag_image", _DASHBOARD_IMAGE_ID, _DASHBOARD_ALIAS),
    ]


@pytest.mark.asyncio
async def test_linux_capture_refuses_drift_before_creating_missing_alias(
    tmp_path: Path,
) -> None:
    package, current_compose = _linux_package(tmp_path)
    executor = DockerExecutor()
    executor.tags = {_BOT_ALIAS: "sha256:" + "7" * 64}
    handoff = DockerHandoff(executor)
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path),
        executor=executor,
        handoff_executor=handoff,
        current_compose=current_compose,
    )

    with pytest.raises(UpgradeCompatibilityError) as exc_info:
        await adapter.capture_current(package)

    assert exc_info.value.code == "current_alias_drifted"
    assert [call for call in handoff.calls if call[0] == "tag_image"] == []
    assert _DASHBOARD_ALIAS not in executor.tags


@pytest.mark.asyncio
async def test_linux_capture_retry_converges_after_second_alias_write_fails(
    tmp_path: Path,
) -> None:
    class FailingSecondAliasHandoff(DockerHandoff):
        fail_dashboard_once = True

        async def tag_image(self, image_id, repo, tag):
            reference = f"{repo}:{tag}"
            if reference == _DASHBOARD_ALIAS and self.fail_dashboard_once:
                self.fail_dashboard_once = False
                raise DockerRuntimeError("injected dashboard alias failure")
            await super().tag_image(image_id, repo, tag)

    package, current_compose = _linux_package(tmp_path)
    executor = DockerExecutor()
    executor.tags.clear()
    handoff = FailingSecondAliasHandoff(executor)
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path),
        executor=executor,
        handoff_executor=handoff,
        current_compose=current_compose,
    )

    with pytest.raises(DockerRuntimeError, match="dashboard alias failure"):
        await adapter.capture_current(package)

    assert executor.tags == {_BOT_ALIAS: _BOT_IMAGE_ID}
    current = await adapter.capture_current(package)
    assert current["current_aliases"]["dashboard_manager"] == {
        "name": _DASHBOARD_ALIAS,
        "image_id": _DASHBOARD_IMAGE_ID,
    }
    assert executor.tags == {
        _BOT_ALIAS: _BOT_IMAGE_ID,
        _DASHBOARD_ALIAS: _DASHBOARD_IMAGE_ID,
    }


@pytest.mark.asyncio
async def test_linux_upgrade_loads_release_images_without_registry_pull(
    tmp_path: Path,
):
    package, current_compose = _linux_package(tmp_path)
    layout = InstanceLayout.from_root(tmp_path / "instance")
    # A real Dashboard DB must exist so prepare_recovery can snapshot it.
    layout.dashboard_data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.dashboard_db) as connection:
        connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO facts (id) VALUES (1)")
    executor = DockerExecutor()
    handoff = DockerHandoff()
    adapter = LinuxBundleUpgradeAdapter(
        layout=layout,
        executor=executor,
        handoff_executor=handoff,
        current_compose=current_compose,
    )

    preflight = await adapter.preflight(package)
    current = await adapter.capture_current(package)
    # Match the coordinator's real ordering: Bot was recorded in the generic
    # original list, then stopped before capture; Dashboard remains available.
    current["containers"]["bot"]["running"] = False
    current["containers"]["dashboard"]["running"] = True
    assert current["source_version"] and current["source_version"] != "unknown"
    assert current["current_aliases"] == {
        "bot": {"name": _BOT_ALIAS, "image_id": _BOT_IMAGE_ID},
        "dashboard_manager": {
            "name": _DASHBOARD_ALIAS,
            "image_id": _DASHBOARD_IMAGE_ID,
        },
    }
    staged = await adapter.stage(package, "a" * 32)
    staged["current"] = current
    staged["operation_id"] = "b" * 32
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id="a" * 32,
        source_version="3.0.0rc20",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    switch = await adapter.switch(
        package,
        current=current,
        staged=staged,
        transaction_id="a" * 32,
    )

    assert preflight["network"] == "not_used"
    assert [call[0] for call in executor.calls] == [
        "capture",
        "capture_manager",
        "inspect_tag_optional",
        "inspect_tag_optional",
        "load",
        "resolve",
    ]
    # restart=no is set on manager/bot/dashboard before the Updater exists
    assert [call[0] for call in handoff.calls] == [
        "set_restart_policy",
        "set_restart_policy",
        "set_restart_policy",
        "create",
        "start",
    ]
    assert {
        call[2] for call in handoff.calls if call[0] == "set_restart_policy"
    } == {"no"}
    create_call = next(call for call in handoff.calls if call[0] == "create")
    host_config = create_call[4]["HostConfig"]
    assert "Binds" not in host_config
    assert host_config["Mounts"] == [
        {
            "Type": "bind",
            "Source": "/srv/dicepp/manager/recovery/" + "a" * 32,
            "Target": "/transaction",
            "ReadOnly": False,
        },
        {
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "Target": "/var/run/docker.sock",
            "ReadOnly": False,
        },
    ]
    assert switch["handoff_required"] is True
    assert all("pull" not in str(call).lower() for call in executor.calls)
    stage_dir = Path(staged["stage_dir"])
    assert stage_dir.is_dir()

    # The recovery material carries a real Dashboard DB snapshot, not the
    # placeholder digest.
    snapshot = staged["dashboard_db"]
    tx_snapshot = layout.manager_recovery_dir / ("a" * 32) / "dashboard.db"
    assert snapshot["sha256"] != "0" * 64
    assert tx_snapshot.is_file()
    assert staged["request"]["dashboard_db"]["sha256"] == snapshot["sha256"]
    assert staged["request"]["dashboard_db"]["path"] == snapshot["path"]
    assert staged["request"]["original_running"] == {
        "bot": True,
        "dashboard": True,
    }
    with sqlite3.connect(tx_snapshot) as connection:
        assert connection.execute("SELECT id FROM facts").fetchone() == (1,)

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
async def test_linux_runtime_rejects_manager_change_without_handoff_protocol(
    tmp_path: Path,
):
    """Manager scope without a supported handoff protocol fails closed."""
    package, current = _linux_package(
        tmp_path,
        change_scope=["runtime", "manager"],
        handoff_protocol=None,
    )
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )

    with pytest.raises(UpgradeCompatibilityError, match="fields mismatch"):
        await adapter.preflight(package)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_linux_runtime_rejects_manager_change_with_unsupported_protocol(
    tmp_path: Path,
):
    """An unsupported handoff protocol version for a Manager change fails closed."""
    package, current = _linux_package(
        tmp_path,
        change_scope=["runtime", "manager"],
        handoff_protocol=99,
    )
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        current_compose=current,
    )

    with pytest.raises(UpgradeCompatibilityError, match="handoff"):
        await adapter.preflight(package)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_linux_runtime_allows_manager_change_with_handoff_protocol(
    tmp_path: Path,
):
    """Manager scope with the supported handoff protocol is automatic."""
    package, current = _linux_package(
        tmp_path,
        change_scope=["runtime", "manager"],
    )
    executor = DockerExecutor()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        handoff_executor=DockerExecutor(),
        current_compose=current,
    )

    preflight = await adapter.preflight(package)

    assert preflight["status"] == "ok"


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
async def test_linux_prepare_recovery_fails_closed_without_dashboard_db(
    tmp_path: Path,
):
    """No Dashboard DB means no recovery contract: the transaction must not start."""
    package, current_compose = _linux_package(tmp_path)
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=DockerExecutor(),
        handoff_executor=DockerHandoff(),
        current_compose=current_compose,
    )

    current = await adapter.capture_current(package)
    staged = {
        "current": current,
        "images": {
            "bot": {"image_id": _BOT_IMAGE_ID},
            "dashboard": {"image_id": _DASHBOARD_IMAGE_ID},
        },
    }

    with pytest.raises(
        UpgradeCompatibilityError, match="Dashboard database snapshot"
    ):
        await adapter.prepare_recovery(
            staged,
            transaction_id="a" * 32,
            source_version="3.0.0rc20",
            target_version="3.1.0",
            pre_upgrade_filename="pre-upgrade.zip",
            original_running=[],
        )


@pytest.mark.asyncio
async def test_linux_verify_target_container_images_fails_on_image_mismatch(
    tmp_path: Path,
):
    package, current_compose = _linux_package(tmp_path)
    executor = DockerExecutor()
    handoff = DockerHandoff()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        handoff_executor=handoff,
        current_compose=current_compose,
    )
    transaction_id = "a" * 32
    request = {
        "transaction_id": transaction_id,
        "labels": {
            "transaction": "io.dicepp.upgrade-transaction",
            "role": "io.dicepp.upgrade-role",
        },
        "target_images": {
            "bot": _BOT_IMAGE_ID,
            "dashboard": _DASHBOARD_IMAGE_ID,
        },
        "target_manager_image_id": _DASHBOARD_IMAGE_ID,
    }
    # The running targets carry this transaction's labels: the target Manager
    # (role=manager), the target Bot/Dashboard (role=runtime, compose
    # services preserved) and the Updater itself (role=updater, skipped).
    manager_id, bot_id, dashboard_id, updater_id = (
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
    )
    handoff.containers = [manager_id, bot_id, dashboard_id, updater_id]
    handoff.labels = {
        manager_id: {"io.dicepp.upgrade-role": "manager"},
        bot_id: {
            "io.dicepp.upgrade-role": "runtime",
            "com.docker.compose.service": "bot",
        },
        dashboard_id: {
            "io.dicepp.upgrade-role": "runtime",
            "com.docker.compose.service": "dashboard",
        },
        updater_id: {"io.dicepp.upgrade-role": "updater"},
    }
    handoff.images = {
        manager_id: _DASHBOARD_IMAGE_ID,
        bot_id: _BOT_IMAGE_ID,
        dashboard_id: _DASHBOARD_IMAGE_ID,
    }

    # All three running target containers match the request's target images.
    await adapter.verify_target_container_images(request)

    # A running container on a different image fails closed.
    handoff.images[bot_id] = "sha256:" + ("7" * 64)
    with pytest.raises(
        UpgradeCompatibilityError, match="does not match the staged target"
    ):
        await adapter.verify_target_container_images(request)

    # A missing target container also fails closed.
    handoff.images[bot_id] = _BOT_IMAGE_ID
    handoff.containers = [manager_id, dashboard_id, updater_id]
    with pytest.raises(
        UpgradeCompatibilityError, match="incomplete"
    ):
        await adapter.verify_target_container_images(request)


def _target_runtime_identity(
    request: dict[str, object],
    role: str,
    container_id: str,
    *,
    running: bool,
    restart_policy: str,
) -> ContainerIdentity:
    names = {"bot": "dicepp", "dashboard": "dicepp-dashboard"}
    labels = request["labels"]
    target_images = request["target_images"]
    assert isinstance(labels, dict)
    assert isinstance(target_images, dict)
    transaction_id = str(request["transaction_id"])
    image_id = str(target_images[role])
    return ContainerIdentity(
        container_id=container_id,
        name=names[role],
        image_id=image_id,
        image_reference=image_id,
        running=running,
        restart_policy=restart_policy,
        labels={
            "com.docker.compose.project": str(request["compose_project"]),
            "com.docker.compose.service": role,
            str(labels["transaction"]): transaction_id,
            str(labels["role"]): "runtime",
        },
    )


@pytest.mark.asyncio
async def test_linux_commit_converges_runtime_state_from_bound_request(
    tmp_path: Path,
):
    package, current_compose = _linux_package(tmp_path)
    handoff = DockerHandoff()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=DockerExecutor(),
        handoff_executor=handoff,
        current_compose=current_compose,
    )
    request = request_payload()
    bot_id, dashboard_id = "6" * 64, "7" * 64
    handoff.containers = [bot_id, dashboard_id]
    handoff.identities = {
        bot_id: _target_runtime_identity(
            request, "bot", bot_id, running=False, restart_policy="no"
        ),
        dashboard_id: _target_runtime_identity(
            request, "dashboard", dashboard_id, running=False, restart_policy="no"
        ),
    }

    await adapter.restore_runtime_policies(
        {
            "transaction_id": request["transaction_id"],
            "platform_staged": {"request": request},
        }
    )

    assert ("start", bot_id) in handoff.calls
    assert ("start", dashboard_id) not in handoff.calls
    assert handoff.identities[bot_id].running is True
    assert handoff.identities[dashboard_id].running is False
    assert handoff.identities[bot_id].restart_policy == "unless-stopped"
    assert handoff.identities[dashboard_id].restart_policy == "unless-stopped"


@pytest.mark.asyncio
async def test_linux_generic_runtime_state_allows_commit_convergence(
    tmp_path: Path,
):
    executor = DockerExecutor()
    handoff = DockerHandoff()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=executor,
        handoff_executor=handoff,
    )
    current = await executor.capture_images([])
    # Real coordinator order: the generic Bot RuntimeUnit was recorded as
    # running and then quiesced before Docker current-state capture.  The
    # Dashboard is not that RuntimeUnit and remains available at capture.
    current["containers"]["bot"]["running"] = False
    current["containers"]["dashboard"]["running"] = True
    captured_state = adapter._captured_original_running(
        current, ["dicepp-runtime"]
    )
    assert captured_state == {"bot": True, "dashboard": True}

    request = request_payload(original_running=captured_state)
    assert request["original_running"] == {"bot": True, "dashboard": True}
    bot_id, dashboard_id = "6" * 64, "7" * 64
    handoff.containers = [bot_id, dashboard_id]
    handoff.identities = {
        # The target takeover restarts the generic Bot RuntimeUnit before the
        # committed convergence runs.  Correct request binding must accept it.
        bot_id: _target_runtime_identity(
            request,
            "bot",
            bot_id,
            running=True,
            restart_policy="no",
        ),
        dashboard_id: _target_runtime_identity(
            request,
            "dashboard",
            dashboard_id,
            running=False,
            restart_policy="no",
        ),
    }

    await adapter.restore_runtime_policies(
        {
            "transaction_id": request["transaction_id"],
            "platform_staged": {"request": request},
        }
    )

    assert ("start", bot_id) not in handoff.calls
    assert ("start", dashboard_id) in handoff.calls
    assert handoff.identities[bot_id].running is True
    assert handoff.identities[dashboard_id].running is True


def test_linux_original_runtime_state_requires_bound_runtime_unit_label():
    current = {
        "containers": {
            "bot": {"config": {"Labels": {}}, "running": False},
            "dashboard": {"running": True},
        }
    }

    with pytest.raises(UpgradeCompatibilityError) as exc_info:
        LinuxBundleUpgradeAdapter._captured_original_running(
            current, ["dicepp-runtime"]
        )

    assert exc_info.value.code == "original_runtime_state_invalid"


@pytest.mark.asyncio
async def test_linux_commit_runtime_convergence_is_idempotent(
    tmp_path: Path,
):
    package, current_compose = _linux_package(tmp_path)
    handoff = DockerHandoff()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=DockerExecutor(),
        handoff_executor=handoff,
        current_compose=current_compose,
    )
    request = request_payload(
        original_running={"bot": True, "dashboard": True}
    )
    bot_id, dashboard_id = "6" * 64, "7" * 64
    handoff.containers = [bot_id, dashboard_id]
    handoff.identities = {
        bot_id: _target_runtime_identity(
            request,
            "bot",
            bot_id,
            running=True,
            restart_policy="unless-stopped",
        ),
        dashboard_id: _target_runtime_identity(
            request,
            "dashboard",
            dashboard_id,
            running=True,
            restart_policy="unless-stopped",
        ),
    }

    await adapter.restore_runtime_policies(
        {
            "transaction_id": request["transaction_id"],
            "platform_staged": {"request": request},
        }
    )

    assert not any(
        call[0] in {"start", "set_restart_policy"} for call in handoff.calls
    )


@pytest.mark.asyncio
async def test_linux_commit_runtime_convergence_validates_all_before_mutation(
    tmp_path: Path,
):
    package, current_compose = _linux_package(tmp_path)
    handoff = DockerHandoff()
    adapter = LinuxBundleUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path / "instance"),
        executor=DockerExecutor(),
        handoff_executor=handoff,
        current_compose=current_compose,
    )
    request = request_payload()
    bot_id, dashboard_id = "6" * 64, "7" * 64
    bot = _target_runtime_identity(
        request, "bot", bot_id, running=False, restart_policy="no"
    )
    dashboard = _target_runtime_identity(
        request, "dashboard", dashboard_id, running=False, restart_policy="no"
    )
    handoff.containers = [bot_id, dashboard_id]
    handoff.identities = {
        bot_id: bot,
        dashboard_id: replace(dashboard, image_id="sha256:" + "f" * 64),
    }

    with pytest.raises(
        UpgradeCompatibilityError, match="does not match the handoff request"
    ):
        await adapter.restore_runtime_policies(
            {
                "transaction_id": request["transaction_id"],
                "platform_staged": {"request": request},
            }
        )

    assert not any(
        call[0] in {"start", "set_restart_policy"} for call in handoff.calls
    )
