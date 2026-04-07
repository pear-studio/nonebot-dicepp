"""
Tests for core/persona/loader.py and core/persona/models.py

Covers:
  9.3  Persona loading and fallback
"""
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "src" / "plugins" / "DicePP"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.persona.loader import PersonaLoader
from core.persona.models import PersonaModel


# ── helpers ───────────────────────────────────────────────────────────────────


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def personas_dir(tmp_path):
    d = tmp_path / "personas"
    d.mkdir()
    return tmp_path  # PersonaLoader takes the parent (data_path)


# ── PersonaModel ──────────────────────────────────────────────────────────────


def test_persona_model_defaults():
    p = PersonaModel()
    assert p.name == "default"
    assert p.localization == {}
    assert p.chat == {}
    assert p.llm_personality == ""


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
    loader = PersonaLoader(str(tmp_path))
    p = loader.get("default")
    assert isinstance(p, PersonaModel)


def test_loader_loads_default_persona(personas_dir):
    _write(personas_dir / "personas" / "default.json", {
        "name": "default",
        "localization": {"hello": "你好"},
        "chat": {"^hi$": "嗨"},
        "llm_personality": "友好的助手",
    })
    loader = PersonaLoader(str(personas_dir))
    p = loader.get("default")
    assert p.get_loc_texts("hello") == ["你好"]
    assert p.llm_personality == "友好的助手"


def test_loader_loads_multiple_personas(personas_dir):
    _write(personas_dir / "personas" / "default.json", {"name": "default"})
    _write(personas_dir / "personas" / "kawaii.json", {
        "name": "kawaii",
        "localization": {"greeting": "好可爱呀~"},
    })
    loader = PersonaLoader(str(personas_dir))
    assert "default" in loader.available_names()
    assert "kawaii" in loader.available_names()


def test_loader_get_existing_persona(personas_dir):
    _write(personas_dir / "personas" / "default.json", {"name": "default"})
    _write(personas_dir / "personas" / "cool.json", {
        "name": "cool",
        "llm_personality": "酷炫助手",
    })
    loader = PersonaLoader(str(personas_dir))
    p = loader.get("cool")
    assert p.llm_personality == "酷炫助手"


# ── PersonaLoader: fallback ───────────────────────────────────────────────────


def test_loader_fallback_to_default_when_name_missing(personas_dir):
    _write(personas_dir / "personas" / "default.json", {
        "name": "default",
        "localization": {"key": "default_value"},
    })
    loader = PersonaLoader(str(personas_dir))
    p = loader.get("nonexistent")
    assert p.get_loc_texts("key") == ["default_value"]


def test_loader_fallback_to_empty_persona_when_no_default(personas_dir):
    _write(personas_dir / "personas" / "other.json", {"name": "other"})
    loader = PersonaLoader(str(personas_dir))
    p = loader.get("missing")
    assert isinstance(p, PersonaModel)
    assert p.localization == {}


def test_loader_ignores_malformed_json(personas_dir):
    (personas_dir / "personas").mkdir(exist_ok=True)
    (personas_dir / "personas" / "default.json").write_text("NOT JSON", encoding="utf-8")
    loader = PersonaLoader(str(personas_dir))
    p = loader.get("default")
    assert isinstance(p, PersonaModel)  # empty fallback, no exception


def test_loader_ignores_validation_error(personas_dir):
    _write(personas_dir / "personas" / "default.json", {
        "name": "default",
        "localization": "should_be_dict_not_string",
    })
    loader = PersonaLoader(str(personas_dir))
    p = loader.get("default")
    assert isinstance(p, PersonaModel)


# ── PersonaLoader: reload ─────────────────────────────────────────────────────


def test_loader_reload_picks_up_new_file(personas_dir):
    _write(personas_dir / "personas" / "default.json", {"name": "default"})
    loader = PersonaLoader(str(personas_dir))
    assert "newpersona" not in loader.available_names()

    _write(personas_dir / "personas" / "newpersona.json", {
        "name": "newpersona",
        "llm_personality": "new!",
    })
    loader.reload()
    assert "newpersona" in loader.available_names()
    assert loader.get("newpersona").llm_personality == "new!"


def test_loader_reload_picks_up_changes(personas_dir):
    _write(personas_dir / "personas" / "default.json", {
        "name": "default",
        "llm_personality": "before",
    })
    loader = PersonaLoader(str(personas_dir))
    assert loader.get("default").llm_personality == "before"

    _write(personas_dir / "personas" / "default.json", {
        "name": "default",
        "llm_personality": "after",
    })
    loader.reload()
    assert loader.get("default").llm_personality == "after"


def test_loader_reload_clears_removed_persona(personas_dir):
    _write(personas_dir / "personas" / "default.json", {"name": "default"})
    _write(personas_dir / "personas" / "temp.json", {"name": "temp"})
    loader = PersonaLoader(str(personas_dir))
    assert "temp" in loader.available_names()

    (personas_dir / "personas" / "temp.json").unlink()
    loader.reload()
    assert "temp" not in loader.available_names()
