from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build.candidate_receipt import (
    ContainerCandidate,
    build_candidate_receipt,
)
from scripts.build.release_build_metadata import validate_release_version
from tests.support.fs_utils import symlink_or_skip


VERSION = "3.1.0rc1"
COMMIT_SHA = "1" * 40
PACKAGE_TREE_SHA256 = "2" * 64
TOOLCHAINS = {
    "docker": "Docker version 27.0.0",
    "python": "Python 3.11.9",
    "ubuntu-runner": "ubuntu24/20260801.1",
    "uv": "uv 0.5.24",
    "velopack": "vpk 1.2.0",
    "zstd": "zstd 1.5.6",
}


def _project(tmp_path: Path, version: str = VERSION) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    return path


def _artifacts(tmp_path: Path, version: str = VERSION) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir(exist_ok=True)
    for index, filename in enumerate(
        (
            f"DicePP-v{version}-linux-amd64.zip",
            f"DicePP-v{version}-win64-Portable.zip",
            f"DicePP-v{version}-win64-Setup.exe",
            "velopack.win-x64.zip",
        )
    ):
        (root / filename).write_bytes(f"artifact-{index}".encode())
    return root


def _containers() -> list[ContainerCandidate]:
    return [
        ContainerCandidate(
            "runtime",
            "ghcr.io/pear-studio/nonebot-dicepp:candidate-10-1",
            f"sha256:{'3' * 64}",
            f"sha256:{'4' * 64}",
        ),
        ContainerCandidate(
            "dashboard",
            "ghcr.io/pear-studio/dicepp-dashboard:candidate-10-1",
            f"sha256:{'5' * 64}",
            f"sha256:{'6' * 64}",
        ),
    ]


def _build(tmp_path: Path, **overrides: object) -> dict:
    arguments = {
        "artifact_root": overrides.pop("artifact_root", None),
        "project_file": overrides.pop("project_file", None),
        "version": VERSION,
        "commit_sha": COMMIT_SHA,
        "repository": "pear-studio/nonebot-dicepp",
        "workflow_ref": "pear-studio/nonebot-dicepp/.github/workflows/candidate.yml@refs/heads/master",
        "run_id": 10,
        "run_attempt": 1,
        "workflow_sha": COMMIT_SHA,
        "package_tree_sha256": PACKAGE_TREE_SHA256,
        "containers": _containers(),
        "toolchains": TOOLCHAINS,
    }
    if arguments["artifact_root"] is None:
        arguments["artifact_root"] = _artifacts(tmp_path)
    if arguments["project_file"] is None:
        arguments["project_file"] = _project(tmp_path)
    arguments.update(overrides)
    return build_candidate_receipt(**arguments)  # type: ignore[arg-type]


def test_candidate_receipt_is_deterministic_and_records_final_byte_identities(
    tmp_path: Path,
) -> None:
    receipt = _build(tmp_path)
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"))

    assert receipt["target"] == {
        "version": VERSION,
        "tag": f"v{VERSION}",
        "commit_sha": COMMIT_SHA,
    }
    assert receipt["workflow"]["workflow_sha"] == COMMIT_SHA
    assert [
        (item["platform"], item["arch"], item["purpose"])
        for item in receipt["artifacts"]
    ] == [
        ("linux", "amd64", "linux-bundle"),
        ("windows", "amd64", "portable"),
        ("windows", "amd64", "setup"),
        ("windows", "amd64", "velopack-bundle"),
    ]
    assert all(item["size"] > 0 for item in receipt["artifacts"])
    assert all(len(item["sha256"]) == 64 for item in receipt["artifacts"])
    assert [item["role"] for item in receipt["containers"]] == [
        "dashboard",
        "runtime",
    ]
    assert encoded == json.dumps(
        _build(tmp_path), sort_keys=True, separators=(",", ":")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "renamed",
        "extra",
        "empty",
        "directory",
        "broken-symlink",
        "receipt-symlink",
    ],
)
def test_candidate_receipt_rejects_any_final_artifact_set_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _artifacts(tmp_path)
    portable = root / f"DicePP-v{VERSION}-win64-Portable.zip"
    if mutation == "missing":
        portable.unlink()
    elif mutation == "renamed":
        portable.rename(root / "portable.zip")
    elif mutation == "empty":
        portable.write_bytes(b"")
    elif mutation == "directory":
        portable.unlink()
        portable.mkdir()
    elif mutation == "broken-symlink":
        portable.unlink()
        symlink_or_skip(portable, root / "missing-portable.zip")
    elif mutation == "receipt-symlink":
        symlink_or_skip(
            root / "dicepp-candidate.json", root / "missing-receipt.json"
        )
    else:
        (root / "debug-symbols.zip").write_bytes(b"unexpected")

    with pytest.raises(
        ValueError, match="artifact set|artifact is empty|non-regular entry"
    ):
        _build(tmp_path, artifact_root=root)


def test_candidate_receipt_rejects_version_and_commit_drift(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="differs from project version"):
        _build(tmp_path, project_file=_project(tmp_path, "3.1.0"))

    with pytest.raises(ValueError, match="full lowercase Git SHA-1"):
        _build(tmp_path, commit_sha="A" * 40)

    with pytest.raises(ValueError, match="tag must be"):
        _build(tmp_path, version="../3.1.0")

    with pytest.raises(ValueError, match="workflow definition SHA differs"):
        _build(tmp_path, workflow_sha="7" * 40)


@pytest.mark.parametrize(
    "version",
    ["0.0.0", "1.2.3", "1.2.3a0", "1.2.3b12", "1.2.3rc0"],
)
def test_candidate_version_uses_the_release_tag_grammar(
    tmp_path: Path,
    version: str,
) -> None:
    receipt = _build(
        tmp_path,
        artifact_root=_artifacts(tmp_path, version),
        project_file=_project(tmp_path, version),
        version=version,
    )
    assert receipt["target"]["version"] == version


@pytest.mark.parametrize(
    "version",
    ["1.2", "1.2.3rc01", "01.2.3", "1.02.3", "1.2.03", "1.2.3post1"],
)
def test_candidate_version_rejects_every_version_the_release_tag_rejects(
    version: str,
) -> None:
    with pytest.raises(ValueError, match="tag must be"):
        validate_release_version(version)


def test_candidate_receipt_requires_the_complete_container_and_toolchain_sets(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="exactly dashboard and runtime"):
        _build(tmp_path, containers=_containers()[:1])

    incomplete = dict(TOOLCHAINS)
    incomplete.pop("velopack")
    with pytest.raises(ValueError, match="complete contract v1 set"):
        _build(tmp_path, toolchains=incomplete)


@pytest.mark.parametrize(
    "candidate",
    [
        ContainerCandidate(
            "runtime",
            "ghcr.io/pear-studio/nonebot-dicepp:candidate-9-1",
            f"sha256:{'3' * 64}",
            f"sha256:{'4' * 64}",
        ),
        ContainerCandidate(
            "runtime",
            "ghcr.io/pear-studio/dicepp-dashboard:candidate-10-1",
            f"sha256:{'3' * 64}",
            f"sha256:{'4' * 64}",
        ),
    ],
)
def test_candidate_receipt_rejects_container_from_another_run_or_repository(
    tmp_path: Path,
    candidate: ContainerCandidate,
) -> None:
    containers = _containers()
    containers[0] = candidate
    with pytest.raises(ValueError, match="differs from this workflow run"):
        _build(tmp_path, containers=containers)
