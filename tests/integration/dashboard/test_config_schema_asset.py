"""Frozen Dashboard configuration-schema packaging contract."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from dashboard.src.config import DashboardPaths
from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))
CANONICAL_SCHEMA = ROOT / "src" / "plugins" / "DicePP" / "core" / "config" / "pydantic_models.py"


def test_schema_loader_uses_frozen_asset_without_source_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The onefile asset supplies UpdateConfig even when no source tree exists."""
    frozen_root = tmp_path / "_MEIPASS"
    schema_path = frozen_root / "dashboard_config_schema" / "pydantic_models.py"
    schema_path.parent.mkdir(parents=True)
    shutil.copyfile(CANONICAL_SCHEMA, schema_path)
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
        assert schema.UpdateConfig.model_validate({"check_interval_hours": 12.0}).check_interval_hours == 12.0
        with pytest.raises(ValidationError, match="cache_versions"):
            schema.UpdateConfig.model_validate({"cache_versions": True})
    finally:
        app_module._pydantic_module_cache = previous_cache


def test_windows_pyinstaller_spec_carries_only_the_canonical_schema_asset() -> None:
    """Windows onefile must embed the schema, not a complete DicePP source tree."""
    spec = (ROOT / "scripts" / "build" / "dashboard.spec").read_text(encoding="utf-8")

    assert "pydantic_models.py" in spec
    assert "dashboard_config_schema" in spec
    assert 'collect_submodules("pydantic")' in spec
    assert 'os.path.join(PROJECT_ROOT, "src", "plugins", "DicePP")' not in spec
