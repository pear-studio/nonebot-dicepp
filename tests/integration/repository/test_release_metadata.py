from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build.release_metadata import parse_release_metadata


def _notes(**overrides: str) -> str:
    fields = {
        "数据变更": "no",
        "配置变更": "no",
        "变更范围": "runtime, dashboard",
        "自动升级": "no",
        "最低 Manager 版本": "1.0",
    }
    fields.update(overrides)
    bullets = "\n".join(f"- {key}: {value}" for key, value in fields.items())
    return f"# v3.1.0\n\n{bullets}\n\n## Changed\n- example\n"


def test_release_metadata_accepts_notes_without_artifact_display_fields(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "release.md"
    notes.write_text(_notes(), encoding="utf-8")

    metadata = parse_release_metadata(notes, expected_version="v3.1.0")

    assert metadata.change_scope == ("runtime", "dashboard")
    assert metadata.automatic_upgrade is False


def test_release_metadata_parses_optional_linux_handoff_protocol(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "release.md"
    notes.write_text(
        _notes(**{"Linux Manager handoff 协议": "1"}),
        encoding="utf-8",
    )

    metadata = parse_release_metadata(notes, expected_version="v3.1.0")

    assert metadata.linux_manager_handoff_protocol == 1


def test_release_metadata_accepts_no_linux_handoff_protocol(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "release.md"
    notes.write_text(
        _notes(**{"Linux Manager handoff 协议": "no"}),
        encoding="utf-8",
    )

    metadata = parse_release_metadata(notes, expected_version="v3.1.0")

    assert metadata.linux_manager_handoff_protocol is None


@pytest.mark.parametrize("value", ["maybe", "0", "-1", "1.5", ""])
def test_release_metadata_rejects_malformed_linux_handoff_protocol(
    tmp_path: Path,
    value: str,
) -> None:
    notes = tmp_path / "release.md"
    notes.write_text(
        _notes(**{"Linux Manager handoff 协议": value}),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="Linux Manager handoff 协议 must be a positive integer or no",
    ):
        parse_release_metadata(notes, expected_version="v3.1.0")


def test_real_rc9_notes_drive_machine_upgrade_metadata() -> None:
    metadata = parse_release_metadata(
        Path("docs/releases/v3.0.0rc9.md"),
        expected_version="v3.0.0rc9",
    )

    assert metadata.data_changed is True
    assert metadata.config_changed is False
    assert metadata.automatic_upgrade is True
    assert metadata.minimum_manager_version == "1.0"
    assert metadata.change_scope == (
        "runtime",
        "dashboard",
        "deployment",
        "data",
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda text: text.replace("- 自动升级: no\n", ""),
            "Missing release metadata",
        ),
        (
            lambda text: text.replace("自动升级: no", "自动升级: maybe"),
            "exactly yes or no",
        ),
        (
            lambda text: text.replace(
                "变更范围: runtime, dashboard",
                "变更范围: runtime, runtime",
            ),
            "duplicate",
        ),
        (
            lambda text: text.replace("# v3.1.0", "# v3.2.0"),
            "does not match",
        ),
        (
            lambda text: text.replace("数据变更: no", "数据变更: yes"),
            "conflicts",
        ),
    ],
)
def test_release_metadata_fails_closed(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    notes = tmp_path / "release.md"
    notes.write_text(mutation(_notes()), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        parse_release_metadata(notes, expected_version="v3.1.0")
