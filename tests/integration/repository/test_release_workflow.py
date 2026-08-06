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
CANDIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "candidate.yml"
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


def test_release_docs_record_temporary_upgrade_gate_and_remote_acceptance():
    docs = RELEASE_README.read_text(encoding="utf-8")
    skill = VERSION_RELEASE_SKILL.read_text(encoding="utf-8")

    for text in (docs, skill):
        assert "B-260802-3e3e23" in text
        assert "自动升级" in text and "no" in text
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
    for text in (docs, skill, UPDATES_DOC.read_text(encoding="utf-8")):
        assert "当前不做 Gitee 镜像同步，恢复需单独设计并经用户确认" in text


def test_updates_document_matches_the_sealed_public_asset_contract():
    updates = UPDATES_DOC.read_text(encoding="utf-8")

    assert "automatic_upgrade: no` 时固定为七个 assets" in updates
    assert "dicepp-candidate.json" in updates
    assert "总数为八个" in updates
    assert "dicepp-upgrade-evidence.json" in updates


def test_only_promotion_requests_github_contents_write():
    writers = []
    for workflow_path in (ROOT / ".github" / "workflows").glob("*.yml"):
        workflow = _workflow(workflow_path)
        for job_name, job in workflow.get("jobs", {}).items():
            if (job.get("permissions") or {}).get("contents") == "write":
                writers.append((workflow_path.name, job_name))

    assert writers == [("release.yml", "promote")]
    assert "environment" not in _job(RELEASE_WORKFLOW, "promote")
