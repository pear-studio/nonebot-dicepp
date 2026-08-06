from __future__ import annotations

import pytest

from dicepp_manager.release import ReleaseContractError
from scripts.build.generate_release_manifest import artifact_record, build_manifest
from scripts.build.release_metadata import ReleaseMetadata


def test_generator_hashes_real_artifacts_and_emits_valid_contract(tmp_path) -> None:
    linux = tmp_path / "DicePP-v3.1.0-linux-amd64.zip"
    windows = tmp_path / "DicePP-v3.1.0-win64-Portable.zip"
    velopack = tmp_path / "velopack.win-x64.zip"
    linux.write_bytes(b"linux package")
    windows.write_bytes(b"windows package")
    velopack.write_bytes(b"velopack bundle")

    manifest = build_manifest(
        version="v3.1.0",
        channel="stable",
        artifacts=[
            artifact_record(f"linux:amd64:linux-bundle:{linux}"),
            artifact_record(f"windows:amd64:portable:{windows}"),
            artifact_record(
                f"windows:amd64:velopack-bundle:{velopack}"
            ),
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


def test_outer_release_contract_rejects_automatic_manager_change(
    tmp_path,
) -> None:
    linux = tmp_path / "package.zip"
    linux.write_bytes(b"linux package")

    with pytest.raises(
        ReleaseContractError, match="change_scope includes manager"
    ):
        build_manifest(
            version="v3.1.0",
            channel="stable",
            artifacts=[artifact_record(f"linux:amd64:linux-bundle:{linux}")],
            metadata=ReleaseMetadata(
                version="3.1.0",
                data_changed=False,
                config_changed=False,
                change_scope=("runtime", "manager"),
                automatic_upgrade=True,
                minimum_manager_version="1.0",
            ),
        )


def test_automatic_upgrade_manifest_requires_commit_bound_matrix_evidence(
    tmp_path,
) -> None:
    linux = tmp_path / "package.zip"
    velopack = tmp_path / "velopack.win-x64.zip"
    linux.write_bytes(b"linux package")
    velopack.write_bytes(b"velopack bundle")

    with pytest.raises(
        ValueError, match="commit-bound cross-version evidence"
    ):
        build_manifest(
            version="v3.1.0",
            channel="stable",
            artifacts=[
                artifact_record(f"linux:amd64:linux-bundle:{linux}"),
                artifact_record(
                    f"windows:amd64:velopack-bundle:{velopack}"
                ),
            ],
            metadata=ReleaseMetadata(
                version="3.1.0",
                data_changed=False,
                config_changed=False,
                change_scope=("runtime",),
                automatic_upgrade=True,
                minimum_manager_version="1.0",
            ),
        )
