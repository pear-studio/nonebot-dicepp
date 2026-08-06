"""Seal the exact files and provenance attested by a candidate workflow.

The trusted candidate workflow remains responsible for running package smoke tests
and the canonical release-manifest and upgrade-evidence validators.  This sealer
does not replay those expensive checks.  It binds their successful output to the
final bytes and rechecks the target fields needed to prevent cross-run substitution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts.build.release_build_metadata import validate_release_version
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_build_metadata import validate_release_version


CONTRACT_VERSION = 2
VALIDATION_SUMMARY_CONTRACT_VERSION = 1
RECEIPT_FILENAME = "dicepp-candidate.json"
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
EXPECTED_TOOLCHAINS = frozenset(
    {"docker", "python", "ubuntu-runner", "uv", "velopack", "zstd"}
)


@dataclass(frozen=True, slots=True)
class ContainerCandidate:
    role: str
    candidate_ref: str
    manifest_digest: str
    image_id: str


@dataclass(frozen=True, slots=True)
class ValidatedArtifact:
    filename: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ArtifactSpec:
    platform: str
    arch: str
    purpose: str
    filename: str
    validated: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_specs(version: str) -> tuple[_ArtifactSpec, ...]:
    tag = f"v{version}"
    return (
        _ArtifactSpec(
            "linux", "amd64", "linux-bundle", f"DicePP-{tag}-linux-amd64.zip", True
        ),
        _ArtifactSpec(
            "windows",
            "amd64",
            "portable",
            f"DicePP-{tag}-win64-Portable.zip",
            True,
        ),
        _ArtifactSpec(
            "windows",
            "amd64",
            "setup",
            f"DicePP-{tag}-win64-Setup.exe",
            True,
        ),
        _ArtifactSpec(
            "windows", "amd64", "velopack-bundle", "velopack.win-x64.zip", True
        ),
    )


def _release_asset_specs(
    version: str, *, automatic_upgrade: bool
) -> tuple[_ArtifactSpec, ...]:
    specs = (*_package_specs(version),)
    metadata_specs = (
        _ArtifactSpec("linux", "amd64", "docker-compose", "docker-compose.yml", False),
        _ArtifactSpec("all", "any", "release-manifest", "dicepp-release.json", False),
    )
    if automatic_upgrade:
        return (
            *specs,
            *metadata_specs,
            _ArtifactSpec(
                "all",
                "any",
                "upgrade-evidence",
                "dicepp-upgrade-evidence.json",
                False,
            ),
        )
    return (*specs, *metadata_specs)


def _validate_project_version(project_file: Path, version: str) -> None:
    try:
        project = tomllib.loads(project_file.read_text(encoding="utf-8"))
        actual = project["project"]["version"]
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ValueError("project metadata is unreadable") from exc
    if actual != version:
        raise ValueError(
            f"candidate version {version!r} differs from project version {actual!r}"
        )


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _safe_filename(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
    ):
        raise ValueError(f"{label} must be a plain filename")
    return value


def _is_strict_int(
    value: object,
    *,
    expected: int | None = None,
    minimum: int | None = None,
) -> bool:
    """Accept JSON integers without Python's bool-as-int or coercion semantics."""
    if type(value) is not int:
        return False
    if expected is not None and value != expected:
        return False
    return minimum is None or value >= minimum


def parse_validation_summary(
    payload: object, *, label: str = "validation summary"
) -> tuple[ValidatedArtifact, ...]:
    """Parse the closed validator-to-sealer file identity contract."""

    if not isinstance(payload, dict) or set(payload) != {
        "contract_version",
        "artifacts",
    }:
        raise ValueError(f"{label} fields do not match contract version 1")
    if not _is_strict_int(
        payload["contract_version"],
        expected=VALIDATION_SUMMARY_CONTRACT_VERSION,
    ):
        raise ValueError(f"unsupported {label} contract version")
    raw_artifacts = payload["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ValueError(f"{label} artifacts must be a non-empty list")

    artifacts: list[ValidatedArtifact] = []
    for raw in raw_artifacts:
        if not isinstance(raw, dict) or set(raw) != {"filename", "size", "sha256"}:
            raise ValueError(f"invalid {label} artifact")
        filename = _safe_filename(raw["filename"], label=f"{label} filename")
        size = raw["size"]
        sha256 = raw["sha256"]
        if not _is_strict_int(size, minimum=1):
            raise ValueError(f"invalid {label} artifact size")
        if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
            raise ValueError(f"invalid {label} artifact SHA-256")
        artifacts.append(ValidatedArtifact(filename, size, sha256))
    names = [artifact.filename for artifact in artifacts]
    if len(names) != len(set(names)):
        raise ValueError(f"{label} contains duplicate artifact filenames")
    return tuple(artifacts)


def load_validation_summaries(paths: Iterable[Path]) -> tuple[ValidatedArtifact, ...]:
    records: list[ValidatedArtifact] = []
    for path in paths:
        records.extend(
            parse_validation_summary(
                _load_json_object(path, label="validation summary"),
                label=f"validation summary {path}",
            )
        )
    names = [record.filename for record in records]
    if len(names) != len(set(names)):
        raise ValueError("validation summaries contain duplicate artifact filenames")
    return tuple(records)


def _normalize_containers(
    containers: Iterable[ContainerCandidate],
    *,
    run_id: int,
    run_attempt: int,
) -> list[dict[str, str]]:
    records = sorted(containers, key=lambda item: item.role)
    if [item.role for item in records] != ["dashboard", "runtime"]:
        raise ValueError("container candidates must contain exactly dashboard and runtime")
    normalized: list[dict[str, str]] = []
    for item in records:
        repository = {
            "dashboard": "ghcr.io/pear-studio/dicepp-dashboard",
            "runtime": "ghcr.io/pear-studio/nonebot-dicepp",
        }[item.role]
        expected_ref = f"{repository}:candidate-{run_id}-{run_attempt}"
        if item.candidate_ref != expected_ref:
            raise ValueError("container candidate reference differs from this workflow run")
        if _PREFIXED_SHA256_PATTERN.fullmatch(item.manifest_digest) is None:
            raise ValueError("container manifest digest is invalid")
        if _PREFIXED_SHA256_PATTERN.fullmatch(item.image_id) is None:
            raise ValueError("container image ID is invalid")
        normalized.append(
            {
                "role": item.role,
                "candidate_ref": item.candidate_ref,
                "manifest_digest": item.manifest_digest,
                "image_id": item.image_id,
            }
        )
    return normalized


def _candidate_identities(
    containers: Sequence[Mapping[str, str]], package_tree_sha256: str
) -> list[dict[str, str]]:
    identities = [
        {
            "platform": "linux",
            "name": f"{item['role']}-manifest",
            "sha256": item["manifest_digest"].removeprefix("sha256:"),
        }
        for item in containers
    ]
    identities.append(
        {
            "platform": "windows",
            "name": "package-tree",
            "sha256": package_tree_sha256,
        }
    )
    identities.sort(key=lambda item: (item["platform"], item["name"]))
    return identities


def _candidate_digest(identities: Sequence[Mapping[str, str]]) -> str:
    canonical = json.dumps(
        identities,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _release_manifest_mode(path: Path, *, version: str) -> tuple[dict[str, Any], bool]:
    manifest = _load_json_object(path, label="release manifest")
    if manifest.get("version") != version:
        raise ValueError("release manifest version differs from candidate target")
    automatic_upgrade = manifest.get("automatic_upgrade")
    if not isinstance(automatic_upgrade, bool):
        raise ValueError("release manifest automatic_upgrade must be a boolean")
    return manifest, automatic_upgrade


def validate_manifest_artifact_attestation(
    manifest: Mapping[str, Any], artifacts: Sequence[Mapping[str, Any]], version: str
) -> None:
    """Recheck byte identities, not the full canonical release contract."""

    raw_records = manifest.get("artifacts")
    if not isinstance(raw_records, list):
        raise ValueError("release manifest artifacts must be a list")
    package_names = {spec.filename for spec in _package_specs(version)}
    expected = {
        item["filename"]: {
            key: item[key]
            for key in ("platform", "arch", "purpose", "filename", "size", "sha256")
        }
        for item in artifacts
        if item["filename"] in package_names
    }
    actual: dict[str, dict[str, Any]] = {}
    for item in raw_records:
        if not isinstance(item, dict) or set(item) != {
            "platform",
            "arch",
            "purpose",
            "filename",
            "size",
            "sha256",
        }:
            raise ValueError("release manifest contains an invalid artifact record")
        filename = item.get("filename")
        if not isinstance(filename, str) or filename in actual:
            raise ValueError("release manifest contains duplicate artifact filenames")
        actual[filename] = item
    if actual != expected:
        raise ValueError("release manifest artifacts differ from sealed package bytes")


def validate_upgrade_evidence_target_attestation(
    path: Path,
    *,
    version: str,
    commit_sha: str,
    candidate_identities: Sequence[Mapping[str, str]],
) -> None:
    """Recheck target binding after canonical evidence validation succeeded."""

    evidence = _load_json_object(path, label="upgrade evidence")
    if set(evidence) != {"contract_version", "target", "results"}:
        raise ValueError("upgrade evidence fields do not match contract version 1")
    if not _is_strict_int(
        evidence["contract_version"], expected=1
    ) or not isinstance(evidence["results"], list):
        raise ValueError("unsupported upgrade evidence contract")
    expected_target = {
        "version": version,
        "commit_sha": commit_sha,
        "candidate_identities": list(candidate_identities),
        "candidate_digest": _candidate_digest(candidate_identities),
    }
    if evidence["target"] != expected_target:
        raise ValueError("upgrade evidence target does not match sealed candidate")


def validate_release_asset_directory(
    root: Path, expected_names: set[str], *, label: str
) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{label} root is not a regular directory")
    entries = list(root.iterdir())
    actual_names = {entry.name for entry in entries}
    if len(entries) != len(expected_names) or actual_names != expected_names:
        raise ValueError(f"{label} file set does not match candidate contract v2")
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise ValueError(f"{label} root contains a non-regular entry")


def _artifact_records(
    artifact_root: Path,
    specs: Sequence[_ArtifactSpec],
    validated_artifacts: Iterable[ValidatedArtifact],
) -> list[dict[str, Any]]:
    summaries = {record.filename: record for record in validated_artifacts}
    required_validated = {spec.filename for spec in specs if spec.validated}
    if set(summaries) != required_validated:
        raise ValueError(
            "validation summaries must cover exactly the four validated release packages"
        )

    artifacts: list[dict[str, Any]] = []
    for spec in specs:
        path = artifact_root / spec.filename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"candidate artifact is not a regular file: {spec.filename}")
        size = path.stat().st_size
        if size < 1:
            raise ValueError(f"candidate artifact is empty: {spec.filename}")
        digest = sha256_file(path)
        summary = summaries.get(spec.filename)
        if summary is not None and (summary.size != size or summary.sha256 != digest):
            raise ValueError(
                f"candidate artifact bytes differ from validator summary: {spec.filename}"
            )
        artifacts.append(
            {
                "platform": spec.platform,
                "arch": spec.arch,
                "purpose": spec.purpose,
                "filename": spec.filename,
                "size": size,
                "sha256": digest,
                "validated": spec.validated,
            }
        )
    return artifacts


def build_candidate_receipt(
    *,
    artifact_root: Path,
    project_file: Path,
    version: str,
    commit_sha: str,
    repository: str,
    workflow_ref: str,
    run_id: int,
    run_attempt: int,
    workflow_sha: str,
    artifact_name: str,
    package_tree_sha256: str,
    containers: Iterable[ContainerCandidate],
    toolchains: dict[str, str],
    validated_artifacts: Iterable[ValidatedArtifact],
) -> dict[str, Any]:
    validate_release_version(version)
    if type(commit_sha) is not str or _COMMIT_PATTERN.fullmatch(commit_sha) is None:
        raise ValueError("candidate commit SHA must be a full lowercase Git SHA-1")
    if type(workflow_sha) is not str or _COMMIT_PATTERN.fullmatch(workflow_sha) is None:
        raise ValueError("workflow definition SHA must be a full lowercase Git SHA-1")
    if workflow_sha != commit_sha:
        raise ValueError("workflow definition SHA differs from candidate commit")
    if _REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError("workflow repository is invalid")
    if not workflow_ref.startswith(f"{repository}/.github/workflows/") or "@" not in workflow_ref:
        raise ValueError("workflow reference is invalid")
    if not _is_strict_int(run_id, minimum=1) or not _is_strict_int(
        run_attempt, minimum=1
    ):
        raise ValueError("workflow run identity is incomplete")
    expected_artifact_name = f"dicepp-final-candidate-{run_id}-{run_attempt}"
    if (
        _ARTIFACT_NAME_PATTERN.fullmatch(artifact_name) is None
        or artifact_name != expected_artifact_name
    ):
        raise ValueError("candidate artifact name differs from workflow run identity")
    if set(toolchains) != EXPECTED_TOOLCHAINS or any(
        not isinstance(value, str) or not value.strip() for value in toolchains.values()
    ):
        raise ValueError("toolchains must contain the complete contract v2 set")
    if _SHA256_PATTERN.fullmatch(package_tree_sha256) is None:
        raise ValueError("Windows package tree digest is invalid")
    _validate_project_version(project_file, version)

    manifest, automatic_upgrade = _release_manifest_mode(
        artifact_root / "dicepp-release.json", version=version
    )
    specs = _release_asset_specs(version, automatic_upgrade=automatic_upgrade)
    validate_release_asset_directory(
        artifact_root, {spec.filename for spec in specs}, label="candidate artifact"
    )
    artifacts = _artifact_records(artifact_root, specs, validated_artifacts)

    normalized_containers = _normalize_containers(
        containers, run_id=run_id, run_attempt=run_attempt
    )
    identities = _candidate_identities(normalized_containers, package_tree_sha256)
    validate_manifest_artifact_attestation(manifest, artifacts, version)
    if automatic_upgrade:
        validate_upgrade_evidence_target_attestation(
            artifact_root / "dicepp-upgrade-evidence.json",
            version=version,
            commit_sha=commit_sha,
            candidate_identities=identities,
        )

    receipt = {
        "contract_version": CONTRACT_VERSION,
        "target": {
            "version": version,
            "tag": f"v{version}",
            "commit_sha": commit_sha,
            "automatic_upgrade": automatic_upgrade,
            "is_prerelease": re.search(r"(?:a|b|rc)\d+$", version) is not None,
        },
        "workflow": {
            "repository": repository,
            "workflow_ref": workflow_ref,
            "workflow_sha": workflow_sha,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "artifact_name": artifact_name,
        },
        "build": {
            "toolchains": dict(sorted(toolchains.items())),
            "candidate_identities": identities,
            "validated_artifacts": sorted(
                spec.filename for spec in specs if spec.validated
            ),
        },
        "artifacts": artifacts,
        "containers": normalized_containers,
    }
    validate_candidate_receipt(receipt)
    return receipt


def validate_candidate_receipt(payload: object) -> dict[str, Any]:
    """Validate the closed v2 receipt schema and its internal provenance links."""

    if not isinstance(payload, dict) or set(payload) != {
        "contract_version",
        "target",
        "workflow",
        "build",
        "artifacts",
        "containers",
    }:
        raise ValueError("candidate receipt fields do not match contract version 2")
    if not _is_strict_int(payload["contract_version"], expected=CONTRACT_VERSION):
        raise ValueError("unsupported candidate receipt contract version")
    target = payload["target"]
    if not isinstance(target, dict) or set(target) != {
        "version",
        "tag",
        "commit_sha",
        "automatic_upgrade",
        "is_prerelease",
    }:
        raise ValueError("candidate receipt target is invalid")
    version = target["version"]
    if not isinstance(version, str):
        raise ValueError("candidate receipt target version is invalid")
    validate_release_version(version)
    commit_sha = target["commit_sha"]
    if (
        target["tag"] != f"v{version}"
        or type(commit_sha) is not str
        or _COMMIT_PATTERN.fullmatch(commit_sha) is None
    ):
        raise ValueError("candidate receipt target is invalid")
    if not isinstance(target["automatic_upgrade"], bool) or target[
        "is_prerelease"
    ] is not (re.search(r"(?:a|b|rc)\d+$", version) is not None):
        raise ValueError("candidate receipt release mode is invalid")

    workflow = payload["workflow"]
    if not isinstance(workflow, dict) or set(workflow) != {
        "repository",
        "workflow_ref",
        "workflow_sha",
        "run_id",
        "run_attempt",
        "artifact_name",
    }:
        raise ValueError("candidate receipt workflow provenance is invalid")
    repository = workflow["repository"]
    run_id = workflow["run_id"]
    run_attempt = workflow["run_attempt"]
    if (
        not isinstance(repository, str)
        or _REPOSITORY_PATTERN.fullmatch(repository) is None
        or not isinstance(workflow["workflow_ref"], str)
        or not workflow["workflow_ref"].startswith(
            f"{repository}/.github/workflows/"
        )
        or "@" not in workflow["workflow_ref"]
        or type(workflow["workflow_sha"]) is not str
        or _COMMIT_PATTERN.fullmatch(workflow["workflow_sha"]) is None
        or workflow["workflow_sha"] != commit_sha
        or not _is_strict_int(run_id, minimum=1)
        or not _is_strict_int(run_attempt, minimum=1)
        or workflow["artifact_name"]
        != f"dicepp-final-candidate-{run_id}-{run_attempt}"
    ):
        raise ValueError("candidate receipt workflow provenance is invalid")

    containers_raw = payload["containers"]
    if not isinstance(containers_raw, list):
        raise ValueError("candidate receipt containers are invalid")
    try:
        containers = [ContainerCandidate(**item) for item in containers_raw]
    except (TypeError, AttributeError) as exc:
        raise ValueError("candidate receipt containers are invalid") from exc
    normalized_containers = _normalize_containers(
        containers, run_id=run_id, run_attempt=run_attempt
    )
    if containers_raw != normalized_containers:
        raise ValueError("candidate receipt containers are not canonical")

    build = payload["build"]
    if not isinstance(build, dict) or set(build) != {
        "toolchains",
        "candidate_identities",
        "validated_artifacts",
    }:
        raise ValueError("candidate receipt build provenance is invalid")
    toolchains = build["toolchains"]
    if (
        not isinstance(toolchains, dict)
        or set(toolchains) != EXPECTED_TOOLCHAINS
        or any(not isinstance(value, str) or not value.strip() for value in toolchains.values())
        or list(toolchains) != sorted(toolchains)
    ):
        raise ValueError("candidate receipt toolchains are invalid")
    identities = build["candidate_identities"]
    if not isinstance(identities, list) or len(identities) != 3:
        raise ValueError("candidate receipt identities are invalid")
    package_identity = next(
        (
            item
            for item in identities
            if isinstance(item, dict)
            and item.get("platform") == "windows"
            and item.get("name") == "package-tree"
        ),
        None,
    )
    if (
        package_identity is None
        or set(package_identity) != {"platform", "name", "sha256"}
        or not isinstance(package_identity["sha256"], str)
        or _SHA256_PATTERN.fullmatch(package_identity["sha256"]) is None
        or identities
        != _candidate_identities(normalized_containers, package_identity["sha256"])
    ):
        raise ValueError("candidate receipt identities are invalid")

    specs = _release_asset_specs(
        version, automatic_upgrade=target["automatic_upgrade"]
    )
    expected_validated = sorted(spec.filename for spec in specs if spec.validated)
    if build["validated_artifacts"] != expected_validated:
        raise ValueError("candidate receipt validated artifact set is invalid")
    raw_artifacts = payload["artifacts"]
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(specs):
        raise ValueError("candidate receipt artifact set is invalid")
    for raw, spec in zip(raw_artifacts, specs):
        if not isinstance(raw, dict) or set(raw) != {
            "platform",
            "arch",
            "purpose",
            "filename",
            "size",
            "sha256",
            "validated",
        }:
            raise ValueError("candidate receipt contains an invalid artifact record")
        fixed = {
            "platform": spec.platform,
            "arch": spec.arch,
            "purpose": spec.purpose,
            "filename": spec.filename,
            "validated": spec.validated,
        }
        if any(raw[key] != value for key, value in fixed.items()):
            raise ValueError("candidate receipt artifact set is invalid")
        if (
            not _is_strict_int(raw["size"], minimum=1)
            or not isinstance(raw["sha256"], str)
            or _SHA256_PATTERN.fullmatch(raw["sha256"]) is None
        ):
            raise ValueError("candidate receipt contains an invalid artifact identity")
    return payload


def _parse_pair(spec: str, *, label: str) -> tuple[str, str]:
    try:
        key, value = spec.split("=", 1)
    except ValueError as exc:
        raise ValueError(f"{label} must be KEY=VALUE") from exc
    if not key or not value:
        raise ValueError(f"{label} must be KEY=VALUE")
    return key, value


def _parse_container(spec: str) -> ContainerCandidate:
    try:
        role, candidate_ref, manifest_digest, image_id = spec.split("|", 3)
    except ValueError as exc:
        raise ValueError(
            "container must be ROLE|REFERENCE|MANIFEST_DIGEST|IMAGE_ID"
        ) from exc
    return ContainerCandidate(role, candidate_ref, manifest_digest, image_id)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--project-file", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validated-summary", type=Path, action="append", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--package-tree-sha256", required=True)
    parser.add_argument("--container", action="append", default=[])
    parser.add_argument("--toolchain", action="append", default=[])
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    toolchains = dict(_parse_pair(spec, label="toolchain") for spec in args.toolchain)
    if len(toolchains) != len(args.toolchain):
        raise ValueError("toolchain entries contain duplicate keys")
    receipt = build_candidate_receipt(
        artifact_root=args.artifact_root,
        project_file=args.project_file,
        version=args.version,
        commit_sha=args.commit_sha,
        repository=args.repository,
        workflow_ref=args.workflow_ref,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        workflow_sha=args.workflow_sha,
        artifact_name=args.artifact_name,
        package_tree_sha256=args.package_tree_sha256,
        containers=(_parse_container(spec) for spec in args.container),
        toolchains=toolchains,
        validated_artifacts=load_validation_summaries(args.validated_summary),
    )
    _write_json(args.output, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
