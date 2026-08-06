"""Create an immutable receipt for a complete set of final release candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.build.release_build_metadata import validate_release_version
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_build_metadata import validate_release_version


CONTRACT_VERSION = 1
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
EXPECTED_TOOLCHAINS = frozenset(
    {"docker", "python", "ubuntu-runner", "uv", "velopack", "zstd"}
)


@dataclass(frozen=True, slots=True)
class ContainerCandidate:
    role: str
    candidate_ref: str
    manifest_digest: str
    image_id: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_artifacts(version: str) -> tuple[tuple[str, str, str, str], ...]:
    tag = f"v{version}"
    return (
        ("linux", "amd64", "linux-bundle", f"DicePP-{tag}-linux-amd64.zip"),
        ("windows", "amd64", "portable", f"DicePP-{tag}-win64-Portable.zip"),
        ("windows", "amd64", "setup", f"DicePP-{tag}-win64-Setup.exe"),
        ("windows", "amd64", "velopack-bundle", "velopack.win-x64.zip"),
    )


def _validate_project_version(project_file: Path, version: str) -> None:
    try:
        project = tomllib.loads(project_file.read_text(encoding="utf-8"))
        actual = project["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ValueError("project metadata is unreadable") from exc
    if actual != version:
        raise ValueError(
            f"candidate version {version!r} differs from project version {actual!r}"
        )


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
        if _SHA256_PATTERN.fullmatch(item.manifest_digest) is None:
            raise ValueError("container manifest digest is invalid")
        if _SHA256_PATTERN.fullmatch(item.image_id) is None:
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
    package_tree_sha256: str,
    containers: Iterable[ContainerCandidate],
    toolchains: dict[str, str],
) -> dict[str, Any]:
    validate_release_version(version)
    if _COMMIT_PATTERN.fullmatch(commit_sha) is None:
        raise ValueError("candidate commit SHA must be a full lowercase Git SHA-1")
    if _COMMIT_PATTERN.fullmatch(workflow_sha) is None:
        raise ValueError("workflow definition SHA must be a full lowercase Git SHA-1")
    if workflow_sha != commit_sha:
        raise ValueError("workflow definition SHA differs from candidate commit")
    if not repository or not workflow_ref or run_id < 1 or run_attempt < 1:
        raise ValueError("workflow run identity is incomplete")
    if set(toolchains) != EXPECTED_TOOLCHAINS or any(
        not isinstance(value, str) or not value.strip()
        for value in toolchains.values()
    ):
        raise ValueError("toolchains must contain the complete contract v1 set")
    if re.fullmatch(r"[0-9a-f]{64}", package_tree_sha256) is None:
        raise ValueError("Windows package tree digest is invalid")
    _validate_project_version(project_file, version)

    expected = _expected_artifacts(version)
    expected_names = {item[3] for item in expected}
    if not artifact_root.is_dir():
        raise ValueError("candidate artifact root is not a directory")
    entries = list(artifact_root.iterdir())
    actual_names = {path.name for path in entries}
    if len(entries) != len(expected_names) or actual_names != expected_names:
        raise ValueError("final artifact set does not match candidate contract v1")
    if any(path.is_symlink() or not path.is_file() for path in entries):
        raise ValueError("candidate artifact root contains a non-regular entry")

    artifacts = []
    for platform, arch, purpose, filename in expected:
        path = artifact_root / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"candidate artifact is not a regular file: {filename}")
        size = path.stat().st_size
        if size < 1:
            raise ValueError(f"candidate artifact is empty: {filename}")
        artifacts.append(
            {
                "platform": platform,
                "arch": arch,
                "purpose": purpose,
                "filename": filename,
                "sha256": _sha256(path),
                "size": size,
            }
        )

    normalized_containers = _normalize_containers(
        containers, run_id=run_id, run_attempt=run_attempt
    )
    candidate_identities = [
        {
            "platform": "linux",
            "name": f"{item['role']}-manifest",
            "sha256": item["manifest_digest"].removeprefix("sha256:"),
        }
        for item in normalized_containers
    ]
    candidate_identities.append(
        {
            "platform": "windows",
            "name": "package-tree",
            "sha256": package_tree_sha256,
        }
    )
    candidate_identities.sort(key=lambda item: (item["platform"], item["name"]))

    return {
        "contract_version": CONTRACT_VERSION,
        "target": {
            "version": version,
            "tag": f"v{version}",
            "commit_sha": commit_sha,
        },
        "workflow": {
            "repository": repository,
            "workflow_ref": workflow_ref,
            "workflow_sha": workflow_sha,
            "run_id": run_id,
            "run_attempt": run_attempt,
        },
        "build": {
            "toolchains": dict(sorted(toolchains.items())),
            "candidate_identities": candidate_identities,
        },
        "artifacts": artifacts,
        "containers": normalized_containers,
    }


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
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
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--package-tree-sha256", required=True)
    parser.add_argument("--container", action="append", default=[])
    parser.add_argument("--toolchain", action="append", default=[])
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    toolchains = dict(
        _parse_pair(spec, label="toolchain") for spec in args.toolchain
    )
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
        package_tree_sha256=args.package_tree_sha256,
        containers=(_parse_container(spec) for spec in args.container),
        toolchains=toolchains,
    )
    _write_json(args.output, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
