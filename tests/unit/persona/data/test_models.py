"""Persona 数据模型单元测试。"""

from plugins.DicePP.core.config.pydantic_models import PersonaConfig


class TestPersonaConfig:
    def test_default_values(self):
        config = PersonaConfig()
        assert config.enabled is False
        assert config.whitelist_enabled is True
        assert config.daily_limit == 20


class TestCharacterState:
    """CharacterState 只保留角色自身生活状态。"""

    def test_extra_ignore(self):
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState(
            energy=80,
            unknown_field="should be ignored",
            legacy_extra={"old_key": "old_value"},
        )
        assert state.energy == 80

    def test_default_values(self):
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState()
        assert state.energy is None
        assert state.mood is None
        assert state.health is None

    def test_extra_ignore_does_not_store_unknown(self):
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState(energy=80, extra_field="ignored")
        dumped = state.model_dump()
        assert "extra_field" not in dumped
        assert dumped["energy"] == 80
