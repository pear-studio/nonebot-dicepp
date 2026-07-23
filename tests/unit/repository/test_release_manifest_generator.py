from __future__ import annotations

from scripts.build.generate_release_manifest import (
    artifact_record,
    build_manifest,
    velopack_channel,
    velopack_version,
)
from scripts.build.release_metadata import ReleaseMetadata


def test_generator_hashes_real_artifacts_and_emits_valid_contract(tmp_path) -> None:
    linux = tmp_path / "DicePP-v3.1.0-linux-amd64.zip"
    windows = tmp_path / "DicePP-v3.1.0-win64-Portable.zip"
    linux.write_bytes(b"linux package")
    windows.write_bytes(b"windows package")

    manifest = build_manifest(
        version="v3.1.0",
        channel="stable",
        artifacts=[
            artifact_record(f"linux:amd64:linux-bundle:{linux}"),
            artifact_record(f"windows:amd64:portable:{windows}"),
        ],
        metadata=ReleaseMetadata(
            version="3.1.0",
            data_changed=False,
            config_changed=False,
            change_scope=("runtime", "dashboard"),
            automatic_upgrade=False,
            minimum_manager_version="1.0",
        ),
    )

    assert manifest["version"] == "3.1.0"
    assert manifest["artifacts"][0]["filename"] == linux.name
    assert manifest["artifacts"][0]["size"] == len(b"linux package")
    assert len(manifest["artifacts"][0]["sha256"]) == 64
    assert manifest["catalog_version"] == 1
    assert len(manifest["catalog_digest"]) == 64
    assert manifest["automatic_upgrade"] is False
    assert manifest["fallbacks"]["linux_ghcr_images"][0].endswith(":v3.1.0")


def test_velopack_uses_semver2_and_architecture_scoped_channels() -> None:
    assert velopack_version("3.0.0rc9") == "3.0.0-rc.9"
    assert velopack_version("v3.1.0") == "3.1.0"
    assert velopack_channel("stable", "amd64") == "win-x64-stable"
    assert velopack_channel("prerelease", "amd64") == "win-x64-prerelease"
