from __future__ import annotations

import hashlib

import pytest

from scripts.build.generate_linux_package_manifest import (
    build_linux_package_manifest,
)
from scripts.build.release_metadata import ReleaseMetadata


def test_linux_inner_contract_hashes_compose_and_image_archive(tmp_path) -> None:
    package = tmp_path / "DicePP-v3.1.0-linux-amd64"
    images = package / "images"
    images.mkdir(parents=True)
    compose = package / "docker-compose.yml"
    archive = images / "DicePP-v3.1.0-linux-amd64-images.tar.zst"
    compose.write_bytes(b"services: {}")
    archive.write_bytes(b"zstd image archive")

    manifest = build_linux_package_manifest(
        version="v3.1.0",
        package_root=package,
        compose=compose,
        image_archive=archive,
        images=[
            "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
        ],
        image_ids=["sha256:" + ("1" * 64), "sha256:" + ("2" * 64)],
        metadata=ReleaseMetadata(
            version="3.1.0",
            data_changed=False,
            config_changed=False,
            change_scope=("runtime",),
            automatic_upgrade=True,
            minimum_manager_version="1.0",
        ),
    )

    assert manifest["format_version"] == 1
    assert manifest["deployment_schema_version"] > 0
    assert manifest["automatic_upgrade"] is True
    assert manifest["change_scope"] == ["runtime"]
    assert manifest["images"] == [
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
    ]
    assert manifest["compose"] == {
        "path": "docker-compose.yml",
        "size": len(b"services: {}"),
        "sha256": hashlib.sha256(b"services: {}").hexdigest(),
    }
    assert manifest["image_archive"]["path"].startswith("images/")
    assert len(manifest["catalog_digest"]) == 64


def test_linux_inner_contract_rejects_automatic_manager_change(tmp_path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    compose = package / "docker-compose.yml"
    archive = package / "images.tar.zst"
    compose.write_bytes(b"services: {}")
    archive.write_bytes(b"archive")

    with pytest.raises(ValueError, match="change_scope includes manager"):
        build_linux_package_manifest(
            version="v3.1.0",
            package_root=package,
            compose=compose,
            image_archive=archive,
            images=[
                "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
                "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            ],
            image_ids=[
                "sha256:" + ("1" * 64),
                "sha256:" + ("2" * 64),
            ],
            metadata=ReleaseMetadata(
                version="3.1.0",
                data_changed=False,
                config_changed=False,
                change_scope=("runtime", "manager"),
                automatic_upgrade=True,
                minimum_manager_version="1.0",
            ),
        )
