"""A4+R8: 主动消息回流 — _send_msg 在 port.send 成功后回流到 Chat Conversation

R8 变更：
- 先 port.send() 再 add_message_stream（失败不落记录）
- append_visible_if_active → append_visible（无 active 时创建轻量 Conversation）

覆盖：
- chat_registry=None → 不回流（向后兼容）
- chat_registry 注入 → append_visible 被调且 scope 正确
- append_visible 异常不阻断发送
- display_name = self.character.name 而非 "我"
- port.send 失败 → 不写 stream、不回流
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.gateway.port import MessagePort
from plugins.DicePP.module.persona.life.simulator import LifeSimulator, LifeConfig
from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig


def _make_simulator(*, event_chain=None, proactive_msgs=None,
                     diary: str = '今天很好',
                     chat_registry=None):
    """构造最小可运行的 LifeSimulator（同 test_life_simulator._make_simulator）"""
    store = AsyncMock()
    store.list_all_relationships_raw = AsyncMock(return_value=[])
    store.update_relationship = AsyncMock()
    store.add_score_event = AsyncMock()
    store.prune_llm_traces = AsyncMock(return_value=0)
    character_life = MagicMock()
    character_life.tick = AsyncMock(return_value=event_chain)
    scheduler = MagicMock()
    scheduler.tick = AsyncMock(return_value=proactive_msgs or [])
    scheduler.config = MagicMock(spec=ProactiveConfig())
    scheduler.config.max_shares_per_event = 1
    scheduler.share_event_to_targets = AsyncMock(return_value=[])
    scheduler.schedule_share = MagicMock()
    diary_generator = MagicMock()
    diary_generator.generate_diary = AsyncMock(return_value=diary)
    character = MagicMock()
    character.name = "测试角色"
    character.extensions = MagicMock()
    port = MagicMock()
    port.send = AsyncMock()
    config = LifeConfig(
        trace_enabled=False,
    )
    sim = LifeSimulator(
        store=store, character_life=character_life,
        scheduler=scheduler, diary_generator=diary_generator,
        character=character, config=config, port=port,
        decay_calculator=None, chat_registry=chat_registry,
    )
    return sim


class TestSendMsgDisplayName:
    """A4: display_name 从 '我' 改为 character.name"""

    @pytest.mark.asyncio
    async def test_display_name_is_character_name(self):
        """add_message_stream 的 display_name = self.character.name。"""
        sim = _make_simulator()
        sim.character.name = "测试角色"
        sim.store.add_message_stream = AsyncMock(return_value=12345)
        sim.port.send = AsyncMock(return_value=True)

        await sim._send_msg({'user_id': 'u1', 'group_id': '', 'content': 'hi'})
        call_kwargs = sim.store.add_message_stream.call_args.kwargs
        assert call_kwargs['display_name'] == "测试角色"

    @pytest.mark.asyncio
    async def test_send_with_skip_history_record(self):
        """R8: port.send 传入 skip_history_record=True 防止 hook 写入重复 stream。"""
        sim = _make_simulator()
        sim.store.add_message_stream = AsyncMock(return_value=999)
        sim.port.send = AsyncMock(return_value=True)

        await sim._send_msg({'user_id': 'u2', 'group_id': 'g2', 'content': 'hi'})

        sim.port.send.assert_awaited_once_with(
            'u2', 'g2', 'hi',
            skip_history_record=True,
        )


class TestSendMsgReflow:
    """A4: 主动消息回流到 Chat Conversation"""

    @pytest.mark.asyncio
    async def test_no_chat_registry_no_reflow(self):
        """chat_registry=None 时不回流，发送照常。"""
        from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry
        sim = _make_simulator()
        sim.store.add_message_stream = AsyncMock(return_value=100)
        sim.port.send = AsyncMock(return_value=True)

        await sim._send_msg({'user_id': 'u1', 'group_id': 'g1', 'content': '群消息'})
        # 发送成功
        sim.port.send.assert_awaited_once()
        # 没有 registry 可调用
        assert sim.chat_registry is None

    @pytest.mark.asyncio
    async def test_reflow_group_scope(self):
        """group_id 非空 → ConversationScope.for_group(group_id)。"""
        chat_registry = AsyncMock()
        chat_registry.append_visible = AsyncMock(return_value=MagicMock())
        sim = _make_simulator(chat_registry=chat_registry)
        sim.store.add_message_stream = AsyncMock(return_value=200)
        sim.port.send = AsyncMock(return_value=True)

        await sim._send_msg({'user_id': 'u1', 'group_id': 'g1', 'content': '群消息'})

        chat_registry.append_visible.assert_awaited_once()
        args = chat_registry.append_visible.call_args[0]
        scope = args[0]
        assert scope.namespace == 'chat.group'
        assert scope.key == 'g1'
        assert args[1] == 200  # msg_id
        assert args[2] == 'assistant'  # role

    @pytest.mark.asyncio
    async def test_reflow_private_scope(self):
        """group_id 为空 → ConversationScope.for_private(user_id)。"""
        chat_registry = AsyncMock()
        chat_registry.append_visible = AsyncMock(return_value=MagicMock())
        sim = _make_simulator(chat_registry=chat_registry)
        sim.store.add_message_stream = AsyncMock(return_value=300)
        sim.port.send = AsyncMock(return_value=True)

        await sim._send_msg({'user_id': 'u1', 'group_id': '', 'content': '私聊消息'})

        chat_registry.append_visible.assert_awaited_once()
        args = chat_registry.append_visible.call_args[0]
        scope = args[0]
        assert scope.namespace == 'chat.private'
        assert scope.key == 'u1'
        assert args[1] == 300
        assert args[2] == 'assistant'

    @pytest.mark.asyncio
    async def test_reflow_exception_does_not_block_send(self):
        """append_visible_if_active 异常不阻断消息发送。"""
        chat_registry = AsyncMock()
        chat_registry.append_visible = AsyncMock(
            side_effect=RuntimeError("回流异常")
        )
        sim = _make_simulator(chat_registry=chat_registry)
        sim.store.add_message_stream = AsyncMock(return_value=400)
        sim.port.send = AsyncMock(return_value=True)

        # 不应抛异常
        await sim._send_msg({'user_id': 'u1', 'group_id': 'g1', 'content': '群消息'})
        sim.port.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reflow_no_active_session_still_reflows(self):
        """R8(b): 无 active session 时 append_visible 创建轻量 Conversation（不抛异常）。"""
        chat_registry = AsyncMock()
        chat_registry.append_visible = AsyncMock(return_value=MagicMock())
        sim = _make_simulator(chat_registry=chat_registry)
        sim.store.add_message_stream = AsyncMock(return_value=500)
        sim.port.send = AsyncMock(return_value=True)

        await sim._send_msg({'user_id': 'u1', 'group_id': '', 'content': '私聊'})
        sim.port.send.assert_awaited_once()
        # R8: 即使无 active session，append_visible 也应被调用（创建轻量 Conversation）
        chat_registry.append_visible.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reflow_send_failure_no_reflow(self):
        """R8(a): port.send 失败时不写 stream、不回流。"""
        chat_registry = AsyncMock()
        chat_registry.append_visible = AsyncMock()
        sim = _make_simulator(chat_registry=chat_registry)
        sim.store.add_message_stream = AsyncMock(return_value=600)
        sim.port.send = AsyncMock(return_value=False)

        await sim._send_msg({'user_id': 'u1', 'group_id': 'g1', 'content': '发送失败'})
        # 发送失败 → 不写 stream、不回流
        sim.store.add_message_stream.assert_not_called()
        chat_registry.append_visible.assert_not_called()

    @pytest.mark.asyncio
    async def test_real_message_port_without_proxy_leaves_no_history(self):
        """生产 MessagePort 明确无法投递时，Life 不得记录 stream/ref。"""
        chat_registry = AsyncMock()
        chat_registry.append_visible = AsyncMock()
        sim = _make_simulator(chat_registry=chat_registry)
        bot = MagicMock()
        bot.proxy = None
        bot.account = "bot"
        sim.port = MessagePort(bot)
        sim.store.add_message_stream = AsyncMock(return_value=601)

        await sim._send_msg({'user_id': 'u1', 'group_id': 'g1', 'content': '发送失败'})

        sim.store.add_message_stream.assert_not_awaited()
        chat_registry.append_visible.assert_not_awaited()

    # ── R8: 静默过期轮换 ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_life_reflow_expired_rotation(self):
        """R8(c): active session 已静默过期时，append_visible 被调用（非 append_visible_if_active）。

        过期检测（_is_silence_expired）在 registry 内部实现，依赖 DB session 状态。
        本测试只验证 _send_msg 正确调用 append_visible（而非 append_visible_if_active），
        且 scope/message_stream_id/role 参数正确。
        """
        chat_registry = AsyncMock()
        chat_registry.append_visible = AsyncMock(return_value=MagicMock())
        # 确保 append_visible_if_active 未被调用
        chat_registry.append_visible_if_active = AsyncMock()
        sim = _make_simulator(chat_registry=chat_registry)
        sim.store.add_message_stream = AsyncMock(return_value=700)
        sim.port.send = AsyncMock(return_value=True)

        await sim._send_msg({'user_id': 'u1', 'group_id': '', 'content': '过期轮换消息'})

        # append_visible 被调（不是 append_visible_if_active）
        chat_registry.append_visible.assert_awaited_once()
        chat_registry.append_visible_if_active.assert_not_called()
        args = chat_registry.append_visible.call_args[0]
        scope = args[0]
        assert scope.namespace == 'chat.private'
        assert scope.key == 'u1'
        assert args[1] == 700   # msg_id
        assert args[2] == 'assistant'  # role
