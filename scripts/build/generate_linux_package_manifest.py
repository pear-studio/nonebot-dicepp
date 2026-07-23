"""Generate the install contract embedded inside a Linux release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from packaging.version import Version

from dicepp_data import DATA_CATALOG
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION
try:
    from scripts.build.release_metadata import (
        ReleaseMetadata,
        parse_release_metadata,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_metadata import ReleaseMetadata, parse_release_metadata


def _file_record(path: Path, *, package_root: Path) -> dict:
    resolved = path.resolve()
    root = package_root.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError(f"Package file is outside package root: {path}")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": resolved.relative_to(root).as_posix(),
        "size": resolved.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def build_linux_package_manifest(
    *,
    version: str,
    package_root: Path,
    compose: Path,
    image_archive: Path,
    images: list[str],
    metadata: ReleaseMetadata,
) -> dict:
    normalized_version = str(Version(version.removeprefix("v")))
    if metadata.version != normalized_version:
        raise ValueError("Release metadata version differs from package version")
    if len(images) != 2 or not all(
        item.startswith("ghcr.io/pear-studio/") for item in images
    ):
        raise ValueError("Exactly two official GHCR image references are required")
    return {
        "format_version": 1,
        "version": normalized_version,
        "platform": "linux",
        "arch": "amd64",
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": metadata.minimum_manager_version,
        "catalog_version": DATA_CATALOG.to_dict()["format_version"],
        "catalog_digest": DATA_CATALOG.digest,
        "automatic_upgrade": metadata.automatic_upgrade,
        "compose": _file_record(compose, package_root=package_root),
        "image_archive": _file_record(image_archive, package_root=package_root),
        "images": images,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--image", action="append", required=True)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = parse_release_metadata(
        args.release_notes,
        expected_version=args.version,
    )
    payload = build_linux_package_manifest(
        version=args.version,
        package_root=args.package_root,
        compose=args.compose,
        image_archive=args.image_archive,
        images=args.image,
        metadata=metadata,
    )
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
