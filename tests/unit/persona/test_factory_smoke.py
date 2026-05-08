"""create_persona 成功路径 smoke 测试

覆盖 Round 4 R1 第 7 步：工具注册表在组装时不抛异常，
且三个工具（search_memory / search_history / roll_dice）正确注册到 chat 域。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.factory import create_persona
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.tools.registry import ToolDomain


def _make_bot() -> MagicMock:
    """构造可完整走通 create_persona 的最小 Bot mock"""
    bot = MagicMock()
    cfg = MagicMock()
    cfg.enabled = True
    cfg.character_path = "/tmp/chars"
    cfg.character_name = "test"
    cfg.primary_api_key = "sk-test"
    cfg.primary_base_url = "https://api.example.com"
    cfg.primary_model = "test-model"
    cfg.auxiliary_api_key = ""
    cfg.auxiliary_base_url = ""
    cfg.auxiliary_model = ""
    cfg.max_concurrent_requests = 2
    cfg.timeout = 30
    cfg.daily_limit = 20
    cfg.quota_check_enabled = False
    cfg.trace_enabled = False
    cfg.trace_max_age_days = 7
    cfg.max_short_term_chars = 1500
    cfg.timezone = "Asia/Shanghai"
    cfg.lore_token_budget = 300
    cfg.group_activity_decay_per_day = 10.0
    cfg.group_activity_floor_whitelist = 50.0
    cfg.group_activity_decay_with_content = 5.0
    cfg.group_activity_content_window_hours = 24.0
    cfg.group_max_messages = 40
    cfg.search_chat_history_max_chars = 2000
    cfg.character_life_enabled = True
    cfg.character_life_jitter_minutes = 15
    cfg.character_life_min_event_interval_minutes = 5
    cfg.character_life_chain_max_depth = 3
    cfg.character_life_chain_force_extend_once_prob = 0.0
    cfg.character_life_default_energy = 50
    cfg.character_life_default_mood = 50
    cfg.character_life_default_health = 50
    cfg.character_life_recovery_energy = 20
    cfg.character_life_recovery_mood = 10
    cfg.character_life_recovery_health = 5
    cfg.character_life_diary_time = "23:30"
    cfg.proactive_coordinator_max_failures = 3
    cfg.proactive_coordinator_max_iterations = 5
    cfg.proactive_event_share_threshold = 0.6
    cfg.proactive_always_send_users = []
    cfg.proactive_always_send_groups = []
    cfg.group_activity_min_threshold = 0.0
    cfg.decay_enabled = True
    cfg.decay_grace_period_hours = 8
    cfg.decay_rate_per_hour = 0.5
    cfg.decay_daily_cap = 5.0
    bot.config.persona_ai = cfg
    bot.db = MagicMock()
    bot.db._db = MagicMock()  # 满足 PersonaDataStore 的 db_connection 参数位置
    return bot


@pytest.mark.asyncio
async def test_create_persona_success_registers_three_tools(monkeypatch):
    """create_persona 成功组装后，chat 域命中三个工具定义"""
    bot = _make_bot()

    character = Character(
        name="小骰",
        description="测试角色",
        extensions=PersonaExtensions(),
    )

    class FakeLoader:
        def __init__(self, path):
            pass

        def load(self, name):
            return character

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLoader",
        FakeLoader,
    )

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.LLMRouter",
        MagicMock,
    )

    class FakeStore:
        def __init__(self, db_connection, **kwargs):
            self.db = db_connection

        async def ensure_tables(self):
            pass

        async def get_setting(self, key):
            return None

        async def record_delivery_failure(self, user_id, group_id, content, error=None):
            pass

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.PersonaDataStore",
        FakeStore,
    )

    # 跳过持久化状态加载（不需要真实数据库）
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.ProactiveScheduler.load_persistent_state",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLife.load_persistent_state",
        AsyncMock(),
    )

    app = await create_persona(bot)

    assert app is not None
    assert app.chat is not None
    assert app.chat.tool_registry is not None

    definitions = app.chat.tool_registry.get_definitions_for(ToolDomain.CHAT)
    names = {d["function"]["name"] for d in definitions}

    assert "search_memory" in names, f"缺失 search_memory，实际注册: {names}"
    assert "search_chat_history" in names, f"缺失 search_chat_history，实际注册: {names}"
    assert "roll_dice" in names, f"缺失 roll_dice，实际注册: {names}"
    assert "send_reply_segment" in names, f"缺失 send_reply_segment，实际注册: {names}"
    assert len(names) == 4, f"期望恰好 4 个工具，实际: {names}"

    # R6: 跨组件引用一致性
    assert app.chat is not None
    assert app.life is not None
    assert app.store is not None
    assert app.port is not None
    assert app.segment_dispatcher is not None
    assert app.chat.port is app.port
    assert app.life.scheduler is not None
    assert app.life.character_life is not None
