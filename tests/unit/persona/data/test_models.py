"""Persona 数据模型单元测试。"""

import pytest

from plugins.DicePP.core.config.pydantic_models import PersonaConfig, UserConfig


class TestPersonaConfig:
    def test_default_values(self):
        config = PersonaConfig()
        assert config.enabled is False
        assert UserConfig().daily_ai_limit == 20


class TestCharacterState:
    """CharacterState 只保留角色自身生活状态。"""

    def test_extra_fields_are_rejected(self):
        from plugins.DicePP.module.persona.data.models import CharacterState

        with pytest.raises(ValueError):
            CharacterState(energy=80, unknown_field="should be rejected")

    def test_default_values(self):
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState()
        assert state.model_dump() == {"energy": 50, "mood": 50, "health": 50}

    def test_state_values_require_integers(self):
        from plugins.DicePP.module.persona.data.models import CharacterState

        with pytest.raises(ValueError):
            CharacterState.model_validate({"energy": "50", "mood": 50, "health": 50})
