#!/usr/bin/env python3
"""Create, validate, and path-safely unpack cross-workspace handoff bundles."""

import argparse
import os
import re
import secrets
import shutil
import stat
import tempfile
import zipfile
from binascii import crc32
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

WINDOWS_RESERVED = (
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
REQUIRED_HEADERS = ("Message-ID", "From", "To", "Created", "Subject")
MAX_MEMBERS = 10_000
MAX_HANDOFF_BYTES = 1024 * 1024
MAX_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024


def fail(message: str) -> None:
    raise SystemExit(message)


def new_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%Sz")
    return f"{timestamp}-{secrets.token_hex(4)}"


def validate_id(value: str, label: str) -> None:
    if not ID_PATTERN.fullmatch(value):
        fail(f"invalid {label}: {value!r}")


def parse_metadata(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            break
        if ":" not in line:
            fail(f"invalid handoff header: {line!r}")
        key, value = line.split(":", 1)
        value = value.strip()
        if not key or key in headers:
            fail(f"invalid or duplicate handoff header: {key!r}")
        headers[key] = value

    missing = [key for key in REQUIRED_HEADERS if not headers.get(key)]
    if missing:
        fail(f"missing handoff headers: {', '.join(missing)}")
    for key in ("Message-ID", "From", "To"):
        validate_id(headers[key], key)
    if headers["From"] == "public":
        fail("'public' is reserved and cannot be a sender")
    if reply_to := headers.get("Reply-To"):
        validate_id(reply_to, "Reply-To")
    try:
        created = datetime.fromisoformat(headers["Created"].replace("Z", "+00:00"))
    except ValueError:
        fail(f"invalid Created timestamp: {headers['Created']!r}")
    if created.tzinfo is None:
        fail("Created timestamp must include a timezone")
    return headers


def bundle_path(name: str, is_dir: bool) -> PurePosixPath:
    trimmed = name[:-1] if name.endswith("/") else name
    parts = trimmed.split("/")
    if not trimmed or "\\" in name or any(part in {"", ".", ".."} for part in parts):
        fail(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute():
        fail(f"unsafe archive path: {name!r}")
    for part in parts:
        stem = part.split(".", 1)[0].casefold()
        if (
            part.casefold() == ".git"
            or stem in WINDOWS_RESERVED
            or part.endswith((" ", "."))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
        ):
            fail(f"unsafe cross-platform archive path: {name!r}")

    top = parts[0]
    valid_handoff = top == "handoff.md" and len(parts) == 1 and not is_dir
    valid_attachment = top == "attachments" and (len(parts) > 1 or is_dir)
    if not (valid_handoff or valid_attachment):
        fail(f"unexpected bundle member: {name!r}")
    return path


def validate_bundle(
    bundle: Path,
    *,
    message_id: str | None = None,
    sender: str | None = None,
    target: str | None = None,
) -> dict[str, str]:
    bundle = bundle.resolve(strict=True)
    with zipfile.ZipFile(bundle) as archive:
        seen: set[str] = set()
        handoff_info: zipfile.ZipInfo | None = None
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            fail(f"bundle has more than {MAX_MEMBERS} members")
        if sum(info.file_size for info in infos) > MAX_UNPACKED_BYTES:
            fail("bundle exceeds the uncompressed size limit")
        for info in infos:
            path = bundle_path(info.filename, info.is_dir())
            folded = path.as_posix().casefold()
            if folded in seen:
                fail(f"duplicate archive path: {info.filename!r}")
            seen.add(folded)
            if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                fail(f"symbolic links are not allowed: {info.filename!r}")
            if path.as_posix() == "handoff.md":
                handoff_info = info
        try:
            for info in infos:
                if info.is_dir():
                    continue
                with archive.open(info) as member:
                    for _ in iter(lambda: member.read(1024 * 1024), b""):
                        pass
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            fail(f"cannot read bundle member: {error}")
        if handoff_info is None:
            fail("bundle does not contain handoff.md")
        if handoff_info.file_size > MAX_HANDOFF_BYTES:
            fail("handoff.md exceeds the size limit")
        try:
            metadata = parse_metadata(archive.read(handoff_info).decode("utf-8"))
        except UnicodeDecodeError:
            fail("handoff.md must be UTF-8")

    expected = {
        "Message-ID": message_id,
        "From": sender,
        "To": target,
    }
    for key, value in expected.items():
        if value is not None and metadata[key] != value:
            fail(f"{key} is {metadata[key]!r}, expected {value!r}")
    return metadata


def validate_unpacked(bundle: Path, output: Path) -> None:
    bundle, output = bundle.resolve(strict=True), output.resolve(strict=True)
    validate_bundle(bundle)
    expected: set[str] = set()
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            path = bundle_path(info.filename, info.is_dir())
            if info.is_dir():
                continue
            relative = path.as_posix().casefold()
            expected.add(relative)
            target = output.joinpath(*path.parts)
            if not target.is_file() or target.is_symlink():
                fail(f"cached bundle member is missing or unsafe: {path}")
            checksum = 0
            with target.open("rb") as cached:
                for chunk in iter(lambda: cached.read(1024 * 1024), b""):
                    checksum = crc32(chunk, checksum)
            if target.stat().st_size != info.file_size or checksum & 0xFFFFFFFF != info.CRC:
                fail(f"cached bundle member differs from ZIP: {path}")

    actual: set[str] = set()
    for item in output.rglob("*"):
        if item.is_symlink():
            fail(f"symbolic links are not allowed in cached messages: {item}")
        if item.is_file():
            actual.add(item.relative_to(output).as_posix().casefold())
    if actual != expected:
        fail("cached message contains missing or unexpected files")


def pack(source: Path, output: Path) -> None:
    source, output = source.resolve(strict=True), output.resolve()
    if not source.is_dir() or not (source / "handoff.md").is_file():
        fail("source must be a directory containing handoff.md")
    if output.exists():
        fail(f"output already exists: {output}")
    if output.is_relative_to(source):
        fail("output must be outside the message directory")
    handoff = source / "handoff.md"
    if handoff.stat().st_size > MAX_HANDOFF_BYTES:
        fail("handoff.md exceeds the size limit")
    metadata = parse_metadata(handoff.read_text(encoding="utf-8"))
    if source.name != metadata["Message-ID"]:
        fail("message directory basename must match Message-ID")
    if output.suffix.casefold() != ".zip" or output.stem != metadata["Message-ID"]:
        fail("bundle basename must be <Message-ID>.zip")

    members: list[tuple[Path, str]] = []
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if item.is_symlink():
            fail(f"symbolic links are not allowed: {relative}")
        if item.is_file():
            name = relative.as_posix()
            bundle_path(name, False)
            members.append((item, name))
            if len(members) > MAX_MEMBERS:
                fail(f"bundle has more than {MAX_MEMBERS} members")
    if sum(item.stat().st_size for item, _ in members) > MAX_UNPACKED_BYTES:
        fail("bundle exceeds the uncompressed size limit")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".part", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for item, member_name in members:
                archive.write(item, member_name)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(output)


def unpack(bundle: Path, output: Path, *, announce: bool = True) -> None:
    bundle, output = bundle.resolve(strict=True), output.resolve()
    if output.exists():
        fail(f"output already exists: {output}")
    metadata = validate_bundle(bundle)
    if bundle.suffix.casefold() != ".zip" or bundle.stem != metadata["Message-ID"]:
        fail("bundle basename must be <Message-ID>.zip")
    if output.name != metadata["Message-ID"]:
        fail("message directory basename must match Message-ID")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", suffix=".part", dir=output.parent)
    )
    try:
        with zipfile.ZipFile(bundle) as archive:
            for info in archive.infolist():
                path = bundle_path(info.filename, info.is_dir())
                if info.is_dir():
                    continue
                target = temporary.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
        if not (temporary / "handoff.md").is_file():
            fail("bundle does not contain handoff.md")
        temporary.replace(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    if announce:
        print(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("id", help="generate a message ID")
    check_parser = commands.add_parser("check", help="validate a handoff ZIP")
    check_parser.add_argument("bundle", type=Path)
    pack_parser = commands.add_parser("pack", help="create a handoff ZIP")
    pack_parser.add_argument("source", type=Path)
    pack_parser.add_argument("output", type=Path)
    unpack_parser = commands.add_parser("unpack", help="path-safely unpack a handoff ZIP")
    unpack_parser.add_argument("bundle", type=Path)
    unpack_parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.command == "id":
        print(new_id())
    elif args.command == "check":
        metadata = validate_bundle(args.bundle)
        print(
            f"[{metadata['To']}] {metadata['Message-ID']}, "
            f"from {metadata['From']}"
        )
    elif args.command == "pack":
        pack(args.source, args.output)
    else:
        unpack(args.bundle, args.output)


if __name__ == "__main__":
    main()
