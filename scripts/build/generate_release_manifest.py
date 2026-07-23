"""Generate the checksummed machine contract uploaded with a GitHub Release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from packaging.version import Version

from dicepp_data import DATA_CATALOG
from dicepp_manager.deployment import (
    DEPLOYMENT_SCHEMA_VERSION,
)
from dicepp_manager.release import (
    RELEASE_CONTRACT_VERSION,
    validate_release_manifest,
)
try:
    from scripts.build.release_metadata import (
        ReleaseMetadata,
        parse_release_metadata,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_metadata import ReleaseMetadata, parse_release_metadata


def artifact_record(spec: str) -> dict:
    try:
        platform_name, arch, purpose, raw_path = spec.split(":", 3)
    except ValueError as exc:
        raise ValueError(
            "artifact must be PLATFORM:ARCH:PURPOSE:PATH"
        ) from exc
    path = Path(raw_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "platform": platform_name,
        "arch": arch,
        "filename": path.name,
        "purpose": purpose,
        "size": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def build_manifest(
    *,
    version: str,
    channel: str,
    artifacts: list[dict],
    metadata: ReleaseMetadata,
) -> dict:
    normalized_version = str(Version(version.removeprefix("v")))
    if metadata.version != normalized_version:
        raise ValueError("Release metadata version differs from manifest version")
    payload = {
        "contract_version": RELEASE_CONTRACT_VERSION,
        "version": normalized_version,
        "channel": channel,
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": metadata.minimum_manager_version,
        "catalog_version": DATA_CATALOG.to_dict()["format_version"],
        "catalog_digest": DATA_CATALOG.digest,
        "change_scope": list(metadata.change_scope),
        "automatic_upgrade": metadata.automatic_upgrade,
        "artifacts": artifacts,
        "fallbacks": {
            "linux_ghcr_images": [
                f"ghcr.io/pear-studio/nonebot-dicepp:v{normalized_version}",
                f"ghcr.io/pear-studio/dicepp-dashboard:v{normalized_version}",
            ]
        },
    }
    return validate_release_manifest(payload)


def velopack_version(version: str) -> str:
    """Translate public PEP 440 release versions to Velopack SemVer 2."""
    parsed = Version(version.removeprefix("v"))
    base = ".".join(str(item) for item in parsed.release)
    if parsed.pre is None:
        return base
    label, number = parsed.pre
    labels = {"a": "alpha", "b": "beta", "rc": "rc"}
    return f"{base}-{labels[label]}.{number}"


def velopack_channel(channel: str, arch: str) -> str:
    if channel not in {"stable", "prerelease"}:
        raise ValueError("channel must be stable or prerelease")
    arch_label = {"amd64": "x64", "arm64": "arm64"}.get(arch)
    if arch_label is None:
        raise ValueError("unsupported Velopack architecture")
    return f"win-{arch_label}-{channel}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("stable", "prerelease"), required=True)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument(
        "--artifact",
        action="append",
        required=True,
        help="PLATFORM:ARCH:PURPOSE:PATH",
    )
    parser.add_argument("--output", type=Path, default=Path("dicepp-release.json"))
    args = parser.parse_args()
    metadata = parse_release_metadata(
        args.release_notes,
        expected_version=args.version,
    )
    payload = build_manifest(
        version=args.version,
        channel=args.channel,
        artifacts=[artifact_record(value) for value in args.artifact],
        metadata=metadata,
    )
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
