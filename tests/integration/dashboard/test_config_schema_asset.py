"""Frozen Dashboard configuration-schema packaging contract."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from dashboard.src.config import DashboardPaths
from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))
CANONICAL_SCHEMA = ROOT / "src" / "plugins" / "DicePP" / "core" / "config" / "pydantic_models.py"
CANONICAL_CATALOG = ROOT / "src" / "plugins" / "DicePP" / "core" / "config" / "builtin_providers.py"


def test_schema_loader_uses_frozen_asset_without_source_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The onefile asset supplies the Bot schema without a source tree."""
    frozen_root = tmp_path / "_MEIPASS"
    schema_path = frozen_root / "dashboard_config_schema" / "pydantic_models.py"
    schema_path.parent.mkdir(parents=True)
    shutil.copyfile(CANONICAL_SCHEMA, schema_path)
    shutil.copyfile(CANONICAL_CATALOG, schema_path.with_name("builtin_providers.py"))
    instance_root = tmp_path / "instance"
    instance_root.mkdir()

    import dashboard.src.app as app_module

    previous_cache = app_module._pydantic_module_cache
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)
    monkeypatch.setattr(DashboardPaths, "PROJECT_ROOT", instance_root)
    monkeypatch.setattr(DashboardPaths, "SOURCE_ROOT", instance_root)
    app_module._pydantic_module_cache = None
    try:
        schema = app_module._load_pydantic_models_module()

        assert schema is not None
        assert set(schema.BotConfig().persona_ai.providers) == {
            "minimax",
            "deepseek",
            "minimax_image",
            "mimo",
        }
        assert all(
            type(provider) is schema.ProviderConfig
            for provider in schema.BotConfig().persona_ai.providers.values()
        )
    finally:
        app_module._pydantic_module_cache = previous_cache


def test_schema_loader_fails_loudly_when_a_required_asset_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_root = tmp_path / "_MEIPASS"
    schema_path = frozen_root / "dashboard_config_schema" / "pydantic_models.py"
    schema_path.parent.mkdir(parents=True)
    shutil.copyfile(CANONICAL_SCHEMA, schema_path)
    missing_source = tmp_path / "missing-source"

    import dashboard.src.app as app_module

    previous_cache = app_module._pydantic_module_cache
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)
    monkeypatch.setattr(DashboardPaths, "PROJECT_ROOT", missing_source)
    monkeypatch.setattr(DashboardPaths, "SOURCE_ROOT", missing_source)
    app_module._pydantic_module_cache = None
    try:
        with pytest.raises(app_module.DashboardConfigSchemaError):
            app_module._load_pydantic_models_module()
    finally:
        app_module._pydantic_module_cache = previous_cache


def test_windows_pyinstaller_spec_carries_the_complete_standalone_schema_assets() -> None:
    """Windows onefile must embed the schema, not a complete DicePP source tree."""
    spec = (ROOT / "scripts" / "build" / "dashboard.spec").read_text(encoding="utf-8")

    assert "pydantic_models.py" in spec
    assert "builtin_providers.py" in spec
    assert "dashboard_config_schema" in spec
    assert 'collect_submodules("pydantic")' in spec
    assert 'os.path.join(PROJECT_ROOT, "src", "plugins", "DicePP")' not in spec
