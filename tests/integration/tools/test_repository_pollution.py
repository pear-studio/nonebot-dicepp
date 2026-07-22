from pathlib import Path

import pytest

from tests.support.pollution import (
    assert_repository_unchanged,
    capture_repository_snapshot,
)


@pytest.fixture
def repository_root(tmp_path: Path) -> Path:
    (tmp_path / "config" / "bots").mkdir(parents=True)
    (tmp_path / "config" / "global.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "dicepp.db").write_bytes(b"baseline")
    return tmp_path


@pytest.mark.parametrize(
    ("relative_path", "operation", "expected_change"),
    [
        ("config/bots/production.json", "add", "added files"),
        ("config/global.json", "modify", "modified files"),
        ("data/dicepp.db", "remove", "removed files"),
    ],
)
def test_repository_guard_reports_changes_across_config_and_data(
    repository_root: Path,
    relative_path: str,
    operation: str,
    expected_change: str,
) -> None:
    baseline = capture_repository_snapshot(repository_root)
    target = repository_root / relative_path
    if operation == "add":
        target.write_text("{}", encoding="utf-8")
    elif operation == "modify":
        target.write_text('{"changed": true}', encoding="utf-8")
    else:
        target.unlink()

    with pytest.raises(AssertionError) as error:
        assert_repository_unchanged(repository_root, baseline)

    message = str(error.value)
    assert expected_change in message
    assert str(Path(relative_path)) in message


def test_repository_guard_accepts_an_unchanged_snapshot(
    repository_root: Path,
) -> None:
    baseline = capture_repository_snapshot(repository_root)

    assert_repository_unchanged(repository_root, baseline)
