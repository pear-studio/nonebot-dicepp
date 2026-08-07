import hashlib
import json

from plugins.DicePP.core.config.pydantic_models import BotConfig


# Frozen immediately before config/global.json was removed.  The digest covers
# the complete effective legacy default after canonicalization and Pydantic
# validation, without retaining a second full configuration document.
_LEGACY_EFFECTIVE_DEFAULTS_SHA256 = (
    "1493b72d578c32c2582005a8f9d888737b8cc6b31af90d44957a3b5cffdbf8c2"
)


def test_builtin_defaults_match_frozen_legacy_effective_configuration() -> None:
    payload = json.dumps(
        BotConfig().model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert hashlib.sha256(payload).hexdigest() == _LEGACY_EFFECTIVE_DEFAULTS_SHA256


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
