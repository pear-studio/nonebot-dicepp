from pathlib import Path

import pytest

from tests.support.pollution import (
    assert_retired_runtime_unchanged,
    assert_repository_unchanged,
    capture_retired_runtime_state,
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


def test_repository_guard_rejects_legacy_plugin_data_that_predates_baseline(
    repository_root: Path,
) -> None:
    legacy_data = repository_root / "src" / "plugins" / "DicePP" / "Data"
    legacy_data.mkdir(parents=True)
    baseline = capture_repository_snapshot(repository_root)

    with pytest.raises(AssertionError, match="retired runtime path exists") as error:
        assert_repository_unchanged(repository_root, baseline)

    assert str(Path("src/plugins/DicePP/Data")) in str(error.value)


def test_function_guard_attributes_legacy_plugin_data_creation_to_nodeid(
    repository_root: Path,
) -> None:
    baseline = capture_retired_runtime_state(repository_root)
    legacy_data = repository_root / "src" / "plugins" / "DicePP" / "Data"
    legacy_data.mkdir(parents=True)

    with pytest.raises(AssertionError, match="test_writer_nodeid"):
        assert_retired_runtime_unchanged(
            repository_root,
            baseline,
            nodeid="tests/example.py::test_writer_nodeid",
        )


def test_function_guard_attributes_legacy_plugin_data_modification_to_nodeid(
    repository_root: Path,
) -> None:
    legacy_data = repository_root / "src" / "plugins" / "DicePP" / "Data"
    legacy_data.mkdir(parents=True)
    marker = legacy_data / "state.txt"
    marker.write_text("before", encoding="utf-8")
    baseline = capture_retired_runtime_state(repository_root)
    marker.write_text("after with different size", encoding="utf-8")

    with pytest.raises(AssertionError, match="test_modifier_nodeid"):
        assert_retired_runtime_unchanged(
            repository_root,
            baseline,
            nodeid="tests/example.py::test_modifier_nodeid",
        )
