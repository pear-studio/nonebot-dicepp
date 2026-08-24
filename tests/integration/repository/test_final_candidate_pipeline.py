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


def test_validators_declare_hashes_only_after_full_candidate_smoke() -> None:
    windows = WINDOWS_VALIDATOR.read_text(encoding="utf-8")
    linux = LINUX_VALIDATOR.read_text(encoding="utf-8")

    windows_declaration = windows.index("# The declaration is intentionally the last write")
    assert windows_declaration > windows.rindex("Invoke-DetachedLaunchSmoke $stableDashboard")
    assert windows_declaration > windows.index('-Scenario "final-setup-install"')
    assert "contract_version = 1" in windows
    assert "Get-FileHash -LiteralPath $path -Algorithm SHA256" in windows
    assert "size = $item.Length" in windows
    assert "Portable must not contain DicePP-UpdateGuard.exe" in windows
    assert "Setup must not install DicePP-UpdateGuard.exe" in windows
    assert windows.count("Assert-ManualMigrationSentinels") == 2
    assert windows.count("Write-ManualMigrationSentinels") == 2
    assert windows.count('"manager\\control\\user-preserved.txt"') == 2
    assert "Portable payload must not contain config/global.json" in windows
    assert "Setup payload must not install config/global.json" in windows
    assert windows.index("Write-ManualMigrationSentinels $extractRoot") < windows.index(
        "[System.IO.Compression.ZipFile]::ExtractToDirectory"
    )

    linux_declaration = linux.index('if [ -n "$VALIDATED_SUMMARY" ]')
    assert linux_declaration > linux.index("up -d --pull never --wait")
    assert linux_declaration > linux.index(".State.Health.Status")
    assert 'stat -Lc \'%s\' -- "$PACKAGE_ZIP"' in linux
    assert 'sha256sum -- "$PACKAGE_ZIP"' in linux
    assert '"contract_version": 1' in linux
    assert 'assert not Path("/app/config/global.json").exists()' in linux
    assert 'assert config.chat_interval == 31' in linux
    assert 'assert config.nickname == "image-bot-layer"' in linux
    assert "Runtime image modified legacy config/global.json" in linux


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
