"""Fail-closed validation for cross-version automatic-upgrade evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.build.release_metadata import parse_release_metadata
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_metadata import parse_release_metadata


UPGRADE_MATRIX_CONTRACT_VERSION = 1
UPGRADE_EVIDENCE_CONTRACT_VERSION = 1
REQUIRED_PLATFORMS = (
    ("windows", "amd64"),
    ("linux", "amd64"),
)
EXPECTED_CANDIDATE_KEYS = frozenset(
    {
        ("linux", "runtime-manifest"),
        ("linux", "dashboard-manifest"),
        ("windows", "package-tree"),
    }
)
REQUIRED_SCENARIOS = (
    "healthy_commit",
    "target_health_failure_rollback",
    "retry_after_rollback",
    "apply_failure_before_target_execution",
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    platform: str
    name: str
    sha256: str


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def parse_candidate_identity(spec: str) -> CandidateIdentity:
    try:
        platform, name, digest = spec.split(":", 2)
    except ValueError as exc:
        raise ValueError("candidate must be PLATFORM:NAME:SHA256") from exc
    digest = digest.removeprefix("sha256:")
    if not platform or not name or _SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError("candidate must be PLATFORM:NAME:SHA256")
    return CandidateIdentity(platform=platform, name=name, sha256=digest)


def candidate_digest(identities: Iterable[CandidateIdentity]) -> str:
    records = normalize_candidate_identities(identities)
    canonical = json.dumps(
        records,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def normalize_candidate_identities(
    identities: Iterable[CandidateIdentity],
) -> list[dict[str, str]]:
    records = sorted(
        (
            {
                "platform": identity.platform,
                "name": identity.name,
                "sha256": identity.sha256,
            }
            for identity in identities
        ),
        key=lambda item: (item["platform"], item["name"]),
    )
    if not records:
        raise ValueError("at least one candidate identity is required")
    keys = [(item["platform"], item["name"]) for item in records]
    if len(keys) != len(set(keys)):
        raise ValueError("candidate identities contain duplicate platform/name pairs")
    if set(keys) != EXPECTED_CANDIDATE_KEYS:
        raise ValueError(
            "candidate identities must contain exactly the contract v1 keys"
        )
    if any(_SHA256_PATTERN.fullmatch(item["sha256"]) is None for item in records):
        raise ValueError("candidate identity SHA-256 is invalid")
    return records


def validate_upgrade_matrix(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("upgrade matrix must be a JSON object")
    if set(payload) != {
        "contract_version",
        "required_platforms",
        "required_scenarios",
        "supported_sources",
    }:
        raise ValueError("upgrade matrix fields do not match contract version 1")
    if payload["contract_version"] != UPGRADE_MATRIX_CONTRACT_VERSION:
        raise ValueError("unsupported upgrade matrix contract version")
    required_platforms = payload["required_platforms"]
    expected_platforms = [
        {"platform": platform, "arch": arch}
        for platform, arch in REQUIRED_PLATFORMS
    ]
    if required_platforms != expected_platforms:
        raise ValueError(
            "upgrade matrix required platforms must match contract version 1"
        )
    platform_keys: list[tuple[str, str]] = []
    for item in required_platforms:
        if not isinstance(item, dict) or set(item) != {"platform", "arch"}:
            raise ValueError("invalid required platform entry")
        key = (item["platform"], item["arch"])
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError("invalid required platform entry")
        platform_keys.append(key)
    if len(platform_keys) != len(set(platform_keys)):
        raise ValueError("upgrade matrix contains duplicate required platforms")
    if payload["required_scenarios"] != list(REQUIRED_SCENARIOS):
        raise ValueError("upgrade matrix must require the complete scenario contract")
    sources = payload["supported_sources"]
    if not isinstance(sources, list):
        raise ValueError("supported_sources must be a list")
    source_keys: list[tuple[str, str, str]] = []
    for source in sources:
        _validate_matrix_source(source, set(platform_keys))
        source_keys.append(
            (source["platform"], source["arch"], source["source_version"])
        )
    if len(source_keys) != len(set(source_keys)):
        raise ValueError("upgrade matrix contains duplicate source rows")
    return payload


def _validate_matrix_source(
    source: Any,
    required_platforms: set[tuple[str, str]],
) -> None:
    if not isinstance(source, dict) or set(source) != {
        "platform",
        "arch",
        "source_version",
        "assets",
    }:
        raise ValueError("invalid supported source entry")
    platform_key = (source["platform"], source["arch"])
    if platform_key not in required_platforms:
        raise ValueError("supported source uses an undeclared platform")
    if (
        not isinstance(source["source_version"], str)
        or not source["source_version"]
    ):
        raise ValueError("supported source version is invalid")
    assets = source["assets"]
    if not isinstance(assets, list) or not assets:
        raise ValueError("supported source must pin at least one asset")
    names: list[str] = []
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {"name", "url", "sha256"}:
            raise ValueError("invalid supported source asset")
        if not isinstance(asset["name"], str) or not asset["name"]:
            raise ValueError("supported source asset name is invalid")
        if (
            not isinstance(asset["url"], str)
            or not asset["url"].startswith("https://")
        ):
            raise ValueError("supported source asset URL must use HTTPS")
        if _SHA256_PATTERN.fullmatch(asset["sha256"]) is None:
            raise ValueError("supported source asset SHA-256 is invalid")
        names.append(asset["name"])
    if len(names) != len(set(names)):
        raise ValueError("supported source contains duplicate asset names")


def validate_upgrade_evidence(
    evidence: Any,
    *,
    matrix: dict[str, Any],
    target_version: str,
    target_commit_sha: str,
    target_candidate_identities: Iterable[CandidateIdentity],
) -> dict[str, Any]:
    validate_upgrade_matrix(matrix)
    validate_upgrade_matrix_coverage(matrix)
    if _COMMIT_PATTERN.fullmatch(target_commit_sha) is None:
        raise ValueError("target commit SHA must be a full lowercase Git SHA-1")
    normalized_identities = normalize_candidate_identities(
        target_candidate_identities
    )
    target_candidate_digest = candidate_digest(
        CandidateIdentity(**identity) for identity in normalized_identities
    )
    if not isinstance(evidence, dict) or set(evidence) != {
        "contract_version",
        "target",
        "results",
    }:
        raise ValueError("upgrade evidence fields do not match contract version 1")
    if evidence["contract_version"] != UPGRADE_EVIDENCE_CONTRACT_VERSION:
        raise ValueError("unsupported upgrade evidence contract version")
    expected_target = {
        "version": target_version.removeprefix("v"),
        "commit_sha": target_commit_sha,
        "candidate_identities": normalized_identities,
        "candidate_digest": target_candidate_digest,
    }
    if evidence["target"] != expected_target:
        raise ValueError("upgrade evidence target does not match this candidate")

    expected_sources = {
        (source["platform"], source["arch"], source["source_version"]): source
        for source in matrix["supported_sources"]
    }
    results = evidence["results"]
    if not isinstance(results, list):
        raise ValueError("upgrade evidence results must be a list")
    actual: dict[tuple[str, str, str], dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict) or set(result) != {
            "platform",
            "arch",
            "source_version",
            "source_assets",
            "scenarios",
        }:
            raise ValueError("invalid upgrade evidence result")
        key = (result["platform"], result["arch"], result["source_version"])
        if key in actual:
            raise ValueError("upgrade evidence contains duplicate source results")
        actual[key] = result
    if set(actual) != set(expected_sources):
        raise ValueError("upgrade evidence does not cover the complete supported matrix")
    for key, source in expected_sources.items():
        result = actual[key]
        expected_assets = [
            {"name": asset["name"], "sha256": asset["sha256"]}
            for asset in source["assets"]
        ]
        if result["source_assets"] != expected_assets:
            raise ValueError("upgrade evidence source asset digests differ from matrix")
        scenarios = result["scenarios"]
        expected_scenarios = [
            {"name": name, "status": "passed"} for name in REQUIRED_SCENARIOS
        ]
        if scenarios != expected_scenarios:
            raise ValueError("upgrade evidence scenarios are incomplete or did not pass")
    return evidence


def validate_upgrade_matrix_coverage(matrix: dict[str, Any]) -> None:
    required_platforms = {
        (item["platform"], item["arch"])
        for item in matrix["required_platforms"]
    }
    covered_platforms = {
        (source["platform"], source["arch"])
        for source in matrix["supported_sources"]
    }
    missing_platforms = required_platforms - covered_platforms
    if missing_platforms:
        missing = ", ".join(
            f"{platform}/{arch}"
            for platform, arch in sorted(missing_platforms)
        )
        raise ValueError(f"upgrade matrix has no supported source for: {missing}")


def verify_release(
    *,
    release_notes: Path,
    version: str,
    commit_sha: str,
    candidate_identities: Iterable[CandidateIdentity],
    matrix_path: Path,
    evidence_path: Path,
) -> str | None:
    metadata = parse_release_metadata(release_notes, expected_version=version)
    if not metadata.automatic_upgrade:
        return None
    matrix = validate_upgrade_matrix(
        load_json_object(matrix_path, label="upgrade matrix")
    )
    validate_upgrade_matrix_coverage(matrix)
    identities = list(candidate_identities)
    digest = candidate_digest(identities)
    evidence = load_json_object(evidence_path, label="upgrade evidence")
    validate_upgrade_evidence(
        evidence,
        matrix=matrix,
        target_version=version,
        target_commit_sha=commit_sha,
        target_candidate_identities=identities,
    )
    return digest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-release")
    verify.add_argument("--release-notes", type=Path, required=True)
    verify.add_argument("--version", required=True)
    verify.add_argument("--commit-sha", required=True)
    verify.add_argument("--candidate", action="append", default=[])
    verify.add_argument("--matrix", type=Path, required=True)
    verify.add_argument("--evidence", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "verify-release":
        digest = verify_release(
            release_notes=args.release_notes,
            version=args.version,
            commit_sha=args.commit_sha,
            candidate_identities=(
                parse_candidate_identity(spec) for spec in args.candidate
            ),
            matrix_path=args.matrix,
            evidence_path=args.evidence,
        )
        if digest is not None:
            print(digest)
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
