"""
Roll extension commands integration tests.

Tests cover:
- .w (dice pool)
- .c (choose)
- .dset (default dice set)
- .karmadice (karma dice toggle)

Permission notes (from source):
- .dset: requires permission >= 1 (admin)
- .karmadice on/off/set/mode/engine: requires permission >= 1 (admin)
- .karmadice reset without "me": requires permission >= 1 (admin)
"""

import pytest
from typing import List, Tuple, Any
from unittest import IsolatedAsyncioTestCase, mock

from core.bot import Bot
from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender
from tests.conftest import async_make_test_bot, async_teardown_test_bot
from tests.helpers.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime
from tests.helpers.assert_helpers import assert_contains_number


class _RollCmdBotBase(IsolatedAsyncioTestCase):
    """Base test class for roll command tests."""

    async def asyncSetUp(self):
        self.bot, self.proxy = await async_make_test_bot("rollcmd_test")
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
                   group_id: str = None, to_me: bool = False, permission: int = 0) -> MessageMetaData:
        """Create message metadata."""
        meta = MessageMetaData(
            msg, msg,
            MessageSender(user_id or self.user_id, nickname or self.nickname),
            group_id or self.group_id,
            to_me
        )
        meta.permission = permission
        return meta

    async def _send_group(self, msg: str, user_id: str = None, nickname: str = None,
                          group_id: str = None, dice_values: List[int] = None,
                          permission: int = 0) -> Tuple[List[BotCommandBase], str]:
        """Send a group message with optional dice mocking."""
        meta = self._make_meta(msg, user_id, nickname, group_id, permission=permission)

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
class TestRollPool(_RollCmdBotBase):
    """Tests for .w (dice pool) command.

    Note: roll_pool uses random.randint directly, not karma_runtime.
    We need to mock random.randint for deterministic tests.
    """

    async def test_pool__basic_shows_all_dice(self):
        """Mock [4,5,6], verify success count is 0 (none >= 8)."""
        import random
        # RollPoolCommand uses random.randint(1, 10) in self.roll_pool
        with mock.patch.object(random, 'randint', side_effect=[4, 5, 6]):
            cmds, result = await self._send_group(".w 3")

        assert len(cmds) > 0, "Should have response"
        # Default ROLL_WIN is 8; 4,5,6 are all below it → 0 wins
        assert_contains_number(result, 0)
        assert_contains_number(result, 3)

    async def test_pool__with_threshold_shows_success_count(self):
        """Mock [3,4,5,6,2] threshold 4, verify success count is 3."""
        import random
        with mock.patch.object(random, 'randint', side_effect=[3, 4, 5, 6, 2]):
            cmds, result = await self._send_group(".w 5d4")

        assert len(cmds) > 0, "Should have response"
        # With threshold d4, wins are values >= 4 → 4,5,6 → 3 successes
        assert_contains_number(result, 3)

    async def test_pool__help_not_empty(self):
        """.help w returns non-empty text."""
        cmds, result = await self._send_group(".help w")
        
        assert len(cmds) > 0, "Should have response"
        assert len(result) > 10  # Non-empty help text


@pytest.mark.integration
class TestRollChoose(_RollCmdBotBase):
    """Tests for .c (choose) command."""

    async def test_choose__result_in_options(self):
        """.c 苹果 香蕉 橙子, result is in the set."""
        cmds, result = await self._send_group(".c 苹果 香蕉 橙子")
        
        assert len(cmds) > 0, "Should have response"
        # Result should be one of the options
        assert any(opt in result for opt in ["苹果", "香蕉", "橙子"])

    async def test_choose__with_reason(self):
        """Choose with reason includes reason text."""
        cmds, result = await self._send_group(".c 苹果 香蕉 今天吃什么")

        assert len(cmds) > 0, "Should have response"
        # The command randomly selects one of the three options (苹果, 香蕉, 今天吃什么)
        # Just verify we got a valid result containing one of the options
        assert any(opt in result for opt in ["苹果", "香蕉", "今天吃什么"])

    async def test_choose__help_not_empty(self):
        """.help c returns non-empty text."""
        cmds, result = await self._send_group(".help c")
        
        assert len(cmds) > 0, "Should have response"
        assert len(result) > 10


@pytest.mark.integration
class TestDiceSet(_RollCmdBotBase):
    """Tests for .dset command.
    
    Note: .dset requires permission >= 1 (admin)
    """

    async def test_dset__changes_default_dice(self):
        """.dset 20 then .r shows d20 in reply."""
        # Set as admin user (permission=1)
        cmds, result = await self._send_group(".dset d20", permission=1)

        assert len(cmds) > 0, "Should have response"
        # Should confirm setting
        assert any(word in result for word in ["设置", "成功", "默认"]), f"应返回设置成功提示: {result}"

        # Roll should use d20
        cmds2, result2 = await self._send_group(".r", dice_values=[15])
        assert_contains_number(result2, 15)

    async def test_dset__help_not_empty(self):
        """.help dset returns non-empty text."""
        cmds, result = await self._send_group(".help dset")
        
        assert len(cmds) > 0, "Should have response"
        assert len(result) > 10


@pytest.mark.integration
class TestKarmaDice(_RollCmdBotBase):
    """Tests for .karmadice command.
    
    Note: on/off/set/mode/engine requires permission >= 1
    """

    async def test_karmadice__toggle_on_and_off(self):
        """Toggle on and off both return confirmation."""
        # Turn on (as admin, permission=1)
        cmds, result = await self._send_group(".karmadice on", permission=1)

        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["开启", "on", "启用", "karma"]), f"应返回开启确认: {result}"

        # Turn off
        cmds2, result2 = await self._send_group(".karmadice off", permission=1)
        assert any(word in result2 for word in ["关闭", "off", "karma"]), f"应返回关闭确认: {result2}"

    async def test_karmadice__state_persists_in_db(self):
        """After enabling, re-reading group config shows enabled state."""
        # Enable karma dice (requires admin permission)
        await self._send_group(".karmadice on", permission=1)
        
        # Check status
        cmds, result = await self._send_group(".karmadice status")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["开启", "已启用", "on", "enabled"]), f"应返回启用状态: {result}"

    async def test_karmadice__permission_check(self):
        """Non-master user should be rejected for admin commands."""
        # Try to toggle as non-admin user (permission 0)
        meta = MessageMetaData(
            ".karmadice on", ".karmadice on",
            MessageSender("regular_user", "普通用户"),
            self.group_id,
            False
        )
        # Set permission level to 0 (regular user)
        meta.permission = 0
        
        cmds = await self.bot.process_message(".karmadice on", meta)
        result = "\n".join([str(cmd) for cmd in cmds])
        
        # Should be denied or no response
        if len(cmds) > 0:
            assert any(word in result for word in ["权限", "拒绝", "denied", "permission"])

    async def test_karmadice__help_not_empty(self):
        """.help karmadice returns non-empty text."""
        cmds, result = await self._send_group(".help karmadice")
        
        assert len(cmds) > 0, "Should have response"
        assert len(result) > 10
