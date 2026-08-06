from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.build.release_build_metadata import (
    build_windows_candidate_metadata,
    derive_release_build_metadata,
    validate_windows_candidate_metadata,
)


COMMIT_SHA = "1" * 40


@pytest.mark.parametrize(
    ("version", "channel", "is_prerelease", "velopack_version", "velopack_channel"),
    [
        ("3.1.0", "stable", False, "3.1.0", "win-x64-stable"),
        (
            "3.1.0rc7",
            "prerelease",
            True,
            "3.1.0-rc.7",
            "win-x64-prerelease",
        ),
    ],
)
def test_release_build_metadata_derives_all_protocol_versions_from_one_tag(
    tmp_path: Path,
    version: str,
    channel: str,
    is_prerelease: bool,
    velopack_version: str,
    velopack_channel: str,
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / f"v{version}.md").write_text(
        f"# v{version}\n\nRelease notes.\n",
        encoding="utf-8",
    )

    metadata = derive_release_build_metadata(
        ref=f"refs/tags/v{version}",
        commit_sha=COMMIT_SHA,
        project_file=project,
        release_notes_dir=notes,
    )

    assert metadata.tag == f"v{version}"
    assert metadata.version == version
    assert metadata.commit_sha == COMMIT_SHA
    assert metadata.channel == channel
    assert metadata.is_prerelease is is_prerelease
    assert metadata.velopack_version == velopack_version
    assert metadata.velopack_channel == velopack_channel


def test_release_build_metadata_rejects_a_tag_that_differs_from_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "3.1.0"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="does not match project version"):
        derive_release_build_metadata(
            ref="refs/tags/v3.1.1",
            commit_sha=COMMIT_SHA,
            project_file=project,
        )


def test_windows_candidate_provenance_round_trip_rejects_another_commit(
    tmp_path: Path,
) -> None:
    candidate = build_windows_candidate_metadata(
        tag="v3.1.0rc7",
        version="3.1.0rc7",
        expected_commit_sha=COMMIT_SHA,
        actual_commit_sha=COMMIT_SHA,
        python_version="3.11",
    )
    path = tmp_path / "windows-candidate.json"
    path.write_text(json.dumps(asdict(candidate)), encoding="utf-8")

    assert (
        validate_windows_candidate_metadata(
            path,
            tag="v3.1.0rc7",
            version="3.1.0rc7",
            commit_sha=COMMIT_SHA,
        )
        == candidate
    )

    with pytest.raises(ValueError, match="does not match this release"):
        validate_windows_candidate_metadata(
            path,
            tag="v3.1.0rc7",
            version="3.1.0rc7",
            commit_sha="2" * 40,
        )


def test_windows_candidate_rejects_a_different_checked_out_commit() -> None:
    with pytest.raises(ValueError, match="checked-out commit differs"):
        build_windows_candidate_metadata(
            tag="v3.1.0",
            version="3.1.0",
            expected_commit_sha=COMMIT_SHA,
            actual_commit_sha="2" * 40,
            python_version="3.11",
        )
