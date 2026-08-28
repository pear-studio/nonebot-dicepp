from unittest.mock import MagicMock

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.config.pydantic_models import BotConfig, PersonaConfig
from plugins.DicePP.core.localization import LocalizationManager
from plugins.DicePP.core.persona.models import PersonaModel


def _start_bot_with_persona_skin(*, enabled: bool) -> LocalizationManager:
    loader = MagicMock()
    loader.get.side_effect = {
        "default": PersonaModel(
            name="default",
            localization={"identity_test": "默认皮肤"},
        ),
        "qiqi.local": PersonaModel(
            name="qiqi.local",
            localization={"identity_test": "qiqi 皮肤"},
        ),
    }.get
    loc_helper = LocalizationManager(persona_loader=loader)
    loc_helper.register_loc_text("identity_test", "内置默认文本")

    bot = Bot.__new__(Bot)
    bot.config = BotConfig(
        persona_ai=PersonaConfig(enabled=enabled, character_name="qiqi.local")
    )
    bot.loc_helper = loc_helper
    bot.register_command = MagicMock()
    Bot.start_up(bot)
    return loc_helper


def test_disabled_persona_uses_default_localization_skin():
    loc_helper = _start_bot_with_persona_skin(enabled=False)

    assert loc_helper.format_loc_text("identity_test") == "默认皮肤"


def test_enabled_persona_uses_configured_character_localization_skin():
    loc_helper = _start_bot_with_persona_skin(enabled=True)

    assert loc_helper.format_loc_text("identity_test") == "qiqi 皮肤"
