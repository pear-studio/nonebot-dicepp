"""
Tests for core/persona/loader.py and core/persona/models.py

Covers:
  9.3  Persona loading and explicit skin selection
"""
from pathlib import Path

import pytest
import yaml

from plugins.DicePP.core.persona.loader import PersonaLoader


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_skin(base_dir: Path, name: str, data: dict) -> None:
    """Write a skin.yaml file in base_dir/{name}/skin.yaml."""
    char_dir = base_dir / name
    char_dir.mkdir(parents=True, exist_ok=True)
    skin_path = char_dir / "skin.yaml"
    skin_path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


@pytest.fixture
def chars_dir(tmp_path):
    """Temp dir used as character_path for PersonaLoader."""
    return tmp_path


# ── PersonaLoader: loading ────────────────────────────────────────────────────


def test_loader_loads_default_persona(chars_dir):
    _write_skin(chars_dir, "default", {
        "name": "default",
        "localization": {"hello": "你好"},
    })
    loader = PersonaLoader(str(chars_dir))
    p = loader.get("default")
    assert p.get_loc_texts("hello") == ["你好"]


def test_loader_loads_multiple_personas(chars_dir):
    _write_skin(chars_dir, "default", {"name": "default"})
    _write_skin(chars_dir, "kawaii", {
        "name": "kawaii",
        "localization": {"greeting": "好可爱呀~"},
    })
    loader = PersonaLoader(str(chars_dir))
    assert "default" in loader.available_names()
    assert "kawaii" in loader.available_names()


# ── PersonaLoader: explicit skin selection ───────────────────────────────────


def test_required_skin_missing_fails_without_default_fallback(chars_dir):
    _write_skin(chars_dir, "default", {
        "name": "default",
        "localization": {"key": "default_value"},
    })
    loader = PersonaLoader(str(chars_dir))

    with pytest.raises(ValueError, match="skin.yaml 不存在"):
        loader.require("configured")


def test_required_empty_skin_loads_as_its_own_persona(chars_dir):
    skin_dir = chars_dir / "configured"
    skin_dir.mkdir()
    (skin_dir / "skin.yaml").write_text("", encoding="utf-8")
    loader = PersonaLoader(str(chars_dir))

    persona = loader.require("configured")

    assert persona.name == "configured"
    assert persona.localization == {}


# ── PersonaLoader: reload ─────────────────────────────────────────────────────


def test_loader_reload_picks_up_new_file(chars_dir):
    _write_skin(chars_dir, "default", {"name": "default"})
    loader = PersonaLoader(str(chars_dir))
    assert "newpersona" not in loader.available_names()

    _write_skin(chars_dir, "newpersona", {
        "name": "newpersona",
        "localization": {"greeting": "new!"},
    })
    loader.reload()
    assert "newpersona" in loader.available_names()
    assert loader.get("newpersona").get_loc_texts("greeting") == ["new!"]


def test_loader_reload_picks_up_changes(chars_dir):
    _write_skin(chars_dir, "default", {
        "name": "default",
        "localization": {"greeting": "before"},
    })
    loader = PersonaLoader(str(chars_dir))
    assert loader.get("default").get_loc_texts("greeting") == ["before"]

    _write_skin(chars_dir, "default", {
        "name": "default",
        "localization": {"greeting": "after"},
    })
    loader.reload()
    assert loader.get("default").get_loc_texts("greeting") == ["after"]


def test_loader_reload_clears_removed_persona(chars_dir):
    _write_skin(chars_dir, "default", {"name": "default"})
    _write_skin(chars_dir, "temp", {"name": "temp"})
    loader = PersonaLoader(str(chars_dir))
    assert "temp" in loader.available_names()

    (chars_dir / "temp" / "skin.yaml").unlink()
    (chars_dir / "temp").rmdir()
    loader.reload()
    assert "temp" not in loader.available_names()
