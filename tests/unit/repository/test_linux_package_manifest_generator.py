from __future__ import annotations

import hashlib

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
    assert manifest["compose"] == {
        "path": "docker-compose.yml",
        "size": len(b"services: {}"),
        "sha256": hashlib.sha256(b"services: {}").hexdigest(),
    }
    assert manifest["image_archive"]["path"].startswith("images/")
    assert len(manifest["catalog_digest"]) == 64
