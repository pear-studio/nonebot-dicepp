import re
import tomllib
from pathlib import Path

import yaml


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TEST_SUITE_WORKFLOW = ROOT / ".github" / "workflows" / "test-suite.yml"
PINNED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "astral-sh/setup-uv": "38f3f104447c67c051c4a08e39b64a148898af3a",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
}


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
    dockerfile_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert [line for line in dockerfile_text.splitlines() if line.startswith("FROM python:")] == [
        "FROM python:3.13-slim AS builder",
        "FROM python:3.13-slim",
    ]
    assert 'CMD ["python", "-m", "dashboard"]' in dockerfile_text
    assert not (ROOT / "Dockerfile.dashboard").exists()


def test_release_workflows_pin_actions_and_toolchain_versions() -> None:
    sha_pattern = re.compile(r"^[0-9a-f]{40}$")
    for workflow_path in (
        RELEASE_WORKFLOW,
        TEST_SUITE_WORKFLOW,
        ROOT / ".github" / "workflows" / "ci.yml",
    ):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                action = step.get("uses")
                if action is None:
                    continue
                repository, revision = action.split("@", 1)
                assert sha_pattern.fullmatch(revision), action
                assert revision == PINNED_ACTIONS[repository]
                if repository == "actions/setup-python":
                    assert step["with"]["python-version"] == "3.13"
                if repository == "astral-sh/setup-uv":
                    assert step["with"]["version"] == "0.11.16"


def test_windows_workflow_builds_and_verifies_the_portable_contract() -> None:
    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))
    steps = suite["jobs"]["windows-package"]["steps"]
    build = _step(suite["jobs"]["windows-package"], "Build and assemble Portable")["run"]
    verify = _step(suite["jobs"]["windows-package"], "Verify normal Portable startup")["run"]

    assert "scripts/build/dicepp.spec" in build
    assert "scripts/build/dashboard.spec" in build
    assert "assemble_windows_package.ps1" in build
    assert "verify_windows_package.ps1 -DistDir dist/DicePP" in verify
    assert "--" + "smoke-" + "check" not in verify
    assert "Compress-Archive" in verify
    assert len(steps) == 7


def test_test_suite_docker_smoke_uses_default_cmd_and_checks_both_ports() -> None:
    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))
    smoke = _step(suite["jobs"]["docker-smoke"], "Smoke test Dashboard and Bot entrypoints")["run"]

    assert "docker run -d" in smoke
    assert "-e DICEPP_ONEBOT_HOST=0.0.0.0" in smoke
    assert "-p 127.0.0.1:4090:4090 -p 127.0.0.1:8080:8080" in smoke
    assert "\n  dicepp:ci\n" in smoke
    assert "api/health" in smoke
    assert "127.0.0.1:8080/" in smoke
    assert "docker rm -f dicepp-ci-smoke" in smoke


def test_reusable_suite_guards_current_runtime_normal_path() -> None:
    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))
    job = suite["jobs"]["runtime-normal-path"]
    command = _step(job, "Run current runtime normal-path contracts")["run"]

    assert job["needs"] == "quick"
    assert "uv run pytest -n0 -q" in command
    for test_path in (
        "tests/system/process/dashboard/test_bot_process.py",
        "tests/integration/dashboard/test_launcher_runtime_log.py",
        "tests/integration/dashboard/test_archives_local.py",
        "tests/integration/dashboard/test_instance_data.py",
    ):
        assert test_path in command
    assert "tests/integration/manager" not in command


def test_dashboard_setup_instructions_use_the_single_dicepp_service() -> None:
    html = (ROOT / "dashboard" / "src" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "docker compose run --rm --no-deps dicepp python -m dashboard admin init" in html
    assert "docker compose run --rm --no-deps dashboard" not in html


def test_release_updates_latest_only_for_stable_tags() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    publish = workflow["jobs"]["publish"]
    build = _step(publish, "Build and fresh-start the single DicePP image")["run"]
    push = _step(publish, "Push the single GHCR image")["run"]

    assert 'test "$GITHUB_REF_NAME" = "v$expected"' in build
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in build
    assert "-p 127.0.0.1:4090:4090 -p 127.0.0.1:8080:8080" in build
    assert "127.0.0.1:8080/" in build
    assert 'docker_args+=( -t "$LATEST_IMAGE" )' in build
    assert 'docker push "$LATEST_IMAGE"' in push


def test_release_marks_non_stable_tags_as_prereleases() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    create = _step(workflow["jobs"]["publish"], "Create GitHub Release")["run"]

    assert 'if [[ ! "$GITHUB_REF_NAME" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in create
    assert "release_args+=(--prerelease)" in create
    assert '"${release_args[@]}"' in create


def test_release_publishes_the_verified_windows_artifact_from_test_suite() -> None:
    release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))
    publish = release["jobs"]["publish"]
    download = _step(publish, "Download verified Windows Portable artifact")
    rename = _step(publish, "Name verified Windows Portable artifact")
    publish_commands = "\n".join(
        step.get("run", "") for step in publish["steps"] if "run" in step
    )
    windows_upload = next(
        step for step in suite["jobs"]["windows-package"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )

    assert publish["needs"] == "tests"
    assert "windows-portable" not in release["jobs"]
    assert download["with"]["name"] == "dicepp-windows-ci"
    assert rename["run"] == (
        'mv "dist/DicePP-ci-win64-Portable.zip" '
        '"dist/DicePP-${{ github.ref_name }}-win64-Portable.zip"'
    )
    assert windows_upload["with"]["name"] == "dicepp-windows-ci"
    assert "pyinstaller" not in publish_commands
    assert "verify_windows_package" not in publish_commands
    assert "pytest" not in publish_commands
    assert "uv sync" not in publish_commands
