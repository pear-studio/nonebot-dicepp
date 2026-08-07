"""Linux release bundle fixtures shared by upgrade matrix tests.

The orchestrator and its tests only need the manifest fields the orchestrator
consumes (``images``, ``version``, ``change_scope``, compatibility metadata);
a full checksum manifest is not required because bundle validation runs inside
the Manager container, not in these fixtures.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

DEFAULT_MINIMUM_MANAGER_VERSION = "1.0"
DEFAULT_CATALOG_DIGEST = "c" * 64


def build_linux_bundle_bytes(
    *,
    version: str,
    compose: str,
    change_scope: list[str] | None = None,
    bot_image_id: str | None = None,
    dashboard_image_id: str | None = None,
    image_archive_path: str | None = None,
    archive_member: bytes | None = None,
    automatic_upgrade: bool = True,
) -> bytes:
    """Return a zip archive with ``docker-compose.yml`` and a manifest.

    ``image_archive_path`` overrides the manifest's image archive member
    path (useful for zip-slip guard tests); ``archive_member`` additionally
    writes that member into the archive when provided.
    """
    normalized_version = version.removeprefix("v")
    bot_id = bot_image_id or ("sha256:" + "a" * 64)
    dashboard_id = dashboard_image_id or ("sha256:" + "b" * 64)
    if archive_member is not None:
        archive_size = len(archive_member)
        archive_sha256 = hashlib.sha256(archive_member).hexdigest()
    else:
        archive_size = 1
        archive_sha256 = "0" * 64
    manifest = {
        "format_version": 1,
        "version": normalized_version,
        "platform": "linux",
        "arch": "amd64",
        "deployment_schema_version": 2,
        "minimum_manager_version": DEFAULT_MINIMUM_MANAGER_VERSION,
        "catalog_version": 2,
        "catalog_digest": DEFAULT_CATALOG_DIGEST,
        "automatic_upgrade": automatic_upgrade,
        "change_scope": change_scope or ["upgrade-runtime"],
        "compose": {
            "path": "docker-compose.yml",
            "size": len(compose.encode("utf-8")),
            "sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
        },
        "image_archive": {
            "path": image_archive_path or "images/test.tar.zst",
            "size": archive_size,
            "sha256": archive_sha256,
        },
        "images": [
            {
                "role": "bot",
                "reference": (
                    f"ghcr.io/pear-studio/nonebot-dicepp:v{normalized_version}"
                ),
                "image_id": bot_id,
            },
            {
                "role": "dashboard",
                "reference": (
                    f"ghcr.io/pear-studio/dicepp-dashboard:v{normalized_version}"
                ),
                "image_id": dashboard_id,
            },
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("docker-compose.yml", compose)
        archive.writestr("dicepp-package.json", json.dumps(manifest))
        if archive_member is not None:
            archive.writestr(manifest["image_archive"]["path"], archive_member)
    return buffer.getvalue()


def build_bundle_bytes_without_manifest(compose: str | None = None) -> bytes:
    """Return a zip archive that lacks ``dicepp-package.json``."""
    compose = compose or (
        "services:\n"
        "  manager:\n"
        "    image: test\n"
        "  bot:\n"
        "    image: test\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("docker-compose.yml", compose)
    return buffer.getvalue()


def build_bundle_bytes_with_non_object_manifest() -> bytes:
    """Return a zip archive whose manifest is not a JSON object."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("dicepp-package.json", "[1, 2, 3]")
    return buffer.getvalue()


def read_bundle_member(path: Path, name: str) -> bytes:
    """Read one member from a Linux bundle fixture."""
    with zipfile.ZipFile(path, "r") as archive:
        return archive.read(name)


def write_linux_bundle(
    path: Path,
    *,
    version: str,
    compose: str | None = None,
    change_scope: list[str] | None = None,
    bot_image_id: str | None = None,
    dashboard_image_id: str | None = None,
    image_archive_path: str | None = None,
    archive_member: bytes | None = None,
    automatic_upgrade: bool = True,
) -> Path:
    """Write a Linux release bundle fixture to ``path`` and return it."""
    compose = compose or (
        "services:\n"
        "  manager:\n"
        "    image: test\n"
        "  bot:\n"
        "    image: test\n"
    )
    path.write_bytes(
        build_linux_bundle_bytes(
            version=version,
            compose=compose,
            change_scope=change_scope,
            bot_image_id=bot_image_id,
            dashboard_image_id=dashboard_image_id,
            image_archive_path=image_archive_path,
            archive_member=archive_member,
            automatic_upgrade=automatic_upgrade,
        )
    )
    return path

