import os

from plugins.DicePP.frozen import get_app_dir


def test_dicepp_app_dir_env_override(monkeypatch, tmp_path):
    """DICEPP_APP_DIR overrides both development and frozen paths."""
    override = tmp_path / "custom_app_root"
    override.mkdir()
    monkeypatch.setenv("DICEPP_APP_DIR", str(override))
    assert get_app_dir() == os.path.abspath(str(override))
