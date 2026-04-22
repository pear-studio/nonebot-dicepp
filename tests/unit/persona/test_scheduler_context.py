"""
单元测试: ProactiveScheduler 上下文构建与格式化辅助方法

覆盖 _build_share_context 默认值、_format_user_profile_facts、_format_recent_history。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.proactive.scheduler import ProactiveScheduler, ProactiveConfig
from plugins.DicePP.module.persona.data.models import RelationshipState


def _make_mock_character():
    char = MagicMock()
    char.name = "七七"
    char.description = "一个喜欢户外活动的女孩"
    char.get_warmth_labels = MagicMock(return_value=["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"])
    char.extensions = MagicMock()
    char.extensions.share_message_examples = None
    return char


@pytest.fixture
def mock_data_store():
    store = MagicMock()
    store.get_user_profile = AsyncMock(return_value=None)
    store.get_relationship = AsyncMock(return_value=None)
    store.get_recent_messages = AsyncMock(return_value=[])
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
        share_threshold=0.5,
        share_message_concurrent=3,
        share_max_chars=200,
        share_context_history_limit=5,
    )


@pytest.fixture
def scheduler(config, mock_data_store, mock_character):
    return ProactiveScheduler(
        config=config,
        data_store=mock_data_store,
        character=mock_character,
        target_selector=MagicMock(),
    )


class TestBuildAndGenerateShareMessage:
    """测试 _build_and_generate_share_message"""

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_defaults_when_no_data(self, scheduler, mock_data_store):
        """当 rel=None, user_profile=None, recent_msgs=[] 时使用默认值"""
        from plugins.DicePP.module.persona.proactive.models import ShareTarget

        mock_agent = MagicMock()
        mock_agent.generate_share_message = AsyncMock(return_value="默认消息")
        scheduler.event_agent = mock_agent

        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        msg = await scheduler._build_and_generate_share_message(
            target=target,
            event_description="下雨了",
            reaction="有点闷",
            message_type="scheduled_event",
            environment="private",
        )

        assert msg is not None
        assert msg["user_id"] == "u1"
        assert msg["content"] == "默认消息"
        assert msg["type"] == "scheduled_event"

        # 验证传给 generate_share_message 的 context 包含默认值
        ctx = mock_agent.generate_share_message.call_args[0][0]
        assert ctx.relationship_score == 0.0
        assert ctx.warmth_label == ""
        assert ctx.user_profile_facts == "（无）"
        assert ctx.recent_history == "（无）"

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_with_relationship(self, scheduler, mock_data_store):
        """当有关系记录时 warmth_label 和 score 正确解析"""
        from plugins.DicePP.module.persona.proactive.models import ShareTarget

        rel = RelationshipState(user_id="u1", group_id="", intimacy=65.0, passion=60.0, trust=70.0, secureness=60.0)
        mock_data_store.get_relationship = AsyncMock(return_value=rel)

        mock_agent = MagicMock()
        mock_agent.generate_share_message = AsyncMock(return_value="关系消息")
        scheduler.event_agent = mock_agent

        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        msg = await scheduler._build_and_generate_share_message(
            target=target,
            event_description="事件",
            reaction="反应",
            message_type="miss_you",
            environment="private",
        )

        ctx = mock_agent.generate_share_message.call_args[0][0]
        assert ctx.relationship_score == 64.5  # composite_score = 65*0.3 + 60*0.2 + 70*0.3 + 60*0.2
        assert ctx.warmth_label == "亲近"
        assert ctx.message_type == "miss_you"

    @pytest.mark.asyncio
    async def test_build_and_generate_share_message_returns_none_on_agent_failure(self, scheduler):
        """generate_share_message 返回 None 时 _build_and_generate_share_message 也返回 None"""
        from plugins.DicePP.module.persona.proactive.models import ShareTarget

        mock_agent = MagicMock()
        mock_agent.generate_share_message = AsyncMock(return_value=None)
        scheduler.event_agent = mock_agent

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
        """event_agent 为 None 时返回 None"""
        from plugins.DicePP.module.persona.proactive.models import ShareTarget

        scheduler.event_agent = None
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
        from plugins.DicePP.module.persona.proactive.models import ShareTarget

        mock_data_store.get_user_profile = AsyncMock(side_effect=Exception("db error"))

        mock_agent = MagicMock()
        mock_agent.generate_share_message = AsyncMock(return_value="消息")
        scheduler.event_agent = mock_agent

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

    def test_empty_messages(self):
        assert ProactiveScheduler._format_recent_history([]) == "（无）"

    def test_user_and_assistant(self):
        msg1 = MagicMock()
        msg1.role = "user"
        msg1.content = "你好"
        msg2 = MagicMock()
        msg2.role = "assistant"
        msg2.content = "你好呀"
        result = ProactiveScheduler._format_recent_history([msg1, msg2])
        assert "- 用户: 你好" in result
        assert "- 我: 你好呀" in result

    def test_system_role(self):
        msg = MagicMock()
        msg.role = "system"
        msg.content = "系统提示"
        result = ProactiveScheduler._format_recent_history([msg])
        assert "- 系统: 系统提示" in result

    def test_tool_role(self):
        msg = MagicMock()
        msg.role = "tool"
        msg.content = "工具结果"
        result = ProactiveScheduler._format_recent_history([msg])
        assert "- 工具: 工具结果" in result

    def test_unknown_role(self):
        msg = MagicMock()
        msg.role = "unknown"
        msg.content = "未知内容"
        result = ProactiveScheduler._format_recent_history([msg])
        assert "- 用户: 未知内容" in result  # 兜底为"用户"

    def test_long_content_truncation(self):
        msg = MagicMock()
        msg.role = "user"
        msg.content = "哈" * 100
        result = ProactiveScheduler._format_recent_history([msg])
        assert result.endswith("...")
        assert len(result) < 100

    def test_limit_respected(self):
        msgs = []
        for i in range(10):
            m = MagicMock()
            m.role = "user"
            m.content = f"消息{i}"
            msgs.append(m)
        result = ProactiveScheduler._format_recent_history(msgs, limit=3)
        lines = result.split("\n")
        assert len(lines) == 3
        assert "消息7" in result
        assert "消息0" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
