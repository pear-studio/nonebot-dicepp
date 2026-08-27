import pytest

from plugins.DicePP.core.persona.models import PersonaModel


pytestmark = pytest.mark.quick


def test_persona_model_defaults():
    persona = PersonaModel()
    assert persona.name == "default"
    assert persona.localization == {}


def test_persona_model_get_loc_texts_missing_key():
    assert PersonaModel().get_loc_texts("nonexistent") == []


def test_persona_model_get_loc_texts_string_value():
    assert PersonaModel(localization={"greeting": "你好"}).get_loc_texts("greeting") == ["你好"]


def test_persona_model_get_loc_texts_list_value():
    assert PersonaModel(localization={"greeting": ["你好", "嗨"]}).get_loc_texts("greeting") == ["你好", "嗨"]
