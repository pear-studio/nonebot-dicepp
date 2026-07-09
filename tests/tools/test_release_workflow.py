from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TEST_SUITE_WORKFLOW = ROOT / ".github" / "workflows" / "test-suite.yml"
WINDOWS_PACKAGE_SCRIPT = ROOT / "scripts" / "build" / "assemble_windows_package.ps1"


def _workflow_step(workflow_path: Path, job_name: str, step_name: str) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    for step in workflow["jobs"][job_name]["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"{step_name} step not found in {workflow_path.name}:{job_name}")


def _create_release_script() -> str:
    return _workflow_step(RELEASE_WORKFLOW, "publish", "Create GitHub Release")["run"]


def test_create_release_uploads_only_user_facing_assets():
    script = _create_release_script()

    assert "ASSETS=(docker-compose.yml)" in script
    assert "ZIP_ASSETS=(DicePP-*-win64.zip)" in script
    assert 'ASSETS+=("${ZIP_ASSETS[@]}")' in script
    assert "OFFLINE_IMAGE_ASSETS=(DicePP-*-linux-amd64-offline.zip)" in script
    assert 'ASSETS+=("$RELEASE_MD")' not in script
    assert "DicePP-*-linux-amd64-offline.zip.sha256" not in script
    assert "DicePP-*-linux-amd64-images.tar.zst" not in script
    assert "ASSETS+=(docs/linux.md)" not in script
    assert '"${ASSETS[@]}"' in script


def test_create_release_uses_metadata_as_notes_file_when_available():
    script = _create_release_script()

    assert 'RELEASE_MD="docs/releases/${TAG}.md"' in script
    assert '--notes-file "$RELEASE_MD"' in script
    assert '--notes "DicePP ${TAG}"' in script


def test_linux_offline_package_embeds_docs_and_usage_guide():
    step = _workflow_step(RELEASE_WORKFLOW, "publish", "Package Linux amd64 offline bundle")
    script = step["run"]

    assert 'PACKAGE_DIR="DicePP-${TAG}-linux-amd64-offline"' in script
    assert 'cp docs/linux.md "${PACKAGE_DIR}/docs/linux.md"' in script
    assert 'cp docs/configuration.md "${PACKAGE_DIR}/docs/configuration.md"' in script
    assert 'cat > "${PACKAGE_DIR}/使用说明.md"' in script
    assert 'zip -r "${PACKAGE_ZIP}" "${PACKAGE_DIR}"' in script
    assert 'sha256sum "${PACKAGE_ZIP}" > "${PACKAGE_SHA}"' not in script


def test_create_release_does_not_pass_empty_zip_argument():
    script = _create_release_script()

    assert "nullglob" in script
    assert "$ZIP" not in script


def test_windows_package_only_keeps_localized_usage_readme():
    script = WINDOWS_PACKAGE_SCRIPT.read_text(encoding="utf-8")

    assert 'Destination (Join-Path $DistDir "README.md")' not in script
    assert "$localizedReadmeName" in script
    assert 'Destination (Join-Path $DistDir $localizedReadmeName)' in script


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
