from plugins.DicePP.core.config.pydantic_models import BotConfig


def test_builtin_provider_catalog_is_complete_and_not_shared() -> None:
    first = BotConfig()
    second = BotConfig()

    assert set(first.persona_ai.providers) == {"deepseek"}
    assert {
        name: [model.name for model in provider.models]
        for name, provider in first.persona_ai.providers.items()
    } == {
        "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-pro-t"],
    }

    first.persona_ai.providers["deepseek"].api_key = "changed"
    first.persona_ai.providers["deepseek"].models[0].enabled = False

    assert second.persona_ai.providers["deepseek"].api_key == ""
    assert second.persona_ai.providers["deepseek"].models[0].enabled is True
