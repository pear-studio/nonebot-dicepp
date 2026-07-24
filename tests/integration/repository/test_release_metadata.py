from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build.release_metadata import parse_release_metadata


def _notes(**overrides: str) -> str:
    fields = {
        "镜像": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
        "Windows": "DicePP-v3.1.0-win64-Portable.zip",
        "数据变更": "no",
        "配置变更": "no",
        "变更范围": "runtime, dashboard",
        "自动升级": "no",
        "最低 Manager 版本": "1.0",
    }
    fields.update(overrides)
    bullets = "\n".join(f"- {key}: {value}" for key, value in fields.items())
    return f"# v3.1.0\n\n{bullets}\n\n## Changed\n- example\n"


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
