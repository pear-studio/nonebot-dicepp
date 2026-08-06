from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dicepp_manager.release import validate_release_manifest
from scripts.build.candidate_receipt import (
    ContainerCandidate,
    ValidatedArtifact,
    build_candidate_receipt,
    parse_validation_summary,
    validate_candidate_receipt,
)
from scripts.build.promotion_candidate import (
    promotion_outputs,
    verify_promotion_candidate,
)
from scripts.build.upgrade_evidence import (
    CandidateIdentity,
    FinalAssetIdentity,
    required_scenarios_for,
    validate_upgrade_evidence,
)
from tests.support.fs_utils import symlink_or_skip
from tests.support.paths import find_repository_root


VERSION = "3.1.0rc1"
COMMIT_SHA = "1" * 40
PACKAGE_TREE_SHA256 = "2" * 64
REPOSITORY = "pear-studio/nonebot-dicepp"
WORKFLOW_REF = (
    "pear-studio/nonebot-dicepp/.github/workflows/candidate.yml@refs/heads/master"
)
ARTIFACT_NAME = "dicepp-final-candidate-10-1"
TOOLCHAINS = {
    "docker": "Docker version 27.0.0",
    "python": "Python 3.13.3",
    "ubuntu-runner": "ubuntu24/20260801.1",
    "uv": "uv 0.11.16",
    "velopack": "vpk 1.2.0",
    "zstd": "zstd 1.5.6",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_specs() -> tuple[tuple[str, str, str, str], ...]:
    return (
        ("linux", "amd64", "linux-bundle", f"DicePP-v{VERSION}-linux-amd64.zip"),
        (
            "windows",
            "amd64",
            "portable",
            f"DicePP-v{VERSION}-win64-Portable.zip",
        ),
        ("windows", "amd64", "setup", f"DicePP-v{VERSION}-win64-Setup.exe"),
        ("windows", "amd64", "velopack-bundle", "velopack.win-x64.zip"),
    )


def _containers() -> list[ContainerCandidate]:
    return [
        ContainerCandidate(
            "runtime",
            "ghcr.io/pear-studio/nonebot-dicepp:candidate-10-1",
            f"sha256:{'3' * 64}",
            f"sha256:{'4' * 64}",
        ),
        ContainerCandidate(
            "dashboard",
            "ghcr.io/pear-studio/dicepp-dashboard:candidate-10-1",
            f"sha256:{'5' * 64}",
            f"sha256:{'6' * 64}",
        ),
    ]


def _candidate_identities() -> list[dict[str, str]]:
    return [
        {"platform": "linux", "name": "dashboard-manifest", "sha256": "5" * 64},
        {"platform": "linux", "name": "runtime-manifest", "sha256": "3" * 64},
        {
            "platform": "windows",
            "name": "package-tree",
            "sha256": PACKAGE_TREE_SHA256,
        },
    ]


def _candidate_digest() -> str:
    canonical = json.dumps(
        _candidate_identities(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _upgrade_matrix() -> dict:
    root = find_repository_root(Path(__file__))
    matrix = json.loads(
        (root / "scripts/build/upgrade_matrix.json").read_text(encoding="utf-8")
    )
    # Production intentionally has no Windows source during the rc20 manual
    # protocol break. Receipt cryptographic tests use a closed synthetic matrix
    # so they continue to protect the future automatic-upgrade path itself.
    matrix["supported_sources"].insert(
        0,
        {
            "platform": "windows",
            "arch": "amd64",
            "source_version": "3.0.0rc20",
            "assets": [
                {
                    "purpose": "portable",
                    "name": "source-portable.zip",
                    "url": "https://example.invalid/source-portable.zip",
                    "sha256": "7" * 64,
                },
                {
                    "purpose": "velopack-bundle",
                    "name": "source-velopack.zip",
                    "url": "https://example.invalid/source-velopack.zip",
                    "sha256": "8" * 64,
                },
            ],
        },
    )
    return matrix


def _upgrade_matrix_path(tmp_path: Path) -> Path:
    path = tmp_path / "test-upgrade-matrix.json"
    if not path.exists():
        path.write_text(json.dumps(_upgrade_matrix()), encoding="utf-8")
    return path


def _scenario_result(name: str, source_version: str) -> dict:
    records = {
        "healthy_commit": (
            {
                "source_started": True,
                "target_started": True,
                "local_health_passed": True,
                "journal_committed": True,
            },
            {
                "source_version_before": source_version,
                "target_version_after": VERSION,
                "journal_status": "committed",
                "health_status": "healthy",
            },
        ),
        "target_health_failure_rollback": (
            {
                "target_executed": True,
                "health_failure_injected": True,
                "program_restored": True,
                "data_restored": True,
                "source_restarted": True,
                "journal_rolled_back": True,
            },
            {
                "target_version_observed": VERSION,
                "restored_version": source_version,
                "journal_status": "rolled_back",
                "rollback_marker_status": "restored",
            },
        ),
        "retry_after_rollback": (
            {
                "prior_rollback_observed": True,
                "retry_started_same_instance": True,
                "target_started": True,
                "journal_committed": True,
            },
            {
                "first_transaction_status": "rolled_back",
                "retry_transaction_status": "committed",
                "final_version": VERSION,
            },
        ),
        "apply_failure_before_target_execution": (
            {
                "apply_failure_injected": True,
                "target_never_executed": True,
                "source_remained_or_restored": True,
                "no_target_migration": True,
                "terminal_state_recorded": True,
            },
            {
                "target_process_start_count": 0,
                "source_version_after": source_version,
                "journal_status": "rolled_back",
                "apply_exit_code": 17,
            },
        ),
        "manual_restore_after_target_failure": (
            {
                "target_failure_observed": True,
                "recovery_material_preserved": True,
                "manual_restore_invoked": True,
                "whole_program_tree_restored": True,
                "data_restored": True,
                "source_restarted": True,
                "journal_manually_restored": True,
            },
            {
                "target_version_observed": VERSION,
                "restored_version": source_version,
                "journal_status": "manually_restored",
                "recovery_trigger": "manual",
                "program_restore_mode": "whole_current_directory",
            },
        ),
    }
    assertions, observations = records[name]
    return {
        "name": name,
        "status": "passed",
        "assertions": assertions,
        "observations": observations,
    }


def _canonical_upgrade_evidence(root: Path) -> dict:
    matrix = _upgrade_matrix()
    final_assets = [
        FinalAssetIdentity(
            platform,
            arch,
            purpose,
            filename,
            (root / filename).stat().st_size,
            _sha256(root / filename),
        )
        for platform, arch, purpose, filename in _package_specs()
    ]
    evidence = {
        "contract_version": 2,
        "target": {
            "version": VERSION,
            "commit_sha": COMMIT_SHA,
            "candidate_identities": _candidate_identities(),
            "candidate_digest": _candidate_digest(),
            "final_assets": [
                {
                    "platform": item.platform,
                    "arch": item.arch,
                    "purpose": item.purpose,
                    "filename": item.filename,
                    "size": item.size,
                    "sha256": item.sha256,
                }
                for item in final_assets
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
                    _scenario_result(name, source["source_version"])
                    for name in required_scenarios_for(
                        matrix,
                        platform=source["platform"],
                        arch=source["arch"],
                    )
                ],
            }
            for source in matrix["supported_sources"]
        ],
    }
    return validate_upgrade_evidence(
        evidence,
        matrix=matrix,
        target_version=VERSION,
        target_commit_sha=COMMIT_SHA,
        target_candidate_identities=[
            CandidateIdentity(**identity) for identity in _candidate_identities()
        ],
        target_final_assets=final_assets,
    )


def _prepare_assets(
    tmp_path: Path, *, automatic_upgrade: bool = True
) -> tuple[Path, list[ValidatedArtifact]]:
    root = tmp_path / "candidate"
    root.mkdir()
    for index, (*_, filename) in enumerate(_package_specs()):
        (root / filename).write_bytes(f"validated-package-{index}".encode())
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manifest_artifacts = [
        {
            "platform": platform,
            "arch": arch,
            "purpose": purpose,
            "filename": filename,
            "size": (root / filename).stat().st_size,
            "sha256": _sha256(root / filename),
        }
        for platform, arch, purpose, filename in _package_specs()
    ]
    manifest = validate_release_manifest(
        {
            "contract_version": 2,
            "version": VERSION,
            "channel": "prerelease",
            "deployment_schema_version": 1,
            "minimum_manager_version": "1.0",
            "catalog_version": 1,
            "catalog_digest": "9" * 64,
            "change_scope": ["runtime", "dashboard"],
            "automatic_upgrade": automatic_upgrade,
            "artifacts": manifest_artifacts,
            "fallbacks": {
                "linux_ghcr_images": [
                    f"ghcr.io/pear-studio/nonebot-dicepp:v{VERSION}",
                    f"ghcr.io/pear-studio/dicepp-dashboard:v{VERSION}",
                ]
            },
        }
    )
    (root / "dicepp-release.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    if automatic_upgrade:
        (root / "dicepp-upgrade-evidence.json").write_text(
            json.dumps(_canonical_upgrade_evidence(root)),
            encoding="utf-8",
        )
    summaries = [
        ValidatedArtifact(
            filename=filename,
            size=(root / filename).stat().st_size,
            sha256=_sha256(root / filename),
        )
        for *_, filename in _package_specs()
    ]
    return root, summaries


def _project(tmp_path: Path) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nversion = "{VERSION}"\n', encoding="utf-8")
    return path


def _seal(
    tmp_path: Path,
    *,
    artifact_root: Path | None = None,
    validated_artifacts: list[ValidatedArtifact] | None = None,
    **overrides: object,
) -> dict:
    if artifact_root is None:
        artifact_root, default_summaries = _prepare_assets(tmp_path)
    else:
        default_summaries = [
            ValidatedArtifact(
                filename=filename,
                size=(artifact_root / filename).stat().st_size,
                sha256=_sha256(artifact_root / filename),
            )
            for *_, filename in _package_specs()
            if (artifact_root / filename).is_file()
        ]
    arguments: dict[str, object] = {
        "artifact_root": artifact_root,
        "project_file": _project(tmp_path),
        "version": VERSION,
        "commit_sha": COMMIT_SHA,
        "repository": REPOSITORY,
        "workflow_ref": WORKFLOW_REF,
        "run_id": 10,
        "run_attempt": 1,
        "workflow_sha": COMMIT_SHA,
        "artifact_name": ARTIFACT_NAME,
        "package_tree_sha256": PACKAGE_TREE_SHA256,
        "containers": _containers(),
        "toolchains": TOOLCHAINS,
        "validated_artifacts": (
            default_summaries
            if validated_artifacts is None
            else validated_artifacts
        ),
        "upgrade_matrix_path": _upgrade_matrix_path(tmp_path),
    }
    arguments.update(overrides)
    return build_candidate_receipt(**arguments)  # type: ignore[arg-type]


def _write_receipt(root: Path, receipt: dict) -> Path:
    path = root / "dicepp-candidate.json"
    path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _promote(root: Path, receipt_path: Path, **overrides: object) -> dict:
    arguments: dict[str, object] = {
        "candidate_root": root,
        "receipt_path": receipt_path,
        "repository": REPOSITORY,
        "workflow_ref": WORKFLOW_REF,
        "run_id": 10,
        "run_attempt": 1,
        "artifact_name": ARTIFACT_NAME,
        "commit_sha": COMMIT_SHA,
        "version": VERSION,
        "upgrade_matrix_path": _upgrade_matrix_path(root.parent),
    }
    arguments.update(overrides)
    return verify_promotion_candidate(**arguments)  # type: ignore[arg-type]


def test_seal_records_every_release_asset_and_complete_provenance(tmp_path: Path) -> None:
    receipt = _seal(tmp_path)

    assert receipt["contract_version"] == 2
    assert receipt["target"] == {
        "version": VERSION,
        "tag": f"v{VERSION}",
        "commit_sha": COMMIT_SHA,
        "automatic_upgrade": True,
        "is_prerelease": True,
    }
    assert receipt["workflow"]["artifact_name"] == ARTIFACT_NAME
    assert [item["filename"] for item in receipt["artifacts"]] == [
        *[spec[3] for spec in _package_specs()],
        "docker-compose.yml",
        "dicepp-release.json",
        "dicepp-upgrade-evidence.json",
    ]
    assert [item["validated"] for item in receipt["artifacts"]] == [
        True,
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert receipt["containers"][0]["candidate_ref"].endswith("candidate-10-1")
    assert "artifact_digest" not in json.dumps(receipt)


def test_seal_rejects_equal_size_drift_after_validator_success(tmp_path: Path) -> None:
    root, summaries = _prepare_assets(tmp_path)
    portable = root / f"DicePP-v{VERSION}-win64-Portable.zip"
    original = portable.read_bytes()
    portable.write_bytes(bytes(byte ^ 1 for byte in original))
    assert portable.stat().st_size == summaries[1].size

    with pytest.raises(ValueError, match="bytes differ from validator summary"):
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)


@pytest.mark.parametrize("mutation", ["missing-summary", "extra-file", "bad-evidence"])
def test_seal_rejects_incomplete_validation_or_release_set(
    tmp_path: Path, mutation: str
) -> None:
    root, summaries = _prepare_assets(tmp_path)
    if mutation == "missing-summary":
        summaries.pop()
    elif mutation == "extra-file":
        (root / "debug-symbols.zip").write_bytes(b"not a release asset")
    else:
        evidence_path = root / "dicepp-upgrade-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["target"]["commit_sha"] = "9" * 40
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="validation summaries|file set|evidence target"):
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)


def test_validation_summary_schema_is_closed_and_preserves_exact_identity() -> None:
    payload = {
        "contract_version": 1,
        "artifacts": [{"filename": "asset.zip", "size": 7, "sha256": "a" * 64}],
    }
    assert parse_validation_summary(payload) == (
        ValidatedArtifact("asset.zip", 7, "a" * 64),
    )
    payload["artifacts"][0]["path"] = "ignored/by/older/sealer"
    with pytest.raises(ValueError, match="invalid validation summary artifact"):
        parse_validation_summary(payload)


@pytest.mark.parametrize("invalid_version", [True, 1.0, "1"])
def test_validation_summary_rejects_non_integer_contract_version(
    invalid_version: object,
) -> None:
    payload = {
        "contract_version": invalid_version,
        "artifacts": [{"filename": "asset.zip", "size": 7, "sha256": "a" * 64}],
    }

    with pytest.raises(ValueError, match="unsupported validation summary contract"):
        parse_validation_summary(payload)


@pytest.mark.parametrize("invalid_size", [True, 7.0, "7"])
def test_validation_summary_rejects_non_integer_artifact_size(
    invalid_size: object,
) -> None:
    payload = {
        "contract_version": 1,
        "artifacts": [
            {"filename": "asset.zip", "size": invalid_size, "sha256": "a" * 64}
        ],
    }

    with pytest.raises(ValueError, match="invalid validation summary artifact size"):
        parse_validation_summary(payload)


@pytest.mark.parametrize("invalid_version", [True, 1.0, "1"])
def test_seal_rejects_non_integer_upgrade_evidence_contract_version(
    tmp_path: Path,
    invalid_version: object,
) -> None:
    root, summaries = _prepare_assets(tmp_path)
    evidence_path = root / "dicepp-upgrade-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["contract_version"] = invalid_version
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported upgrade evidence contract"):
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)


def test_seal_rejects_evidence_bound_to_different_final_package_bytes(
    tmp_path: Path,
) -> None:
    root, summaries = _prepare_assets(tmp_path)
    evidence_path = root / "dicepp-upgrade-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["target"]["final_assets"][0]["sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match sealed candidate"):
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)


def test_seal_fully_revalidates_upgrade_scenario_observations(tmp_path: Path) -> None:
    root, summaries = _prepare_assets(tmp_path)
    evidence_path = root / "dicepp-upgrade-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["results"][0]["scenarios"][0]["observations"][
        "target_version_after"
    ] = "totally-wrong"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="observations are inconsistent"):
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)


@pytest.mark.parametrize("invalid_version", [True, 2.0, "2"])
def test_receipt_rejects_non_integer_contract_version(
    tmp_path: Path,
    invalid_version: object,
) -> None:
    receipt = _seal(tmp_path)
    receipt["contract_version"] = invalid_version

    with pytest.raises(ValueError, match="unsupported candidate receipt contract"):
        validate_candidate_receipt(receipt)


@pytest.mark.parametrize("field", ["target.commit_sha", "workflow.workflow_sha"])
def test_receipt_rejects_non_string_sha_without_coercion(
    tmp_path: Path, field: str
) -> None:
    receipt = _seal(tmp_path)
    section, name = field.split(".")
    receipt[section][name] = int(COMMIT_SHA)

    with pytest.raises(ValueError, match="target|workflow provenance"):
        validate_candidate_receipt(receipt)


def test_promotion_preflight_verifies_every_byte_and_writes_bound_outputs(
    tmp_path: Path,
) -> None:
    root, summaries = _prepare_assets(tmp_path)
    receipt_path = _write_receipt(
        root,
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries),
    )

    verified = _promote(root, receipt_path)
    outputs = promotion_outputs(verified, receipt_path)

    assert outputs["receipt_sha256"] == _sha256(receipt_path)
    assert outputs["automatic_upgrade"] == "true"
    assert outputs["runtime_candidate_ref"].endswith("candidate-10-1")
    assert outputs["dashboard_manifest_digest"] == f"sha256:{'5' * 64}"


def test_promotion_fully_revalidates_sealed_upgrade_scenarios(tmp_path: Path) -> None:
    root, summaries = _prepare_assets(tmp_path)
    receipt = _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)
    evidence_path = root / "dicepp-upgrade-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["results"][0]["scenarios"][1]["observations"][
        "restored_version"
    ] = "totally-wrong"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    evidence_record = next(
        item
        for item in receipt["artifacts"]
        if item["filename"] == "dicepp-upgrade-evidence.json"
    )
    evidence_record["size"] = evidence_path.stat().st_size
    evidence_record["sha256"] = _sha256(evidence_path)
    receipt_path = _write_receipt(root, receipt)

    with pytest.raises(ValueError, match="observations are inconsistent"):
        _promote(root, receipt_path)


@pytest.mark.parametrize("mutation", ["equal-size-drift", "extra", "wrong-run"])
def test_promotion_preflight_fails_closed_on_bytes_set_or_explicit_identity(
    tmp_path: Path, mutation: str
) -> None:
    root, summaries = _prepare_assets(tmp_path)
    receipt_path = _write_receipt(
        root,
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries),
    )
    overrides: dict[str, object] = {}
    if mutation == "equal-size-drift":
        compose = root / "docker-compose.yml"
        compose.write_bytes(bytes(byte ^ 1 for byte in compose.read_bytes()))
    elif mutation == "extra":
        (root / "unsealed.txt").write_text("extra", encoding="utf-8")
    else:
        overrides["run_attempt"] = 2

    with pytest.raises(ValueError, match="bytes differ|file set|promotion request"):
        _promote(root, receipt_path, **overrides)


def test_no_upgrade_candidate_forbids_unreceipted_evidence(tmp_path: Path) -> None:
    root, summaries = _prepare_assets(tmp_path, automatic_upgrade=False)
    receipt = _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)
    assert receipt["target"]["automatic_upgrade"] is False
    assert "dicepp-upgrade-evidence.json" not in {
        item["filename"] for item in receipt["artifacts"]
    }

    (root / "dicepp-upgrade-evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="file set"):
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)


def test_seal_rejects_symlinked_release_asset(tmp_path: Path) -> None:
    root, summaries = _prepare_assets(tmp_path)
    portable = root / f"DicePP-v{VERSION}-win64-Portable.zip"
    external = tmp_path / "external-portable.zip"
    external.write_bytes(portable.read_bytes())
    portable.unlink()
    symlink_or_skip(portable, external)

    with pytest.raises(ValueError, match="non-regular entry"):
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries)


def test_promotion_rejects_symlinked_receipt(tmp_path: Path) -> None:
    root, summaries = _prepare_assets(tmp_path)
    receipt_path = _write_receipt(
        root,
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries),
    )
    external = tmp_path / "real-receipt.json"
    receipt_path.replace(external)
    symlink_or_skip(receipt_path, external)

    with pytest.raises(ValueError, match="regular file"):
        _promote(root, receipt_path)


def test_promotion_rejects_non_regular_candidate_entry(tmp_path: Path) -> None:
    root, summaries = _prepare_assets(tmp_path)
    receipt_path = _write_receipt(
        root,
        _seal(tmp_path, artifact_root=root, validated_artifacts=summaries),
    )
    compose = root / "docker-compose.yml"
    compose.unlink()
    compose.mkdir()

    with pytest.raises(ValueError, match="non-regular entry"):
        _promote(root, receipt_path)
