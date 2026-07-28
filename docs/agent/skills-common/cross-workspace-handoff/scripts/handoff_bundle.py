#!/usr/bin/env python3
"""Create and path-safely unpack cross-workspace handoff bundles."""

import argparse
import os
import secrets
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

WINDOWS_RESERVED = (
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def fail(message: str) -> None:
    raise SystemExit(message)


def new_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    return f"{timestamp}-{secrets.token_hex(4)}"


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


def pack(source: Path, output: Path) -> None:
    source, output = source.resolve(strict=True), output.resolve()
    if not source.is_dir() or not (source / "handoff.md").is_file():
        fail("source must be a directory containing handoff.md")
    if output.exists():
        fail(f"output already exists: {output}")
    if output.is_relative_to(source):
        fail("output must be outside the message directory")

    members: list[tuple[Path, str]] = []
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if item.is_symlink():
            fail(f"symbolic links are not allowed: {relative}")
        if item.is_file():
            name = relative.as_posix()
            bundle_path(name, False)
            members.append((item, name))

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


def unpack(bundle: Path, output: Path) -> None:
    bundle, output = bundle.resolve(strict=True), output.resolve()
    if output.exists():
        fail(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", suffix=".part", dir=output.parent)
    )
    try:
        with zipfile.ZipFile(bundle) as archive:
            seen: set[str] = set()
            for info in archive.infolist():
                path = bundle_path(info.filename, info.is_dir())
                folded = path.as_posix().casefold()
                if folded in seen:
                    fail(f"duplicate archive path: {info.filename!r}")
                seen.add(folded)
                if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    fail(f"symbolic links are not allowed: {info.filename!r}")
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
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("id", help="generate a message ID")
    pack_parser = commands.add_parser("pack", help="create a handoff ZIP")
    pack_parser.add_argument("source", type=Path)
    pack_parser.add_argument("output", type=Path)
    unpack_parser = commands.add_parser("unpack", help="path-safely unpack a handoff ZIP")
    unpack_parser.add_argument("bundle", type=Path)
    unpack_parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.command == "id":
        print(new_id())
    elif args.command == "pack":
        pack(args.source, args.output)
    else:
        unpack(args.bundle, args.output)


if __name__ == "__main__":
    main()
