import os
from pathlib import Path

import pytest


def test_runtime_paths_are_isolated():
    from plugins.DicePP.core.config.basic import Paths
    from plugins.DicePP.frozen import get_app_dir, get_project_root

    project_root = Path(os.environ["DICEPP_PROJECT_ROOT"]).resolve()
    app_dir = Path(os.environ["DICEPP_APP_DIR"]).resolve()

    assert Path(get_project_root()).resolve() == project_root
    assert Path(get_app_dir()).resolve() == app_dir
    assert Paths.PROJECT_ROOT.resolve() == project_root
    assert Paths.DATA_DIR.resolve() == project_root / "data"
    assert Paths.CONFIG_DIR.resolve() == project_root / "config"


def test_bot_data_path_is_isolated(fresh_bot):
    bot, _ = fresh_bot
    project_root = Path(os.environ["DICEPP_PROJECT_ROOT"]).resolve()

    assert Path(bot.data_path).resolve().is_relative_to(project_root)


def test_retired_homebrew_assets_warn_once_without_being_changed(tmp_path, monkeypatch):
    from dicepp_data import InstanceLayout
    from plugins.DicePP.core.config import basic

    layout = InstanceLayout.from_root(tmp_path)
    excel_dir = layout.content_dir / "excel"
    homebrew_dir = layout.data_bots_dir / "12345" / "QueryHomebrew"
    excel_dir.mkdir(parents=True)
    homebrew_dir.mkdir(parents=True)
    (excel_dir / "legacy.xlsx").write_bytes(b"legacy")
    (homebrew_dir / "HB100.db").write_bytes(b"legacy")

    messages: list[str] = []
    monkeypatch.setattr(basic.logger, "warning", messages.append)
    basic._legacy_warning_roots.discard(layout.root.resolve())

    basic._warn_legacy_content_once(layout)
    basic._warn_legacy_content_once(layout)

    assert len(messages) == 1
    assert str(excel_dir) in messages[0]
    assert str(homebrew_dir) in messages[0]
    assert (excel_dir / "legacy.xlsx").read_bytes() == b"legacy"
    assert (homebrew_dir / "HB100.db").read_bytes() == b"legacy"


@pytest.mark.parametrize(
    ("directories", "files"),
    [
        (["content/excel"], []),
        (["content/excel"], ["content/excel/.gitkeep"]),
        (["content/excel"], ["content/excel/.hidden.xlsx"]),
        (["data/bots/12345/QueryHomebrew"], []),
        (
            ["data/bots/12345/QueryHomebrew"],
            ["data/bots/12345/QueryHomebrew/not-homebrew.db"],
        ),
    ],
)
def test_empty_or_placeholder_legacy_paths_do_not_warn(
    tmp_path, monkeypatch, directories, files
):
    from dicepp_data import InstanceLayout
    from plugins.DicePP.core.config import basic

    layout = InstanceLayout.from_root(tmp_path)
    for relative in directories:
        (layout.root / relative).mkdir(parents=True, exist_ok=True)
    for relative in files:
        path = layout.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    messages: list[str] = []
    monkeypatch.setattr(basic.logger, "warning", messages.append)
    basic._legacy_warning_roots.discard(layout.root.resolve())

    basic._warn_legacy_content_once(layout)

    assert messages == []
    assert all((layout.root / relative).is_dir() for relative in directories)
    assert all((layout.root / relative).is_file() for relative in files)
