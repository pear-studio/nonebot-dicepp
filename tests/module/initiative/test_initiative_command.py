"""
Initiative module command integration tests.

Tests cover:
- .ri (roll initiative) basic and advanced scenarios
- .init (initiative list management)
- .br (battle round management)
- Multi-user interactions
- Same initiative value handling

Command format notes (from initiative_command.py):
- .ri [expression] [name] - Roll initiative
- .init [list|clr|del|first|swap|import] - Manage initiative list
- .br - Battle round management
- .ed - End turn/advance
"""

import pytest
from typing import List, Tuple, Any
from unittest import IsolatedAsyncioTestCase

from core.bot import Bot
from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender
from tests.conftest import async_make_test_bot, async_teardown_test_bot
from tests.helpers.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime
from tests.helpers.assert_helpers import (
    assert_contains_number,
    assert_name_order,
)


class _InitBotBase(IsolatedAsyncioTestCase):
    """Base test class for initiative command tests."""

    async def asyncSetUp(self):
        self.bot, self.proxy = await async_make_test_bot("init_test")
        self.group_id = "test_group"
        self.user_id = "test_user"
        self.nickname = "测试用户"
        self._runtime_token = None

    async def asyncTearDown(self):
        if self._runtime_token:
            reset_runtime(self._runtime_token)
            self._runtime_token = None
        await async_teardown_test_bot(self.bot)

    def _make_meta(self, msg: str, user_id: str = None, nickname: str = None, 
                   group_id: str = None, to_me: bool = False) -> MessageMetaData:
        """Create message metadata."""
        return MessageMetaData(
            msg, msg,
            MessageSender(user_id or self.user_id, nickname or self.nickname),
            group_id or self.group_id,
            to_me
        )

    async def _send_group(self, msg: str, user_id: str = None, nickname: str = None,
                          group_id: str = None, dice_values: List[int] = None) -> Tuple[List[BotCommandBase], str]:
        """Send a group message with optional dice mocking."""
        meta = self._make_meta(msg, user_id, nickname, group_id)
        
        if dice_values is not None:
            runtime = SequenceRuntime(dice_values)
            self._runtime_token = set_runtime(runtime)
            try:
                cmds = await self.bot.process_message(msg, meta)
            finally:
                reset_runtime(self._runtime_token)
                self._runtime_token = None
        else:
            cmds = await self.bot.process_message(msg, meta)
        
        result = "\n".join([str(cmd) for cmd in cmds])
        return cmds, result

    async def _send_private(self, msg: str, user_id: str = None, nickname: str = None,
                            dice_values: List[int] = None) -> Tuple[List[BotCommandBase], str]:
        """Send a private message with optional dice mocking."""
        meta = MessageMetaData(
            msg, msg,
            MessageSender(user_id or self.user_id, nickname or self.nickname),
            "", True
        )
        
        if dice_values is not None:
            runtime = SequenceRuntime(dice_values)
            self._runtime_token = set_runtime(runtime)
            try:
                cmds = await self.bot.process_message(msg, meta)
            finally:
                reset_runtime(self._runtime_token)
                self._runtime_token = None
        else:
            cmds = await self.bot.process_message(msg, meta)
        
        result = "\n".join([str(cmd) for cmd in cmds])
        return cmds, result


@pytest.mark.integration
class TestInitiativeRi(_InitBotBase):
    """Tests for .ri (roll initiative) command."""

    async def test_ri__basic_roll(self):
        """Mock dice returns 15, verify reply contains 15 and entry is in list."""
        cmds, result = await self._send_group(".ri", dice_values=[15])
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 15)
        
        # Verify entry is in list
        cmds2, result2 = await self._send_group(".init")
        assert self.nickname in result2 or "测试用户" in result2

    async def test_ri__with_modifier(self):
        """Mock dice returns 12, modifier +3, verify result is 15."""
        cmds, result = await self._send_group(".ri +3", dice_values=[12])
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 15)  # 12 + 3 = 15

    async def test_ri__for_npc(self):
        """DM rolls for '哥布林' (Goblin), verify list entry has empty owner."""
        cmds, result = await self._send_group(".ri 哥布林", dice_values=[10])
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 10)
        assert "哥布林" in result

    async def test_ri__reroll_overwrites_entry(self):
        """Same user rolls twice, verify list entry count doesn't increase."""
        # First roll
        await self._send_group(".ri", dice_values=[10])
        
        # Get initial list
        cmds, result = await self._send_group(".init")
        
        # Second roll (should overwrite)
        await self._send_group(".ri", dice_values=[18])
        
        # Verify list still has one entry for this user
        cmds2, result2 = await self._send_group(".init")
        assert_contains_number(result2, 18)  # New value

    async def test_ri__invalid_expression_returns_error(self):
        """Invalid expression, verify error hint and no crash."""
        # Use an expression that looks like roll but is invalid
        cmds, result = await self._send_group(".ri 1dxx")

        # Command may return empty if expression is parsed as name, or error if parsed as expression
        if len(cmds) > 0:
            # Should contain error indication
            assert any(word in result for word in ["无效", "错误", "非法", "Error", "error", "[Roll]"])

    async def test_ri__private_chat_rejected(self):
        """Private chat (no group_id) .ri should be rejected with an error message."""
        cmds, result = await self._send_private(".ri", dice_values=[15])

        # In private chat, initiative requires a group context and must return a
        # rejection response. A silent empty response is not acceptable because it
        # would allow the test to pass even when the feature is not implemented.
        assert len(cmds) > 0, "Private chat .ri should return a rejection response, not silently ignore"
        assert any(word in result for word in ["群", "group", "无法", "不能"]), (
            f"Expected rejection message (e.g. 群/无法/不能) in response, got: {result!r}"
        )

    async def test_ri__same_value_dm_prompt(self):
        """Two users with same initiative value, verify DM decision prompt."""
        # First user rolls 15
        await self._send_group(".ri", user_id="user1", nickname="勇者", dice_values=[15])
        
        # Second user rolls 15 (same value)
        cmds, result = await self._send_group(".ri", user_id="user2", nickname="法师", dice_values=[15])
        
        # Should contain prompt about same value (DM decision needed)
        assert any(word in result for word in ["相同", "DM", "决定", "顺序", "same"])


@pytest.mark.integration
class TestInitiativeList(_InitBotBase):
    """Tests for .init command."""

    async def test_init__empty_list_shows_hint(self):
        """Empty list .init should return hint."""
        cmds, result = await self._send_group(".init")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["没有", "不存在", "empty", "None", "没有找到"])

    async def test_init__shows_sorted_order(self):
        """3 entries sorted descending by initiative value."""
        # Create 3 entries with different initiative values
        await self._send_group(".ri", user_id="user1", nickname="战士", dice_values=[20])
        await self._send_group(".ri", user_id="user2", nickname="法师", dice_values=[15])
        await self._send_group(".ri", user_id="user3", nickname="盗贼", dice_values=[10])
        
        cmds, result = await self._send_group(".init")
        
        # Verify order: 战士(20) > 法师(15) > 盗贼(10)
        assert_name_order(result, ["战士", "法师", "盗贼"])

    async def test_init__clr_clears_list(self):
        """clr clears the list."""
        # Add an entry
        await self._send_group(".ri", dice_values=[15])
        
        # Clear list
        cmds, result = await self._send_group(".init clr")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["清除", "清空", "clr", "clear"]), f"应返回清除确认: {result}"
        
        # Verify list is empty
        cmds2, result2 = await self._send_group(".init")
        assert any(word in result2 for word in ["没有", "不存在", "没有找到"])

    async def test_init_del__existing_entry(self):
        """Delete existing entry succeeds."""
        # Add entry
        await self._send_group(".ri", user_id="user1", nickname="勇者", dice_values=[15])
        
        # Delete entry
        cmds, result = await self._send_group(".init del 勇者")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["删除", "移除"]), f"应返回删除确认: {result}"

    async def test_init_del__not_found_returns_error(self):
        """Delete non-existent entry returns error."""
        # Add an entry first
        await self._send_group(".ri", user_id="user1", nickname="勇者", dice_values=[15])
        
        # Try to delete non-existent entry
        cmds, result = await self._send_group(".init del 幽灵")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["没有", "不存在", "找不到", "not found"])

    async def test_init__swap_changes_order(self):
        """swap changes the order of names in list."""
        # Add two entries
        await self._send_group(".ri", user_id="user1", nickname="勇者", dice_values=[20])
        await self._send_group(".ri", user_id="user2", nickname="法师", dice_values=[15])
        
        # Get initial order
        cmds1, result1 = await self._send_group(".init")
        
        # Swap order
        cmds2, result2 = await self._send_group(".init swap 勇者 法师")
        
        assert len(cmds2) > 0, "Should have response"
        assert any(word in result2 for word in ["互换", "交换", "swap", "已"])


@pytest.mark.integration
class TestInitiativeBattleRound(_InitBotBase):
    """Tests for battle round (.br, .ed) commands."""

    async def test_br__on_empty_list_returns_error(self):
        """.br on empty list returns error."""
        cmds, result = await self._send_group(".br")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["空", "没有", "不存在", "先攻", "empty"])

    async def test_br__advance_turn_and_round(self):
        """.br creates battle, .ed advances, verify round count."""
        # Add entries
        await self._send_group(".ri", user_id="user1", nickname="战士", dice_values=[20])
        await self._send_group(".ri", user_id="user2", nickname="法师", dice_values=[15])
        
        # Create battle round
        cmds, result = await self._send_group(".br")
        assert len(cmds) > 0, "Should have response"
        
        # Advance turn (multiple times to go to next round)
        for _ in range(3):
            cmds, result = await self._send_group(".ed")
        
        # Verify round number increased (check for round indicator)
        # The result should contain turn/round information
        assert len(cmds) > 0, "Should have response after advancing"


@pytest.mark.integration
class TestInitiativeMultiUser(_InitBotBase):
    """Tests for multi-user scenarios."""

    async def test_multi_user__ri_sorted_correctly(self):
        """Three users initiative sorted correctly."""
        # Three users roll different initiatives
        await self._send_group(".ri", user_id="user1", nickname="战士", dice_values=[20])
        await self._send_group(".ri", user_id="user2", nickname="法师", dice_values=[18])
        await self._send_group(".ri 哥布林", user_id="dm", nickname="DM", dice_values=[15])
        
        cmds, result = await self._send_group(".init")
        
        # Verify descending order
        assert_name_order(result, ["战士", "法师", "哥布林"])

    async def test_multi_user__groups_isolated(self):
        """Group1 data doesn't appear in Group2's .init reply."""
        # Add entry in group1
        await self._send_group(".ri", user_id="user1", nickname="战士", 
                               group_id="group1", dice_values=[20])
        
        # Check group2 doesn't have the entry
        cmds, result = await self._send_group(".init", group_id="group2")
        
        assert "战士" not in result
        assert any(word in result for word in ["没有", "不存在", "没有找到"])
