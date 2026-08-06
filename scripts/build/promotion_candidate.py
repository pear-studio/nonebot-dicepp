"""Fail-closed byte/provenance preflight for a successful candidate run.

The caller must first establish through the GitHub Actions API that the artifact
belongs to a successful trusted candidate workflow run.  This preflight verifies
the receipt and sealed bytes; it intentionally does not replay package smoke tests
or replace the canonical release-manifest and upgrade-evidence validators that ran
inside that candidate workflow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TextIO

try:
    from scripts.build.candidate_receipt import (
        RECEIPT_FILENAME,
        sha256_file,
        validate_candidate_receipt,
        validate_manifest_artifact_attestation,
        validate_release_asset_directory,
        validate_upgrade_evidence_target_attestation,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from candidate_receipt import (
        RECEIPT_FILENAME,
        sha256_file,
        validate_candidate_receipt,
        validate_manifest_artifact_attestation,
        validate_release_asset_directory,
        validate_upgrade_evidence_target_attestation,
    )


def verify_promotion_candidate(
    *,
    candidate_root: Path,
    receipt_path: Path,
    repository: str,
    workflow_ref: str,
    run_id: int,
    run_attempt: int,
    artifact_name: str,
    commit_sha: str,
    version: str,
) -> dict[str, Any]:
    """Verify explicit provenance and every byte before any promotion side effect."""

    if receipt_path.name != RECEIPT_FILENAME or receipt_path.is_symlink():
        raise ValueError(
            f"candidate receipt must be a regular file named {RECEIPT_FILENAME}"
        )
    if receipt_path.parent.resolve() != candidate_root.resolve():
        raise ValueError("candidate receipt must be a direct child of candidate root")
    try:
        raw_receipt = receipt_path.read_text(encoding="utf-8")
        payload = json.loads(raw_receipt)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate receipt is unreadable") from exc
    receipt = validate_candidate_receipt(payload)

    expected_target = {
        "version": version,
        "commit_sha": commit_sha,
    }
    if any(receipt["target"][key] != value for key, value in expected_target.items()):
        raise ValueError("candidate receipt target differs from promotion request")
    expected_workflow = {
        "repository": repository,
        "workflow_ref": workflow_ref,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "artifact_name": artifact_name,
    }
    if any(
        receipt["workflow"][key] != value for key, value in expected_workflow.items()
    ):
        raise ValueError("candidate receipt workflow differs from promotion request")

    artifacts = receipt["artifacts"]
    expected_names = {item["filename"] for item in artifacts}
    validate_release_asset_directory(
        candidate_root,
        expected_names | {RECEIPT_FILENAME},
        label="promotion candidate",
    )
    for item in artifacts:
        path = candidate_root / item["filename"]
        if path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            raise ValueError(
                f"promotion candidate bytes differ from receipt: {item['filename']}"
            )

    manifest_path = candidate_root / "dicepp-release.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("release manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise ValueError("release manifest must be a JSON object")
    if (
        manifest.get("version") != version
        or manifest.get("automatic_upgrade")
        is not receipt["target"]["automatic_upgrade"]
    ):
        raise ValueError("release manifest target differs from candidate receipt")
    validate_manifest_artifact_attestation(manifest, artifacts, version)
    if receipt["target"]["automatic_upgrade"]:
        validate_upgrade_evidence_target_attestation(
            candidate_root / "dicepp-upgrade-evidence.json",
            version=version,
            commit_sha=commit_sha,
            candidate_identities=receipt["build"]["candidate_identities"],
        )
    return receipt


def promotion_outputs(receipt: dict[str, Any], receipt_path: Path) -> dict[str, str]:
    containers = {item["role"]: item for item in receipt["containers"]}
    target = receipt["target"]
    workflow = receipt["workflow"]
    outputs = {
        "tag": target["tag"],
        "version": target["version"],
        "commit_sha": target["commit_sha"],
        "run_id": str(workflow["run_id"]),
        "run_attempt": str(workflow["run_attempt"]),
        "artifact_name": workflow["artifact_name"],
        "automatic_upgrade": str(target["automatic_upgrade"]).lower(),
        "is_prerelease": str(target["is_prerelease"]).lower(),
        "receipt_sha256": sha256_file(receipt_path),
    }
    for role in ("runtime", "dashboard"):
        outputs[f"{role}_candidate_ref"] = containers[role]["candidate_ref"]
        outputs[f"{role}_manifest_digest"] = containers[role]["manifest_digest"]
        outputs[f"{role}_image_id"] = containers[role]["image_id"]
    return outputs


def write_github_outputs(outputs: dict[str, str], stream: TextIO) -> None:
    for key, value in outputs.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"GitHub output {key} contains a newline")
        stream.write(f"{key}={value}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--github-output", type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    receipt = verify_promotion_candidate(
        candidate_root=args.candidate_root,
        receipt_path=args.receipt,
        repository=args.repository,
        workflow_ref=args.workflow_ref,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        artifact_name=args.artifact_name,
        commit_sha=args.commit_sha,
        version=args.version,
    )
    outputs = promotion_outputs(receipt, args.receipt)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            write_github_outputs(outputs, stream)
    print(json.dumps(outputs, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
