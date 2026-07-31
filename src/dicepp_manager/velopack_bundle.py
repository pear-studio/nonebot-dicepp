"""Strict contract and safe reader for the Windows Velopack update bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO

from packaging.version import InvalidVersion, Version

from ._path_security import (
    UnsafePathError,
    assert_contained_no_reparse,
    assert_directory_no_reparse,
    delete_path_entry_no_follow,
    open_regular_binary_no_follow,
)

VELOPACK_BUNDLE_NAME = "velopack.win-x64.zip"
VELOPACK_BUNDLE_FORMAT_VERSION = 1
VELOPACK_BUNDLE_MANIFEST_NAME = "manifest.json"
MAX_VELOPACK_BUNDLE_BYTES = 2 * 1024**3
MAX_VELOPACK_MANIFEST_BYTES = 1024 * 1024
MAX_VELOPACK_MEMBER_COUNT = 2
MAX_VELOPACK_MEMBER_BYTES = 2 * 1024**3
MAX_VELOPACK_TOTAL_BYTES = 2 * 1024**3 + MAX_VELOPACK_MANIFEST_BYTES
MAX_VELOPACK_COMPRESSION_RATIO = 200
MAX_NUPKG_MEMBER_COUNT = 50_000
MAX_NUPKG_MEMBER_BYTES = 2 * 1024**3
MAX_NUPKG_TOTAL_BYTES = 8 * 1024**3
MAX_NUPKG_COMPRESSION_RATIO = 500
MAX_NUSPEC_BYTES = 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_SEMVER_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
)


class VelopackBundleError(ValueError):
    """The bundle cannot be trusted as Velopack update material."""


@dataclass(frozen=True, slots=True)
class ValidatedVelopackBundle:
    path: Path
    outer_size: int
    outer_sha256: str
    device: int
    inode: int
    manifest: dict[str, Any]
    nupkg_name: str
    nupkg_size: int
    nupkg_sha256: str


def build_velopack_bundle_manifest(
    *,
    dicepp_version: str,
    velopack_version: str,
    channel: str,
    nupkg_path: Path,
    nupkg_name: str | None = None,
) -> dict[str, Any]:
    """Build and validate the inner manifest for a release-produced nupkg."""

    payload = {
        "format_version": VELOPACK_BUNDLE_FORMAT_VERSION,
        "dicepp_version": _normalized_version(dicepp_version),
        "velopack_version": velopack_version,
        "channel": channel,
        "platform": "windows",
        "arch": "amd64",
        "nupkg": {
            "filename": nupkg_name if nupkg_name is not None else nupkg_path.name,
            "size": nupkg_path.stat().st_size,
            "sha256": _sha256_file(nupkg_path),
        },
    }
    return validate_velopack_bundle_manifest(payload)


def validate_velopack_bundle_manifest(
    payload: Any,
    *,
    expected_dicepp_version: str | None = None,
    expected_velopack_version: str | None = None,
    expected_channel: str | None = None,
    expected_platform: str = "windows",
    expected_arch: str = "amd64",
) -> dict[str, Any]:
    """Validate the exact, versioned inner bundle contract."""

    required = {
        "format_version",
        "dicepp_version",
        "velopack_version",
        "channel",
        "platform",
        "arch",
        "nupkg",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise VelopackBundleError("Velopack bundle manifest fields mismatch")
    if (
        type(payload["format_version"]) is not int
        or payload["format_version"] != VELOPACK_BUNDLE_FORMAT_VERSION
    ):
        raise VelopackBundleError("Unsupported Velopack bundle format version")
    try:
        dicepp = Version(payload["dicepp_version"])
        velopack = Version(payload["velopack_version"])
    except (InvalidVersion, TypeError) as exc:
        raise VelopackBundleError(
            "Velopack bundle contains an invalid version"
        ) from exc
    if (
        type(payload["velopack_version"]) is not str
        or not _SEMVER_RE.fullmatch(payload["velopack_version"])
    ):
        raise VelopackBundleError("Velopack version must use SemVer 2")
    if dicepp != velopack:
        raise VelopackBundleError("DicePP and Velopack versions differ")
    channel = payload["channel"]
    if channel not in {"stable", "prerelease"}:
        raise VelopackBundleError("Invalid Velopack bundle channel")
    if (channel == "prerelease") != dicepp.is_prerelease:
        raise VelopackBundleError(
            "Velopack bundle channel and DicePP version differ"
        )
    if (
        payload["platform"] != expected_platform
        or payload["arch"] != expected_arch
    ):
        raise VelopackBundleError("Velopack bundle target differs")
    if expected_dicepp_version is not None:
        try:
            expected_dicepp = Version(expected_dicepp_version)
        except (InvalidVersion, TypeError) as exc:
            raise VelopackBundleError("Expected DicePP version is invalid") from exc
        if dicepp != expected_dicepp:
            raise VelopackBundleError(
                "Velopack bundle and Release versions differ"
            )
    if (
        expected_velopack_version is not None
        and payload["velopack_version"] != expected_velopack_version
    ):
        raise VelopackBundleError(
            "Velopack bundle version differs from build metadata"
        )
    if expected_channel is not None and channel != expected_channel:
        raise VelopackBundleError(
            "Velopack bundle and Release channels differ"
        )
    nupkg = payload["nupkg"]
    if not isinstance(nupkg, dict) or set(nupkg) != {
        "filename",
        "size",
        "sha256",
    }:
        raise VelopackBundleError("Velopack nupkg manifest fields mismatch")
    filename = nupkg["filename"]
    if (
        type(filename) is not str
        or not _SAFE_FILENAME_RE.fullmatch(filename)
        or not filename.casefold().endswith("-full.nupkg")
        or not filename.casefold().endswith(
            f"-{payload['velopack_version'].casefold()}-full.nupkg"
        )
        or Path(filename).name != filename
    ):
        raise VelopackBundleError("Unsafe Velopack nupkg filename")
    if type(nupkg["size"]) is not int or not 0 < nupkg["size"] <= MAX_VELOPACK_MEMBER_BYTES:
        raise VelopackBundleError("Invalid Velopack nupkg size")
    if (
        type(nupkg["sha256"]) is not str
        or not _SHA256_RE.fullmatch(nupkg["sha256"])
    ):
        raise VelopackBundleError("Invalid Velopack nupkg SHA-256")
    return {
        **payload,
        "dicepp_version": str(dicepp),
        "nupkg": dict(nupkg),
    }


def validate_velopack_bundle(
    path: Path,
    *,
    expected_dicepp_version: str | None = None,
    expected_channel: str | None = None,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> ValidatedVelopackBundle:
    """Validate the complete outer zip and nested nupkg without extracting it."""

    try:
        with open_regular_binary_no_follow(path) as bundle_handle:
            metadata = os.fstat(bundle_handle.fileno())
            if (
                metadata.st_size <= 0
                or metadata.st_size > MAX_VELOPACK_BUNDLE_BYTES
            ):
                raise VelopackBundleError(
                    "Velopack bundle is not a bounded regular file"
                )
            if expected_size is not None and metadata.st_size != expected_size:
                raise VelopackBundleError(
                    "Velopack bundle size differs from Release"
                )
            if (
                expected_sha256 is not None
                and not _SHA256_RE.fullmatch(expected_sha256)
            ):
                raise VelopackBundleError(
                    "Expected Velopack bundle digest is invalid"
                )
            outer_sha256 = _sha256_handle(bundle_handle)
            if (
                expected_sha256 is not None
                and outer_sha256 != expected_sha256
            ):
                raise VelopackBundleError(
                    "Velopack bundle digest differs from Release"
                )
            bundle_handle.seek(0)
            with zipfile.ZipFile(bundle_handle, "r") as archive:
                infos = archive.infolist()
                _validate_outer_members(infos)
                by_name = {info.filename: info for info in infos}
                manifest = validate_velopack_bundle_manifest(
                    _read_json_member(
                        archive,
                        by_name[VELOPACK_BUNDLE_MANIFEST_NAME],
                        MAX_VELOPACK_MANIFEST_BYTES,
                    ),
                    expected_dicepp_version=expected_dicepp_version,
                    expected_channel=expected_channel,
                )
                nupkg_name = str(manifest["nupkg"]["filename"])
                nupkg_info = by_name.get(nupkg_name)
                if nupkg_info is None:
                    raise VelopackBundleError(
                        "Velopack manifest nupkg member is missing"
                    )
                if set(by_name) != {VELOPACK_BUNDLE_MANIFEST_NAME, nupkg_name}:
                    raise VelopackBundleError(
                        "Velopack bundle has extra or conflicting members"
                    )
                if nupkg_info.file_size != manifest["nupkg"]["size"]:
                    raise VelopackBundleError(
                        "Velopack nupkg size differs from inner manifest"
                    )
                digest = hashlib.sha256()
                with archive.open(nupkg_info, "r") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                actual_digest = digest.hexdigest()
                if actual_digest != manifest["nupkg"]["sha256"]:
                    raise VelopackBundleError(
                        "Velopack nupkg digest differs from inner manifest"
                    )
                with archive.open(nupkg_info, "r") as nested:
                    actual_version = _nupkg_version(nested)
                if actual_version != manifest["velopack_version"]:
                    raise VelopackBundleError(
                        "Velopack nupkg internal version differs from inner manifest"
                    )
    except (
        OSError,
        UnsafePathError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        KeyError,
    ) as exc:
        if isinstance(exc, VelopackBundleError):
            raise
        raise VelopackBundleError(f"Velopack bundle is invalid: {exc}") from exc
    return ValidatedVelopackBundle(
        path=path,
        outer_size=metadata.st_size,
        outer_sha256=outer_sha256,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        manifest=manifest,
        nupkg_name=nupkg_name,
        nupkg_size=nupkg_info.file_size,
        nupkg_sha256=actual_digest,
    )


def extract_verified_nupkg(
    bundle: ValidatedVelopackBundle,
    destination_dir: Path,
    *,
    destination_name: str | None = None,
) -> Path:
    """Extract the already validated payload into a new trusted directory."""

    output_name = destination_name or bundle.nupkg_name
    if not _safe_destination_name(output_name):
        raise VelopackBundleError("Unsafe Velopack extraction destination name")
    destination = destination_dir / output_name
    created_identity: tuple[int, int] | None = None
    try:
        assert_directory_no_reparse(destination_dir)
        assert_contained_no_reparse(
            destination,
            root=destination_dir,
            allow_missing=True,
        )
        if os.path.lexists(destination):
            raise VelopackBundleError(
                "Velopack extraction destination is not a new regular file"
            )
        with open_regular_binary_no_follow(bundle.path) as bundle_handle:
            metadata = os.fstat(bundle_handle.fileno())
            if metadata.st_size != bundle.outer_size:
                raise VelopackBundleError(
                    "Velopack bundle changed after validation"
                )
            actual_outer_sha256 = _sha256_handle(bundle_handle)
            if actual_outer_sha256 != bundle.outer_sha256:
                raise VelopackBundleError(
                    "Velopack bundle changed after validation"
                )
            bundle_handle.seek(0)
            with zipfile.ZipFile(bundle_handle, "r") as archive:
                info = archive.getinfo(bundle.nupkg_name)
                if (
                    info.file_size != bundle.nupkg_size
                    or info.filename != bundle.nupkg_name
                ):
                    raise VelopackBundleError(
                        "Velopack payload identity changed after validation"
                    )
                digest = hashlib.sha256()
                with archive.open(info, "r") as source, destination.open("xb") as output:
                    opened = os.fstat(output.fileno())
                    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                        raise VelopackBundleError(
                            "Velopack extraction destination is not a private file"
                        )
                    created_identity = (opened.st_dev, opened.st_ino)
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                    written_size = os.fstat(output.fileno()).st_size
        if written_size != bundle.nupkg_size or digest.hexdigest() != bundle.nupkg_sha256:
            raise VelopackBundleError(
                "Extracted Velopack nupkg differs from validated payload"
            )
        return destination
    except Exception as exc:
        if created_identity is not None:
            _unlink_regular_file_if_identity(destination, created_identity)
        if isinstance(exc, VelopackBundleError):
            raise
        if isinstance(
            exc,
            (
                OSError,
                UnsafePathError,
                zipfile.BadZipFile,
                zipfile.LargeZipFile,
                KeyError,
            ),
        ):
            raise VelopackBundleError(
                f"Velopack payload extraction is unsafe or invalid: {exc}"
            ) from exc
        raise


def _safe_destination_name(name: Any) -> bool:
    if (
        not isinstance(name, str)
        or not _SAFE_FILENAME_RE.fullmatch(name)
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        return False
    windows = PureWindowsPath(name)
    return (
        Path(name).name == name
        and not windows.is_absolute()
        and not windows.drive
        and name not in {".", ".."}
    )


def _unlink_regular_file_if_identity(
    path: Path,
    identity: tuple[int, int],
) -> None:
    delete_path_entry_no_follow(
        path,
        expected_identity=identity,
    )


def _validate_outer_members(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) != MAX_VELOPACK_MEMBER_COUNT:
        raise VelopackBundleError(
            "Velopack bundle must contain exactly two members"
        )
    names: set[str] = set()
    folded: set[str] = set()
    total = 0
    for info in infos:
        _validate_zip_info(
            info,
            allow_directory=False,
            max_member_bytes=MAX_VELOPACK_MEMBER_BYTES,
            max_ratio=MAX_VELOPACK_COMPRESSION_RATIO,
        )
        if info.filename in names or info.filename.casefold() in folded:
            raise VelopackBundleError("Velopack bundle has duplicate members")
        names.add(info.filename)
        folded.add(info.filename.casefold())
        total += info.file_size
    if total > MAX_VELOPACK_TOTAL_BYTES:
        raise VelopackBundleError(
            "Velopack bundle extracted size exceeds the limit"
        )
    if VELOPACK_BUNDLE_MANIFEST_NAME not in names:
        raise VelopackBundleError("Velopack bundle manifest is missing")
    nupkgs = [
        name for name in names if name.casefold().endswith("-full.nupkg")
    ]
    if len(nupkgs) != 1:
        raise VelopackBundleError(
            "Velopack bundle must contain exactly one full nupkg"
        )


def _validate_zip_info(
    info: zipfile.ZipInfo,
    *,
    allow_directory: bool,
    max_member_bytes: int,
    max_ratio: int,
) -> None:
    name = info.filename
    if not _safe_member_name(name):
        raise VelopackBundleError(f"Unsafe zip member path: {name!r}")
    if info.flag_bits & 0x1:
        raise VelopackBundleError("Encrypted zip members are not supported")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type and not (
        stat.S_ISREG(mode) or (allow_directory and stat.S_ISDIR(mode))
    ):
        raise VelopackBundleError("Zip member is a symbolic link or special file")
    dos_attributes = info.external_attr & 0xFFFF
    if dos_attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise VelopackBundleError("Zip member is a Windows reparse point")
    if info.is_dir() and not allow_directory:
        raise VelopackBundleError("Velopack bundle may not contain directories")
    if info.file_size < 0 or info.file_size > max_member_bytes:
        raise VelopackBundleError("Zip member size exceeds the limit")
    if info.file_size and info.compress_size <= 0:
        raise VelopackBundleError("Zip member has an invalid compressed size")
    if (
        info.file_size > MAX_VELOPACK_MANIFEST_BYTES
        and info.file_size > info.compress_size * max_ratio
    ):
        raise VelopackBundleError("Zip member compression ratio exceeds the limit")


def _safe_member_name(name: Any) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and all(part not in {"", ".", ".."} for part in posix.parts)
    )


def _read_json_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limit: int,
) -> Any:
    if info.file_size > limit:
        raise VelopackBundleError("Velopack bundle manifest exceeds the limit")
    with archive.open(info, "r") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise VelopackBundleError("Velopack bundle manifest exceeds the limit")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VelopackBundleError("Velopack bundle manifest is invalid JSON") from exc


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise VelopackBundleError(
                f"Velopack bundle manifest has duplicate field {key!r}"
            )
        result[key] = value
    return result


def _nupkg_version(source: BinaryIO) -> str | None:
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_NUPKG_MEMBER_COUNT:
                raise VelopackBundleError("Velopack nupkg has too many members")
            folded: set[str] = set()
            total = 0
            nuspecs: list[zipfile.ZipInfo] = []
            for info in infos:
                _validate_zip_info(
                    info,
                    allow_directory=True,
                    max_member_bytes=MAX_NUPKG_MEMBER_BYTES,
                    max_ratio=MAX_NUPKG_COMPRESSION_RATIO,
                )
                folded_name = info.filename.casefold()
                if folded_name in folded:
                    raise VelopackBundleError(
                        "Velopack nupkg has duplicate members"
                    )
                folded.add(folded_name)
                total += info.file_size
                if total > MAX_NUPKG_TOTAL_BYTES:
                    raise VelopackBundleError(
                        "Velopack nupkg extracted size exceeds the limit"
                    )
                if PurePosixPath(info.filename).suffix.casefold() == ".nuspec":
                    nuspecs.append(info)
            if len(nuspecs) != 1 or nuspecs[0].file_size > MAX_NUSPEC_BYTES:
                raise VelopackBundleError(
                    "Velopack nupkg must contain one bounded nuspec"
                )
            with archive.open(nuspecs[0], "r") as nuspec:
                root = ElementTree.fromstring(nuspec.read(MAX_NUSPEC_BYTES + 1))
    except (
        OSError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        ElementTree.ParseError,
    ) as exc:
        if isinstance(exc, VelopackBundleError):
            raise
        raise VelopackBundleError(f"Velopack nupkg is invalid: {exc}") from exc
    versions = [
        (element.text or "").strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "version"
    ]
    if len(versions) != 1 or not versions[0]:
        raise VelopackBundleError(
            "Velopack nupkg nuspec version is missing or ambiguous"
        )
    return versions[0]


def _normalized_version(value: str) -> str:
    try:
        return str(Version(value.removeprefix("v")))
    except (InvalidVersion, AttributeError) as exc:
        raise VelopackBundleError("Invalid DicePP version") from exc


def _sha256_file(path: Path) -> str:
    with open_regular_binary_no_follow(path) as handle:
        return _sha256_handle(handle)


def _sha256_handle(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()
