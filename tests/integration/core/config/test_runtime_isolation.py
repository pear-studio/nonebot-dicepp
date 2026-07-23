import os
from pathlib import Path


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
