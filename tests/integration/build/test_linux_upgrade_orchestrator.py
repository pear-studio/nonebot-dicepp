"""Integration tests for the Linux upgrade orchestrator — may use filesystem I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
        "data/local_images/sentinel.bin",
    }


def test_prepare_compose_bootstraps_full_instance(tmp_path: Path) -> None:
    """Instance layout, config, release seeds, and API client are all created."""
    orch = _make_orchestrator(tmp_path)
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

    # Global config pins the prerelease channel and disables discovery.
    global_config = json.loads(
        (instance / "config" / "global.json").read_text(encoding="utf-8")
    )
    assert global_config == {
        "update": {"channel": "prerelease", "discovery_enabled": False}
    }

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
