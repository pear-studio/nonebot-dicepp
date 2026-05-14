"""
Tests for core/persona/loader.py and core/persona/models.py

Covers:
  9.3  Persona loading and fallback
"""
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "src" / "plugins" / "DicePP"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.persona.loader import PersonaLoader
from core.persona.models import PersonaModel


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


# ── PersonaModel ──────────────────────────────────────────────────────────────


def test_persona_model_defaults():
    p = PersonaModel()
    assert p.name == "default"
    assert p.localization == {}
    assert p.chat == {}


def test_persona_model_get_loc_texts_missing_key():
    p = PersonaModel()
    assert p.get_loc_texts("nonexistent") == []


def test_persona_model_get_loc_texts_string_value():
    p = PersonaModel(localization={"greeting": "你好"})
    assert p.get_loc_texts("greeting") == ["你好"]


def test_persona_model_get_loc_texts_list_value():
    p = PersonaModel(localization={"greeting": ["你好", "嗨"]})
    assert p.get_loc_texts("greeting") == ["你好", "嗨"]


def test_persona_model_get_chat_responses_missing():
    p = PersonaModel()
    assert p.get_chat_responses("^hi$") == []


def test_persona_model_get_chat_responses_string():
    p = PersonaModel(chat={"^hi$": "Hello!"})
    assert p.get_chat_responses("^hi$") == ["Hello!"]


def test_persona_model_get_chat_responses_list():
    p = PersonaModel(chat={"^hi$": ["Hello!", "Hi there!"]})
    assert p.get_chat_responses("^hi$") == ["Hello!", "Hi there!"]


# ── PersonaLoader: loading ────────────────────────────────────────────────────


def test_loader_missing_dir_returns_default(tmp_path):
    loader = PersonaLoader(str(tmp_path / "nonexistent"))
    p = loader.get("default")
    assert isinstance(p, PersonaModel)


def test_loader_loads_default_persona(chars_dir):
    _write_skin(chars_dir, "default", {
        "name": "default",
        "localization": {"hello": "你好"},
        "chat": {"^hi$": "嗨"},
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


def test_loader_get_existing_persona(chars_dir):
    _write_skin(chars_dir, "default", {"name": "default"})
    _write_skin(chars_dir, "cool", {
        "name": "cool",
        "chat": {"^hi$": ["Yo!"]},
    })
    loader = PersonaLoader(str(chars_dir))
    p = loader.get("cool")
    assert p.get_chat_responses("^hi$") == ["Yo!"]


# ── PersonaLoader: fallback ───────────────────────────────────────────────────


def test_loader_fallback_to_default_when_name_missing(chars_dir):
    _write_skin(chars_dir, "default", {
        "name": "default",
        "localization": {"key": "default_value"},
    })
    loader = PersonaLoader(str(chars_dir))
    p = loader.get("nonexistent")
    assert p.get_loc_texts("key") == ["default_value"]


def test_loader_fallback_to_empty_persona_when_no_default(chars_dir):
    _write_skin(chars_dir, "other", {"name": "other"})
    loader = PersonaLoader(str(chars_dir))
    p = loader.get("missing")
    assert isinstance(p, PersonaModel)
    assert p.localization == {}


def test_loader_ignores_malformed_yaml(chars_dir):
    default_dir = chars_dir / "default"
    default_dir.mkdir(exist_ok=True)
    (default_dir / "skin.yaml").write_text("NOT YAML: [broken", encoding="utf-8")
    loader = PersonaLoader(str(chars_dir))
    p = loader.get("default")
    assert isinstance(p, PersonaModel)  # empty fallback, no exception


def test_loader_ignores_validation_error(chars_dir):
    _write_skin(chars_dir, "default", {
        "name": "default",
        "localization": "should_be_dict_not_string",
    })
    loader = PersonaLoader(str(chars_dir))
    p = loader.get("default")
    assert isinstance(p, PersonaModel)


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
