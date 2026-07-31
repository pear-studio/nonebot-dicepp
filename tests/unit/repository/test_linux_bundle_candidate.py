from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build.validate_linux_bundle_candidate import (
    ExpectedImage,
    validate_linux_bundle_candidate,
)


BOT = ExpectedImage(
    "bot",
    "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
    "sha256:" + ("1" * 64),
)
DASHBOARD = ExpectedImage(
    "dashboard",
    "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
    "sha256:" + ("2" * 64),
)
ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
VALIDATOR = ROOT / "scripts" / "build" / "validate_linux_bundle_candidate.py"


def _write_bundle(tmp_path, *, images=None, archive_path=None, archive_bytes=b"archive"):
    package = tmp_path / "bundle"
    archive = package / (archive_path or "images/release-images.tar.zst")
    archive.parent.mkdir(parents=True)
    archive.write_bytes(archive_bytes)
    payload = {
        "image_archive": {
            "path": archive.relative_to(package).as_posix(),
            "size": len(archive_bytes),
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        },
        "images": images
        if images is not None
        else [
            {"role": image.role, "reference": image.reference, "image_id": image.image_id}
            for image in (BOT, DASHBOARD)
        ],
    }
    manifest = package / "dicepp-package.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return package, manifest, archive


def _rewrite_manifest(manifest, update):
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    update(payload)
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def _validator_command(package, manifest):
    return [
        sys.executable,
        str(VALIDATOR),
        "--package-root",
        str(package),
        "--manifest",
        str(manifest),
        "--expected-image",
        BOT.role,
        BOT.reference,
        BOT.image_id,
        "--expected-image",
        DASHBOARD.role,
        DASHBOARD.reference,
        DASHBOARD.image_id,
    ]


def test_bundle_candidate_returns_only_the_manifest_declared_archive(tmp_path):
    package, manifest, archive = _write_bundle(tmp_path)
    decoy = package / "images/decoy-images.tar.zst"
    decoy.write_bytes(b"decoy")

    result = validate_linux_bundle_candidate(
        package_root=package,
        manifest_path=manifest,
        expected_images=[BOT, DASHBOARD],
    )

    assert result == archive.resolve()
    assert result != decoy.resolve()


@pytest.mark.parametrize(
    "records",
    [
        [
            {
                "role": DASHBOARD.role,
                "reference": DASHBOARD.reference,
                "image_id": DASHBOARD.image_id,
            },
            {"role": BOT.role, "reference": BOT.reference, "image_id": BOT.image_id},
        ],
        [
            {"role": BOT.role, "reference": BOT.reference, "image_id": BOT.image_id},
        ],
        [
            {"role": BOT.role, "reference": BOT.reference, "image_id": BOT.image_id},
            {"role": DASHBOARD.role, "reference": BOT.reference, "image_id": BOT.image_id},
        ],
        [
            {"role": BOT.role, "reference": BOT.reference, "image_id": BOT.image_id},
            {
                "role": DASHBOARD.role,
                "reference": DASHBOARD.reference,
                "image_id": DASHBOARD.image_id,
            },
            {"role": "unexpected", "reference": "example.invalid/image", "image_id": "sha256:3"},
        ],
    ],
    ids=("reordered", "missing", "crossed", "extra"),
)
def test_bundle_candidate_rejects_any_image_role_or_identity_difference(
    tmp_path,
    records,
):
    package, manifest, _ = _write_bundle(tmp_path, images=records)

    with pytest.raises(ValueError, match="image roles, references, or Image IDs"):
        validate_linux_bundle_candidate(
            package_root=package,
            manifest_path=manifest,
            expected_images=[BOT, DASHBOARD],
        )


def test_bundle_candidate_rejects_archive_path_outside_images(tmp_path):
    package, manifest, _ = _write_bundle(
        tmp_path,
        archive_path="release-images.tar.zst",
    )

    with pytest.raises(ValueError, match="stored below images"):
        validate_linux_bundle_candidate(
            package_root=package,
            manifest_path=manifest,
            expected_images=[BOT, DASHBOARD],
        )


@pytest.mark.parametrize(
    ("declared_path", "message"),
    [
        ("images/../../outside.tar.zst", "remain inside"),
        ("/tmp/release-images.tar.zst", "remain inside"),
        ("C:/temp/release-images.tar.zst", "stored below images"),
        ("//server/share/release-images.tar.zst", "remain inside"),
        (r"images\release-images.tar.zst", "portable relative path"),
        (r"\\server\share\release-images.tar.zst", "portable relative path"),
    ],
    ids=(
        "traversal",
        "posix-absolute",
        "windows-drive-absolute",
        "posix-unc",
        "backslash-relative",
        "windows-unc",
    ),
)
def test_bundle_candidate_rejects_non_portable_or_escaping_archive_paths(
    tmp_path,
    declared_path,
    message,
):
    package, manifest, _ = _write_bundle(tmp_path)
    _rewrite_manifest(
        manifest,
        lambda payload: payload["image_archive"].update(path=declared_path),
    )

    with pytest.raises(ValueError, match=message):
        validate_linux_bundle_candidate(
            package_root=package,
            manifest_path=manifest,
            expected_images=[BOT, DASHBOARD],
        )


def test_bundle_candidate_rejects_archive_symlink_escaping_bundle(tmp_path):
    package, manifest, archive = _write_bundle(tmp_path)
    archive_bytes = archive.read_bytes()
    outside = tmp_path / "outside-release-images.tar.zst"
    outside.write_bytes(archive_bytes)
    archive.unlink()
    try:
        archive.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"platform cannot create file symlinks: {exc}")

    with pytest.raises(ValueError, match="escapes the bundle"):
        validate_linux_bundle_candidate(
            package_root=package,
            manifest_path=manifest,
            expected_images=[BOT, DASHBOARD],
        )


def test_bundle_candidate_rejects_equal_size_archive_digest_change(tmp_path):
    package, manifest, archive = _write_bundle(tmp_path)
    assert len(b"archive") == len(b"changed")
    archive.write_bytes(b"changed")

    with pytest.raises(ValueError, match="digest differs"):
        validate_linux_bundle_candidate(
            package_root=package,
            manifest_path=manifest,
            expected_images=[BOT, DASHBOARD],
        )


def test_validator_cli_prints_exactly_the_resolved_declared_archive(tmp_path):
    package, manifest, archive = _write_bundle(tmp_path)

    result = subprocess.run(
        _validator_command(package, manifest),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == f"{archive.resolve()}\n"
    assert result.stderr == ""


def test_validator_cli_failure_never_prints_an_archive_path(tmp_path):
    package, manifest, archive = _write_bundle(tmp_path)
    _rewrite_manifest(
        manifest,
        lambda payload: payload["images"].reverse(),
    )

    result = subprocess.run(
        _validator_command(package, manifest),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert str(archive.resolve()) not in result.stdout
