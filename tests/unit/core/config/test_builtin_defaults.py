import pytest

from plugins.DicePP.core.config.loader import (
    ConfigValidationError,
    validate_config_candidate,
)
from plugins.DicePP.core.config.pydantic_models import BotConfig, UserConfig


def test_user_config_has_one_shared_deepseek_connection():
    first = UserConfig()
    second = UserConfig()

    assert first.deepseek_api_key == ""
    assert first.deepseek_model == "deepseek-v4-flash"
    assert first.deepseek_base_url == "https://api.deepseek.com"

    first.deepseek_api_key = "changed"
    assert second.deepseek_api_key == ""


def test_persona_config_has_no_provider_catalog():
    persona = BotConfig().persona_ai

    assert not hasattr(persona, "providers")
    assert not hasattr(persona, "max_concurrent_requests")
    assert not hasattr(persona, "chat_llm_timeout_seconds")


def test_bot_config_has_no_top_level_persona_and_persona_is_disabled_by_default():
    config = BotConfig()

    assert "persona" not in BotConfig.model_fields
    assert not hasattr(config, "persona")
    assert config.persona_ai.enabled is False
    assert config.persona_ai.character_name == "qiqi.local"


def test_top_level_persona_is_rejected_by_config_schema():
    with pytest.raises(ConfigValidationError, match="unknown field 'persona'"):
        validate_config_candidate({"persona": "legacy"}, model_type=BotConfig)
