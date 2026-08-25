import os
import sys

from plugins.DicePP.frozen import get_app_dir, get_project_root


def test_dicepp_app_dir_env_override(monkeypatch, tmp_path):
    """DICEPP_APP_DIR overrides both development and frozen paths."""
    override = tmp_path / "custom_app_root"
    override.mkdir()
    monkeypatch.setenv("DICEPP_APP_DIR", str(override))
    assert get_app_dir() == os.path.abspath(str(override))


def test_frozen_paths_ignore_external_root_overrides(monkeypatch, tmp_path):
    portable = tmp_path / "portable"
    stale = tmp_path / "stale"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(portable / "DicePP.exe"))
    monkeypatch.setenv("DICEPP_APP_DIR", str(stale))
    monkeypatch.setenv("DICEPP_PROJECT_ROOT", str(stale))

    expected = os.path.abspath(str(portable))
    assert get_app_dir() == expected
    assert get_project_root() == expected
