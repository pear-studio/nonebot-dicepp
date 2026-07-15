"""
单元测试: ProactiveScheduler 上下文构建与格式化辅助方法

覆盖 _build_share_context 默认值、_format_user_profile_facts、_format_recent_history。
"""

from datetime import datetime
from plugins.DicePP.utils.time import wall_now

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.proactive_scheduler import ProactiveScheduler
from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.life.types import AgentResult


def _make_mock_character():
    char = MagicMock()
    char.name = "七七"
    char.description = "一个喜欢户外活动的女孩"
    char.get_relation_labels = MagicMock(return_value=["冷淡", "疏远", "友好", "默契", "亲密"])
    char.extensions = MagicMock()
    char.extensions.share_message_examples = None
    return char


@pytest.fixture
def mock_data_store():
    store = MagicMock()
    from plugins.DicePP.module.persona.data.models import CharacterState
    store.get_user_profile = AsyncMock(return_value=None)
    store.get_relationship = AsyncMock(return_value=None)
    store.get_recent_messages = AsyncMock(return_value=[])
    store.get_character_state = AsyncMock(return_value=CharacterState())
    store.get_daily_events = AsyncMock(return_value=[])
    return store


@pytest.fixture
def mock_character():
    return _make_mock_character()


@pytest.fixture
def config():
    return ProactiveConfig(
        enabled=True,
        min_interval_hours=4,
        max_shares_per_event=3,
        share_time_window_minutes=15,
        miss_enabled=True,
        miss_min_hours=72,
        miss_min_score=40.0,
        timezone="Asia/Shanghai",
        share_message_concurrent=3,
        share_max_chars=200,
        share_context_history_limit=5,
    )


@pytest.fixture
def scheduler(config, mock_data_store, mock_character, mock_coordinator):
    return ProactiveScheduler(
        config=config,
        data_store=mock_data_store,
        character=mock_character,
        target_selector=MagicMock(),
        coordinator=mock_coordinator,
    )


@pytest.mark.skip(reason="_build_and_generate_share_message 已禁用，后续改造为 ChatOrchestrator 路径时恢复")
class TestBuildAndGenerateShareMessage:
    """测试 _build_and_generate_share_message"""

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_defaults_when_no_data(self, scheduler, mock_data_store):
        """当 rel=None, user_profile=None, recent_msgs=[] 时使用默认值"""
        from plugins.DicePP.module.persona.life.models import ShareTarget

        mock_agent = MagicMock()
        mock_agent.share = AsyncMock(return_value=AgentResult(success=True, data="默认消息"))
        scheduler.character_agent = mock_agent

        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        msg = await scheduler._build_and_generate_share_message(
            target=target,
            event_description="下雨了",
            reaction="有点闷",
            message_type="scheduled_event",
            environment="private",
        )

        assert msg["user_id"] == "u1"
        assert msg["content"] == "默认消息"
        assert msg["type"] == "scheduled_event"

        # 验证传给 share 的 context 包含默认值
        ctx = mock_agent.share.call_args[0][0]
        assert ctx["relationship_score"] == 0.0
        assert ctx["relation_label"] == ""
        assert ctx["user_profile_facts"] == "（无）"
        assert ctx["recent_history"] == "（无）"

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_with_relationship(self, scheduler, mock_data_store):
        """当有关系记录时 warmth_label 和 score 正确解析"""
        from plugins.DicePP.module.persona.life.models import ShareTarget

        rel = RelationshipState(user_id="u1", intimacy=100, familiarity=50)
        mock_data_store.get_relationship = AsyncMock(return_value=rel)

        mock_agent = MagicMock()
        mock_agent.share = AsyncMock(return_value=AgentResult(success=True, data="关系消息"))
        scheduler.character_agent = mock_agent

        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        msg = await scheduler._build_and_generate_share_message(
            target=target,
            event_description="事件",
            reaction="反应",
            message_type="miss_you",
            environment="private",
        )

        ctx = mock_agent.share.call_args[0][0]
        # composite_score = familiarity*0.6 + intimacy*0.4 = 50*0.6 + 100*0.4 = 70
        assert ctx["relationship_score"] == 70.0
        assert ctx["relation_label"] == "默契"
        assert ctx["message_type"] == "miss_you"

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_returns_none_on_agent_failure(self, scheduler):
        """generate_share_message 返回 None 时 _build_and_generate_share_message 也返回 None"""
        from plugins.DicePP.module.persona.life.models import ShareTarget

        mock_agent = MagicMock()
        mock_agent.share = AsyncMock(return_value=AgentResult(success=True, data=None))
        scheduler.character_agent = mock_agent

        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        msg = await scheduler._build_and_generate_share_message(
            target=target,
            event_description="事件",
            reaction="反应",
            message_type="random_event",
            environment="group",
        )

        assert msg is None

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_no_agent(self, scheduler):
        """character_agent 为 None 时返回 None"""
        from plugins.DicePP.module.persona.life.models import ShareTarget

        scheduler.character_agent = None
        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        msg = await scheduler._build_and_generate_share_message(
            target=target,
            event_description="事件",
            reaction="反应",
            message_type="random_event",
            environment="private",
        )
        assert msg is None

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_db_error(self, scheduler, mock_data_store):
        """数据库查询异常时返回 None 并记录 warning"""
        from plugins.DicePP.module.persona.life.models import ShareTarget

        mock_data_store.get_user_profile = AsyncMock(side_effect=Exception("db error"))

        mock_agent = MagicMock()
        mock_agent.share = AsyncMock(return_value=AgentResult(success=True, data="消息"))
        scheduler.character_agent = mock_agent

        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        msg = await scheduler._build_and_generate_share_message(
            target=target,
            event_description="事件",
            reaction="反应",
            message_type="random_event",
            environment="private",
        )

        assert msg is None


class TestFormatUserProfileFacts:
    """测试 _format_user_profile_facts"""

    def test_none_profile(self):
        assert ProactiveScheduler._format_user_profile_facts(None) == "（无）"

    def test_empty_facts(self):
        profile = MagicMock()
        profile.facts = {}
        assert ProactiveScheduler._format_user_profile_facts(profile) == "（无）"

    def test_string_value(self):
        profile = MagicMock()
        profile.facts = {"昵称": "小明"}
        result = ProactiveScheduler._format_user_profile_facts(profile)
        assert result == "- 昵称：小明"

    def test_list_value(self):
        profile = MagicMock()
        profile.facts = {"爱好": ["摄影", "旅行", "编程"]}
        result = ProactiveScheduler._format_user_profile_facts(profile)
        assert "摄影、旅行、编程" in result
        assert result == "- 爱好：摄影、旅行、编程"

    def test_dict_value(self):
        profile = MagicMock()
        profile.facts = {"配置": {"a": 1, "b": 2}}
        result = ProactiveScheduler._format_user_profile_facts(profile)
        assert result == '- 配置：{"a":1,"b":2}'

    def test_mixed_values(self):
        profile = MagicMock()
        profile.facts = {
            "昵称": "小明",
            "爱好": ["摄影"],
            "配置": {"key": "value"},
        }
        result = ProactiveScheduler._format_user_profile_facts(profile)
        assert "- 昵称：小明" in result
        assert "- 爱好：摄影" in result
        assert '- 配置：{"key":"value"}' in result


class TestFormatRecentHistory:
    """测试 _format_recent_history"""

    @staticmethod
    def _mock_scheduler():
        s = MagicMock()
        s._now.return_value = wall_now()
        return s

    def test_empty_messages(self):
        assert ProactiveScheduler._format_recent_history(self._mock_scheduler(), []) == "（无）"

    def test_user_and_assistant(self):
        msg1 = MagicMock()
        msg1.role = "user"
        msg1.content = "你好"
        msg2 = MagicMock()
        msg2.role = "assistant"
        msg2.content = "你好呀"
        result = ProactiveScheduler._format_recent_history(self._mock_scheduler(), [msg1, msg2])
        assert "- 用户: 你好" in result
        assert "- 我: 你好呀" in result

    def test_system_role(self):
        msg = MagicMock()
        msg.role = "system"
        msg.content = "系统提示"
        result = ProactiveScheduler._format_recent_history(self._mock_scheduler(), [msg])
        assert "- 系统: 系统提示" in result

    def test_tool_role(self):
        msg = MagicMock()
        msg.role = "tool"
        msg.content = "工具结果"
        result = ProactiveScheduler._format_recent_history(self._mock_scheduler(), [msg])
        assert "- 工具: 工具结果" in result

    def test_unknown_role(self):
        msg = MagicMock()
        msg.role = "unknown"
        msg.content = "未知内容"
        result = ProactiveScheduler._format_recent_history(self._mock_scheduler(), [msg])
        assert "- 用户: 未知内容" in result  # 兜底为"用户"

    def test_long_content_truncation(self):
        msg = MagicMock()
        msg.role = "user"
        msg.content = "哈" * 100
        result = ProactiveScheduler._format_recent_history(self._mock_scheduler(), [msg])
        assert result.endswith("...")
        assert len(result) < 100

    def test_limit_respected(self):
        msgs = []
        for i in range(10):
            m = MagicMock()
            m.role = "user"
            m.content = f"消息{i}"
            msgs.append(m)
        result = ProactiveScheduler._format_recent_history(self._mock_scheduler(), msgs, limit=3)
        lines = result.split("\n")
        assert len(lines) == 3
        assert "消息7" in result
        assert "消息0" not in result

    def test_with_timestamps(self):
        now = wall_now()
        msg = MagicMock()
        msg.role = "user"
        msg.content = "你好"
        msg.created_at = datetime(2026, 5, 11, 9, 15)
        result = ProactiveScheduler._format_recent_history(self._mock_scheduler(), [msg])
        assert "[05-11 09:15" in result
        assert "] 用户: 你好" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
