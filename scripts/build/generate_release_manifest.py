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
    SUPPORTED_LINUX_MANAGER_HANDOFF_PROTOCOL,
    validate_release_manifest,
)
try:
    from scripts.build.release_build_metadata import (
        velopack_channel,
        velopack_version,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_build_metadata import velopack_channel, velopack_version
try:
    from scripts.build.release_metadata import (
        ReleaseMetadata,
        parse_release_metadata,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_metadata import ReleaseMetadata, parse_release_metadata
try:
    from scripts.build.upgrade_evidence import (
        CandidateIdentity,
        FinalAssetIdentity,
        parse_candidate_identity,
        verify_release,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from upgrade_evidence import (
        CandidateIdentity,
        FinalAssetIdentity,
        parse_candidate_identity,
        verify_release,
    )


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
    commit_sha: str | None = None,
    candidate_identities: list[CandidateIdentity] | None = None,
    upgrade_matrix: Path | None = None,
    upgrade_evidence: Path | None = None,
    release_notes: Path | None = None,
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
    if metadata.linux_manager_handoff_protocol is not None:
        payload["linux_manager_handoff_protocol"] = (
            metadata.linux_manager_handoff_protocol
        )
    if (
        metadata.automatic_upgrade
        and "manager" in metadata.change_scope
        and metadata.linux_manager_handoff_protocol
        != SUPPORTED_LINUX_MANAGER_HANDOFF_PROTOCOL
    ):
        raise ValueError(
            "automatic_upgrade with a Manager change requires a supported "
            "linux_manager_handoff_protocol in the release metadata"
        )
    validated = validate_release_manifest(payload)
    if metadata.automatic_upgrade:
        if None in {commit_sha, upgrade_matrix, upgrade_evidence, release_notes}:
            raise ValueError(
                "automatic_upgrade requires commit-bound cross-version evidence"
            )
        verify_release(
            release_notes=release_notes,
            version=version,
            commit_sha=commit_sha,
            candidate_identities=candidate_identities or [],
            final_assets=[FinalAssetIdentity(**artifact) for artifact in artifacts],
            matrix_path=upgrade_matrix,
            evidence_path=upgrade_evidence,
        )
    return validated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("stable", "prerelease"), required=True)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument("--commit-sha")
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="PLATFORM:NAME:SHA256 identity used by upgrade evidence",
    )
    parser.add_argument(
        "--upgrade-matrix",
        type=Path,
        default=Path("scripts/build/upgrade_matrix.json"),
    )
    parser.add_argument("--upgrade-evidence", type=Path)
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
        commit_sha=args.commit_sha,
        candidate_identities=[
            parse_candidate_identity(value) for value in args.candidate
        ],
        upgrade_matrix=args.upgrade_matrix,
        upgrade_evidence=args.upgrade_evidence,
        release_notes=args.release_notes,
    )
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
