"""Create the single Windows machine-update asset."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    from dicepp_manager.velopack_bundle import (
        VELOPACK_BUNDLE_MANIFEST_NAME,
        VELOPACK_BUNDLE_NAME,
        build_velopack_bundle_manifest,
        validate_velopack_bundle,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from dicepp_manager.velopack_bundle import (
        VELOPACK_BUNDLE_MANIFEST_NAME,
        VELOPACK_BUNDLE_NAME,
        build_velopack_bundle_manifest,
        validate_velopack_bundle,
    )


def build_bundle(
    *,
    dicepp_version: str,
    velopack_version: str,
    channel: str,
    nupkg_path: Path,
    output: Path,
) -> dict:
    if not nupkg_path.is_file() or nupkg_path.is_symlink():
        raise ValueError("Velopack full nupkg must be a regular file")
    manifest = build_velopack_bundle_manifest(
        dicepp_version=dicepp_version,
        velopack_version=velopack_version,
        channel=channel,
        nupkg_path=nupkg_path,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(raw_temp)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            archive.writestr(
                VELOPACK_BUNDLE_MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            archive.write(nupkg_path, arcname=nupkg_path.name)
        validate_velopack_bundle(
            temporary,
            expected_dicepp_version=manifest["dicepp_version"],
            expected_channel=channel,
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--velopack-version", required=True)
    parser.add_argument(
        "--channel",
        choices=("stable", "prerelease"),
        required=True,
    )
    parser.add_argument("--nupkg", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(VELOPACK_BUNDLE_NAME),
    )
    args = parser.parse_args()
    if args.output.name != VELOPACK_BUNDLE_NAME:
        parser.error(f"output filename must be {VELOPACK_BUNDLE_NAME}")
    build_bundle(
        dicepp_version=args.version,
        velopack_version=args.velopack_version,
        channel=args.channel,
        nupkg_path=args.nupkg,
        output=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
