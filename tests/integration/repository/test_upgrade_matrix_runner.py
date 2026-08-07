from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import scripts.build.upgrade_matrix_runner as matrix_runner
from scripts.build.upgrade_evidence import (
    LINUX_REQUIRED_SCENARIOS,
    SCENARIO_ASSERTIONS,
    WINDOWS_REQUIRED_SCENARIOS,
    CandidateIdentity,
    required_scenarios_for,
    validate_upgrade_matrix,
    validate_upgrade_matrix_coverage,
    validate_upgrade_matrix_platform_coverage,
    validate_upgrade_protocol_registry,
    validate_upgrade_protocol_registry_ready,
)
from scripts.build.upgrade_matrix_runner import assemble_evidence, run_platform


COMMIT_SHA = "1" * 40
CANDIDATES = [
    CandidateIdentity("linux", "runtime-manifest", "2" * 64),
    CandidateIdentity("linux", "dashboard-manifest", "3" * 64),
    CandidateIdentity("windows", "package-tree", "4" * 64),
]
ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)


def test_transition_registry_allows_both_platform_validations_but_blocks_release_evidence() -> None:
    registry = json.loads(
        (ROOT / "scripts/build/upgrade_protocol_registry.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = validate_upgrade_matrix(
        json.loads(
            (ROOT / "scripts/build/upgrade_matrix.json").read_text(
                encoding="utf-8"
            )
        )
    )

    validate_upgrade_protocol_registry(registry, repository_root=ROOT)
    validate_upgrade_matrix_platform_coverage(
        matrix, platform="linux", arch="amd64"
    )
    validate_upgrade_matrix_platform_coverage(
        matrix, platform="windows", arch="amd64"
    )
    assert required_scenarios_for(
        matrix, platform="windows", arch="amd64"
    ) == WINDOWS_REQUIRED_SCENARIOS
    assert required_scenarios_for(
        matrix, platform="linux", arch="amd64"
    ) == LINUX_REQUIRED_SCENARIOS
    windows_contract = next(
        item
        for item in registry["contracts"]
        if item["name"] == "windows_current_backup_manual_restore"
    )
    assert windows_contract["producer"] == "SimpleWindowsVelopackUpgradeAdapter"
    bundle_contract = next(
        item
        for item in registry["contracts"]
        if item["name"] == "windows_bundle_manifest"
    )
    assert bundle_contract["consumers"] == [
        "ReleaseManager",
        "SimpleWindowsVelopackUpgradeAdapter",
    ]
    with pytest.raises(ValueError, match="windows_current_backup_manual_restore"):
        validate_upgrade_protocol_registry_ready(registry)
    validate_upgrade_matrix_coverage(matrix)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _prepare(tmp_path: Path) -> tuple[Path, Path, dict[str, list[str]]]:
    source_payloads = {
        "windows": [
            ("portable", "source-windows.zip", b"old-windows"),
            ("velopack-bundle", "source-velopack.zip", b"old-velopack"),
        ],
        "linux": [("linux-bundle", "source-linux.zip", b"old-linux")],
    }
    matrix = {
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
                "platform": platform,
                "arch": "amd64",
                "source_version": "3.0.0rc19",
                "assets": [
                    {
                        "purpose": purpose,
                        "name": name,
                        "url": f"https://example.invalid/{name}",
                        "sha256": _sha(payload),
                    }
                    for purpose, name, payload in records
                ],
            }
            for platform, records in source_payloads.items()
        ],
    }
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    cache = tmp_path / "cache"
    for records in source_payloads.values():
        for _purpose, name, payload in records:
            path = cache / "3.0.0rc19" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    assets: dict[str, list[str]] = {"windows": [], "linux": []}
    for platform, purpose, filename in (
        ("linux", "linux-bundle", "target-linux.zip"),
        ("windows", "portable", "target-portable.zip"),
        ("windows", "setup", "target-setup.exe"),
        ("windows", "velopack-bundle", "velopack.win-x64.zip"),
    ):
        path = tmp_path / "target" / platform / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"final-{purpose}".encode())
        assets[platform].append(f"{purpose}={path}")
    return matrix_path, cache, assets


def _fake_harness_run(command, *, cwd, check):
    del check
    context_path = Path(command[command.index("--context") + 1])
    output_path = Path(command[command.index("--output") + 1])
    if not context_path.is_absolute():
        context_path = Path(cwd) / context_path
    if not output_path.is_absolute():
        output_path = Path(cwd) / output_path
    context = json.loads(context_path.read_text(encoding="utf-8"))
    scenario = context["scenario"]
    failed = os.environ.get("DICEPP_TEST_FAIL_SCENARIO") == scenario
    observations = {
        "healthy_commit": {
            "source_version_before": context["source_version"],
            "target_version_after": context["target_version"],
            "journal_status": "committed",
            "health_status": "healthy",
        },
        "target_health_failure_rollback": {
            "target_version_observed": context["target_version"],
            "restored_version": context["source_version"],
            "journal_status": "rolled_back",
            "rollback_marker_status": "restored",
        },
        "retry_after_rollback": {
            "first_transaction_status": "rolled_back",
            "retry_transaction_status": "committed",
            "final_version": context["target_version"],
        },
        "apply_failure_before_target_execution": {
            "target_process_start_count": 0,
            "source_version_after": context["source_version"],
            "journal_status": "rolled_back",
            "apply_exit_code": 17,
        },
        "manual_restore_after_target_failure": {
            "target_version_observed": context["target_version"],
            "restored_version": context["source_version"],
            "journal_status": "manually_restored",
            "recovery_trigger": "manual",
            "program_restore_mode": "whole_current_directory",
        },
        "manager_handoff_commit": {
            "source_version_before": context["source_version"],
            "target_version_after": context["target_version"],
            "handoff_protocol": "1",
            "journal_status": "committed",
            "health_status": "healthy",
        },
        "manager_handoff_rollback": {
            "target_version_observed": context["target_version"],
            "restored_version": context["source_version"],
            "result_status": "source-restored",
            "journal_status": "rolled_back",
            "rollback_marker_status": "restored",
        },
        "manager_handoff_commit_crash_window": {
            "crash_before_commit_final_state": "source_restored",
            "crash_after_commit_final_state": "cleanup_pending",
            "decision_status": "committed",
        },
    }
    wrong_version_scenario = os.environ.get("DICEPP_TEST_WRONG_VERSION_SCENARIO")
    if wrong_version_scenario == scenario:
        version_observation = {
            "healthy_commit": "target_version_after",
            "target_health_failure_rollback": "restored_version",
            "retry_after_rollback": "final_version",
            "apply_failure_before_target_execution": "source_version_after",
            "manual_restore_after_target_failure": "restored_version",
        }[scenario]
        observations[scenario][version_observation] = "totally-wrong"
    output_path.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "platform": context["platform"],
                "arch": context["arch"],
                "source_version": context["source_version"],
                "target_version": context["target_version"],
                "scenario": scenario,
                "status": "failed" if failed else "passed",
                "assertions": {
                    name: not failed for name in SCENARIO_ASSERTIONS[scenario]
                },
                "observations": observations[scenario],
            }
        ),
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(command, 0)


def test_real_harness_protocol_assembles_source_scenarios_and_final_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(matrix_runner.subprocess, "run", _fake_harness_run)
    matrix, cache, assets = _prepare(tmp_path)
    fragments = []
    for platform in ("windows", "linux"):
        output = tmp_path / f"{platform}-result.json"
        run_platform(
            matrix_path=matrix,
            platform=platform,
            arch="amd64",
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_asset_specs=assets[platform],
            source_cache=cache,
            work_root=tmp_path / f"{platform}-work",
            output=output,
        )
        fragments.append(output)

    evidence = assemble_evidence(
        matrix_path=matrix,
        target_version="3.1.0",
        target_commit_sha=COMMIT_SHA,
        candidates=CANDIDATES,
        platform_results=fragments,
        output=tmp_path / "evidence.json",
    )

    assert evidence["contract_version"] == 2
    assert {
        (item["platform"], item["purpose"], item["sha256"])
        for item in evidence["target"]["final_assets"]
    } == {
        (
            platform,
            spec.split("=", 1)[0],
            hashlib.sha256(Path(spec.split("=", 1)[1]).read_bytes()).hexdigest(),
        )
        for platform, specs in assets.items()
        for spec in specs
    }
    assert {
        (result["platform"], result["source_version"])
        for result in evidence["results"]
    } == {("windows", "3.0.0rc19"), ("linux", "3.0.0rc19")}
    matrix_payload = json.loads(matrix.read_text(encoding="utf-8"))
    assert all(
        [scenario["name"] for scenario in result["scenarios"]]
        == list(
            required_scenarios_for(
                matrix_payload,
                platform=result["platform"],
                arch=result["arch"],
            )
        )
        and all(scenario["status"] == "passed" for scenario in result["scenarios"])
        and all(scenario["assertions"] for scenario in result["scenarios"])
        and all(scenario["observations"] for scenario in result["scenarios"])
        for result in evidence["results"]
    )


def test_validation_runner_allows_legacy_subset_but_assembler_rejects_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    matrix_path, cache, assets = _prepare(tmp_path)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    for source in matrix["supported_sources"]:
        if source["platform"] == "linux":
            source["scenarios"] = list(LINUX_REQUIRED_SCENARIOS)[:4]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    monkeypatch.setattr(matrix_runner.subprocess, "run", _fake_harness_run)
    monkeypatch.chdir(tmp_path)

    output = tmp_path / "linux-result.json"
    run_platform(
        matrix_path=matrix_path,
        platform="linux",
        arch="amd64",
        target_version="3.1.0",
        target_commit_sha=COMMIT_SHA,
        target_asset_specs=assets["linux"],
        source_cache=cache,
        work_root=Path("linux-work"),
        output=output,
    )

    fragment = json.loads(output.read_text(encoding="utf-8"))
    linux_result = next(
        result
        for result in fragment["results"]
        if result["platform"] == "linux"
    )
    assert [scenario["name"] for scenario in linux_result["scenarios"]] == list(
        LINUX_REQUIRED_SCENARIOS
    )[:4]

    windows_output = tmp_path / "windows-result.json"
    run_platform(
        matrix_path=matrix_path,
        platform="windows",
        arch="amd64",
        target_version="3.1.0",
        target_commit_sha=COMMIT_SHA,
        target_asset_specs=assets["windows"],
        source_cache=cache,
        work_root=tmp_path / "windows-work",
        output=windows_output,
    )

    evidence_output = tmp_path / "evidence.json"
    with pytest.raises(
        ValueError,
        match="source scenarios are incomplete for release evidence",
    ):
        assemble_evidence(
            matrix_path=matrix_path,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            candidates=CANDIDATES,
            platform_results=[windows_output, output],
            output=evidence_output,
        )
    assert not evidence_output.exists()


def test_failed_behavioral_assertion_cannot_be_recorded_as_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    matrix, cache, assets = _prepare(tmp_path)
    monkeypatch.setattr(matrix_runner.subprocess, "run", _fake_harness_run)
    monkeypatch.setenv("DICEPP_TEST_FAIL_SCENARIO", "retry_after_rollback")

    with pytest.raises(ValueError, match="did not prove its contract"):
        run_platform(
            matrix_path=matrix,
            platform="linux",
            arch="amd64",
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_asset_specs=assets["linux"],
            source_cache=cache,
            work_root=tmp_path / "linux-work",
            output=tmp_path / "result.json",
        )


def test_runner_rejects_same_or_newer_source_before_harness_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    matrix, cache, assets = _prepare(tmp_path)

    def must_not_run(*args, **kwargs):
        raise AssertionError("harness must not run for a non-upgrade version pair")

    monkeypatch.setattr(matrix_runner.subprocess, "run", must_not_run)
    with pytest.raises(ValueError, match="must be older"):
        run_platform(
            matrix_path=matrix,
            platform="windows",
            arch="amd64",
            target_version="3.0.0rc19",
            target_commit_sha=COMMIT_SHA,
            target_asset_specs=assets["windows"],
            source_cache=cache,
            work_root=tmp_path / "windows-work",
            output=tmp_path / "result.json",
        )


def test_runner_rejects_harness_version_observation_outside_matrix_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    matrix, cache, assets = _prepare(tmp_path)
    monkeypatch.setattr(matrix_runner.subprocess, "run", _fake_harness_run)
    monkeypatch.setenv(
        "DICEPP_TEST_WRONG_VERSION_SCENARIO",
        "target_health_failure_rollback",
    )

    with pytest.raises(ValueError, match="observations are inconsistent"):
        run_platform(
            matrix_path=matrix,
            platform="linux",
            arch="amd64",
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_asset_specs=assets["linux"],
            source_cache=cache,
            work_root=tmp_path / "linux-work",
            output=tmp_path / "result.json",
        )


def test_assembler_rejects_tampered_version_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(matrix_runner.subprocess, "run", _fake_harness_run)
    matrix, cache, assets = _prepare(tmp_path)
    fragments = []
    for platform in ("windows", "linux"):
        output = tmp_path / f"{platform}-result.json"
        run_platform(
            matrix_path=matrix,
            platform=platform,
            arch="amd64",
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_asset_specs=assets[platform],
            source_cache=cache,
            work_root=tmp_path / f"{platform}-work",
            output=output,
        )
        fragments.append(output)
    fragment = json.loads(fragments[1].read_text(encoding="utf-8"))
    fragment["results"][0]["scenarios"][2]["observations"][
        "final_version"
    ] = "totally-wrong"
    fragments[1].write_text(json.dumps(fragment), encoding="utf-8")

    with pytest.raises(ValueError, match="retry observations are inconsistent"):
        assemble_evidence(
            matrix_path=matrix,
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            candidates=CANDIDATES,
            platform_results=fragments,
            output=tmp_path / "evidence.json",
        )


def test_tracked_platform_entrypoint_fails_closed_until_real_e2e_exists(
    tmp_path: Path,
) -> None:
    matrix, cache, assets = _prepare(tmp_path)

    with pytest.raises(subprocess.CalledProcessError):
        run_platform(
            matrix_path=matrix,
            platform="linux",
            arch="amd64",
            target_version="3.1.0",
            target_commit_sha=COMMIT_SHA,
            target_asset_specs=assets["linux"],
            source_cache=cache,
            work_root=tmp_path / "linux-work",
            output=tmp_path / "result.json",
        )

    unavailable = json.loads(
        (
            tmp_path
            / "linux-work/3.0.0rc19-healthy_commit/result.json"
        ).read_text(encoding="utf-8")
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["assertions"] == {}
    # The harness must provide a reason; the exact text depends on which
    # prerequisite is missing (Docker, valid bundles, etc.).
    assert isinstance(unavailable["observations"].get("reason"), str)
    assert len(unavailable["observations"]["reason"]) > 0
