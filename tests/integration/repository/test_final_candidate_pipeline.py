import re
import tomllib
from pathlib import Path

import yaml


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
CANDIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "candidate.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
WINDOWS_VALIDATOR = (
    ROOT / "scripts" / "build" / "validate_windows_final_candidate.ps1"
)
LINUX_VALIDATOR = (
    ROOT / "scripts" / "build" / "validate_linux_final_candidate.sh"
)
TEST_SUITE_WORKFLOW = ROOT / ".github" / "workflows" / "test-suite.yml"
PINNED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "astral-sh/setup-uv": "38f3f104447c67c051c4a08e39b64a148898af3a",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "docker/login-action": "c94ce9fb468520275223c153574b00df6fe4bcc9",
}


def _workflow() -> dict:
    return yaml.safe_load(CANDIDATE_WORKFLOW.read_text(encoding="utf-8"))


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_project_and_packaged_runtimes_share_python_313_baseline() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock_header = (ROOT / "uv.lock").read_text(encoding="utf-8").splitlines()[:3]

    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.13"
    assert project["project"]["requires-python"] == ">=3.13,<3.14"
    assert lock_header == [
        "version = 1",
        "revision = 3",
        'requires-python = "==3.13.*"',
    ]
    for dockerfile in ("Dockerfile", "Dockerfile.dashboard"):
        dockerfile_text = (ROOT / dockerfile).read_text(encoding="utf-8")
        from_lines = [
            line
            for line in dockerfile_text.splitlines()
            if line.startswith("FROM python:")
        ]
        assert from_lines == [
            "FROM python:3.13-slim AS builder",
            "FROM python:3.13-slim",
        ]
        assert "RUN pip install uv==0.11.16 " in dockerfile_text


def test_candidate_is_bound_to_an_explicit_current_master_head() -> None:
    workflow = _workflow()
    dispatch = workflow.get("on", workflow.get(True))["workflow_dispatch"]
    guard = _step(
        workflow["jobs"]["candidate-metadata"],
        "Reject an ambiguous target commit",
    )["run"]

    assert set(dispatch["inputs"]) == {
        "version",
        "commit_sha",
        "exercise_upgrade_matrix",
    }
    assert dispatch["inputs"]["version"]["required"] is True
    assert dispatch["inputs"]["commit_sha"]["required"] is True
    assert dispatch["inputs"]["exercise_upgrade_matrix"] == {
        "description": "Exercise upgrade matrices without adding evidence to release assets",
        "required": False,
        "default": False,
        "type": "boolean",
    }
    assert "^[0-9a-f]{40}$" in guard
    assert '"$WORKFLOW_SHA" != "$INPUT_COMMIT_SHA"' in guard
    assert '"$GITHUB_REF" != "refs/heads/master"' in guard
    assert workflow["permissions"] == {"contents": "read"}
    forbidden = ("gh release", "git tag", "git push", "imagetools create")
    workflow_text = CANDIDATE_WORKFLOW.read_text(encoding="utf-8")
    assert not any(command in workflow_text for command in forbidden)


def test_seal_consumes_validation_declarations_and_exposes_unique_artifact() -> None:
    receipt = _workflow()["jobs"]["receipt"]
    seal = _step(receipt, "Generate immutable candidate receipt")["run"]
    upload = _step(receipt, "Upload sealed final candidate")

    assert seal.count("--validated-summary") == 2
    assert '--artifact-name "$ARTIFACT_NAME"' in seal
    expected_name = (
        "dicepp-final-candidate-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert upload["with"]["name"] == expected_name
    assert upload["with"]["retention-days"] == 30
    assert upload["id"] == "upload"
    assert receipt["outputs"] == {
        "artifact_id": "${{ steps.upload.outputs.artifact-id }}",
        "artifact_digest": "${{ steps.upload.outputs.artifact-digest }}",
        "artifact_url": "${{ steps.upload.outputs.artifact-url }}",
    }
    diagnostics = _step(receipt, "Upload candidate seal diagnostics")
    assert diagnostics["if"] == "always()"


def test_validators_declare_hashes_only_after_full_candidate_smoke() -> None:
    windows = WINDOWS_VALIDATOR.read_text(encoding="utf-8")
    linux = LINUX_VALIDATOR.read_text(encoding="utf-8")

    windows_declaration = windows.index("# The declaration is intentionally the last write")
    assert windows_declaration > windows.rindex("Invoke-DetachedLaunchSmoke $stableDashboard")
    assert windows_declaration > windows.index('-Scenario "final-setup-install"')
    assert "contract_version = 1" in windows
    assert "Get-FileHash -LiteralPath $path -Algorithm SHA256" in windows
    assert "size = $item.Length" in windows

    linux_declaration = linux.index('if [ -n "$VALIDATED_SUMMARY" ]')
    assert linux_declaration > linux.index("up -d --pull never --wait")
    assert linux_declaration > linux.index(".State.Health.Status")
    assert 'stat -Lc \'%s\' -- "$PACKAGE_ZIP"' in linux
    assert 'sha256sum -- "$PACKAGE_ZIP"' in linux
    assert '"contract_version": 1' in linux


def test_release_workflows_pin_actions_and_toolchain_versions() -> None:
    sha_pattern = re.compile(r"^[0-9a-f]{40}$")
    for workflow_path in (
        CANDIDATE_WORKFLOW,
        RELEASE_WORKFLOW,
        ROOT / ".github" / "workflows" / "test-suite.yml",
    ):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            if "uses" in job:
                assert str(job["uses"]).startswith("./")
            for step in job.get("steps", []):
                action = step.get("uses")
                if action is None:
                    continue
                repository, revision = action.split("@", 1)
                assert "/" in repository
                assert sha_pattern.fullmatch(revision), action
                assert revision == PINNED_ACTIONS[repository]
                if repository == "actions/setup-python":
                    assert step["with"]["python-version"] == "3.13"
                if repository == "astral-sh/setup-uv":
                    assert step["with"]["version"] == "0.11.16"


def test_critical_python_jobs_pin_python_before_their_first_invocation() -> None:
    targets = (
        (CANDIDATE_WORKFLOW, "upgrade-evidence"),
        (RELEASE_WORKFLOW, "verify-candidate"),
        (RELEASE_WORKFLOW, "promote"),
    )
    for workflow_path, job_name in targets:
        job = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))["jobs"][
            job_name
        ]
        setup_indices = [
            index
            for index, step in enumerate(job["steps"])
            if str(step.get("uses", "")).startswith("actions/setup-python@")
        ]
        python_indices = [
            index
            for index, step in enumerate(job["steps"])
            if re.search(r"\bpython\b", str(step.get("run", "")))
        ]

        assert len(setup_indices) == 1, f"{job_name} must set up Python exactly once"
        assert python_indices, f"{job_name} must invoke Python"
        setup = job["steps"][setup_indices[0]]
        assert setup["with"]["python-version"] == "3.13"
        assert setup_indices[0] < min(python_indices)


def test_windows_package_and_final_validator_share_the_real_process_runner() -> None:
    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))
    assembled = _step(
        suite["jobs"]["windows-package"],
        "Assemble and smoke test package",
    )["run"]
    validator = WINDOWS_VALIDATOR.read_text(encoding="utf-8")

    assert "windows_process_runner.ps1" in assembled
    assert "windows_process_runner.ps1" in validator
    assert "function Invoke-PackagedExe" not in assembled
    assert "function Invoke-PackagedExe" not in validator
    for script in (assembled, validator):
        assert "Invoke-DicePPProcess" in script
        assert "-Scenario" in script
        assert "DiagnosticsRoot" in script


def test_candidate_summary_documents_expiry_and_non_promotable_diagnostics() -> None:
    receipt = _workflow()["jobs"]["receipt"]
    summary = _step(receipt, "Publish sealed candidate identity")["run"]

    assert "Sealed artifact retention: 30 days" in summary
    assert "Failure diagnostics retention: 30 days" in summary
    assert "Intermediate Windows/Linux payload artifacts: 7 days" in summary
    assert "then-current master HEAD" in summary
    assert "do not reconstruct or mix bytes" in summary
