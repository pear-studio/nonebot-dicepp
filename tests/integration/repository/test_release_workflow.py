from pathlib import Path

import yaml


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TEST_SUITE_WORKFLOW = ROOT / ".github" / "workflows" / "test-suite.yml"
WINDOWS_PACKAGE_SCRIPT = ROOT / "scripts" / "build" / "assemble_windows_package.ps1"


def _workflow_job(workflow_path: Path, job_name: str) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return workflow["jobs"][job_name]


def _workflow_step(workflow_path: Path, job_name: str, step_name: str) -> dict:
    for step in _workflow_job(workflow_path, job_name)["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"{step_name} step not found in {workflow_path.name}:{job_name}")


def _create_release_script() -> str:
    return _workflow_step(RELEASE_WORKFLOW, "publish", "Create GitHub Release")["run"]


def test_create_release_uploads_only_user_facing_assets():
    script = _create_release_script()

    assert "ASSETS=(docker-compose.yml dicepp-release.json)" in script
    assert "ZIP_ASSETS=(DicePP-*-win64-Portable.zip DicePP-*-win64-Setup.exe" in script
    assert 'ASSETS+=("${ZIP_ASSETS[@]}")' in script
    assert "LINUX_IMAGE_ASSETS=(DicePP-*-linux-amd64.zip)" in script
    assert 'ASSETS+=("$RELEASE_MD")' not in script
    assert "DicePP-*-linux-amd64-images.tar.zst" not in script
    assert "ASSETS+=(docs/linux.md)" not in script
    assert '"${ASSETS[@]}"' in script


def test_create_release_uses_metadata_as_notes_file_when_available():
    script = _create_release_script()

    assert 'RELEASE_MD="docs/releases/${TAG}.md"' in script
    assert '--notes-file "$RELEASE_MD"' in script
    assert '--notes "DicePP ${TAG}"' in script


def test_linux_release_package_embeds_docs_and_usage_guide_without_offline_name():
    step = _workflow_step(RELEASE_WORKFLOW, "publish", "Package Linux amd64 release bundle")
    script = step["run"]

    assert 'PACKAGE_DIR="DicePP-${TAG}-linux-amd64"' in script
    assert "offline" not in script.lower()
    assert 'cp docs/linux.md "${PACKAGE_DIR}/docs/linux.md"' in script
    assert 'cp docs/configuration.md "${PACKAGE_DIR}/docs/configuration.md"' in script
    assert 'cat > "${PACKAGE_DIR}/使用说明.md"' in script
    assert 'cd "${PACKAGE_DIR}"' in script
    assert 'zip -r "../${PACKAGE_ZIP}" .' in script
    assert 'zip -r "${PACKAGE_ZIP}" "${PACKAGE_DIR}"' not in script
    assert 'sha256sum "${PACKAGE_ZIP}" > "${PACKAGE_SHA}"' not in script
    assert "generate_linux_package_manifest.py" in script
    assert 'dicepp-package.json' in script
    assert '--release-notes "docs/releases/${TAG}.md"' in script
    assert 'docker image inspect --format \'{{.Id}}\'' in script
    assert '--image-id "${BOT_IMAGE_ID}"' in script
    assert '--image-id "${DASHBOARD_IMAGE_ID}"' in script
    assert "--minimum-manager-version" not in script
    assert "--automatic-upgrade" not in script


def test_windows_release_uses_velpack_and_normalizes_public_names():
    step = _workflow_step(RELEASE_WORKFLOW, "windows-build", "Package artifact")
    script = step["run"]

    assert "vpk pack" in script
    assert "--mainExe DicePP.exe" in script
    assert "win64-Portable.zip" in script
    assert "win64-Setup.exe" in script
    assert "*-full.nupkg" in script
    assert "releases.${{ steps.velopack.outputs.channel }}.json" in script
    assert "dotnet tool install -g vpk --version 1.2.0" in script


def test_release_manifest_is_generated_after_all_platform_artifacts():
    step = _workflow_step(RELEASE_WORKFLOW, "publish", "Generate release machine contract")
    script = step["run"]

    assert "generate_release_manifest.py" in script
    assert "--release-notes" in script
    assert "--minimum-manager-version" not in script
    assert "--automatic-upgrade" not in script
    assert "--change-scope" not in script
    assert "windows:amd64:portable:" in script
    assert "windows:amd64:setup:" in script
    assert "linux:amd64:linux-bundle:" in script


def test_runtime_image_ci_runs_isolated_plugin_preflight_after_quick_feedback():
    job = _workflow_job(TEST_SUITE_WORKFLOW, "runtime-image")
    build = _workflow_step(TEST_SUITE_WORKFLOW, "runtime-image", "Build Runtime image")
    smoke = _workflow_step(TEST_SUITE_WORKFLOW, "runtime-image", "Smoke test Runtime image")

    assert job["runs-on"] == "ubuntu-latest"
    assert job["needs"] == "quick"
    assert build["run"].strip() == "docker build -f Dockerfile -t dicepp-runtime:ci ."
    assert smoke["run"].strip() == (
        "docker run --rm --network=none dicepp-runtime:ci python bot.py --smoke-check"
    )
    assert "services" not in job
    assert "docker compose" not in "\n".join(
        step.get("run", "") for step in job["steps"]
    )


def test_dashboard_image_smokes_only_dashboard_health_not_the_manager_control_channel():
    ci_smoke = _workflow_step(
        TEST_SUITE_WORKFLOW, "dashboard-image", "Smoke test Dashboard image"
    )["run"]
    release_smoke = _workflow_step(
        RELEASE_WORKFLOW, "build-docker", "Smoke test Dashboard image"
    )["run"]

    for script in (ci_smoke, release_smoke):
        assert "/api/auth/status" in script
        assert "smoke_dashboard_control_channel" not in script
        assert "/ws/control" not in script


def test_release_bot_image_smoke_preflights_local_tag_without_network():
    image = "ghcr.io/pear-studio/nonebot-dicepp:${{ steps.version.outputs.tag }}"
    build = _workflow_step(RELEASE_WORKFLOW, "build-docker", "Build image (local only)")
    smoke = _workflow_step(RELEASE_WORKFLOW, "build-docker", "Smoke test image")
    script = smoke["run"]

    assert build["with"]["load"] is True
    assert image in build["with"]["tags"]
    assert f'IMAGE="{image}"' in script
    assert 'docker run --rm --network=none "$IMAGE" python bot.py --version' in script
    assert 'docker run --rm --network=none "$IMAGE" python bot.py --smoke-check' in script


def test_create_release_does_not_pass_empty_zip_argument():
    script = _create_release_script()

    assert "nullglob" in script
    assert "$ZIP" not in script


def test_windows_package_only_keeps_localized_usage_readme():
    script = WINDOWS_PACKAGE_SCRIPT.read_text(encoding="utf-8")

    assert 'Destination (Join-Path $DistDir "README.md")' not in script
    assert "$localizedReadmeName" in script
    assert 'Destination (Join-Path $DistDir $localizedReadmeName)' in script


def test_windows_packages_a_standalone_update_guard():
    script = WINDOWS_PACKAGE_SCRIPT.read_text(encoding="utf-8")
    release_build = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Build Windows executables with PyInstaller",
    )["run"]
    ci_build = _workflow_step(
        TEST_SUITE_WORKFLOW,
        "windows-package",
        "Build Windows executables",
    )["run"]

    assert "pyinstaller scripts/build/update_guard.spec" in release_build
    assert "pyinstaller scripts/build/update_guard.spec" in ci_build
    assert '$UpdateGuardSource = "dist/DicePP-UpdateGuard.exe"' in script
    assert 'Destination (Join-Path $DistDir "DicePP-UpdateGuard.exe")' in script


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
    assert (
        'Invoke-PackagedExe "dist/DicePP/DicePP-UpdateGuard.exe" '
        '@("--smoke-check")'
    ) in script
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
    assert (
        'Invoke-PackagedExe "dist/DicePP/DicePP-UpdateGuard.exe" '
        '@("--smoke-check")'
    ) in script
    assert "./dist/DicePP/DicePP.exe --version" not in script
