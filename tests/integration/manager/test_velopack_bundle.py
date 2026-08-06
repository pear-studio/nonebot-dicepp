from __future__ import annotations

import hashlib
import json
import os
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

import dicepp_manager._path_security as path_security
import dicepp_manager.velopack_bundle as bundle_module
from dicepp_manager._path_security import (
    delete_path_entry_no_follow,
    open_regular_binary_no_follow,
)
from dicepp_manager.velopack_bundle import (
    VelopackBundleError,
    extract_verified_nupkg,
    validate_velopack_bundle,
)


def _nupkg(version: str = "3.1.0", marker: str = "program") -> bytes:
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            f"<package><metadata><version>{version}</version></metadata></package>",
        )
        archive.writestr("lib/net8.0/DicePP.exe", marker)
    return output.getvalue()


def _manifest(
    payload: bytes,
    *,
    dicepp_version: str = "3.1.0",
    velopack_version: str = "3.1.0",
    filename: str = "DicePP-3.1.0-full.nupkg",
) -> dict:
    return {
        "format_version": 1,
        "dicepp_version": dicepp_version,
        "velopack_version": velopack_version,
        "channel": "stable",
        "platform": "windows",
        "arch": "amd64",
        "nupkg": {
            "filename": filename,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }


def _write_bundle(
    path: Path,
    *,
    payload: bytes | None = None,
    manifest: dict | str | None = None,
    payload_name: str = "DicePP-3.1.0-full.nupkg",
    extras: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
    compression: int = zipfile.ZIP_STORED,
) -> tuple[bytes, dict]:
    payload = payload if payload is not None else _nupkg()
    contract = manifest if manifest is not None else _manifest(payload)
    raw_manifest = contract if isinstance(contract, str) else json.dumps(contract)
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        archive.writestr("manifest.json", raw_manifest)
        archive.writestr(payload_name, payload)
        for name, body in extras or []:
            archive.writestr(name, body)
    return payload, contract if isinstance(contract, dict) else {}


def test_valid_bundle_extracts_only_the_manifest_declared_payload(
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "velopack.win-x64.zip"
    payload, manifest = _write_bundle(bundle_path)
    validated = validate_velopack_bundle(
        bundle_path,
        expected_dicepp_version="3.1.0",
        expected_channel="stable",
        expected_size=bundle_path.stat().st_size,
        expected_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
    )
    destination = tmp_path / "payload"
    destination.mkdir()

    extracted = extract_verified_nupkg(validated, destination)

    assert extracted.read_bytes() == payload
    assert validated.manifest == manifest
    assert sorted(item.name for item in destination.iterdir()) == [
        "DicePP-3.1.0-full.nupkg"
    ]


def test_extract_rejects_path_replaced_by_another_self_consistent_bundle(
    tmp_path: Path,
) -> None:
    authorized_path = tmp_path / "velopack.win-x64.zip"
    authorized_payload = _nupkg(marker="authorized")
    _write_bundle(authorized_path, payload=authorized_payload)
    validated = validate_velopack_bundle(authorized_path)
    replacement = tmp_path / "replacement.zip"
    replacement_payload = _nupkg(marker="replacement")
    _write_bundle(replacement, payload=replacement_payload)
    replacement.replace(authorized_path)
    destination = tmp_path / "payload"
    destination.mkdir()

    with pytest.raises(VelopackBundleError, match="changed after validation"):
        extract_verified_nupkg(validated, destination)

    assert list(destination.iterdir()) == []


def test_windows_stable_reader_blocks_writers_and_replacement_until_closed(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows file-share semantics")
    target = tmp_path / "bundle.zip"
    target.write_bytes(b"authorized")
    replacement = tmp_path / "replacement.zip"
    replacement.write_bytes(b"replacement")

    with open_regular_binary_no_follow(target):
        with pytest.raises(OSError):
            target.write_bytes(b"mutated")
        with pytest.raises(OSError):
            replacement.replace(target)
        assert target.read_bytes() == b"authorized"

    target.write_bytes(b"writable-after-close")
    replacement.replace(target)
    assert target.read_bytes() == b"replacement"


def test_windows_stable_reader_rejects_an_existing_writer(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows file-share semantics")
    target = tmp_path / "bundle.zip"
    target.write_bytes(b"authorized")

    with target.open("r+b"):
        with pytest.raises(OSError):
            with open_regular_binary_no_follow(target):
                pytest.fail("stable reader must not coexist with a writer")

    with open_regular_binary_no_follow(target) as handle:
        assert handle.read() == b"authorized"


def test_windows_identity_bound_delete_cannot_unlink_a_replacement(
    monkeypatch,
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows handle deletion semantics")
    target = tmp_path / "payload.nupkg"
    target.write_bytes(b"authorized")
    identity = target.lstat()
    replacement = tmp_path / "replacement.nupkg"
    replacement.write_bytes(b"replacement")
    original_commit = path_security._SetFileInformationByHandle
    attempted = []

    def attempt_replacement(*args):
        with pytest.raises(OSError):
            replacement.replace(target)
        attempted.append(True)
        return original_commit(*args)

    monkeypatch.setattr(
        path_security,
        "_SetFileInformationByHandle",
        attempt_replacement,
    )

    assert delete_path_entry_no_follow(
        target,
        expected_identity=(identity.st_dev, identity.st_ino),
    )

    assert attempted == [True]
    assert not target.exists()
    assert replacement.read_bytes() == b"replacement"


def test_posix_quarantine_delete_preserves_replacement_created_at_commit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX dirfd deletion semantics")
    target = tmp_path / "payload.nupkg"
    target.write_bytes(b"authorized")
    identity = target.lstat()
    real_stat = path_security.os.stat
    injected = False

    def replace_after_quarantine(path, *args, **kwargs):
        nonlocal injected
        result = real_stat(path, *args, **kwargs)
        if isinstance(path, str) and path.startswith(".delete-") and not injected:
            target.write_bytes(b"replacement")
            injected = True
        return result

    monkeypatch.setattr(path_security.os, "stat", replace_after_quarantine)

    assert delete_path_entry_no_follow(
        target,
        expected_identity=(identity.st_dev, identity.st_ino),
    )

    assert injected is True
    assert target.read_bytes() == b"replacement"


def test_extract_does_not_delete_an_existing_destination(tmp_path: Path) -> None:
    bundle_path = tmp_path / "velopack.win-x64.zip"
    _write_bundle(bundle_path)
    validated = validate_velopack_bundle(bundle_path)
    destination = tmp_path / "payload"
    destination.mkdir()
    existing = destination / "already-here.nupkg"
    existing.write_bytes(b"must remain")

    with pytest.raises(VelopackBundleError, match="not a new regular file"):
        extract_verified_nupkg(
            validated,
            destination,
            destination_name=existing.name,
        )

    assert existing.read_bytes() == b"must remain"


def test_extraction_failure_cleanup_cannot_delete_a_replacement(
    monkeypatch,
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows handle deletion semantics")
    bundle_path = tmp_path / "velopack.win-x64.zip"
    _write_bundle(bundle_path)
    validated = validate_velopack_bundle(bundle_path)
    destination = tmp_path / "payload"
    destination.mkdir()
    output = destination / "candidate.nupkg"
    replacement = destination / "replacement.nupkg"
    replacement.write_bytes(b"replacement")
    original_commit = path_security._SetFileInformationByHandle
    attempts = []

    def attempt_replacement(*args):
        with pytest.raises(OSError):
            replacement.replace(output)
        attempts.append(True)
        return original_commit(*args)

    monkeypatch.setattr(
        path_security,
        "_SetFileInformationByHandle",
        attempt_replacement,
    )
    monkeypatch.setattr(
        bundle_module.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("injected fsync")),
    )

    with pytest.raises(VelopackBundleError, match="injected fsync"):
        extract_verified_nupkg(
            validated,
            destination,
            destination_name=output.name,
        )

    assert attempts == [True]
    assert not output.exists()
    assert replacement.read_bytes() == b"replacement"


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../outside.nupkg",
        "/outside.nupkg",
        "C:/outside.nupkg",
        r"C:\outside.nupkg",
        "//server/share/outside.nupkg",
        r"\\server\share\outside.nupkg",
        "nested/outside.nupkg",
        r"nested\outside.nupkg",
        ".",
        "..",
    ],
)
def test_extract_rejects_unsafe_destination_without_touching_existing_files(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    bundle_path = tmp_path / "velopack.win-x64.zip"
    _write_bundle(bundle_path)
    validated = validate_velopack_bundle(bundle_path)
    destination = tmp_path / "payload"
    destination.mkdir()
    outside = tmp_path / "outside.nupkg"
    outside.write_bytes(b"must remain")

    with pytest.raises(VelopackBundleError, match="destination name"):
        extract_verified_nupkg(
            validated,
            destination,
            destination_name=unsafe_name,
        )

    assert outside.read_bytes() == b"must remain"
    assert list(destination.iterdir()) == []


def test_bundle_file_and_destination_directory_reparse_points_are_rejected(
    tmp_path: Path,
) -> None:
    real_bundle = tmp_path / "real.zip"
    _write_bundle(real_bundle)
    linked_bundle = tmp_path / "velopack.win-x64.zip"
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_destination = tmp_path / "payload"
    try:
        linked_bundle.symlink_to(real_bundle)
        linked_destination.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Filesystem links are unavailable: {exc}")

    with pytest.raises(VelopackBundleError, match="reparse|symbolic"):
        validate_velopack_bundle(linked_bundle)

    validated = validate_velopack_bundle(real_bundle)
    with pytest.raises((VelopackBundleError, OSError), match="reparse|symbolic"):
        extract_verified_nupkg(validated, linked_destination)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../DicePP-3.1.0-full.nupkg",
        "/DicePP-3.1.0-full.nupkg",
        "C:/DicePP-3.1.0-full.nupkg",
        "//server/share/DicePP-3.1.0-full.nupkg",
        r"nested\DicePP-3.1.0-full.nupkg",
    ],
)
def test_bundle_rejects_posix_and_windows_escaping_member_names(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    bundle_path = tmp_path / "velopack.win-x64.zip"
    _write_bundle(bundle_path, payload_name=unsafe_name)

    with pytest.raises(VelopackBundleError):
        validate_velopack_bundle(bundle_path)


def test_bundle_rejects_symlink_duplicate_and_extra_members(
    tmp_path: Path,
) -> None:
    payload = _nupkg()
    symlink = zipfile.ZipInfo("DicePP-3.1.0-full.nupkg")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    reparse = zipfile.ZipInfo("DicePP-3.1.0-full.nupkg")
    reparse.create_system = 0
    reparse.external_attr = 0x0400
    cases = [
        (
            "symlink.zip",
            [("manifest.json", json.dumps(_manifest(payload)).encode()), (symlink, b"x")],
            "symbolic link",
        ),
        (
            "reparse.zip",
            [
                ("manifest.json", json.dumps(_manifest(payload)).encode()),
                (reparse, b"x"),
            ],
            "reparse point",
        ),
        (
            "duplicate.zip",
            [
                ("manifest.json", json.dumps(_manifest(payload)).encode()),
                ("DicePP-3.1.0-full.nupkg", payload),
                ("DicePP-3.1.0-full.nupkg", payload),
            ],
            "exactly two",
        ),
        (
            "extra.zip",
            [
                ("manifest.json", json.dumps(_manifest(payload)).encode()),
                ("DicePP-3.1.0-full.nupkg", payload),
                ("unexpected.txt", b"unexpected"),
            ],
            "exactly two",
        ),
    ]
    for filename, members, message in cases:
        path = tmp_path / filename
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w") as archive:
                for name, body in members:
                    archive.writestr(name, body)
        with pytest.raises(VelopackBundleError, match=message):
            validate_velopack_bundle(path)


@pytest.mark.parametrize("conflict", ["digest", "filename", "internal-version"])
def test_bundle_rejects_manifest_and_nupkg_identity_conflicts(
    tmp_path: Path,
    conflict: str,
) -> None:
    payload = _nupkg("3.1.0" if conflict != "internal-version" else "3.2.0")
    manifest = _manifest(payload)
    payload_name = manifest["nupkg"]["filename"]
    if conflict == "digest":
        manifest["nupkg"]["sha256"] = "0" * 64
    elif conflict == "filename":
        manifest["nupkg"]["filename"] = "Other-3.1.0-full.nupkg"
    path = tmp_path / f"{conflict}.zip"
    _write_bundle(
        path,
        payload=payload,
        manifest=manifest,
        payload_name=payload_name,
    )

    with pytest.raises(VelopackBundleError):
        validate_velopack_bundle(path)


def test_bundle_rejects_duplicate_manifest_fields_and_zip_bomb_ratio(
    tmp_path: Path,
) -> None:
    payload = _nupkg()
    duplicate = json.dumps(_manifest(payload)).replace(
        '"format_version": 1',
        '"format_version": 1, "format_version": 1',
    )
    duplicate_path = tmp_path / "duplicate-field.zip"
    _write_bundle(duplicate_path, payload=payload, manifest=duplicate)
    with pytest.raises(VelopackBundleError, match="duplicate field"):
        validate_velopack_bundle(duplicate_path)

    bomb_path = tmp_path / "ratio.zip"
    bomb = b"\0" * (2 * 1024 * 1024)
    _write_bundle(
        bomb_path,
        payload=bomb,
        manifest=_manifest(bomb),
        compression=zipfile.ZIP_DEFLATED,
    )
    with pytest.raises(VelopackBundleError, match="compression ratio"):
        validate_velopack_bundle(bomb_path)
