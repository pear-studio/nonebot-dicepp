"""Fail-closed validation for cross-version automatic-upgrade evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

UPGRADE_MATRIX_CONTRACT_VERSION = 1
UPGRADE_EVIDENCE_CONTRACT_VERSION = 2
UPGRADE_PROTOCOL_REGISTRY_CONTRACT_VERSION = 1
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
EXPECTED_FINAL_ASSET_KEYS = frozenset(
    {
        ("windows", "amd64", "portable"),
        ("windows", "amd64", "setup"),
        ("windows", "amd64", "velopack-bundle"),
        ("linux", "amd64", "linux-bundle"),
    }
)
EXPECTED_PROTOCOLS = {
    "update_guard_request": (2,),
    "update_guard_markers": (2,),
    "manager_upgrade_journal": ("unversioned-row-v1",),
    "release_manifest": (2,),
    "windows_bundle_manifest": (1,),
    "linux_bundle_manifest": (1,),
    "deployment_schema": (2,),
}
SCENARIO_ASSERTIONS = {
    "healthy_commit": frozenset(
        {
            "source_started",
            "target_started",
            "local_health_passed",
            "journal_committed",
        }
    ),
    "target_health_failure_rollback": frozenset(
        {
            "target_executed",
            "health_failure_injected",
            "program_restored",
            "data_restored",
            "source_restarted",
            "journal_rolled_back",
        }
    ),
    "retry_after_rollback": frozenset(
        {
            "prior_rollback_observed",
            "retry_started_same_instance",
            "target_started",
            "journal_committed",
        }
    ),
    "apply_failure_before_target_execution": frozenset(
        {
            "apply_failure_injected",
            "target_never_executed",
            "source_remained_or_restored",
            "no_target_migration",
            "terminal_state_recorded",
        }
    ),
}
SCENARIO_OBSERVATION_FIELDS = {
    "healthy_commit": frozenset(
        {
            "source_version_before",
            "target_version_after",
            "journal_status",
            "health_status",
        }
    ),
    "target_health_failure_rollback": frozenset(
        {
            "target_version_observed",
            "restored_version",
            "journal_status",
            "rollback_marker_status",
        }
    ),
    "retry_after_rollback": frozenset(
        {
            "first_transaction_status",
            "retry_transaction_status",
            "final_version",
        }
    ),
    "apply_failure_before_target_execution": frozenset(
        {
            "target_process_start_count",
            "source_version_after",
            "journal_status",
            "apply_exit_code",
        }
    ),
}
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


@dataclass(frozen=True, slots=True)
class FinalAssetIdentity:
    platform: str
    arch: str
    purpose: str
    filename: str
    size: int
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


def parse_final_asset_identity(spec: str) -> FinalAssetIdentity:
    try:
        platform, arch, purpose, filename, raw_size, digest = spec.split(":", 5)
        size = int(raw_size)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "final asset must be PLATFORM:ARCH:PURPOSE:FILENAME:SIZE:SHA256"
        ) from exc
    identity = FinalAssetIdentity(
        platform=platform,
        arch=arch,
        purpose=purpose,
        filename=filename,
        size=size,
        sha256=digest.removeprefix("sha256:"),
    )
    return identity


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


def normalize_final_asset_identities(
    identities: Iterable[FinalAssetIdentity],
) -> list[dict[str, str | int]]:
    records = sorted(
        (
            {
                "platform": item.platform,
                "arch": item.arch,
                "purpose": item.purpose,
                "filename": item.filename,
                "size": item.size,
                "sha256": item.sha256,
            }
            for item in identities
        ),
        key=lambda item: (
            str(item["platform"]),
            str(item["arch"]),
            str(item["purpose"]),
        ),
    )
    keys = {
        (str(item["platform"]), str(item["arch"]), str(item["purpose"]))
        for item in records
    }
    if keys != EXPECTED_FINAL_ASSET_KEYS or len(records) != len(keys):
        raise ValueError(
            "final asset identities must contain exactly the contract v2 keys"
        )
    for item in records:
        if (
            not isinstance(item["filename"], str)
            or not item["filename"]
            or Path(item["filename"]).name != item["filename"]
            or type(item["size"]) is not int
            or item["size"] <= 0
            or not isinstance(item["sha256"], str)
            or _SHA256_PATTERN.fullmatch(item["sha256"]) is None
        ):
            raise ValueError("final asset identity is invalid")
    return records


def validate_upgrade_protocol_registry(
    payload: Any, *, repository_root: Path | None = None
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "contract_version",
        "policy",
        "contracts",
    }:
        raise ValueError(
            "upgrade protocol registry fields do not match contract version 1"
        )
    if payload["contract_version"] != UPGRADE_PROTOCOL_REGISTRY_CONTRACT_VERSION:
        raise ValueError("unsupported upgrade protocol registry contract")
    if payload["policy"] != {
        "current_matrix": "functional_rc_validation",
        "future_release_window": "previous_stable_to_current",
    }:
        raise ValueError("upgrade protocol registry policy is invalid")
    contracts = payload["contracts"]
    if not isinstance(contracts, list):
        raise ValueError("upgrade protocol contracts must be a list")
    actual: dict[str, dict[str, Any]] = {}
    required = {
        "name",
        "medium",
        "producer",
        "consumers",
        "format_versions",
        "support_window",
        "compatibility_fixtures",
        "verification_status",
    }
    for contract in contracts:
        if not isinstance(contract, dict) or set(contract) != required:
            raise ValueError("invalid upgrade protocol contract entry")
        name = contract["name"]
        if not isinstance(name, str) or name in actual:
            raise ValueError("duplicate or invalid upgrade protocol name")
        if tuple(contract["format_versions"]) != EXPECTED_PROTOCOLS.get(name):
            raise ValueError(f"upgrade protocol format versions changed for {name}")
        if not all(
            isinstance(contract[field], str) and contract[field]
            for field in ("medium", "producer", "support_window")
        ):
            raise ValueError("upgrade protocol ownership is incomplete")
        if contract["verification_status"] not in {
            "verified",
            "pending_real_rc17_export",
        }:
            raise ValueError("upgrade protocol verification status is invalid")
        if not isinstance(contract["consumers"], list) or not contract["consumers"]:
            raise ValueError("upgrade protocol consumers are incomplete")
        fixtures = contract["compatibility_fixtures"]
        if not isinstance(fixtures, list) or any(
            not isinstance(path, str) or not path for path in fixtures
        ):
            raise ValueError("upgrade protocol fixtures are invalid")
        if repository_root is not None:
            missing = [
                path for path in fixtures if not (repository_root / path).is_file()
            ]
            if missing:
                raise ValueError(f"upgrade protocol fixture is missing: {missing[0]}")
        actual[name] = contract
    if set(actual) != set(EXPECTED_PROTOCOLS):
        raise ValueError("upgrade protocol registry is incomplete")
    return payload


def validate_upgrade_protocol_registry_ready(payload: Any) -> dict[str, Any]:
    registry = validate_upgrade_protocol_registry(payload)
    pending = [
        contract["name"]
        for contract in registry["contracts"]
        if contract["verification_status"] != "verified"
    ]
    if pending:
        raise ValueError(
            "automatic upgrade protocol verification is incomplete: "
            + ", ".join(sorted(pending))
        )
    return registry


def validate_scenario_result(
    value: Any,
    *,
    expected_name: str,
    expected_source_version: str,
    expected_target_version: str,
) -> dict[str, Any]:
    if (
        not isinstance(expected_source_version, str)
        or not expected_source_version
        or not isinstance(expected_target_version, str)
        or not expected_target_version
    ):
        raise ValueError("upgrade scenario expected versions are invalid")
    if not isinstance(value, dict) or set(value) != {
        "name",
        "status",
        "assertions",
        "observations",
    }:
        raise ValueError("upgrade scenario result fields do not match contract v2")
    assertions = value["assertions"]
    observations = value["observations"]
    if (
        value["name"] != expected_name
        or value["status"] != "passed"
        or not isinstance(assertions, dict)
        or set(assertions) != SCENARIO_ASSERTIONS[expected_name]
        or any(type(result) is not bool or result is not True for result in assertions.values())
        or not isinstance(observations, dict)
        or set(observations) != SCENARIO_OBSERVATION_FIELDS[expected_name]
    ):
        raise ValueError(f"upgrade scenario {expected_name} did not prove its contract")
    if any(
        not isinstance(result, str) or not result
        for name, result in observations.items()
        if name not in {"target_process_start_count", "apply_exit_code"}
    ):
        raise ValueError(f"upgrade scenario {expected_name} observations are invalid")
    if expected_name == "healthy_commit" and (
        observations["source_version_before"] != expected_source_version
        or observations["target_version_after"] != expected_target_version
        or observations["journal_status"] != "committed"
        or observations["health_status"] != "healthy"
    ):
        raise ValueError("healthy commit observations are inconsistent")
    if expected_name == "target_health_failure_rollback" and (
        observations["target_version_observed"] != expected_target_version
        or observations["restored_version"] != expected_source_version
        or observations["journal_status"] != "rolled_back"
        or observations["rollback_marker_status"] != "program_rolled_back"
    ):
        raise ValueError("health rollback observations are inconsistent")
    if expected_name == "retry_after_rollback" and (
        observations["first_transaction_status"] != "rolled_back"
        or observations["retry_transaction_status"] != "committed"
        or observations["final_version"] != expected_target_version
    ):
        raise ValueError("retry observations are inconsistent")
    if expected_name == "apply_failure_before_target_execution" and (
        type(observations["target_process_start_count"]) is not int
        or observations["target_process_start_count"] != 0
        or observations["source_version_after"] != expected_source_version
        or observations["journal_status"] != "aborted_before_switch"
        or type(observations["apply_exit_code"]) is not int
        or observations["apply_exit_code"] == 0
    ):
        raise ValueError("pre-target apply failure observations are inconsistent")
    return value


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
    target_final_assets: Iterable[FinalAssetIdentity],
) -> dict[str, Any]:
    validate_upgrade_matrix(matrix)
    validate_upgrade_matrix_coverage(matrix)
    if _COMMIT_PATTERN.fullmatch(target_commit_sha) is None:
        raise ValueError("target commit SHA must be a full lowercase Git SHA-1")
    normalized_identities = normalize_candidate_identities(
        target_candidate_identities
    )
    normalized_final_assets = normalize_final_asset_identities(target_final_assets)
    target_candidate_digest = candidate_digest(
        CandidateIdentity(**identity) for identity in normalized_identities
    )
    if not isinstance(evidence, dict) or set(evidence) != {
        "contract_version",
        "target",
        "results",
    }:
        raise ValueError("upgrade evidence fields do not match contract version 2")
    if evidence["contract_version"] != UPGRADE_EVIDENCE_CONTRACT_VERSION:
        raise ValueError("unsupported upgrade evidence contract version")
    expected_target = {
        "version": target_version.removeprefix("v"),
        "commit_sha": target_commit_sha,
        "candidate_identities": normalized_identities,
        "candidate_digest": target_candidate_digest,
        "final_assets": normalized_final_assets,
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
        if not isinstance(scenarios, list) or len(scenarios) != len(REQUIRED_SCENARIOS):
            raise ValueError("upgrade evidence scenarios are incomplete or did not pass")
        for scenario, name in zip(scenarios, REQUIRED_SCENARIOS, strict=True):
            validate_scenario_result(
                scenario,
                expected_name=name,
                expected_source_version=source["source_version"],
                expected_target_version=target_version.removeprefix("v"),
            )
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
    final_assets: Iterable[FinalAssetIdentity],
    matrix_path: Path,
    evidence_path: Path,
) -> str | None:
    try:
        from scripts.build.release_metadata import parse_release_metadata
    except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
        from release_metadata import parse_release_metadata

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
        target_final_assets=final_assets,
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
    verify.add_argument(
        "--final-asset",
        action="append",
        default=[],
        help="PLATFORM:ARCH:PURPOSE:FILENAME:SIZE:SHA256",
    )
    verify.add_argument("--matrix", type=Path, required=True)
    verify.add_argument("--evidence", type=Path, required=True)
    readiness = subparsers.add_parser("check-readiness")
    readiness.add_argument("--registry", type=Path, required=True)
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
            final_assets=(
                parse_final_asset_identity(spec) for spec in args.final_asset
            ),
            matrix_path=args.matrix,
            evidence_path=args.evidence,
        )
        if digest is not None:
            print(digest)
        return 0
    if args.command == "check-readiness":
        validate_upgrade_protocol_registry_ready(
            load_json_object(args.registry, label="upgrade protocol registry")
        )
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
