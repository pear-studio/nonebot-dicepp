"""Integration tests for the Linux upgrade orchestrator — may use filesystem I/O."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
import yaml

from scripts.build.linux_upgrade_orchestrator import (
    _LinuxUpgradeOrchestrator,
    _OrchestratorUnavailable,
)
from tests.support.linux_bundle import (
    build_bundle_bytes_without_manifest,
    write_linux_bundle,
)

_SHIPPED_COMPOSE = """\
services:
  manager:
    image: ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}
    volumes: ["./config:/app/config"]
  bot:
    image: ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}
    volumes: ["./config:/app/config"]
"""


def _make_orchestrator(tmp_path: Path, *, work: str = "work") -> _LinuxUpgradeOrchestrator:
    source_bundle = write_linux_bundle(
        tmp_path / "source.zip",
        version="3.0.0rc19",
        compose=_SHIPPED_COMPOSE,
    )
    target_bundle = write_linux_bundle(
        tmp_path / "target.zip",
        version="3.1.0",
        compose=_SHIPPED_COMPOSE,
    )
    return _LinuxUpgradeOrchestrator(
        source_bundle=source_bundle,
        source_version="3.0.0rc19",
        target_bundle=target_bundle,
        target_version="3.1.0",
        work_dir=tmp_path / work,
    )


def test_prepare_compose_writes_byte_identical_compose_file(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path)
    orch._prepare_compose()

    assert orch._compose_file is not None
    assert orch._compose_file.is_file()
    # Byte-identical passthrough: the shipped compose is written verbatim.
    assert orch._compose_file.read_bytes() == _SHIPPED_COMPOSE.encode("utf-8")
    content = orch._compose_file.read_text(encoding="utf-8")
    assert "manager:" in content
    assert "bot:" in content
    assert orch._instance_dir is not None
    assert orch._instance_dir.is_dir()
    assert orch._compose_override is not None
    override = yaml.safe_load(orch._compose_override.read_text(encoding="utf-8"))
    volume = override["services"]["manager"]["volumes"][0]
    assert volume["type"] == "bind"
    assert volume["source"].endswith(".sock")
    assert volume["target"] == "/var/run/docker.sock"


def test_prepare_compose_uses_source_topology_and_catalog_sentinels(
    tmp_path: Path,
) -> None:
    source = write_linux_bundle(
        tmp_path / "source-distinct.zip",
        version="3.0.0rc19",
        compose=_SHIPPED_COMPOSE,
    )
    target = write_linux_bundle(
        tmp_path / "target-distinct.zip",
        version="3.1.0",
        compose="services:\n  candidate-only:\n    image: target:latest\n",
    )
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=source,
        source_version="3.0.0rc19",
        target_bundle=target,
        target_version="3.1.0",
        work_dir=tmp_path / "distinct-work",
    )

    orch._prepare_compose()

    assert orch._compose_file is not None
    assert orch._compose_file.read_text(encoding="utf-8") == _SHIPPED_COMPOSE
    assert orch._instance_dir is not None
    instance = orch._instance_dir
    assert (instance / "config" / "user.json").is_file()
    assert (instance / "data" / "local_images" / "sentinel.bin").is_file()
    assert set(orch._sentinel_digests) == {
        "config/user.json",
        "config/global.json",
        "data/local_images/sentinel.bin",
    }


def test_prepare_compose_bootstraps_full_instance(tmp_path: Path) -> None:
    """Instance layout, config, release seeds, and API client are all created."""
    orch = _make_orchestrator(tmp_path)
    legacy_global = b'{"chat_interval":99,"legacy":"preserve"}\n'
    legacy_path = tmp_path / "work" / "instance" / "config" / "global.json"
    orch._prepare_compose()

    instance = orch._instance_dir
    assert instance is not None
    assert instance.is_dir()
    for relative in (
        "config",
        "data",
        "content",
        "dashboard/data",
        "manager/state",
        "manager/packages/3.1.0",
    ):
        assert (instance / relative).is_dir(), relative

    # User config pins the prerelease channel and disables discovery.
    user_config = json.loads(
        (instance / "config" / "user.json").read_text(encoding="utf-8")
    )
    assert user_config == {
        "chat_interval": 31,
        "update": {"channel": "prerelease", "discovery_enabled": False}
    }
    assert legacy_path.read_bytes() == legacy_global

    for bundle in (orch._source_bundle, orch._target_bundle):
        with zipfile.ZipFile(bundle) as archive:
            assert "config/global.json" not in archive.namelist()

    from plugins.DicePP.core.config.loader import ConfigLoader

    bot_path = instance / "config" / "bots" / "10001.json"
    bot_path.parent.mkdir(parents=True)
    bot_path.write_text('{"nickname":"bot-layer"}', encoding="utf-8")
    loaded = ConfigLoader(str(instance / "config"), "10001").load()
    assert loaded.chat_interval == 31
    assert loaded.nickname == "bot-layer"
    assert legacy_path.read_bytes() == legacy_global

    # Release state and verified metadata are seeded for the target version.
    release_state = json.loads(
        (instance / "manager" / "state" / "release-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert release_state["format_version"] == 1
    assert release_state["available"]["version"] == "3.1.0"
    assert release_state["available"]["channel"] == "prerelease"
    assert release_state["available"]["compatibility"]["automatic_upgrade"] is True

    packages_dir = instance / "manager" / "packages" / "3.1.0"
    verified = json.loads(
        (packages_dir / "verified-release.json").read_text(encoding="utf-8")
    )
    assert verified["version"] == "3.1.0"
    assert verified["verified_path"] == "target.zip"

    # The target bundle was copied and the copy is byte-identical.
    seeded = packages_dir / "target.zip"
    assert seeded.is_file()
    assert seeded.read_bytes() == orch._target_bundle.read_bytes()
    assert orch._seeded_bundle_path == seeded

    # Image identities were read from both bundle manifests.
    assert orch._source_image_ids["bot"].startswith("sha256:")
    assert orch._target_image_ids["bot"].startswith("sha256:")
    assert set(orch._source_image_ids) == {"bot", "dashboard"}
    assert set(orch._target_image_ids) == {"bot", "dashboard"}

    # The Manager API client is ready (token is read after Manager start).
    assert orch._api is not None
    assert orch._api.base_url == "http://127.0.0.1:4091"
    assert orch._api.token is None


def test_mutation_detection_uses_only_mutable_sentinels(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path)
    orch._prepare_compose()
    instance = orch._instance_dir
    assert instance is not None
    user = instance / "config" / "user.json"
    image = instance / "data" / "local_images" / "sentinel.bin"
    legacy_global = instance / "config" / "global.json"
    original_user = user.read_bytes()
    original_image = image.read_bytes()
    original_global = legacy_global.read_bytes()

    orch._mutate_sentinels_for_failure()

    assert orch._sentinels_differ_from_source() is True
    assert legacy_global.read_bytes() == original_global

    image.write_bytes(original_image)
    assert orch._sentinels_differ_from_source() is False

    legacy_global.write_bytes(b'{"changed_global_cannot_substitute":true}\n')
    assert user.read_bytes() != original_user
    assert image.read_bytes() == original_image
    assert orch._sentinels_differ_from_source() is False


def test_prepare_compose_installs_ordered_target_crash_harness(
    tmp_path: Path,
) -> None:
    orch = _make_orchestrator(tmp_path)
    orch._handoff_mode = "target_crash"

    orch._prepare_compose()

    assert orch._harness_control_dir is not None
    wrapper = (
        orch._harness_control_dir / "manager_entrypoint.py"
    ).read_text(encoding="utf-8")
    crash_branch = wrapper[wrapper.index('if mode == "target_crash":') :]
    marker = crash_branch.index('"target-manager-observed.json"')
    assert crash_branch.index("damaged_by_target_manager") < marker
    assert crash_branch.index("damaged-by-target-manager") < marker
    assert crash_branch.index("connection.commit()") < marker
    assert orch._instance_dir is not None
    with sqlite3.connect(
        orch._instance_dir / "dashboard" / "data" / "dashboard.db"
    ) as connection:
        row = connection.execute(
            "SELECT value FROM dicepp_harness_sentinel WHERE id = 1"
        ).fetchone()
    assert row == ("source",)


def test_prepare_compose_routes_manager_api_through_dind(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str, str, dict | None]] = []

    class Sandbox:
        def manager_api_request(
            self,
            manager_name: str,
            method: str,
            path: str,
            token: str,
            body: dict | None,
        ) -> tuple[int, dict]:
            calls.append((manager_name, method, path, token, body))
            return 200, {"ok": True, "dicepp_version": "3.0.0rc19"}

    orch = _make_orchestrator(tmp_path)
    orch._daemon_sandbox = Sandbox()  # type: ignore[assignment]
    orch._prepare_compose()
    assert orch._api is not None
    orch._container_names["manager"] = "dicepp-manager"
    orch._api.token = "token"

    assert orch._api.health()["dicepp_version"] == "3.0.0rc19"
    assert calls == [
        ("dicepp-manager", "GET", "/v1/health", "token", None)
    ]


def test_prepare_compose_rejects_missing_compose_in_bundle(tmp_path: Path) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("other-file.txt", "not a compose")
    bundle = tmp_path / "target.zip"
    bundle.write_bytes(buffer.getvalue())

    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=bundle,
        target_version="3.1.0",
        work_dir=tmp_path / "work",
    )
    with pytest.raises(_OrchestratorUnavailable, match="cannot read compose"):
        orch._prepare_compose()


def test_prepare_compose_rejects_corrupt_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "target.zip"
    bundle.write_bytes(b"not a valid zip file")

    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=bundle,
        target_version="3.1.0",
        work_dir=tmp_path / "work",
    )
    with pytest.raises(_OrchestratorUnavailable, match="cannot read compose"):
        orch._prepare_compose()


def test_prepare_compose_rejects_source_bundle_without_manifest(
    tmp_path: Path,
) -> None:
    """A missing source manifest is a prerequisite failure (fail-closed)."""
    target_bundle = write_linux_bundle(
        tmp_path / "target.zip",
        version="3.1.0",
        compose=_SHIPPED_COMPOSE,
    )
    source_bundle = tmp_path / "source.zip"
    source_bundle.write_bytes(build_bundle_bytes_without_manifest())

    orch = _LinuxUpgradeOrchestrator(
        source_bundle=source_bundle,
        source_version="3.0.0rc19",
        target_bundle=target_bundle,
        target_version="3.1.0",
        work_dir=tmp_path / "work",
    )
    with pytest.raises(_OrchestratorUnavailable, match="shipped compose"):
        orch._prepare_compose()
