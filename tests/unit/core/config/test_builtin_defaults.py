from plugins.DicePP.core.config.pydantic_models import BotConfig


def test_builtin_provider_catalog_is_complete_and_not_shared() -> None:
    first = BotConfig()
    second = BotConfig()

    assert set(first.persona_ai.providers) == {
        "minimax",
        "deepseek",
        "minimax_image",
        "mimo",
    }
    assert {
        name: [model.name for model in provider.models]
        for name, provider in first.persona_ai.providers.items()
    } == {
        "minimax": ["MiniMax-M3", "MiniMax-M3-t"],
        "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-pro-t"],
        "minimax_image": ["image-01"],
        "mimo": ["mimo-v2.5-pro", "mimo-v2.5-pro-t", "mimo-v2.5", "mimo-v2.5-t"],
    }

    first.persona_ai.providers["minimax"].api_key = "changed"
    first.persona_ai.providers["minimax"].models[0].enabled = False

    assert second.persona_ai.providers["minimax"].api_key == ""
    assert second.persona_ai.providers["minimax"].models[0].enabled is True
