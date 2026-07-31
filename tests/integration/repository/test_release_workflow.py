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
    assert "${{ needs.release-metadata.outputs.velopack_version }}" in script
    assert "${{ needs.release-metadata.outputs.velopack_channel }}" in script
    assert "win64-Portable.zip" in script
    assert "win64-Setup.exe" in script
    assert "*-full.nupkg" in script
    assert (
        "releases.${{ needs.release-metadata.outputs.velopack_channel }}.json"
        in script
    )
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
    seed = _workflow_step(
        TEST_SUITE_WORKFLOW,
        "runtime-image",
        "Seed stale Python build artifacts in Docker context",
    )
    build = _workflow_step(TEST_SUITE_WORKFLOW, "runtime-image", "Build Runtime image")
    smoke = _workflow_step(TEST_SUITE_WORKFLOW, "runtime-image", "Smoke test Runtime image")

    assert job["runs-on"] == "ubuntu-latest"
    assert job["needs"] == "quick"
    assert build["run"].strip() == "docker build -f Dockerfile -t dicepp-runtime:ci ."
    assert "Version: 0.0.0-stale" in seed["run"]
    assert "src/plugins/DicePP/__pycache__/stale.cpython-313.pyc" in seed["run"]
    assert "python bot.py --version" in smoke["run"]
    assert "DicePP v$expected" in smoke["run"]
    assert "test ! -e /app/src/dicepp.egg-info" in smoke["run"]
    assert "test ! -d /app/src/plugins/DicePP/__pycache__" in smoke["run"]
    assert "python bot.py --smoke-check" in smoke["run"]
    assert "services" not in job
    assert "docker compose" not in "\n".join(
        step.get("run", "") for step in job["steps"]
    )


def test_dashboard_image_smokes_dashboard_and_manager_without_dashboard_control_channel():
    seed = _workflow_step(
        TEST_SUITE_WORKFLOW,
        "dashboard-image",
        "Seed stale Python build artifacts in Docker context",
    )["run"]
    ci_smoke = _workflow_step(
        TEST_SUITE_WORKFLOW, "dashboard-image", "Smoke test Dashboard image"
    )["run"]
    release_smoke = _workflow_step(
        RELEASE_WORKFLOW, "build-docker", "Smoke test Dashboard image"
    )["run"]
    ci_manager_smoke = _workflow_step(
        TEST_SUITE_WORKFLOW, "dashboard-image", "Smoke test Manager image"
    )["run"]
    release_manager_smoke = _workflow_step(
        RELEASE_WORKFLOW, "build-docker", "Smoke test Manager image"
    )["run"]

    for script in (ci_smoke, release_smoke):
        assert "/api/auth/status" in script
        assert "smoke_dashboard_control_channel" not in script
        assert "/ws/control" not in script

    assert "Version: 0.0.0-stale" in seed
    assert "python -m dashboard --version" in ci_smoke
    assert "DicePP Dashboard v$expected" in ci_smoke
    assert "test ! -e /app/src/dicepp.egg-info" in ci_smoke
    assert "test ! -d /app/src/plugins/DicePP/__pycache__" in ci_smoke

    for script in (ci_manager_smoke, release_manager_smoke):
        assert "python -m dicepp_manager" in script
        assert "dicepp-manager-smoke" in script
        assert "DICEPP_MANAGER_RELEASE_SCHEDULER=false" in script
        assert "/app/manager/state/api-token" in script
        assert "Authorization: Bearer $token" in script
        assert "http://127.0.0.1:4091/v1/health" in script
        assert "smoke_dashboard_control_channel" not in script
        assert "/ws/control" not in script

    assert "from importlib.metadata import version" in ci_manager_smoke
    assert 'assert version("dicepp") == expected' in ci_manager_smoke

    ci_cleanup = _workflow_step(
        TEST_SUITE_WORKFLOW, "dashboard-image", "Clean up image smoke resources"
    )["run"]
    assert "dicepp-manager-smoke" in ci_cleanup
    assert "docker volume rm -f dicepp-manager-smoke" in ci_cleanup
    assert "trap cleanup EXIT" in release_manager_smoke


def test_release_bot_image_smoke_preflights_local_tag_without_network():
    image = (
        "ghcr.io/pear-studio/nonebot-dicepp:"
        "${{ needs.release-metadata.outputs.tag }}"
    )
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
    ci_build = _workflow_step(
        TEST_SUITE_WORKFLOW,
        "windows-package",
        "Build Windows executables",
    )["run"]

    assert "pyinstaller scripts/build/update_guard.spec" in ci_build
    all_build_scripts = "\n".join(
        step.get("run", "")
        for workflow in (RELEASE_WORKFLOW, TEST_SUITE_WORKFLOW)
        for job in yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"].values()
        for step in job.get("steps", [])
    )
    for spec in ("dicepp.spec", "dashboard.spec", "update_guard.spec"):
        assert all_build_scripts.count(f"pyinstaller scripts/build/{spec}") == 1
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


def test_release_packages_the_exact_windows_candidate_tested_by_the_gate():
    release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))

    metadata = release["jobs"]["release-metadata"]
    gate = release["jobs"]["quality-gate"]
    gate_windows = suite["jobs"]["windows-package"]

    assert metadata["outputs"] == {
        key: f"${{{{ steps.release.outputs.{key} }}}}"
        for key in (
            "tag",
            "version",
            "commit_sha",
            "is_prerelease",
            "channel",
            "velopack_version",
            "velopack_channel",
        )
    }
    assert gate["needs"] == "release-metadata"
    assert gate["with"] == {
        "release_tag": "${{ needs.release-metadata.outputs.tag }}",
        "release_version": "${{ needs.release-metadata.outputs.version }}",
        "release_commit_sha": "${{ needs.release-metadata.outputs.commit_sha }}",
    }
    setup_python = next(
        step
        for step in gate_windows["steps"]
        if step.get("uses") == "actions/setup-python@v5"
    )
    assert setup_python["with"]["python-version"] == "3.11"

    record = _workflow_step(
        TEST_SUITE_WORKFLOW,
        "windows-package",
        "Record release candidate provenance",
    )
    upload = _workflow_step(
        TEST_SUITE_WORKFLOW,
        "windows-package",
        "Upload release-ready Windows candidate",
    )
    download = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Download tested Windows candidate",
    )
    validate = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Validate tested Windows candidate",
    )

    assert record["if"] == "inputs.release_tag != ''"
    assert record["shell"] == "pwsh"
    assert "$actualCommit = (git rev-parse HEAD).Trim()" in record["run"]
    assert "uv run --frozen python" in record["run"]
    assert "--expected-commit-sha \"${{ inputs.release_commit_sha }}\"" in record["run"]
    assert '--actual-commit-sha "$actualCommit"' in record["run"]
    assert "${{ github.sha }}" not in record["run"]
    assert upload["with"]["name"] == "dicepp-windows-candidate"
    assert set(upload["with"]["path"].splitlines()) == {
        "dist/DicePP",
        "dist/windows-candidate.json",
    }
    assert upload["with"]["include-hidden-files"] is True
    assert upload["with"]["if-no-files-found"] == "error"
    assert download["with"] == {
        "name": "dicepp-windows-candidate",
        "path": "dist",
    }
    for field in ("tag", "version", "commit_sha"):
        assert f"${{{{ needs.release-metadata.outputs.{field} }}}}" in validate["run"]


def test_release_jobs_consume_one_validated_metadata_derivation():
    release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    job_names = ("build-docker", "windows-build", "publish")
    step_names = [
        step.get("name")
        for job in release["jobs"].values()
        for step in job.get("steps", [])
    ]
    scripts = "\n".join(
        step.get("run", "")
        for job in release["jobs"].values()
        for step in job.get("steps", [])
    )

    assert step_names.count("Derive and validate release metadata") == 1
    assert "Extract version info" not in step_names
    assert "Compute Velopack version and channel" not in step_names
    assert "${GITHUB_REF#refs/tags/}" not in scripts
    for job_name in job_names:
        assert "release-metadata" in release["jobs"][job_name]["needs"]


def test_windows_release_requires_complete_velopack_asset_set():
    validate = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Validate final Windows asset set",
    )["run"]

    assert "win64-Portable.zip" in validate
    assert "win64-Setup.exe" in validate
    velopack_channel = "${{ needs.release-metadata.outputs.velopack_channel }}"
    assert f"releases.{velopack_channel}.json" in validate
    assert f"assets.{velopack_channel}.json" in validate
    assert '"*-full.nupkg"' in validate
    assert "$full.Count -ne 1" in validate


def test_windows_release_smokes_executables_from_the_final_portable():
    job = _workflow_job(RELEASE_WORKFLOW, "windows-build")
    step_names = [step.get("name") for step in job["steps"]]
    smoke = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Smoke test final Windows Portable",
    )
    script = smoke["run"]

    assert step_names.index("Package artifact") < step_names.index(smoke["name"])
    assert "Expand-Archive -LiteralPath $portablePath" in script
    assert (
        'Get-ChildItem -LiteralPath $extractRoot -Recurse -File '
        '-Filter "DicePP-Runtime.exe"'
    ) in script
    assert "$programRoot = $runtimeMatches[0].Directory.FullName" in script
    assert "dist/DicePP" not in script

    for executable in ("$runtime", "$dashboard", "$updateGuard"):
        assert f'Invoke-PackagedExe {executable} @("--version")' in script
        assert f'Invoke-PackagedExe {executable} @("--smoke-check")' in script

    assert "Start-Process" in script
    assert "-WindowStyle Hidden" in script
    assert "-RedirectStandardOutput" in script
    assert "-RedirectStandardError" in script
