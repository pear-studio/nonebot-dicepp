from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build.upgrade_evidence import (
    LINUX_REQUIRED_SCENARIOS,
    WINDOWS_REQUIRED_SCENARIOS,
    CandidateIdentity,
    FinalAssetIdentity,
    candidate_digest,
    normalize_candidate_identities,
    required_scenarios_for,
    source_scenarios_for,
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
            {
                "platform": "windows",
                "arch": "amd64",
                "scenarios": list(WINDOWS_REQUIRED_SCENARIOS),
            },
            {
                "platform": "linux",
                "arch": "amd64",
                "scenarios": list(LINUX_REQUIRED_SCENARIOS),
            },
        ],
        "supported_sources": [
            {
                "platform": "windows",
                "arch": "amd64",
                "source_version": "3.0.0rc19",
                "assets": [
                    {
                        "purpose": "portable",
                        "name": "source-windows-portable.zip",
                        "url": "https://example.invalid/source-windows-portable.zip",
                        "sha256": SOURCE_SHA,
                    },
                    {
                        "purpose": "velopack-bundle",
                        "name": "velopack.win-x64.zip",
                        "url": "https://example.invalid/velopack.win-x64.zip",
                        "sha256": "a" * 64,
                    },
                ],
            },
            {
                "platform": "linux",
                "arch": "amd64",
                "source_version": "3.0.0rc19",
                "scenarios": list(LINUX_REQUIRED_SCENARIOS),
                "assets": [
                    {
                        "purpose": "linux-bundle",
                        "name": "source-linux.zip",
                        "url": "https://example.invalid/source-linux.zip",
                        "sha256": SOURCE_SHA,
                    }
                ],
            },
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
                "rollback_marker_status": "restored",
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
                "journal_status": "rolled_back",
                "apply_exit_code": 17,
            },
        },
        "manual_restore_after_target_failure": {
            "assertions": {
                "target_failure_observed": True,
                "recovery_material_preserved": True,
                "manual_restore_invoked": True,
                "whole_program_tree_restored": True,
                "data_restored": True,
                "source_restarted": True,
                "journal_manually_restored": True,
            },
            "observations": {
                "target_version_observed": "3.1.0",
                "restored_version": "3.0.0rc19",
                "journal_status": "manually_restored",
                "recovery_trigger": "manual",
                "program_restore_mode": "whole_current_directory",
            },
        },
        "manager_handoff_commit": {
            "assertions": {
                "manager_handoff_completed": True,
                "target_containers_started": True,
                "local_health_passed": True,
                "commit_decision_written": True,
            },
            "observations": {
                "source_version_before": "3.0.0rc19",
                "target_version_after": "3.1.0",
                "handoff_protocol": "1",
                "journal_status": "committed",
                "health_status": "healthy",
            },
        },
        "manager_handoff_rollback": {
            "assertions": {
                "target_manager_failed": True,
                "source_manager_restored": True,
                "program_restored": True,
                "data_restored": True,
                "dashboard_db_restored": True,
                "source_restarted": True,
                "journal_rolled_back": True,
            },
            "observations": {
                "target_version_observed": "3.1.0",
                "restored_version": "3.0.0rc19",
                "result_status": "source-restored",
                "journal_status": "rolled_back",
                "rollback_marker_status": "restored",
            },
        },
        "manager_handoff_commit_crash_window": {
            "assertions": {
                "crash_before_commit_allowed_source_restore": True,
                "crash_after_commit_never_rolled_back": True,
                "recovery_material_preserved": True,
                "terminal_state_recorded": True,
            },
            "observations": {
                "crash_before_commit_final_state": "source_restored",
                "crash_after_commit_final_state": "cleanup_pending",
                "decision_status": "committed",
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
                    {
                        "purpose": asset["purpose"],
                        "name": asset["name"],
                        "sha256": asset["sha256"],
                    }
                    for asset in source["assets"]
                ],
                "scenarios": [
                    {
                        "name": name,
                        "status": "passed",
                        **scenario_records[name],
                        "observations": scenario_records[name]["observations"],
                    }
                    for name in source_scenarios_for(matrix, source)
                ],
            }
            for source in matrix["supported_sources"]
        ],
    }


def _notes(automatic_upgrade: str) -> str:
    return f"""# v3.1.0

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


def test_linux_health_rollback_rejects_legacy_guard_status() -> None:
    matrix = _matrix()
    evidence = _evidence(matrix)
    result = next(
        item for item in evidence["results"] if item["platform"] == "linux"
    )
    result["scenarios"][1]["observations"][
        "rollback_marker_status"
    ] = "program_rolled_back"

    with pytest.raises(ValueError, match="health rollback observations"):
        validate_upgrade_evidence(
            evidence,
            matrix=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_candidate_identities=CANDIDATES,
            target_final_assets=FINAL_ASSETS,
        )


def test_windows_manual_restore_requires_whole_current_directory() -> None:
    matrix = _matrix()
    evidence = _evidence(matrix)
    result = next(
        item for item in evidence["results"] if item["platform"] == "windows"
    )
    result["scenarios"][1]["observations"]["program_restore_mode"] = "file_merge"

    with pytest.raises(ValueError, match="manual restore observations"):
        validate_upgrade_evidence(
            evidence,
            matrix=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_candidate_identities=CANDIDATES,
            target_final_assets=FINAL_ASSETS,
        )


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
            lambda evidence: evidence["results"][1]["scenarios"][3].update(
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
    ("result_index", "scenario_index", "observation"),
    [
        (0, 0, "source_version_before"),
        (0, 0, "target_version_after"),
        (0, 1, "target_version_observed"),
        (0, 1, "restored_version"),
        (1, 2, "final_version"),
        (1, 3, "source_version_after"),
    ],
)
def test_evidence_rejects_scenario_versions_outside_the_matrix_cell(
    result_index: int,
    scenario_index: int,
    observation: str,
) -> None:
    matrix = _matrix()
    evidence = _evidence(matrix)
    evidence["results"][result_index]["scenarios"][scenario_index]["observations"][
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


def test_tracked_registry_is_ready_with_previous_release_sources() -> None:
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
    validated_matrix = validate_upgrade_matrix(matrix)
    assert required_scenarios_for(
        validated_matrix, platform="windows", arch="amd64"
    ) == WINDOWS_REQUIRED_SCENARIOS
    assert required_scenarios_for(
        validated_matrix, platform="linux", arch="amd64"
    ) == LINUX_REQUIRED_SCENARIOS
    validate_upgrade_matrix_coverage(validated_matrix)

    assert {
        (
            row["platform"],
            row["source_version"],
            tuple((asset["purpose"], asset["sha256"]) for asset in row["assets"]),
        )
        for row in matrix["supported_sources"]
    } == {
        (
            "windows",
            "3.0.0rc21",
            (
                (
                    "portable",
                    "23ac4ad653782b76b1a1239ebd52765eba9090d3e645d34ab9f65886a868e028",
                ),
                (
                    "velopack-bundle",
                    "d035d0a9a8cc85bc22732e9980da46a0651bb4201cdb5c57249018fb14cc5be4",
                ),
            ),
        ),
        (
            "linux",
            "3.0.0rc21",
            (
                (
                    "linux-bundle",
                    "9abc2fe939082be095bcf81fda03308fab3fd211a7e368ca45f679227b161413",
                ),
            ),
        ),
    }
    assert validate_upgrade_protocol_registry_ready(registry) == registry


def test_release_evidence_rejects_validation_only_legacy_source_subset() -> None:
    matrix = _matrix()
    legacy_linux = next(
        source
        for source in matrix["supported_sources"]
        if source["platform"] == "linux"
    )
    classic = list(LINUX_REQUIRED_SCENARIOS)[:4]
    legacy_linux["scenarios"] = classic
    evidence = _evidence(matrix)

    validated_matrix = validate_upgrade_matrix(matrix)
    assert source_scenarios_for(validated_matrix, legacy_linux) == tuple(classic)

    with pytest.raises(ValueError) as exc_info:
        validate_upgrade_evidence(
            evidence,
            matrix=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_candidate_identities=CANDIDATES,
            target_final_assets=FINAL_ASSETS,
        )

    message = str(exc_info.value)
    assert "linux/amd64 source 3.0.0rc19" in message
    assert "incomplete for release evidence" in message
    for scenario in LINUX_REQUIRED_SCENARIOS[4:]:
        assert scenario in message


def test_automatic_release_rejects_validation_only_subset_before_evidence_read(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "notes.md"
    matrix_path = tmp_path / "matrix.json"
    notes.write_text(_notes("yes"), encoding="utf-8")
    matrix = _matrix()
    legacy_linux = next(
        source
        for source in matrix["supported_sources"]
        if source["platform"] == "linux"
    )
    legacy_linux["scenarios"] = list(LINUX_REQUIRED_SCENARIOS)[:4]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="source scenarios are incomplete for release evidence",
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


def test_source_row_scenarios_cannot_exceed_its_platform_requirement() -> None:
    matrix = _matrix()
    legacy_windows = next(
        source
        for source in matrix["supported_sources"]
        if source["platform"] == "windows"
    )
    legacy_windows["scenarios"] = [
        *WINDOWS_REQUIRED_SCENARIOS,
        "manager_handoff_commit",
    ]

    with pytest.raises(
        ValueError, match="scenarios exceed its required platform set"
    ):
        validate_upgrade_matrix(matrix)


def test_tracked_registry_declares_linux_manager_handoff_protocol() -> None:
    root = find_repository_root(Path(__file__))
    registry = json.loads(
        (root / "scripts/build/upgrade_protocol_registry.json").read_text(
            encoding="utf-8"
        )
    )

    validated = validate_upgrade_protocol_registry(registry, repository_root=root)
    contract = next(
        item
        for item in validated["contracts"]
        if item["name"] == "linux_manager_handoff"
    )

    assert contract["medium"] == "json-files-in-recovery-directory"
    assert contract["producer"] == "LinuxBundleUpgradeAdapter"
    assert contract["format_versions"] == [1]
    assert contract["verification_status"] == "verified"
    assert contract["support_window"] == "previous published handoff release to current"
    validate_upgrade_protocol_registry_ready(registry)
