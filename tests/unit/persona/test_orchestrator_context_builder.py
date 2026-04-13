"""
单元测试: Orchestrator 中 ContextBuilder 参数传递
"""

import pytest

from unittest.mock import MagicMock

from plugins.DicePP.module.persona.orchestrator import PersonaOrchestrator
from plugins.DicePP.module.persona.character.models import Character
from plugins.DicePP.module.persona.memory.context_builder import ContextBuilder


class TestOrchestratorContextBuilder:
    """验证 Orchestrator 传递给 ContextBuilder 的参数"""

    def test_create_context_builder_uses_config_budget(self):
        """_create_context_builder 应使用 config 中的 lore_token_budget"""
        mock_bot = MagicMock()
        mock_bot.config.persona_ai.character_name = "default"
        mock_bot.config.persona_ai.character_path = "./content/characters"
        mock_bot.config.persona_ai.enabled = False  # 避免实际初始化
        mock_bot.config.persona_ai.max_short_term_chars = 1500
        mock_bot.config.persona_ai.lore_token_budget = 450

        orchestrator = PersonaOrchestrator(mock_bot)
        char = Character(name="测试角色")
        builder = orchestrator._create_context_builder(char)

        assert isinstance(builder, ContextBuilder)
        assert builder.character is char
        assert builder.max_short_term_chars == 1500
        assert builder.lore_token_budget == 450

    @pytest.mark.asyncio
    async def test_reload_character_updates_context_builder(self):
        """reload_character 后 context_builder 应使用新角色的配置"""
        mock_bot = MagicMock()
        mock_bot.config.persona_ai.character_name = "old_char"
        mock_bot.config.persona_ai.character_path = "./content/characters"
        mock_bot.config.persona_ai.enabled = False
        mock_bot.config.persona_ai.max_short_term_chars = 1500
        mock_bot.config.persona_ai.lore_token_budget = 200

        orchestrator = PersonaOrchestrator(mock_bot)

        old_char = Character(name="旧角色")
        orchestrator.character = old_char
        orchestrator.context_builder = orchestrator._create_context_builder(old_char)
        assert orchestrator.context_builder.lore_token_budget == 200

        new_char = Character(name="新角色")
        orchestrator.character_loader = MagicMock()
        orchestrator.character_loader.load.return_value = new_char
        success, _ = await orchestrator.reload_character()

        assert success is True
        assert orchestrator.character is new_char
        assert orchestrator.context_builder is not None
        assert orchestrator.context_builder.character is new_char
        assert orchestrator.context_builder.lore_token_budget == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
