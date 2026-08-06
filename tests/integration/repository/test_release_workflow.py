from pathlib import Path

import yaml


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TEST_SUITE_WORKFLOW = ROOT / ".github" / "workflows" / "test-suite.yml"
CANDIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "candidate.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
WINDOWS_PACKAGE_SCRIPT = ROOT / "scripts" / "build" / "assemble_windows_package.ps1"
WINDOWS_FINAL_VALIDATOR = (
    ROOT / "scripts" / "build" / "validate_windows_final_candidate.ps1"
)
LINUX_FINAL_VALIDATOR = (
    ROOT / "scripts" / "build" / "validate_linux_final_candidate.sh"
)
RELEASE_METADATA_REQUIREMENTS = (
    ROOT / "scripts" / "build" / "release-metadata-requirements.txt"
)
VELOPACK_VERSION_FILE = ROOT / "scripts" / "build" / "velopack-tool-version.txt"
VELOPACK_INSTALLER = ROOT / "scripts" / "build" / "install_velopack_tool.ps1"


def _workflow_job(workflow_path: Path, job_name: str) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return workflow["jobs"][job_name]


def _workflow_step(workflow_path: Path, job_name: str, step_name: str) -> dict:
    for step in _workflow_job(workflow_path, job_name)["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"{step_name} step not found in {workflow_path.name}:{job_name}")


def _workflow_call(workflow_path: Path) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return workflow.get("on", workflow.get(True))["workflow_call"]


def _workflow_dispatch(workflow_path: Path) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return workflow.get("on", workflow.get(True))["workflow_dispatch"]


def test_final_candidate_dispatch_binds_every_build_to_an_exact_commit():
    dispatch = _workflow_dispatch(CANDIDATE_WORKFLOW)
    candidate = yaml.safe_load(CANDIDATE_WORKFLOW.read_text(encoding="utf-8"))
    metadata = candidate["jobs"]["candidate-metadata"]
    gate = candidate["jobs"]["quality-gate"]

    assert dispatch["inputs"] == {
        "version": {
            "description": "Exact PEP 440 project version to build (without v prefix)",
            "required": True,
            "type": "string",
        },
        "commit_sha": {
            "description": "Full lowercase 40-character source commit SHA",
            "required": True,
            "type": "string",
        },
    }
    metadata_checkout = next(
        step for step in metadata["steps"] if step.get("uses") == "actions/checkout@v4"
    )
    target_guard = _workflow_step(
        CANDIDATE_WORKFLOW,
        "candidate-metadata",
        "Reject an ambiguous target commit",
    )
    assert target_guard["env"] == {
        "INPUT_COMMIT_SHA": "${{ inputs.commit_sha }}",
        "WORKFLOW_SHA": "${{ github.sha }}",
    }
    assert '"$WORKFLOW_SHA" != "$INPUT_COMMIT_SHA"' in target_guard["run"]
    assert metadata_checkout["with"]["ref"] == "${{ inputs.commit_sha }}"
    derive = _workflow_step(
        CANDIDATE_WORKFLOW,
        "candidate-metadata",
        "Derive metadata from the checked-out source",
    )["run"]
    assert 'actual_commit="$(git rev-parse HEAD)"' in derive
    assert 'actual_commit" != "$INPUT_COMMIT_SHA' in derive
    assert gate["uses"] == "./.github/workflows/test-suite.yml"
    assert gate["with"]["release_commit_sha"] == (
        "${{ needs.candidate-metadata.outputs.commit_sha }}"
    )
    assert candidate["permissions"] == {"contents": "read"}
    assert gate["permissions"] == {"contents": "read", "packages": "write"}
    assert candidate["jobs"]["linux-final"]["permissions"] == {
        "contents": "read",
        "packages": "read",
    }
    for job_name in ("candidate-metadata", "windows-final", "receipt"):
        assert candidate["jobs"][job_name]["permissions"] == {"contents": "read"}

    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))
    for job in suite["jobs"].values():
        checkout = next(
            step
            for step in job["steps"]
            if step.get("uses") == "actions/checkout@v4"
        )
        assert checkout["with"]["ref"] == (
            "${{ inputs.release_commit_sha || github.sha }}"
        )
    for job_name in ("runtime-image", "dashboard-image", "windows-package"):
        verify = _workflow_step(
            TEST_SUITE_WORKFLOW,
            job_name,
            "Verify checked-out source identity",
        )
        assert verify["if"] == "inputs.release_commit_sha != ''"
        assert "git rev-parse HEAD" in verify["run"]


def test_parser_entry_jobs_install_the_hash_pinned_dependency_before_execution():
    expected_install = [
        "python",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-deps",
        "--only-binary=:all:",
        "--require-hashes",
        "-r",
        "scripts/build/release-metadata-requirements.txt",
    ]
    for workflow, job_name, derive_name in (
        (
            CANDIDATE_WORKFLOW,
            "candidate-metadata",
            "Derive metadata from the checked-out source",
        ),
        (
            RELEASE_WORKFLOW,
            "release-metadata",
            "Derive and validate release metadata",
        ),
        (
            CANDIDATE_WORKFLOW,
            "receipt",
            "Generate immutable candidate receipt",
        ),
    ):
        steps = _workflow_job(workflow, job_name)["steps"]
        names = [step.get("name") for step in steps]
        setup_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("uses") == "actions/setup-python@v5"
        )
        install = _workflow_step(
            workflow, job_name, "Install release metadata dependency"
        )
        assert install["run"].replace("\\\n", " ").split() == expected_install
        assert setup_index < names.index(install["name"]) < names.index(derive_name)

    assert RELEASE_METADATA_REQUIREMENTS.read_text(encoding="utf-8") == (
        "packaging==26.2 "
        "--hash=sha256:5fc45236b9446107ff2415ce77c807cee2862cb6fac22b8a73826d0693b0980e\n"
    )


def test_final_candidate_seals_the_complete_public_artifact_contract_without_release():
    workflow_text = CANDIDATE_WORKFLOW.read_text(encoding="utf-8")
    receipt = _workflow_step(
        CANDIDATE_WORKFLOW,
        "receipt",
        "Generate immutable candidate receipt",
    )["run"]
    upload = _workflow_step(
        CANDIDATE_WORKFLOW,
        "receipt",
        "Upload sealed final candidate",
    )
    windows = _workflow_step(
        CANDIDATE_WORKFLOW,
        "windows-final",
        "Build final Velopack artifacts",
    )["run"]
    linux = _workflow_step(
        CANDIDATE_WORKFLOW,
        "linux-final",
        "Package final Linux bundle",
    )["run"]
    candidate_windows_validation = _workflow_step(
        CANDIDATE_WORKFLOW,
        "windows-final",
        "Validate final Windows candidate bytes",
    )["run"]
    release_windows_validation = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Validate final Windows asset set",
    )["run"]

    assert "vpk pack" in windows
    assert "generate_velopack_bundle.py" in windows
    assert "docker save" in linux
    assert "generate_linux_package_manifest.py" in linux
    assert "validate_windows_final_candidate.ps1" in candidate_windows_validation
    assert "validate_windows_final_candidate.ps1" in release_windows_validation
    assert "candidate_receipt.py" in receipt
    assert receipt.count("--container ") == 2
    assert receipt.count("--toolchain ") == 6
    assert "--package-tree-sha256" in receipt
    assert '--workflow-sha "$WORKFLOW_SHA"' in receipt
    assert upload["with"]["path"] == "dist/final"
    assert upload["with"]["retention-days"] == 30
    assert "gh release" not in workflow_text
    assert "git push" not in workflow_text
    assert "tags:" not in workflow_text

    release_manifest = _workflow_step(
        RELEASE_WORKFLOW,
        "publish",
        "Generate release machine contract",
    )["run"]
    for identity in (
        "windows:amd64:portable:",
        "windows:amd64:setup:",
        "windows:amd64:velopack-bundle:",
        "linux:amd64:linux-bundle:",
    ):
        assert identity in release_manifest
    for filename in (
        "win64-Portable.zip",
        "win64-Setup.exe",
        "velopack.win-x64.zip",
        "linux-amd64.zip",
    ):
        assert filename in workflow_text
        assert filename in release_manifest


def test_shared_windows_final_validator_covers_portable_setup_and_exact_set():
    validator = WINDOWS_FINAL_VALIDATOR.read_text(encoding="utf-8")

    assert "Compare-Object" in validator
    assert "Final Windows asset set is incomplete, renamed, or ambiguous" in validator
    for executable in ("DicePP-Runtime.exe", "DicePP.exe", "DicePP-UpdateGuard.exe"):
        assert executable in validator
    assert validator.count("--smoke-check") == 3
    assert "test_windows_package_detached_launch.py" in validator
    assert '--silent", "--installto"' in validator
    assert 'Join-Path $installRoot "DicePP.exe"' in validator
    assert 'Join-Path $installRoot "current\\DicePP.exe"' in validator


def _create_release_script() -> str:
    return _workflow_step(RELEASE_WORKFLOW, "publish", "Create GitHub Release")["run"]


def test_create_release_adds_auditable_evidence_only_for_automatic_upgrade():
    script = _create_release_script()

    for asset in (
        "DicePP-${TAG}-win64-Setup.exe",
        "DicePP-${TAG}-win64-Portable.zip",
        "DicePP-${TAG}-linux-amd64.zip",
        "velopack.win-x64.zip",
        "dicepp-release.json",
        "docker-compose.yml",
    ):
        assert f'"{asset}"' in script
    assert 'for asset in "${ASSETS[@]}"' in script
    assert (
        'if [ "${{ needs.release-metadata.outputs.automatic_upgrade }}" = "true" ]'
        in script
    )
    assert 'ASSETS+=("dicepp-upgrade-evidence.json")' in script
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
    publish = _workflow_job(RELEASE_WORKFLOW, "publish")
    steps = [step.get("name") for step in publish["steps"]]
    verify = _workflow_step(
        RELEASE_WORKFLOW,
        "publish",
        "Pull and verify promoted images from GHCR",
    )["run"]
    step = _workflow_step(RELEASE_WORKFLOW, "publish", "Package Linux amd64 release bundle")
    script = step["run"]
    normalized_package = " ".join(script.replace("\\\n", "").split())

    assert "promote-docker" in publish["needs"]
    assert steps.index("Pull and verify promoted images from GHCR") < steps.index(
        "Package Linux amd64 release bundle"
    )
    assert "${{ needs.promote-docker.outputs.runtime_digest }}" in verify
    assert "${{ needs.promote-docker.outputs.runtime_image_id }}" in verify
    assert "${{ needs.promote-docker.outputs.dashboard_digest }}" in verify
    assert "${{ needs.promote-docker.outputs.dashboard_image_id }}" in verify
    assert verify.count("docker buildx imagetools inspect") == 2
    assert verify.count("docker image inspect --format '{{.Id}}'") == 2
    assert (
        'EXPECTED_BOT_DIGEST="${{ needs.promote-docker.outputs.runtime_digest }}"'
        in verify
    )
    assert (
        'EXPECTED_BOT_IMAGE_ID="${{ needs.promote-docker.outputs.runtime_image_id }}"'
        in verify
    )
    assert (
        'EXPECTED_DASHBOARD_DIGEST="${{ needs.promote-docker.outputs.dashboard_digest }}"'
        in verify
    )
    assert (
        'EXPECTED_DASHBOARD_IMAGE_ID="${{ needs.promote-docker.outputs.dashboard_image_id }}"'
        in verify
    )
    assert 'docker buildx imagetools inspect "$BOT_IMAGE"' in verify
    assert 'docker buildx imagetools inspect "$DASHBOARD_IMAGE"' in verify
    assert 'docker image inspect --format \'{{.Id}}\' "$BOT_IMAGE"' in verify
    assert 'docker image inspect --format \'{{.Id}}\' "$DASHBOARD_IMAGE"' in verify
    assert '[ "$ACTUAL_BOT_DIGEST" != "$EXPECTED_BOT_DIGEST" ]' in verify
    assert (
        '[ "$ACTUAL_DASHBOARD_DIGEST" != "$EXPECTED_DASHBOARD_DIGEST" ]' in verify
    )
    assert '[ "$ACTUAL_BOT_IMAGE_ID" != "$EXPECTED_BOT_IMAGE_ID" ]' in verify
    assert (
        '[ "$ACTUAL_DASHBOARD_IMAGE_ID" != "$EXPECTED_DASHBOARD_IMAGE_ID" ]'
        in verify
    )
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
    assert (
        '--image "${BOT_IMAGE}" --image "${DASHBOARD_IMAGE}" '
        '--image-id "${BOT_IMAGE_ID}" --image-id "${DASHBOARD_IMAGE_ID}"'
        in normalized_package
    )
    assert "--minimum-manager-version" not in script
    assert "--automatic-upgrade" not in script


def test_final_linux_zip_passes_shared_offline_round_trip_before_receipt_or_release():
    release = _workflow_job(RELEASE_WORKFLOW, "publish")
    release_names = [step.get("name") for step in release["steps"]]
    release_verify = _workflow_step(
        RELEASE_WORKFLOW,
        "publish",
        "Verify final Linux bundle offline round trip",
    )
    candidate = _workflow_job(CANDIDATE_WORKFLOW, "linux-final")
    candidate_names = [step.get("name") for step in candidate["steps"]]
    candidate_verify = _workflow_step(
        CANDIDATE_WORKFLOW,
        "linux-final",
        "Validate final Linux bundle bytes",
    )
    validator = LINUX_FINAL_VALIDATOR.read_text(encoding="utf-8")
    normalized = " ".join(validator.replace("\\\n", "").split())
    python_blocks = [
        block.split("\nPY", 1)[0]
        for block in validator.split("<<'PY'\n")[1:]
    ]

    assert release_names.index("Package Linux amd64 release bundle") < release_names.index(
        release_verify["name"]
    )
    assert release_names.index(release_verify["name"]) < release_names.index(
        "Generate release machine contract"
    )
    assert candidate_names.index("Package final Linux bundle") < candidate_names.index(
        candidate_verify["name"]
    )
    invocation = "bash scripts/build/validate_linux_final_candidate.sh"
    assert invocation in release_verify["run"]
    assert invocation in candidate_verify["run"]
    assert release_verify["env"] == {
        "TAG": "${{ needs.release-metadata.outputs.tag }}",
        "RUNTIME_IMAGE_ID": "${{ needs.promote-docker.outputs.runtime_image_id }}",
        "DASHBOARD_IMAGE_ID": "${{ needs.promote-docker.outputs.dashboard_image_id }}",
    }
    assert candidate_verify["env"] == {
        "TAG": "${{ needs.candidate-metadata.outputs.tag }}",
        "RUNTIME_IMAGE_ID": "${{ needs.quality-gate.outputs.runtime_candidate_image_id }}",
        "DASHBOARD_IMAGE_ID": "${{ needs.quality-gate.outputs.dashboard_candidate_image_id }}",
    }

    purge_index = validator.index("# Delete all local references and content")
    absent_index = validator.index(
        "pre-package image identity still exists", purge_index
    )
    unzip_index = validator.index('unzip -q "$PACKAGE_ZIP"')
    purge = validator[purge_index:absent_index]
    assert "timeout 60s docker image rm --force" in purge
    assert '"$EXPECTED_BOT_IMAGE_ID" "$EXPECTED_DASHBOARD_IMAGE_ID"' in purge
    assert '"$BOT_IMAGE" "$DASHBOARD_IMAGE"' in purge
    assert '"$EXPECTED_BOT_IMAGE_ID" "$EXPECTED_DASHBOARD_IMAGE_ID"' in purge
    assert purge_index < absent_index < unzip_index
    assert "sha256sum -c checksums.sha256" in validator
    assert "validate_linux_bundle_candidate.py" in validator
    assert (
        '--expected-image bot "$BOT_IMAGE" "$EXPECTED_BOT_IMAGE_ID"'
        in normalized
    )
    assert (
        '--expected-image dashboard "$DASHBOARD_IMAGE" "$EXPECTED_DASHBOARD_IMAGE_ID"'
        in normalized
    )
    assert 'zstd -d "$IMAGE_ARCHIVE_ZST"' in validator
    assert "timeout 120s docker load" in validator
    assert '[ "$LOADED_BOT_IMAGE_ID" != "$EXPECTED_BOT_IMAGE_ID" ]' in validator
    assert (
        '[ "$LOADED_DASHBOARD_IMAGE_ID" != "$EXPECTED_DASHBOARD_IMAGE_ID" ]'
        in validator
    )
    assert '-f "${SMOKE_ROOT}/docker-compose.yml" config --format json' in normalized
    assert 'services["bot"]["image"] != bot_image' in validator
    assert 'for role in ("dashboard", "manager")' in validator
    assert 'for key in ("build", "container_name", "ports")' in validator
    assert 'service["restart"] = "no"' in validator
    assert "DICEPP_MANAGER_RELEASE_SCHEDULER" in validator
    assert "up -d --pull never --wait --wait-timeout 180" in normalized
    assert "timeout 210s docker compose" in validator
    assert "for service in bot dashboard manager" in validator
    assert "for service in dashboard manager" in validator
    assert "ps --status running -q" in normalized
    assert "{{.State.Health.Status}}" in validator
    assert "trap cleanup EXIT" in validator
    assert "down --volumes --remove-orphans" in normalized
    assert 'RUNNER_TEMP_ROOT="$(realpath -e -- "$RUNNER_TEMP_INPUT")"' in validator
    assert '[ -L "$SMOKE_ROOT_CREATED" ]' in validator
    assert '"$(dirname -- "$SMOKE_ROOT")" != "$RUNNER_TEMP_ROOT"' in validator
    assert "^dicepp-linux-bundle\\.[[:alnum:]]{6}$" in validator
    assert "SMOKE_ROOT_IDENTITY=\"$(stat -Lc '%d:%i'" in validator
    assert "validate_smoke_root_for_removal()" in validator
    assert '[ "$identity" = "$SMOKE_ROOT_IDENTITY" ]' in validator
    assert "refusing to remove unverified smoke directory" in validator
    assert validator.count("uv run --frozen python") == 3
    assert not any(line.startswith("python ") for line in validator.splitlines())
    assert "|| true" not in validator
    assert "local main_status=$?" in validator
    assert "local cleanup_status=0" in validator
    assert 'if [ -f "$SMOKE_COMPOSE" ]' in validator
    assert "timeout 60s docker compose" in validator
    assert "timeout 60s docker image rm --force" in validator
    assert "timeout 30s sudo --non-interactive rm -rf" in validator
    assert "offline smoke directory still exists after cleanup" in validator
    assert 'if [ "$main_status" -ne 0 ]' in validator
    assert 'exit "$main_status"' in validator
    assert 'exit "$cleanup_status"' in validator
    assert len(python_blocks) == 2
    for index, block in enumerate(python_blocks):
        compile(block, f"linux-final-python-{index}", "exec")


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
    assert "generate_velopack_bundle.py" in script
    assert '--output "velopack.win-x64.zip"' in script
    assert "releases." not in script
    assert "assets." not in script
    assert "install_velopack_tool.ps1" in script


def test_windows_final_jobs_install_and_record_one_pinned_velpack_tool_version():
    candidate = _workflow_job(CANDIDATE_WORKFLOW, "windows-final")
    install = _workflow_step(
        CANDIDATE_WORKFLOW, "windows-final", "Install build dependencies"
    )
    record = _workflow_step(
        CANDIDATE_WORKFLOW, "windows-final", "Record Windows toolchain"
    )
    release_pack = _workflow_step(
        RELEASE_WORKFLOW, "windows-build", "Package artifact"
    )
    installer = VELOPACK_INSTALLER.read_text(encoding="utf-8")
    workflow_text = (
        CANDIDATE_WORKFLOW.read_text(encoding="utf-8")
        + RELEASE_WORKFLOW.read_text(encoding="utf-8")
    )

    assert install["id"] == "install-toolchain"
    assert install["shell"] == "pwsh"
    assert "install_velopack_tool.ps1" in install["run"]
    assert "install_velopack_tool.ps1" in release_pack["run"]
    assert candidate["outputs"]["velopack_version"] == (
        "${{ steps.install-toolchain.outputs.velopack_version }}"
    )
    assert "vpk --version" not in record["run"]
    assert "dotnet tool install -g vpk --version 1.2.0" not in workflow_text
    assert VELOPACK_VERSION_FILE.read_text(encoding="utf-8") == "1.2.0\n"
    assert "dotnet tool install -g vpk --version $version" in installer
    assert '"velopack_version=$version" >> $env:GITHUB_OUTPUT' in installer


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
    assert "windows:amd64:velopack-bundle:velopack.win-x64.zip" in script
    assert "linux:amd64:linux-bundle:" in script
    assert '--commit-sha "${{ needs.release-metadata.outputs.commit_sha }}"' in script
    assert "linux:runtime-manifest:" in script
    assert "linux:dashboard-manifest:" in script
    assert "windows:package-tree:" in script
    assert "--upgrade-matrix scripts/build/upgrade_matrix.json" in script
    assert "--upgrade-evidence dist/upgrade-evidence/evidence.json" in script


def test_automatic_upgrade_evidence_is_required_before_public_promotion():
    release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    gate = release["jobs"]["upgrade-evidence-gate"]
    steps = gate["steps"]
    download = next(
        step
        for step in steps
        if step.get("name") == "Download commit-bound upgrade evidence"
    )
    verify = next(
        step
        for step in steps
        if step.get("name") == "Require evidence before publishing automatic upgrades"
    )

    assert set(gate["needs"]) == {"release-metadata", "quality-gate"}
    assert gate["permissions"] == {"actions": "read", "contents": "read"}
    automatic_upgrade = (
        "needs.release-metadata.outputs.automatic_upgrade == 'true'"
    )
    assert download["if"] == automatic_upgrade
    assert download["continue-on-error"] is True
    assert download["with"] == {
        "name": "dicepp-upgrade-evidence",
        "path": "dist/upgrade-evidence",
    }
    assert verify["if"] == automatic_upgrade
    assert "upgrade_evidence.py verify-release" in verify["run"]
    assert "needs.quality-gate.outputs.runtime_candidate_digest" in verify["run"]
    assert "needs.quality-gate.outputs.dashboard_candidate_digest" in verify["run"]
    assert "needs.quality-gate.outputs.windows_candidate_digest" in verify["run"]
    assert verify["run"].count("--candidate ") == 3
    for key in (
        "linux:runtime-manifest:",
        "linux:dashboard-manifest:",
        "windows:package-tree:",
    ):
        assert verify["run"].count(key) == 1
    for job_name in ("promote-docker", "windows-build"):
        assert "upgrade-evidence-gate" in release["jobs"][job_name]["needs"]

    publish_steps = release["jobs"]["publish"]["steps"]
    stage = next(
        step
        for step in publish_steps
        if step.get("name") == "Stage verified upgrade evidence for audit"
    )
    assert stage["if"] == automatic_upgrade
    assert (
        "cp dist/upgrade-evidence/evidence.json dicepp-upgrade-evidence.json"
        in stage["run"]
    )
    names = [step.get("name") for step in publish_steps]
    assert names.index("Generate release machine contract") < names.index(stage["name"])
    assert names.index(stage["name"]) < names.index("Create GitHub Release")


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
    assert 'IMAGE="dicepp-runtime:ci"' in build["run"]
    assert 'docker build -f Dockerfile -t "$IMAGE" .' in build["run"]
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
    ci_manager_smoke = _workflow_step(
        TEST_SUITE_WORKFLOW, "dashboard-image", "Smoke test Manager image"
    )["run"]

    assert "/api/auth/status" in ci_smoke
    assert "smoke_dashboard_control_channel" not in ci_smoke
    assert "/ws/control" not in ci_smoke

    assert "Version: 0.0.0-stale" in seed
    assert "python -m dashboard --version" in ci_smoke
    assert "DicePP Dashboard v$expected" in ci_smoke
    assert "test ! -e /app/src/dicepp.egg-info" in ci_smoke
    assert "test ! -d /app/src/plugins/DicePP/__pycache__" in ci_smoke

    assert "python -m dicepp_manager" in ci_manager_smoke
    assert "dicepp-manager-smoke" in ci_manager_smoke
    assert "DICEPP_MANAGER_RELEASE_SCHEDULER=false" in ci_manager_smoke
    assert "/app/manager/state/api-token" in ci_manager_smoke
    assert "Authorization: Bearer $token" in ci_manager_smoke
    assert "http://127.0.0.1:4091/v1/health" in ci_manager_smoke
    assert "smoke_dashboard_control_channel" not in ci_manager_smoke
    assert "/ws/control" not in ci_manager_smoke

    assert "from importlib.metadata import version" in ci_manager_smoke
    assert 'assert version("dicepp") == expected' in ci_manager_smoke

    ci_cleanup = _workflow_step(
        TEST_SUITE_WORKFLOW, "dashboard-image", "Clean up image smoke resources"
    )["run"]
    assert "dicepp-manager-smoke" in ci_cleanup
    assert "docker volume rm -f dicepp-manager-smoke" in ci_cleanup


def test_release_gate_publishes_only_images_that_passed_the_image_smokes():
    call = _workflow_call(TEST_SUITE_WORKFLOW)
    release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    gate = release["jobs"]["quality-gate"]

    expected_outputs = {
        f"{role}_candidate_{field}": {
            "description": call["outputs"][f"{role}_candidate_{field}"]["description"],
            "value": (
                "${{ jobs."
                + ("runtime-image" if role == "runtime" else "dashboard-image")
                + f".outputs.candidate_{field} }}}}"
            ),
        }
        for role in ("runtime", "dashboard")
        for field in ("ref", "digest", "image_id")
    }
    expected_outputs["windows_candidate_digest"] = {
        "description": "SHA-256 identity of the tested Windows package tree",
        "value": "${{ jobs.windows-package.outputs.candidate_digest }}",
    }
    assert call["outputs"] == expected_outputs
    assert gate["permissions"] == {"contents": "read", "packages": "write"}

    for job_name, role in (
        ("runtime-image", "Runtime"),
        ("dashboard-image", "Dashboard"),
    ):
        job = _workflow_job(TEST_SUITE_WORKFLOW, job_name)
        names = [step.get("name") for step in job["steps"]]
        publish = _workflow_step(
            TEST_SUITE_WORKFLOW,
            job_name,
            f"Publish tested {role} candidate",
        )
        login = _workflow_step(
            TEST_SUITE_WORKFLOW,
            job_name,
            "Login to GHCR for release candidate",
        )
        last_smoke = (
            "Smoke test Runtime image"
            if job_name == "runtime-image"
            else "Smoke test Manager image"
        )

        assert names.index(last_smoke) < names.index(publish["name"])
        # 镜像作业不得显式声明 permissions: 普通 CI 调用方只授予 contents:read,
        # 嵌套作业声明 packages:write 会触发 GitHub 启动校验失败; release 路径
        # 由 quality-gate 调用方授予 packages:write, 被调用作业继承即可.
        assert "permissions" not in job
        assert login["if"] == "inputs.release_tag != ''"
        assert login["with"]["password"] == "${{ github.token }}"
        assert publish["if"] == "inputs.release_tag != ''"
        assert publish["id"] == "publish-candidate"
        assert "candidate-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in _workflow_step(
            TEST_SUITE_WORKFLOW,
            job_name,
            f"Build {role} image",
        )["run"]
        assert "docker push \"$IMAGE\"" in publish["run"]
        assert "docker buildx imagetools inspect" in publish["run"]
        assert "PULLED_IMAGE_ID" in publish["run"]
        assert "no automatic GHCR deletion" in publish["run"]


def test_ordinary_ci_never_requests_or_publishes_release_candidates():
    ci = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    caller = ci["jobs"]["test-suite"]
    suite = yaml.safe_load(TEST_SUITE_WORKFLOW.read_text(encoding="utf-8"))

    assert ci["permissions"] == {"contents": "read"}
    assert "with" not in caller
    assert "permissions" not in caller
    for job_name in ("quick", "windows-package", "full"):
        assert suite["jobs"][job_name]["permissions"] == {"contents": "read"}
    for job_name in ("runtime-image", "dashboard-image"):
        job = suite["jobs"][job_name]
        login_steps = [
            step for step in job["steps"] if step.get("uses") == "docker/login-action@v3"
        ]
        push_steps = [
            step for step in job["steps"] if "docker push" in step.get("run", "")
        ]
        assert len(login_steps) == len(push_steps) == 1
        assert login_steps[0]["if"] == "inputs.release_tag != ''"
        assert push_steps[0]["if"] == "inputs.release_tag != ''"


def test_release_promotes_candidate_manifest_digests_without_rebuilding():
    release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    promotion = release["jobs"]["promote-docker"]
    script = _workflow_step(
        RELEASE_WORKFLOW,
        "promote-docker",
        "Promote candidates without rebuilding",
    )["run"]
    normalized = " ".join(script.replace("\\\n", "").split())

    assert set(promotion["needs"]) == {
        "release-metadata",
        "quality-gate",
        "upgrade-evidence-gate",
    }
    assert promotion["outputs"] == {
        "runtime_digest": "${{ steps.promote.outputs.runtime_digest }}",
        "runtime_image_id": "${{ steps.promote.outputs.runtime_image_id }}",
        "dashboard_digest": "${{ steps.promote.outputs.dashboard_digest }}",
        "dashboard_image_id": "${{ steps.promote.outputs.dashboard_image_id }}",
    }
    assert "docker build" not in script.replace("docker buildx imagetools", "")
    assert "docker buildx imagetools create" in script
    assert "--prefer-index=false" in script
    for identity in (
        "runtime_candidate_ref",
        "runtime_candidate_digest",
        "runtime_candidate_image_id",
        "dashboard_candidate_ref",
        "dashboard_candidate_digest",
        "dashboard_candidate_image_id",
    ):
        assert f"${{{{ needs.quality-gate.outputs.{identity} }}}}" in script
    assert (
        'RUNTIME_CANDIDATE="${{ needs.quality-gate.outputs.runtime_candidate_ref }}"'
        in script
    )
    assert (
        'RUNTIME_DIGEST="${{ needs.quality-gate.outputs.runtime_candidate_digest }}"'
        in script
    )
    assert (
        'RUNTIME_IMAGE_ID="${{ needs.quality-gate.outputs.runtime_candidate_image_id }}"'
        in script
    )
    assert (
        'DASHBOARD_CANDIDATE="${{ needs.quality-gate.outputs.dashboard_candidate_ref }}"'
        in script
    )
    assert (
        'DASHBOARD_DIGEST="${{ needs.quality-gate.outputs.dashboard_candidate_digest }}"'
        in script
    )
    assert (
        'DASHBOARD_IMAGE_ID="${{ needs.quality-gate.outputs.dashboard_candidate_image_id }}"'
        in script
    )
    assert 'if [ "$promoted_digest" != "$candidate_digest" ]' in script
    assert (
        'promote_exact_digest "$RUNTIME_CANDIDATE" "$RUNTIME_DIGEST" '
        '"${RUNTIME_REPOSITORY}:${TAG}"'
    ) in normalized
    assert (
        'promote_exact_digest "$DASHBOARD_CANDIDATE" "$DASHBOARD_DIGEST" '
        '"${DASHBOARD_REPOSITORY}:${TAG}"'
    ) in normalized
    assert (
        'promote_exact_digest "$RUNTIME_CANDIDATE" "$RUNTIME_DIGEST" '
        '"${RUNTIME_REPOSITORY}:latest"'
    ) in normalized
    assert (
        'promote_exact_digest "$DASHBOARD_CANDIDATE" "$DASHBOARD_DIGEST" '
        '"${DASHBOARD_REPOSITORY}:latest"'
    ) in normalized
    assert 'echo "runtime_image_id=$RUNTIME_IMAGE_ID"' in script
    assert 'echo "dashboard_image_id=$DASHBOARD_IMAGE_ID"' in script
    assert '${{ needs.release-metadata.outputs.is_prerelease }}' in script


def test_create_release_fails_before_publishing_if_any_fixed_asset_is_missing():
    script = _create_release_script()

    assert 'if [ ! -f "$asset" ]' in script
    assert 'echo "Missing final release asset: $asset"' in script
    assert "nullglob" not in script


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
            "automatic_upgrade",
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
    assert record["id"] == "record-candidate"
    assert record["shell"] == "pwsh"
    assert "$actualCommit = (git rev-parse HEAD).Trim()" in record["run"]
    assert "uv run --frozen python" in record["run"]
    assert "--expected-commit-sha \"${{ inputs.release_commit_sha }}\"" in record["run"]
    assert '--actual-commit-sha "$actualCommit"' in record["run"]
    assert "--package-root dist/DicePP" in record["run"]
    assert '--github-output "$env:GITHUB_OUTPUT"' in record["run"]
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
    assert "--package-root dist/DicePP" in validate["run"]


def test_release_jobs_consume_one_validated_metadata_derivation():
    release = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    job_names = ("promote-docker", "windows-build", "publish")
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


def test_windows_release_requires_single_velopack_bundle_asset():
    validate = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Validate final Windows asset set",
    )["run"]

    assert "win64-Portable.zip" in validate
    assert "win64-Setup.exe" in validate
    assert "velopack.win-x64.zip" in validate


def test_windows_package_job_runs_all_real_package_smokes():
    smoke = _workflow_step(
        TEST_SUITE_WORKFLOW,
        "windows-package",
        "Run Windows package smokes",
    )

    assert smoke["env"]["DICEPP_WINDOWS_PACKAGE_SMOKE"] == "1"
    assert "pytest tests/system/package/windows " in smoke["run"]
    assert "test_windows_package_playwright.py" not in smoke["run"]


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
    assert '$stableDashboard = Join-Path $extractRoot "DicePP.exe"' in script
    assert "test_windows_package_detached_launch.py" in script


def test_windows_release_installs_and_smokes_the_final_setup():
    job = _workflow_job(RELEASE_WORKFLOW, "windows-build")
    step_names = [step.get("name") for step in job["steps"]]
    setup = _workflow_step(
        RELEASE_WORKFLOW,
        "windows-build",
        "Smoke test final Windows Setup install",
    )
    script = setup["run"]

    assert step_names.index("Package artifact") < step_names.index(setup["name"])
    assert "--silent" in script
    assert "--installto" in script
    assert "WaitForExit(20000)" in script
    assert 'Join-Path $installRoot "DicePP.exe"' in script
    assert 'Join-Path $installRoot "current\\DicePP.exe"' in script
    assert "test_windows_package_detached_launch.py" in script
