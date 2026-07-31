"""Validate the container identity declared by a final Linux release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import NamedTuple


class ExpectedImage(NamedTuple):
    role: str
    reference: str
    image_id: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_path(package_root: Path, record: object) -> Path:
    if not isinstance(record, dict):
        raise ValueError("image_archive must be an object")
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise ValueError("image_archive.path must be a portable relative path")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("image_archive.path must remain inside the bundle")
    if len(relative.parts) < 2 or relative.parts[0] != "images":
        raise ValueError("image_archive.path must be stored below images/")
    if not relative.name.endswith(".tar.zst"):
        raise ValueError("image_archive.path must identify a .tar.zst archive")

    root = package_root.resolve()
    archive = root.joinpath(*relative.parts).resolve()
    if not archive.is_relative_to(root):
        raise ValueError("image_archive.path escapes the bundle")
    if not archive.is_file():
        raise ValueError(f"image archive is missing: {raw_path}")

    size = record.get("size")
    sha256 = record.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("image_archive.size must be a non-negative integer")
    if archive.stat().st_size != size:
        raise ValueError("image archive size differs from dicepp-package.json")
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise ValueError("image_archive.sha256 must be a SHA-256 digest")
    if _sha256(archive) != sha256.lower():
        raise ValueError("image archive digest differs from dicepp-package.json")
    return archive


def validate_linux_bundle_candidate(
    *,
    package_root: Path,
    manifest_path: Path,
    expected_images: list[ExpectedImage],
) -> Path:
    """Return the declared archive after validating its exact release identity."""

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("dicepp-package.json is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("dicepp-package.json must contain an object")

    expected_records = [
        {
            "role": image.role,
            "reference": image.reference,
            "image_id": image.image_id,
        }
        for image in expected_images
    ]
    records = payload.get("images")
    if records != expected_records:
        raise ValueError(
            "Linux bundle image roles, references, or Image IDs differ "
            "from the tested candidates"
        )
    return _archive_path(package_root, payload.get("image_archive"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--expected-image",
        action="append",
        nargs=3,
        metavar=("ROLE", "REFERENCE", "IMAGE_ID"),
        required=True,
    )
    args = parser.parse_args()
    expected_images = [ExpectedImage(*values) for values in args.expected_image]
    archive = validate_linux_bundle_candidate(
        package_root=args.package_root,
        manifest_path=args.manifest,
        expected_images=expected_images,
    )
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
