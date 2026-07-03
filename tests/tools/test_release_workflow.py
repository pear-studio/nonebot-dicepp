from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TEST_SUITE_WORKFLOW = ROOT / ".github" / "workflows" / "test-suite.yml"


def _workflow_step(workflow_path: Path, job_name: str, step_name: str) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    for step in workflow["jobs"][job_name]["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"{step_name} step not found in {workflow_path.name}:{job_name}")


def _create_release_script() -> str:
    return _workflow_step(RELEASE_WORKFLOW, "publish", "Create GitHub Release")["run"]


def test_create_release_uploads_metadata_linux_docs_and_optional_zip_assets():
    script = _create_release_script()

    assert "ASSETS=(docker-compose.yml)" in script
    assert 'if [ -f "docs/linux.md" ]; then' in script
    assert "ASSETS+=(docs/linux.md)" in script
    assert 'ASSETS+=("$RELEASE_MD")' in script
    assert "ZIP_ASSETS=(DicePP-*-win64.zip)" in script
    assert 'ASSETS+=("${ZIP_ASSETS[@]}")' in script
    assert '"${ASSETS[@]}"' in script


def test_create_release_uses_metadata_as_notes_file_when_available():
    script = _create_release_script()

    assert 'RELEASE_MD="docs/releases/${TAG}.md"' in script
    assert '--notes-file "$RELEASE_MD"' in script
    assert '--notes "DicePP ${TAG}"' in script


def test_create_release_does_not_pass_empty_zip_argument():
    script = _create_release_script()

    assert "nullglob" in script
    assert "$ZIP" not in script


def test_windows_package_smoke_waits_for_windowed_dashboard_launcher():
    step = _workflow_step(TEST_SUITE_WORKFLOW, "windows-package", "Assemble and smoke test package")
    script = step["run"]

    assert step["shell"] == "pwsh"
    assert "Start-Process" in script
    assert "-Wait" in script
    assert "-RedirectStandardOutput" in script
    assert "$null -eq $out" in script
    assert "$null -eq $err" in script
    assert "(Get-Content $stdout -Raw -ErrorAction SilentlyContinue).Trim()" not in script
    assert 'Invoke-PackagedExe "dist/DicePP/DicePP.exe" @("--version")' in script
    assert 'Invoke-PackagedExe "dist/DicePP/DicePP.exe" @("--smoke-check")' in script
    assert "(& dist/DicePP/DicePP.exe --version)" not in script


def test_release_windows_smoke_waits_for_windowed_dashboard_launcher():
    step = _workflow_step(RELEASE_WORKFLOW, "windows-build", "Smoke test")
    script = step["run"]

    assert step["shell"] == "pwsh"
    assert "Start-Process" in script
    assert "-Wait" in script
    assert "-RedirectStandardOutput" in script
    assert "$null -eq $out" in script
    assert "$null -eq $err" in script
    assert "(Get-Content $stdout -Raw -ErrorAction SilentlyContinue).Trim()" not in script
    assert 'Invoke-PackagedExe "dist/DicePP/DicePP.exe" @("--version")' in script
    assert 'Invoke-PackagedExe "dist/DicePP/DicePP.exe" @("--smoke-check")' in script
    assert "./dist/DicePP/DicePP.exe --version" not in script
