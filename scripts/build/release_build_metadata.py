"""Derive release build metadata and validate Windows build provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

try:
    from scripts.build.release_metadata import parse_release_metadata
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from release_metadata import parse_release_metadata


WINDOWS_CANDIDATE_CONTRACT_VERSION = 2
WINDOWS_CANDIDATE_PYTHON = "3.11"
_TAG_PATTERN = re.compile(
    r"^v(?P<base>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"
    r"(?:(?P<pre>a|b|rc)(?P<pre_number>0|[1-9]\d*))?$"
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class ReleaseBuildMetadata:
    tag: str
    version: str
    commit_sha: str
    is_prerelease: bool
    channel: str
    velopack_version: str
    velopack_channel: str
    automatic_upgrade: bool


@dataclass(frozen=True, slots=True)
class WindowsCandidateMetadata:
    contract_version: int
    platform: str
    arch: str
    tag: str
    version: str
    commit_sha: str
    python_version: str
    package_tree_sha256: str


def package_tree_sha256(package_root: Path) -> str:
    """Hash every regular package file and its relative path deterministically."""
    if not package_root.is_dir():
        raise ValueError("Windows candidate package root is not a directory")
    records: list[dict[str, object]] = []
    for path in sorted(package_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError("Windows candidate package tree contains a symlink")
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        records.append(
            {
                "path": path.relative_to(package_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    if not records:
        raise ValueError("Windows candidate package tree is empty")
    canonical = json.dumps(
        records,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def velopack_version(version: str) -> str:
    """Translate DicePP's supported PEP 440 versions to Velopack SemVer 2."""
    match = _match_release_tag(f"v{version.removeprefix('v')}")
    prerelease_label = match["pre"]
    if prerelease_label is None:
        return match["base"]
    labels = {"a": "alpha", "b": "beta", "rc": "rc"}
    return (
        f"{match['base']}-{labels[prerelease_label]}.{match['pre_number']}"
    )


def validate_release_version(version: str) -> str:
    """Validate an unprefixed version against the canonical release tag grammar."""
    _match_release_tag(f"v{version}")
    return version


def velopack_channel(channel: str, arch: str) -> str:
    if channel not in {"stable", "prerelease"}:
        raise ValueError("channel must be stable or prerelease")
    arch_label = {"amd64": "x64", "arm64": "arm64"}.get(arch)
    if arch_label is None:
        raise ValueError("unsupported Velopack architecture")
    return f"win-{arch_label}-{channel}"


def derive_release_build_metadata(
    *,
    ref: str,
    commit_sha: str,
    project_file: Path,
    release_notes_dir: Path | None = None,
) -> ReleaseBuildMetadata:
    prefix = "refs/tags/"
    if not ref.startswith(prefix):
        raise ValueError("release ref must be a tag ref")
    tag = ref.removeprefix(prefix)
    match = _match_release_tag(tag)
    _validate_commit_sha(commit_sha)

    project = tomllib.loads(project_file.read_text(encoding="utf-8"))
    project_version = project.get("project", {}).get("version")
    if not isinstance(project_version, str):
        raise ValueError("project.version is missing")
    if project_version != tag.removeprefix("v"):
        raise ValueError(
            f"release tag {tag} does not match project version {project_version}"
        )

    automatic_upgrade = False
    if release_notes_dir is not None:
        notes = release_notes_dir / f"{tag}.md"
        if not notes.is_file():
            raise FileNotFoundError(notes)
        first_line = notes.read_text(encoding="utf-8").splitlines()[:1]
        if first_line != [f"# {tag}"]:
            raise ValueError(f"release notes title must be '# {tag}'")
        automatic_upgrade = parse_release_metadata(
            notes,
            expected_version=tag,
        ).automatic_upgrade

    is_prerelease = match["pre"] is not None
    channel = "prerelease" if is_prerelease else "stable"
    return ReleaseBuildMetadata(
        tag=tag,
        version=tag.removeprefix("v"),
        commit_sha=commit_sha,
        is_prerelease=is_prerelease,
        channel=channel,
        velopack_version=velopack_version(tag),
        velopack_channel=velopack_channel(channel, "amd64"),
        automatic_upgrade=automatic_upgrade,
    )


def build_windows_candidate_metadata(
    *,
    tag: str,
    version: str,
    expected_commit_sha: str,
    actual_commit_sha: str,
    python_version: str,
    package_tree_digest: str,
) -> WindowsCandidateMetadata:
    _match_release_tag(tag)
    _validate_commit_sha(expected_commit_sha)
    _validate_commit_sha(actual_commit_sha)
    if tag.removeprefix("v") != version:
        raise ValueError("candidate tag and version differ")
    if actual_commit_sha != expected_commit_sha:
        raise ValueError("checked-out commit differs from release metadata")
    if python_version != WINDOWS_CANDIDATE_PYTHON:
        raise ValueError(
            f"Windows candidate must use Python {WINDOWS_CANDIDATE_PYTHON}"
        )
    if re.fullmatch(r"[0-9a-f]{64}", package_tree_digest) is None:
        raise ValueError("Windows candidate package tree digest is invalid")
    return WindowsCandidateMetadata(
        contract_version=WINDOWS_CANDIDATE_CONTRACT_VERSION,
        platform="windows",
        arch="amd64",
        tag=tag,
        version=version,
        commit_sha=actual_commit_sha,
        python_version=python_version,
        package_tree_sha256=package_tree_digest,
    )


def validate_windows_candidate_metadata(
    path: Path,
    *,
    tag: str,
    version: str,
    commit_sha: str,
    package_root: Path | None = None,
) -> WindowsCandidateMetadata:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Windows candidate metadata is unreadable") from exc
    fixed_fields = {
        "contract_version": WINDOWS_CANDIDATE_CONTRACT_VERSION,
        "platform": "windows",
        "arch": "amd64",
        "tag": tag,
        "version": version,
        "commit_sha": commit_sha,
        "python_version": WINDOWS_CANDIDATE_PYTHON,
    }
    if any(payload.get(key) != value for key, value in fixed_fields.items()):
        raise ValueError("Windows candidate metadata does not match this release")
    tree_digest = payload.get("package_tree_sha256")
    if (
        not isinstance(tree_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", tree_digest) is None
    ):
        raise ValueError("Windows candidate metadata has an invalid package tree digest")
    if set(payload) != {*fixed_fields, "package_tree_sha256"}:
        raise ValueError("Windows candidate metadata does not match this release")
    if (
        package_root is not None
        and package_tree_sha256(package_root) != tree_digest
    ):
        raise ValueError("Windows candidate package tree differs from tested provenance")
    return WindowsCandidateMetadata(**payload)


def write_github_outputs(
    metadata: ReleaseBuildMetadata,
    output: TextIO,
) -> None:
    values = asdict(metadata)
    for key, value in values.items():
        if isinstance(value, bool):
            value = str(value).lower()
        output.write(f"{key}={value}\n")


def _match_release_tag(tag: str) -> dict[str, str | None]:
    match = _TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError("tag must be vX.Y.Z or a supported PEP 440 prerelease")
    return match.groupdict()


def _validate_commit_sha(commit_sha: str) -> None:
    if _COMMIT_PATTERN.fullmatch(commit_sha) is None:
        raise ValueError("commit SHA must be a full lowercase Git SHA-1")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    derive = subparsers.add_parser("derive")
    derive.add_argument("--ref", required=True)
    derive.add_argument("--commit-sha", required=True)
    derive.add_argument("--project-file", type=Path, default=Path("pyproject.toml"))
    derive.add_argument(
        "--release-notes-dir",
        type=Path,
        default=Path("docs/releases"),
    )
    derive.add_argument("--github-output", type=Path, required=True)

    write_candidate = subparsers.add_parser("write-windows-candidate")
    write_candidate.add_argument("--output", type=Path, required=True)
    write_candidate.add_argument("--tag", required=True)
    write_candidate.add_argument("--version", required=True)
    write_candidate.add_argument("--expected-commit-sha", required=True)
    write_candidate.add_argument("--actual-commit-sha", required=True)
    write_candidate.add_argument("--package-root", type=Path, required=True)
    write_candidate.add_argument("--github-output", type=Path)

    validate_candidate = subparsers.add_parser("validate-windows-candidate")
    validate_candidate.add_argument("--path", type=Path, required=True)
    validate_candidate.add_argument("--tag", required=True)
    validate_candidate.add_argument("--version", required=True)
    validate_candidate.add_argument("--commit-sha", required=True)
    validate_candidate.add_argument("--package-root", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "derive":
        metadata = derive_release_build_metadata(
            ref=args.ref,
            commit_sha=args.commit_sha,
            project_file=args.project_file,
            release_notes_dir=args.release_notes_dir,
        )
        with args.github_output.open("a", encoding="utf-8") as output:
            write_github_outputs(metadata, output)
        return 0
    if args.command == "write-windows-candidate":
        metadata = build_windows_candidate_metadata(
            tag=args.tag,
            version=args.version,
            expected_commit_sha=args.expected_commit_sha,
            actual_commit_sha=args.actual_commit_sha,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
            package_tree_digest=package_tree_sha256(args.package_root),
        )
        _write_json(args.output, asdict(metadata))
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(f"package_tree_sha256={metadata.package_tree_sha256}\n")
        return 0
    if args.command == "validate-windows-candidate":
        validate_windows_candidate_metadata(
            args.path,
            tag=args.tag,
            version=args.version,
            commit_sha=args.commit_sha,
            package_root=args.package_root,
        )
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
