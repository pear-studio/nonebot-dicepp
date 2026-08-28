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
