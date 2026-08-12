import ast
import json
import re
import tomllib
from itertools import product
from pathlib import Path

import yaml


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CANDIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "candidate.yml"
TEST_SUITE_WORKFLOW = ROOT / ".github" / "workflows" / "test-suite.yml"
SYNC_WORKFLOW = ROOT / ".github" / "workflows" / "sync2gitee.yml"
PYPROJECT = ROOT / "pyproject.toml"
RELEASE_README = ROOT / "docs" / "releases" / "README.md"
VERSION_RELEASE_SKILL = ROOT / "docs" / "agent" / "skills-dev" / "version-release" / "SKILL.md"
UPDATES_DOC = ROOT / "docs" / "updates.md"


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _trigger(workflow: dict, name: str) -> dict:
    return workflow.get("on", workflow.get(True))[name]


def _job(path: Path, name: str) -> dict:
    return _workflow(path)["jobs"][name]


def _step(path: Path, job_name: str, step_name: str) -> dict:
    for step in _job(path, job_name)["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"missing step {path.name}:{job_name}:{step_name}")


def _job_text(path: Path, job_name: str) -> str:
    return yaml.safe_dump(_job(path, job_name), sort_keys=False)


def _evaluate_github_gate(expression: str, values: dict[str, object]) -> bool:
    rendered = expression
    for name in sorted(values, key=len, reverse=True):
        rendered = rendered.replace(name, repr(values[name]))
    rendered = rendered.replace("always()", "True")
    rendered = rendered.replace("&&", " and ").replace("||", " or ")
    rendered = re.sub(r"(?<!['\"])\btrue\b(?!['\"])", "True", rendered)
    rendered = re.sub(r"(?<!['\"])\bfalse\b(?!['\"])", "False", rendered)
    tree = ast.parse(rendered, mode="eval")
    allowed = (
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Constant,
    )
    assert all(isinstance(node, allowed) for node in ast.walk(tree)), rendered

    def evaluate(node: ast.AST) -> object:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            items = [bool(evaluate(item)) for item in node.values]
            return all(items) if isinstance(node.op, ast.And) else any(items)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left = evaluate(node.left)
            right = evaluate(node.comparators[0])
            return left == right if isinstance(node.ops[0], ast.Eq) else left != right
        raise AssertionError(f"unsupported gate node: {ast.dump(node)}")

    return bool(evaluate(tree))


def test_windows_candidate_excludes_transient_runtime_state_symmetrically():
    cleanup = _step(
        TEST_SUITE_WORKFLOW,
        "windows-package",
        "Record release candidate provenance",
    )["run"]
    upload = _step(
        TEST_SUITE_WORKFLOW,
        "windows-package",
        "Upload release-ready Windows candidate",
    )["with"]["path"]

    for path in (
        "dist/DicePP/manager/state/api-token",
        "dist/DicePP/manager/control",
        "dist/DicePP/manager/packages",
    ):
        assert f'"{path}"' in cleanup
    for exclusion in (
        "!dist/DicePP/manager/state/api-token",
        "!dist/DicePP/manager/control/**",
        "!dist/DicePP/manager/packages/**",
    ):
        assert exclusion in upload


def test_windows_upgrade_matrix_has_bounded_job_timeout():
    assert _job(CANDIDATE_WORKFLOW, "windows-upgrade-matrix")["timeout-minutes"] == 60


def test_promotion_requires_an_explicit_candidate_run_and_artifact_per_version():
    workflow = _workflow(RELEASE_WORKFLOW)
    dispatch = _trigger(workflow, "workflow_dispatch")

    assert set(dispatch["inputs"]) == {
        "version",
        "candidate_run_id",
        "candidate_artifact_id",
    }
    assert all(item["required"] for item in dispatch["inputs"].values())
    assert workflow["concurrency"] == {
        "group": "promote-release-global",
        "cancel-in-progress": False,
    }
    assert "push" not in workflow.get("on", workflow.get(True))


def test_promotion_requires_no_admin_preflight_secret_or_environment():
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    for removed_contract in (
        "RELEASE_PREFLIGHT_TOKEN",
        "RELEASE_TAG_RULESET_ID",
        "RELEASE_GITHUB_ACTIONS_APP_ID",
        "immutable-releases",
        "actions/permissions/workflow",
        "actions/permissions\"",
        "environments/release",
        "rulesets/",
        "bypass_actors",
        "required_reviewers",
    ):
        assert removed_contract not in text
    assert "environment" not in _job(RELEASE_WORKFLOW, "promote")


def test_read_only_gate_authenticates_every_selected_github_identity():
    workflow = _workflow(RELEASE_WORKFLOW)
    verify = workflow["jobs"]["verify-candidate"]
    selection = _step(
        RELEASE_WORKFLOW,
        "verify-candidate",
        "Validate run, workflow, attempt, head, and artifact ownership",
    )["run"]

    assert workflow["permissions"] == {
        "actions": "read",
        "contents": "read",
        "packages": "read",
    }
    assert verify["permissions"] == workflow["permissions"]
    for identity in (
        ".workflow_id run.json",
        '"$RUN_PATH" != ".github/workflows/candidate.yml"',
        ".event run.json",
        ".conclusion run.json",
        ".head_sha run.json",
        ".run_attempt run.json",
        ".head_branch run.json",
        ".repository.full_name run.json",
        ".workflow_run.id artifact.json",
        ".workflow_run.repository_id artifact.json",
        ".workflow_run.head_repository_id artifact.json",
        ".workflow_run.head_sha artifact.json",
    ):
        assert identity in selection
    assert 'EXPECTED_NAME="dicepp-final-candidate-${RUN_ID}-${RUN_ATTEMPT}"' in selection
    assert "^sha256:[0-9a-f]{64}$" in selection
    assert "Promotion workflow must run from the current default-branch HEAD" in selection
    assert '[ "$HEAD_SHA" != "$DEFAULT_SHA" ]' in selection
    assert '(.size_in_bytes | type) == "number"' in selection
    assert ".size_in_bytes == (.size_in_bytes | floor)" in selection


def test_artifact_archive_and_each_candidate_file_are_verified_before_promotion():
    steps = _job(RELEASE_WORKFLOW, "verify-candidate")["steps"]
    checkout_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    download_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name")
        == "Download and authenticate the selected artifact archive"
    )
    download = _step(
        RELEASE_WORKFLOW,
        "verify-candidate",
        "Download and authenticate the selected artifact archive",
    )["run"]
    verify = _step(
        RELEASE_WORKFLOW,
        "verify-candidate",
        "Verify receipt and every sealed file digest",
    )["run"]
    reverify = _step(
        RELEASE_WORKFLOW,
        "promote",
        "Re-verify sealed bytes before publishing",
    )["run"]

    assert checkout_index < download_index
    assert "sha256sum candidate-artifact.zip" in download
    assert 'ACTUAL_DIGEST" != "$EXPECTED_DIGEST' in download
    assert "path.name != member.filename" in download
    assert "stat.S_ISLNK" in download
    assert "promotion_candidate.py" in verify
    for argument in (
        "--candidate-root",
        "--receipt",
        "--repository",
        "--workflow-ref",
        "--run-id",
        "--run-attempt",
        "--artifact-name",
        "--commit-sha",
        "--version",
        "--github-output",
    ):
        assert argument in verify
    assert "promotion_candidate.py" in reverify
    assert "EXPECTED_RECEIPT_SHA256" in reverify


def test_candidate_container_registry_identities_are_checked_before_any_write():
    verify = _step(
        RELEASE_WORKFLOW,
        "verify-candidate",
        "Verify candidate container manifests before any write",
    )["run"]

    assert "${ref%@*}@${expected_digest}" in verify
    assert "{{.Manifest.Digest}}" in verify
    assert "--raw | jq -r .config.digest" in verify
    assert "expected_image_id" in verify
    assert "docker buildx imagetools create" not in _job_text(
        RELEASE_WORKFLOW, "verify-candidate"
    )
    assert "gh release" not in _job_text(RELEASE_WORKFLOW, "verify-candidate")


def test_promotion_consumes_original_bytes_without_any_build_or_repack_step():
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    for forbidden in (
        "vpk pack",
        "docker build ",
        "docker buildx build",
        "docker save",
        "docker image save",
        "Compress-Archive",
        "zip -",
        "generate_release_manifest.py",
        "generate_linux_package_manifest.py",
    ):
        assert forbidden not in text
    assert "gh release upload" in text
    assert "dist/candidate/${asset}" in text


def test_every_immutable_resource_is_compared_before_creation_or_retag():
    preflight = _step(
        RELEASE_WORKFLOW,
        "promote",
        "Preflight every immutable public resource",
    )["run"]
    promote = _step(
        RELEASE_WORKFLOW,
        "promote",
        "Promote immutable version image manifests",
    )["run"]
    upload = _step(
        RELEASE_WORKFLOW,
        "promote",
        "Upload only missing original candidate bytes",
    )["run"]

    assert "Existing version tag does not identify the candidate commit" in preflight
    assert "Candidate is no longer the current default-branch HEAD" in preflight
    assert "Release asset digest differs" in preflight
    assert "public Release is incomplete" in preflight
    assert "Existing immutable image tag has a different manifest digest" in preflight
    assert "imagetools inspect" in preflight
    assert "imagetools inspect" in promote
    assert "imagetools create --prefer-index=false" in promote
    assert "--clobber" not in upload
    assert "--force" not in RELEASE_WORKFLOW.read_text(encoding="utf-8")


def test_release_is_staged_as_a_draft_and_latest_is_the_final_mutation():
    steps = _job(RELEASE_WORKFLOW, "promote")["steps"]
    names = [step.get("name") for step in steps]

    expected_order = (
        "Preflight every immutable public resource",
        "Create draft Release before tag publication",
        "Upload only missing original candidate bytes",
        "Verify every staged Release asset digest",
        "Promote immutable version image manifests",
        "Revalidate publication boundary immediately before publish",
        "Publish the fully staged Release",
        "Verify published Release, tag, assets, and image manifests",
        "Update stable latest manifests last",
    )
    assert [names.index(name) for name in expected_order] == sorted(
        names.index(name) for name in expected_order
    )
    assert names[-1] == "Update stable latest manifests last"
    draft = _step(
        RELEASE_WORKFLOW, "promote", "Create draft Release before tag publication"
    )["run"]
    latest = steps[-1]
    assert "draft: true" in draft
    assert 'git/refs"' not in _job_text(RELEASE_WORKFLOW, "promote")
    assert "environment" not in _job(RELEASE_WORKFLOW, "promote")
    assert latest["if"] == "needs.verify-candidate.outputs.is_prerelease != 'true'"
    assert ":latest" in latest["run"]
    assert "CURRENT_DEFAULT_SHA" in latest["run"]
    assert "releases/latest" in latest["run"]


def test_publish_has_an_adjacent_adversarial_revalidation_boundary():
    steps = _job(RELEASE_WORKFLOW, "promote")["steps"]
    names = [step.get("name") for step in steps]
    boundary_name = "Revalidate publication boundary immediately before publish"
    publish_name = "Publish the fully staged Release"
    boundary = _step(RELEASE_WORKFLOW, "promote", boundary_name)["run"]

    assert names.index(publish_name) == names.index(boundary_name) + 1
    for contract in (
        "CURRENT_DEFAULT_SHA",
        "publication-release.json",
        "expected-assets.json",
        "publication-tag.json",
        "RUNTIME_DIGEST",
        "DASHBOARD_DIGEST",
    ):
        assert contract in boundary
    assert 'release.get("body", "")' in boundary


def test_published_release_rechecks_every_public_candidate_identity():
    verify = _step(
        RELEASE_WORKFLOW,
        "promote",
        "Verify published Release, tag, assets, and image manifests",
    )["run"]

    for contract in (
        "published-release.json",
        "published-tag.json",
        "expected-assets.json",
        "release-notes.md",
        "COMMIT_SHA",
        "IS_PRERELEASE",
        "RUNTIME_DIGEST",
        "DASHBOARD_DIGEST",
        "docker buildx imagetools inspect",
    ):
        assert contract in verify
    assert "actual_by_name != expected_by_name" in verify
    assert 'release.get("immutable") is not True' in verify


def test_interrupted_draft_and_completed_public_release_are_idempotent_states():
    preflight = _step(
        RELEASE_WORKFLOW, "promote", "Preflight every immutable public resource"
    )["run"]
    create = _step(
        RELEASE_WORKFLOW, "promote", "Create draft Release before tag publication"
    )
    upload = _step(
        RELEASE_WORKFLOW, "promote", "Upload only missing original candidate bytes"
    )
    publish = _step(
        RELEASE_WORKFLOW, "promote", "Publish the fully staged Release"
    )

    assert "missing = sorted(set(expected_by_name) - present)" in preflight
    assert "An existing public Release must be immutable" in preflight
    assert create["if"] == "steps.preflight.outputs.release_exists != 'true'"
    assert upload["if"] == "steps.preflight.outputs.release_public != 'true'"
    assert publish["if"] == "steps.preflight.outputs.release_public != 'true'"


def test_release_assets_come_from_the_closed_receipt_set():
    preflight = _step(
        RELEASE_WORKFLOW,
        "promote",
        "Preflight every immutable public resource",
    )["run"]

    assert ".artifacts[]" in preflight
    assert 'name: "dicepp-candidate.json"' in preflight
    assert "unexpected or duplicate assets" in preflight
    assert "missing-assets.txt" in preflight
    staged = _step(
        RELEASE_WORKFLOW, "promote", "Verify every staged Release asset digest"
    )["run"]
    assert "actual_by_name != expected_by_name" in staged
    assert "Release became public before staged asset verification" in staged


def test_automatic_upgrade_evidence_policy_is_enforced_by_the_receipt_verifier():
    verify = _step(
        RELEASE_WORKFLOW,
        "verify-candidate",
        "Verify receipt and every sealed file digest",
    )["run"]
    outputs = _job(RELEASE_WORKFLOW, "verify-candidate")["outputs"]

    assert "promotion_candidate.py" in verify
    assert outputs["automatic_upgrade"] == (
        "${{ steps.candidate.outputs.automatic_upgrade }}"
    )
    assert "dicepp-upgrade-evidence.json" not in RELEASE_WORKFLOW.read_text(
        encoding="utf-8"
    )


def test_automatic_upgrade_evidence_requires_both_final_platform_artifacts_before_receipt():
    windows = _job(CANDIDATE_WORKFLOW, "windows-upgrade-matrix")
    linux = _job(CANDIDATE_WORKFLOW, "linux-upgrade-matrix")
    evidence = _job(CANDIDATE_WORKFLOW, "upgrade-evidence")
    receipt = _job(CANDIDATE_WORKFLOW, "receipt")

    assert set(windows["needs"]) == {"candidate-metadata", "windows-final"}
    assert set(linux["needs"]) == {"candidate-metadata", "linux-final"}
    assert {
        "candidate-metadata",
        "quality-gate",
        "windows-upgrade-matrix",
        "linux-upgrade-matrix",
    } == set(evidence["needs"])
    assert "upgrade-evidence" in receipt["needs"]

    windows_run = _step(
        CANDIDATE_WORKFLOW,
        "windows-upgrade-matrix",
        "Run the Windows cross-version matrix against final bytes",
    )["run"]
    linux_run = _step(
        CANDIDATE_WORKFLOW,
        "linux-upgrade-matrix",
        "Run the Linux cross-version matrix against final bytes",
    )["run"]
    assemble = _step(
        CANDIDATE_WORKFLOW,
        "upgrade-evidence",
        "Assemble closed cross-version evidence",
    )["run"]
    assert "dicepp-final-windows-candidate" in json.dumps(windows["steps"])
    assert "dicepp-final-linux-candidate" in json.dumps(linux["steps"])
    assert all(name in windows_run for name in ("portable=", "setup=", "velopack-bundle="))
    assert "linux-bundle=" in linux_run
    assert "check-readiness" in windows_run
    assert "check-readiness" in linux_run
    assert "uv run python scripts/build/upgrade_matrix_runner.py" in windows_run
    assert _step(
        CANDIDATE_WORKFLOW,
        "windows-upgrade-matrix",
        "Install project dependencies",
    )["run"] == "uv sync --frozen"
    windows_result = _step(
        CANDIDATE_WORKFLOW,
        "windows-upgrade-matrix",
        "Upload Windows matrix result",
    )["with"]
    linux_result = _step(
        CANDIDATE_WORKFLOW,
        "linux-upgrade-matrix",
        "Upload Linux matrix result",
    )["with"]
    assert windows_result["path"] == "dist/upgrade-result"
    assert linux_result["path"] == "dist/upgrade-result"
    assert "dist/upgrade-work" in _step(
        CANDIDATE_WORKFLOW,
        "windows-upgrade-matrix",
        "Upload Windows matrix diagnostics",
    )["with"]["path"]
    assert "dist/upgrade-work" in _step(
        CANDIDATE_WORKFLOW,
        "linux-upgrade-matrix",
        "Upload Linux matrix diagnostics",
    )["with"]["path"]
    assert "${{ vars." not in windows_run
    assert "${{ vars." not in linux_run
    runner_text = (
        ROOT / "scripts/build/upgrade_matrix_runner.py"
    ).read_text(encoding="utf-8")
    assert "windows_upgrade_matrix_harness.py" in runner_text
    assert "linux_upgrade_matrix_harness.py" in runner_text
    assert assemble.count("--platform-result") == 2


def test_validation_only_upgrade_matrix_mode_covers_all_policy_combinations():
    workflow = _workflow(CANDIDATE_WORKFLOW)
    dispatch = _trigger(workflow, "workflow_dispatch")
    exercise = dispatch["inputs"]["exercise_upgrade_matrix"]
    windows = _job(CANDIDATE_WORKFLOW, "windows-upgrade-matrix")
    linux = _job(CANDIDATE_WORKFLOW, "linux-upgrade-matrix")
    evidence = _job(CANDIDATE_WORKFLOW, "upgrade-evidence")

    assert exercise == {
        "description": "Exercise a pinned source that supports the target upgrade contract",
        "required": False,
        "default": False,
        "type": "boolean",
    }
    linux_condition = (
        "needs.candidate-metadata.outputs.automatic_upgrade == 'true' || "
        "inputs.exercise_upgrade_matrix == true"
    )
    automatic_condition = (
        "needs.candidate-metadata.outputs.automatic_upgrade == 'true'"
    )
    assert windows["if"] == linux_condition
    assert linux["if"] == linux_condition

    combinations = {
        (False, False): False,
        (False, True): True,
        (True, False): True,
        (True, True): True,
    }
    for (automatic_upgrade, validation_only), expected in combinations.items():
        terms = {
            "needs.candidate-metadata.outputs.automatic_upgrade == 'true'": (
                automatic_upgrade
            ),
            "inputs.exercise_upgrade_matrix == true": validation_only,
        }
        actual = any(terms[term.strip()] for term in linux_condition.split("||"))
        assert actual is expected

    evidence_gate = evidence["if"]
    for automatic_upgrade, exercise_matrix, windows_success, linux_success in product(
        (False, True), repeat=4
    ):
        actual = _evaluate_github_gate(
            evidence_gate,
            {
                "needs.candidate-metadata.result": "success",
                "needs.candidate-metadata.outputs.automatic_upgrade": (
                    "true" if automatic_upgrade else "false"
                ),
                "inputs.exercise_upgrade_matrix": exercise_matrix,
                "needs.windows-upgrade-matrix.result": (
                    "success" if windows_success else "failure"
                ),
                "needs.linux-upgrade-matrix.result": (
                    "success" if linux_success else "failure"
                ),
            },
        )
        if automatic_upgrade:
            expected = windows_success and linux_success
        elif exercise_matrix:
            expected = windows_success and linux_success
        else:
            expected = True
        assert actual is expected, (
            automatic_upgrade,
            exercise_matrix,
            windows_success,
            linux_success,
        )
    transition_condition = (
        "needs.candidate-metadata.outputs.automatic_upgrade != 'true' && "
        "inputs.exercise_upgrade_matrix == true"
    )
    assert {step["if"] for step in evidence["steps"]} == {
        automatic_condition,
        transition_condition,
    }
    assemble = _step(
        CANDIDATE_WORKFLOW,
        "upgrade-evidence",
        "Assemble closed cross-version evidence",
    )["run"]
    assert "--target-commit-sha" in assemble
    assert assemble.count("--candidate") == 3
    assert assemble.count("--platform-result") == 2
    assert "continue-on-error" not in windows
    assert "continue-on-error" not in linux


def test_validation_only_windows_matrix_is_not_misrepresented_as_release_evidence():
    windows = _job(CANDIDATE_WORKFLOW, "windows-upgrade-matrix")
    linux = _job(CANDIDATE_WORKFLOW, "linux-upgrade-matrix")
    evidence = _job(CANDIDATE_WORKFLOW, "upgrade-evidence")
    registry = json.loads(
        (ROOT / "scripts/build/upgrade_protocol_registry.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = json.loads(
        (ROOT / "scripts/build/upgrade_matrix.json").read_text(encoding="utf-8")
    )

    assert "inputs.exercise_upgrade_matrix == true" in windows["if"]
    assert "inputs.exercise_upgrade_matrix == true" in linux["if"]
    sources = {
        (source["platform"], source["source_version"])
        for source in matrix["supported_sources"]
    }
    assert sources == {
        ("windows", "3.0.0rc21"),
        ("linux", "3.0.0rc21"),
    }
    windows_contract = next(
        item
        for item in registry["contracts"]
        if item["name"] == "windows_current_backup_manual_restore"
    )
    assert windows_contract["verification_status"] == "verified"
    transition = _step(
        CANDIDATE_WORKFLOW,
        "upgrade-evidence",
        "Record validation-only transition coverage",
    )["run"]
    assert "Windows: previous published source" in transition
    assert "Linux: previous published source" in transition
    assert "No upgrade evidence is promotable" in transition


def test_validation_only_evidence_never_enters_receipt_or_release_assets():
    receipt = _job(CANDIDATE_WORKFLOW, "receipt")
    receipt_text = json.dumps(receipt)
    download = _step(
        CANDIDATE_WORKFLOW,
        "receipt",
        "Download commit-bound upgrade evidence",
    )
    stage = _step(
        CANDIDATE_WORKFLOW,
        "receipt",
        "Stage every final GitHub Release asset",
    )

    assert "exercise_upgrade_matrix" not in receipt_text
    assert download["if"] == (
        "needs.candidate-metadata.outputs.automatic_upgrade == 'true'"
    )
    assert 'if [ "$AUTOMATIC_UPGRADE" = "true" ]; then' in stage["run"]
    assert "dicepp-upgrade-evidence.json" in stage["run"]


def test_linux_upgrade_diagnostics_exclude_runtime_credentials() -> None:
    upload = _step(
        CANDIDATE_WORKFLOW,
        "linux-upgrade-matrix",
        "Upload Linux matrix diagnostics",
    )
    assert "exclude" not in upload["with"]
    paths = upload["with"]["path"]
    assert "!dist/upgrade-work/**/manager/state/api-token" in paths
    assert "!dist/upgrade-work/**/manager/control/**" in paths
    assert "!dist/upgrade-work/**/manager/docker-proxy.sock" in paths


def test_final_candidate_upload_identity_is_run_attempt_unique_and_auditable():
    upload = _step(
        CANDIDATE_WORKFLOW, "receipt", "Upload sealed final candidate"
    )

    assert upload["with"]["name"] == (
        "dicepp-final-candidate-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert upload["with"]["path"] == "dist/final"
    assert upload["with"]["retention-days"] == 30
    assert upload["id"] == "upload"
    receipt_outputs = _job(CANDIDATE_WORKFLOW, "receipt")["outputs"]
    assert receipt_outputs["artifact_id"] == "${{ steps.upload.outputs.artifact-id }}"
    assert receipt_outputs["artifact_digest"] == (
        "${{ steps.upload.outputs.artifact-digest }}"
    )


def test_gitee_mirror_is_not_part_of_the_release_pipeline():
    release_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    workflow_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
    ).lower()

    assert not SYNC_WORKFLOW.exists()
    for removed_contract in (
        "gitee_private_key",
        "gitee_token",
        "gitee_user",
        "hub-mirror-action",
        "sync2gitee",
    ):
        assert removed_contract not in workflow_text
    for removed_release_event in (
        "repository_dispatch",
        "release-published",
        "repos/${repository}/dispatches",
    ):
        assert removed_release_event not in release_text.lower()


def test_release_actions_are_pinned_to_full_commits():
    uses = []
    workflow = _workflow(RELEASE_WORKFLOW)
    uses.extend(
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    )

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in uses)
    assert uses.count(
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    ) == 2


def test_version_bump_defaults_cannot_create_a_partial_commit_or_tag():
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    bump = project["tool"]["bumpversion"]

    assert bump["commit"] is False
    assert bump["tag"] is False
    source = PYPROJECT.read_text(encoding="utf-8")
    assert "默认只改文件，不自动 commit/tag" in source


def test_release_docs_record_candidate_bound_upgrade_gate_and_remote_acceptance():
    docs = RELEASE_README.read_text(encoding="utf-8")
    skill = VERSION_RELEASE_SKILL.read_text(encoding="utf-8")

    for text in (docs, skill):
        assert "完成并通过明确验收前" not in text
        assert "自动升级" in text and "Windows/Linux 跨版本矩阵" in text
        assert "validation-only" in text and "Receipt" in text
        assert "30 天" in text
    for contract in (
        "Immutable Releases",
        "refs/tags/v*",
        "candidate-{run_id}-{run_attempt}",
    ):
        assert contract in docs
    assert "本地实现与测试通过”不表示远端已经启用" in docs
    for removed_setting in (
        "RELEASE_PREFLIGHT_TOKEN",
        "RELEASE_TAG_RULESET_ID",
        "RELEASE_GITHUB_ACTIONS_APP_ID",
        "release` environment",
    ):
        assert removed_setting not in docs
    assert "这些设置由管理员一次性启用，不在每次 Promotion 中重复读取管理配置" in docs
    assert "`GITHUB_TOKEN` 外，发布不要求额外凭据" in docs

    assert "velopack.win-x64.zip" in skill
    for text in (docs, skill):
        assert "当前不做 Gitee 镜像同步，恢复需单独设计并经用户确认" in text


def test_updates_document_routes_maintainer_contracts_out_of_the_user_guide():
    updates = UPDATES_DOC.read_text(encoding="utf-8")

    assert "## Dashboard 更新流程" in updates
    assert "## 必须手工处理的情况" in updates
    assert "[Manager、归档恢复与升级架构](./dev/manager-architecture.md)" in updates
    assert "[DicePP 发版系统](./releases/README.md)" in updates
    assert "dicepp-candidate.json" not in updates
    assert "dicepp-upgrade-evidence.json" not in updates


def test_only_promotion_requests_github_contents_write():
    writers = []
    for workflow_path in (ROOT / ".github" / "workflows").glob("*.yml"):
        workflow = _workflow(workflow_path)
        for job_name, job in workflow.get("jobs", {}).items():
            if (job.get("permissions") or {}).get("contents") == "write":
                writers.append((workflow_path.name, job_name))

    assert writers == [("release.yml", "promote")]
    assert "environment" not in _job(RELEASE_WORKFLOW, "promote")
