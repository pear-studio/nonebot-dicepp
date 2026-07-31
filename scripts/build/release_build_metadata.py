"""Derive release build metadata and validate Windows build provenance."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO


WINDOWS_CANDIDATE_CONTRACT_VERSION = 1
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


@dataclass(frozen=True, slots=True)
class WindowsCandidateMetadata:
    contract_version: int
    platform: str
    arch: str
    tag: str
    version: str
    commit_sha: str
    python_version: str


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

    if release_notes_dir is not None:
        notes = release_notes_dir / f"{tag}.md"
        if not notes.is_file():
            raise FileNotFoundError(notes)
        first_line = notes.read_text(encoding="utf-8").splitlines()[:1]
        if first_line != [f"# {tag}"]:
            raise ValueError(f"release notes title must be '# {tag}'")

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
    )


def build_windows_candidate_metadata(
    *,
    tag: str,
    version: str,
    expected_commit_sha: str,
    actual_commit_sha: str,
    python_version: str,
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
    return WindowsCandidateMetadata(
        contract_version=WINDOWS_CANDIDATE_CONTRACT_VERSION,
        platform="windows",
        arch="amd64",
        tag=tag,
        version=version,
        commit_sha=actual_commit_sha,
        python_version=python_version,
    )


def validate_windows_candidate_metadata(
    path: Path,
    *,
    tag: str,
    version: str,
    commit_sha: str,
) -> WindowsCandidateMetadata:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Windows candidate metadata is unreadable") from exc
    expected = WindowsCandidateMetadata(
        contract_version=WINDOWS_CANDIDATE_CONTRACT_VERSION,
        platform="windows",
        arch="amd64",
        tag=tag,
        version=version,
        commit_sha=commit_sha,
        python_version=WINDOWS_CANDIDATE_PYTHON,
    )
    if payload != asdict(expected):
        raise ValueError("Windows candidate metadata does not match this release")
    return expected


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

    validate_candidate = subparsers.add_parser("validate-windows-candidate")
    validate_candidate.add_argument("--path", type=Path, required=True)
    validate_candidate.add_argument("--tag", required=True)
    validate_candidate.add_argument("--version", required=True)
    validate_candidate.add_argument("--commit-sha", required=True)
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
        )
        _write_json(args.output, asdict(metadata))
        return 0
    if args.command == "validate-windows-candidate":
        validate_windows_candidate_metadata(
            args.path,
            tag=args.tag,
            version=args.version,
            commit_sha=args.commit_sha,
        )
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
