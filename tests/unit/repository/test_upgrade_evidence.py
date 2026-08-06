from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build.upgrade_evidence import (
    CandidateIdentity,
    FinalAssetIdentity,
    REQUIRED_SCENARIOS,
    candidate_digest,
    normalize_candidate_identities,
    validate_upgrade_evidence,
    validate_upgrade_matrix,
    validate_upgrade_matrix_coverage,
    validate_upgrade_protocol_registry,
    validate_upgrade_protocol_registry_ready,
    verify_release,
)
from tests.support.paths import find_repository_root


COMMIT_SHA = "1" * 40
SOURCE_SHA = "2" * 64
CANDIDATES = [
    CandidateIdentity("linux", "runtime-manifest", "3" * 64),
    CandidateIdentity("linux", "dashboard-manifest", "4" * 64),
    CandidateIdentity("windows", "package-tree", "5" * 64),
]
FINAL_ASSETS = [
    FinalAssetIdentity(
        "linux", "amd64", "linux-bundle", "target-linux.zip", 101, "6" * 64
    ),
    FinalAssetIdentity(
        "windows", "amd64", "portable", "target-portable.zip", 102, "7" * 64
    ),
    FinalAssetIdentity("windows", "amd64", "setup", "target-setup.exe", 103, "8" * 64),
    FinalAssetIdentity(
        "windows",
        "amd64",
        "velopack-bundle",
        "velopack.win-x64.zip",
        104,
        "9" * 64,
    ),
]


def _matrix() -> dict:
    return {
        "contract_version": 1,
        "required_platforms": [
            {"platform": "windows", "arch": "amd64"},
            {"platform": "linux", "arch": "amd64"},
        ],
        "required_scenarios": list(REQUIRED_SCENARIOS),
        "supported_sources": [
            {
                "platform": platform,
                "arch": "amd64",
                "source_version": "3.0.0rc19",
                "assets": [
                    {
                        "name": asset_name,
                        "url": f"https://example.invalid/{asset_name}",
                        "sha256": SOURCE_SHA,
                    }
                ],
            }
            for platform, asset_name in (
                ("windows", "DicePP-v3.0.0rc19-win64-Portable.zip"),
                ("linux", "DicePP-v3.0.0rc19-linux-amd64.zip"),
            )
        ],
    }


def _evidence(matrix: dict) -> dict:
    scenario_records = {
        "healthy_commit": {
            "assertions": {
                "source_started": True,
                "target_started": True,
                "local_health_passed": True,
                "journal_committed": True,
            },
            "observations": {
                "source_version_before": "3.0.0rc19",
                "target_version_after": "3.1.0",
                "journal_status": "committed",
                "health_status": "healthy",
            },
        },
        "target_health_failure_rollback": {
            "assertions": {
                "target_executed": True,
                "health_failure_injected": True,
                "program_restored": True,
                "data_restored": True,
                "source_restarted": True,
                "journal_rolled_back": True,
            },
            "observations": {
                "target_version_observed": "3.1.0",
                "restored_version": "3.0.0rc19",
                "journal_status": "rolled_back",
                "rollback_marker_status": "program_rolled_back",
            },
        },
        "retry_after_rollback": {
            "assertions": {
                "prior_rollback_observed": True,
                "retry_started_same_instance": True,
                "target_started": True,
                "journal_committed": True,
            },
            "observations": {
                "first_transaction_status": "rolled_back",
                "retry_transaction_status": "committed",
                "final_version": "3.1.0",
            },
        },
        "apply_failure_before_target_execution": {
            "assertions": {
                "apply_failure_injected": True,
                "target_never_executed": True,
                "source_remained_or_restored": True,
                "no_target_migration": True,
                "terminal_state_recorded": True,
            },
            "observations": {
                "target_process_start_count": 0,
                "source_version_after": "3.0.0rc19",
                "journal_status": "aborted_before_switch",
                "apply_exit_code": 17,
            },
        },
    }
    return {
        "contract_version": 2,
        "target": {
            "version": "3.1.0",
            "commit_sha": COMMIT_SHA,
            "candidate_identities": normalize_candidate_identities(CANDIDATES),
            "candidate_digest": candidate_digest(CANDIDATES),
            "final_assets": [
                {
                    "platform": item.platform,
                    "arch": item.arch,
                    "purpose": item.purpose,
                    "filename": item.filename,
                    "size": item.size,
                    "sha256": item.sha256,
                }
                for item in FINAL_ASSETS
            ],
        },
        "results": [
            {
                "platform": source["platform"],
                "arch": source["arch"],
                "source_version": source["source_version"],
                "source_assets": [
                    {"name": asset["name"], "sha256": asset["sha256"]}
                    for asset in source["assets"]
                ],
                "scenarios": [
                    {
                        "name": name,
                        "status": "passed",
                        **scenario_records[name],
                    }
                    for name in REQUIRED_SCENARIOS
                ],
            }
            for source in matrix["supported_sources"]
        ],
    }


def _notes(automatic_upgrade: str) -> str:
    return f"""# v3.1.0

- 镜像: ghcr.io/pear-studio/nonebot-dicepp:v3.1.0
- Windows: DicePP-v3.1.0-win64-Portable.zip
- 数据变更: no
- 配置变更: no
- 变更范围: runtime
- 自动升级: {automatic_upgrade}
- 最低 Manager 版本: 1.0

## Changed
"""


def test_complete_evidence_binds_commit_candidates_sources_and_scenarios() -> None:
    matrix = _matrix()

    validated = validate_upgrade_evidence(
        _evidence(matrix),
        matrix=matrix,
        target_version="v3.1.0",
        target_commit_sha=COMMIT_SHA,
        target_candidate_identities=CANDIDATES,
        target_final_assets=FINAL_ASSETS,
    )

    assert validated["target"]["commit_sha"] == COMMIT_SHA
    assert {result["platform"] for result in validated["results"]} == {
        "windows",
        "linux",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda evidence: evidence["target"].update(commit_sha="9" * 40),
            "target does not match",
        ),
        (
            lambda evidence: evidence["target"]["candidate_identities"][0].update(
                sha256="8" * 64
            ),
            "target does not match",
        ),
        (
            lambda evidence: evidence["results"][0]["source_assets"][0].update(
                sha256="9" * 64
            ),
            "asset digests differ",
        ),
        (
            lambda evidence: evidence["results"][0]["scenarios"][3].update(
                status="failed"
            ),
            "did not prove its contract",
        ),
    ],
)
def test_evidence_rejects_identity_or_scenario_mismatch(
    mutation,
    message: str,
) -> None:
    matrix = _matrix()
    evidence = _evidence(matrix)
    mutation(evidence)

    with pytest.raises(ValueError, match=message):
        validate_upgrade_evidence(
            evidence,
            matrix=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_candidate_identities=CANDIDATES,
            target_final_assets=FINAL_ASSETS,
        )


@pytest.mark.parametrize(
    ("scenario_index", "observation"),
    [
        (0, "source_version_before"),
        (0, "target_version_after"),
        (1, "target_version_observed"),
        (1, "restored_version"),
        (2, "final_version"),
        (3, "source_version_after"),
    ],
)
def test_evidence_rejects_scenario_versions_outside_the_matrix_cell(
    scenario_index: int,
    observation: str,
) -> None:
    matrix = _matrix()
    evidence = _evidence(matrix)
    evidence["results"][0]["scenarios"][scenario_index]["observations"][
        observation
    ] = "totally-wrong"

    with pytest.raises(ValueError, match="observations are inconsistent"):
        validate_upgrade_evidence(
            evidence,
            matrix=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_candidate_identities=CANDIDATES,
            target_final_assets=FINAL_ASSETS,
        )


def test_required_platform_without_pinned_source_fails_closed() -> None:
    matrix = _matrix()
    matrix["supported_sources"] = matrix["supported_sources"][:1]
    evidence = _evidence(matrix)

    with pytest.raises(ValueError, match="no supported source for: linux/amd64"):
        validate_upgrade_evidence(
            evidence,
            matrix=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_candidate_identities=CANDIDATES,
            target_final_assets=FINAL_ASSETS,
        )


def test_matrix_cannot_redefine_complete_coverage_by_removing_linux() -> None:
    matrix = _matrix()
    matrix["required_platforms"] = matrix["required_platforms"][:1]
    matrix["supported_sources"] = matrix["supported_sources"][:1]
    evidence = _evidence(matrix)

    with pytest.raises(ValueError, match="must match contract version 1"):
        validate_upgrade_evidence(
            evidence,
            matrix=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_candidate_identities=CANDIDATES,
            target_final_assets=FINAL_ASSETS,
        )


@pytest.mark.parametrize(
    "identities",
    [
        CANDIDATES[:-1],
        [*CANDIDATES, CandidateIdentity("linux", "extra", "6" * 64)],
        [
            CANDIDATES[0],
            CandidateIdentity("linux", "renamed-dashboard", "4" * 64),
            CANDIDATES[2],
        ],
    ],
)
def test_candidate_identity_contract_rejects_missing_extra_or_renamed_keys(
    identities,
) -> None:
    with pytest.raises(ValueError, match="exactly the contract v1 keys"):
        normalize_candidate_identities(identities)


def test_release_with_automatic_upgrade_no_needs_no_evidence(tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text(_notes("no"), encoding="utf-8")

    assert verify_release(
        release_notes=notes,
        version="3.1.0",
        commit_sha="not-needed",
        candidate_identities=[],
        final_assets=[],
        matrix_path=tmp_path / "missing-matrix.json",
        evidence_path=tmp_path / "missing-evidence.json",
    ) is None


def test_release_with_automatic_upgrade_yes_requires_real_matrix_evidence(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "notes.md"
    matrix_path = tmp_path / "matrix.json"
    evidence_path = tmp_path / "evidence.json"
    notes.write_text(_notes("yes"), encoding="utf-8")
    matrix = _matrix()
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    evidence_path.write_text(json.dumps(_evidence(matrix)), encoding="utf-8")

    assert verify_release(
        release_notes=notes,
        version="3.1.0",
        commit_sha=COMMIT_SHA,
        candidate_identities=CANDIDATES,
        final_assets=FINAL_ASSETS,
        matrix_path=matrix_path,
        evidence_path=evidence_path,
    ) == candidate_digest(CANDIDATES)


def test_release_with_empty_matrix_reports_source_gap_before_missing_evidence(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "notes.md"
    matrix_path = tmp_path / "matrix.json"
    notes.write_text(_notes("yes"), encoding="utf-8")
    matrix = _matrix()
    matrix["supported_sources"] = []
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="no supported source for: linux/amd64, windows/amd64",
    ):
        verify_release(
            release_notes=notes,
            version="3.1.0",
            commit_sha=COMMIT_SHA,
            candidate_identities=CANDIDATES,
            final_assets=FINAL_ASSETS,
            matrix_path=matrix_path,
            evidence_path=tmp_path / "missing-evidence.json",
        )


def test_tracked_registry_and_functional_rc_matrix_are_complete() -> None:
    root = find_repository_root(Path(__file__))
    registry = json.loads(
        (root / "scripts/build/upgrade_protocol_registry.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = json.loads(
        (root / "scripts/build/upgrade_matrix.json").read_text(encoding="utf-8")
    )

    validate_upgrade_protocol_registry(registry, repository_root=root)
    validate_upgrade_matrix_coverage(validate_upgrade_matrix(matrix))

    assert {
        (row["platform"], row["source_version"], row["assets"][0]["sha256"])
        for row in matrix["supported_sources"]
    } == {
        (
            "windows",
            "3.0.0rc17",
            "a5aec34c8ffd35e409bdaddcf4eee3bc6b3142ad496005a8e44769be3d488b13",
        ),
        (
            "windows",
            "3.0.0rc19",
            "8caa1e9ad82edb5b6c486ba997a57cfe2f12e08a5841e735325f06276d188106",
        ),
        (
            "linux",
            "3.0.0rc19",
            "2d1cc5452112abab31baba9e9d4d276a344bf8534b0c2098b35078d56e4d5dd6",
        ),
    }
    with pytest.raises(
        ValueError,
        match="verification is incomplete: manager_upgrade_journal",
    ):
        validate_upgrade_protocol_registry_ready(registry)
