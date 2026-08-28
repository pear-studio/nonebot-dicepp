"""ChatConfig 的内部默认策略。"""

from types import SimpleNamespace

from plugins.DicePP.module.persona.chat.chat_config import ChatConfig


def test_chat_config_owns_chat_policy_defaults():
    config = ChatConfig()

    assert (
        config.max_history_turns,
        config.max_history_tokens,
        config.tools_max_rounds,
        config.lore_token_budget,
        config.private_session_gap_seconds,
        config.group_session_gap_seconds,
        config.private_session_token_budget,
        config.group_session_token_budget,
        config.segment_target_chars,
        config.segment_max_chars,
        config.segment_soft_limit,
        config.segment_hard_limit,
        config.segment_count_max,
    ) == (10, 4000, 10, 300, 86400, 1800, 64000, 64000, 30, 80, 100, 120, 10)


def test_from_persona_maps_only_public_chat_settings():
    persona = SimpleNamespace(
        timezone="UTC",
        search_max_chars=321,
    )

    config = ChatConfig.from_persona(persona)

    assert config.timezone == "UTC"
    assert config.search_max_chars == 321
    assert config.max_history_turns == 10
    assert config.segment_max_chars == 80
