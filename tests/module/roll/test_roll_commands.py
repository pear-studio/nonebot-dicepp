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

pytestmark = pytest.mark.slow


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
        assert cmds, f"{msg!r} should return a command response"
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

        # Default ROLL_WIN is 8; 4,5,6 are all below it → 0 wins
        assert_contains_number(result, 0)
        assert_contains_number(result, 3)

    async def test_pool__with_threshold_shows_success_count(self):
        """Mock [3,4,5,6,2] threshold 4, verify success count is 3."""
        import random
        with mock.patch.object(random, 'randint', side_effect=[3, 4, 5, 6, 2]):
            cmds, result = await self._send_group(".w 5d4")

        # With threshold d4, wins are values >= 4 → 4,5,6 → 3 successes
        assert_contains_number(result, 3)

    async def test_pool__help_not_empty(self):
        """.help w returns non-empty text about pool."""
        cmds, result = await self._send_group(".help w")

        assert any(word in result for word in ['pool', 'WOD', '骰池', '.pool'])

    # ── Q71: safety limit overflow ────────────────────────────────────────────

    async def test_pool__overflow_returns_error(self):
        """.w with base_times > 300 returns '过多骰目' error."""
        cmds, result = await self._send_group(".w 301")

        assert "过多骰目" in result

    async def test_pool__boundary_300_works(self):
        """.w with base_times == 300 does NOT trigger safety limit."""
        import random
        with mock.patch.object(random, 'randint', return_value=5):
            cmds, result = await self._send_group(".w 300")

        assert "过多骰目" not in result
        assert_contains_number(result, 0)


@pytest.mark.integration
class TestRollChoose(_RollCmdBotBase):
    """Tests for .c (choose) command."""

    async def test_choose__result_in_options(self):
        """.c 苹果 香蕉 橙子, result is the first option when shuffle is fixed."""
        import random
        with mock.patch.object(random, 'shuffle', side_effect=lambda x: None):
            cmds, result = await self._send_group(".c 苹果 香蕉 橙子")

        # With shuffle as no-op, first arg "苹果" is always chosen
        assert "苹果" in result

    async def test_choose__with_reason(self):
        """Choose with reason: first option is chosen when shuffle is fixed."""
        import random
        with mock.patch.object(random, 'shuffle', side_effect=lambda x: None):
            cmds, result = await self._send_group(".c 苹果 香蕉 今天吃什么")

        # With shuffle as no-op, first arg "苹果" is always chosen
        assert "苹果" in result

    async def test_choose__compact_count_still_supported(self):
        """.c2 苹果 香蕉 橙子 keeps the compact count syntax."""
        cmds, result = await self._send_group(".c2 苹果 香蕉 橙子")

        assert "随机选择2个对象" in result
        assert any(opt in result for opt in ["苹果", "香蕉", "橙子"])

    async def test_choose__help_not_empty(self):
        """.help c returns non-empty text about choose."""
        cmds, result = await self._send_group(".help c")

        assert any(word in result for word in ['choose', '选择', '.choose'])

    # ── Q72: out-of-bounds choose_time ───────────────────────────────────────

    async def test_choose__choose_time_exceeds_options(self):
        """.c with choose_time > options returns error."""
        cmds, result = await self._send_group(".c 5 苹果 香蕉")

        assert "选不出来" in result

    async def test_choose__choose_time_zero(self):
        """.c with choose_time == 0 returns error."""
        cmds, result = await self._send_group(".c 0 苹果 香蕉")

        assert "选不出来" in result

    async def test_choose__choose_time_exceeds_options_with_reason(self):
        """.c with choose_time > options and a reason arg returns error."""
        cmds, result = await self._send_group(".c 4 苹果 香蕉 今天吃什么")

        assert "选不出来" in result


@pytest.mark.integration
class TestDiceSet(_RollCmdBotBase):
    """Tests for .dset command.
    
    Note: .dset requires permission >= 1 (admin)
    """

    async def test_dset__changes_default_dice(self):
        """.dset 20 then .r shows d20 in reply."""
        # Set as admin user (permission=1)
        cmds, result = await self._send_group(".dset d20", permission=1)

        # Should confirm setting
        assert any(word in result for word in ["设置", "成功", "默认"]), f"应返回设置成功提示: {result}"

        # Roll should use d20
        cmds2, result2 = await self._send_group(".r", dice_values=[15])
        assert_contains_number(result2, 15)

    async def test_dset__help_not_empty(self):
        """.help dset returns non-empty text about dset."""
        cmds, result = await self._send_group(".help dset")

        assert any(word in result for word in ['dset', '骰设', '.dset'])

    # ── Q70: invalid expression error path ─────────────────────────────────

    async def test_dset__invalid_placeholder_d(self):
        """.dset with placeholder D (no face) returns invalid hint."""
        cmds, result = await self._send_group(".dset 1D", permission=1)

        assert "未指定面" in result or "无效" in result

    async def test_dset__invalid_illegal_char(self):
        """.dset with illegal character returns invalid hint."""
        cmds, result = await self._send_group(".dset !!!", permission=1)

        assert "非法字符" in result or "无效" in result

    async def test_dset__invalid_face_too_small(self):
        """.dset with face < 2 returns invalid hint."""
        cmds, result = await self._send_group(".dset 1", permission=1)

        assert "至少为2" in result or "无效" in result


@pytest.mark.integration
class TestKarmaDice(_RollCmdBotBase):
    """Tests for .karmadice command.
    
    Note: on/off/set/mode/engine requires permission >= 1
    """

    async def test_karmadice__toggle_on_and_off(self):
        """Toggle on and off both return confirmation."""
        # Turn on (as admin, permission=1)
        cmds, result = await self._send_group(".karmadice on", permission=1)

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
        
        assert any(word in result for word in ["开启", "已启用", "on", "enabled", "业力"]), f"应返回启用状态: {result}"

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

        # Should be denied
        assert cmds, "expected at least one command response"
        assert any(word in result for word in ["权限", "拒绝", "denied", "permission"])

    async def test_karmadice__help_not_empty(self):
        """.help karmadice returns non-empty text about karmadice."""
        cmds, result = await self._send_group(".help karmadice")

        assert any(word in result for word in ['karmadice', '业力', '.karmadice'])

    # ── Q69: set/mode/engine/reset ─────────────────────────────────────────

    async def test_karmadice__set_valid_params(self):
        """.karmadice set 70 20 updates params and returns confirmation."""
        cmds, result = await self._send_group(".karmadice set 70 20", permission=1)

        assert "参数已更新" in result or "70" in result

    async def test_karmadice__set_invalid_params(self):
        """.karmadice set with non-numeric target returns invalid hint."""
        cmds, result = await self._send_group(".karmadice set abc", permission=1)

        assert any(word in result for word in ["参数无效", "无效", "invalid"])

    async def test_karmadice__mode_valid(self):
        """.karmadice mode hero switches to hero mode."""
        # Enable first
        await self._send_group(".karmadice on", permission=1)

        cmds, result = await self._send_group(".karmadice mode 主角光环", permission=1)

        assert "英雄" in result or "主角光环" in result or "模式" in result

    async def test_karmadice__mode_invalid(self):
        """.karmadice mode unknown returns invalid hint."""
        cmds, result = await self._send_group(".karmadice mode unknown", permission=1)

        assert any(word in result for word in ["未知", "invalid", "模式"])

    async def test_karmadice__engine_valid(self):
        """.karmadice engine advantage switches engine."""
        # Enable first
        await self._send_group(".karmadice on", permission=1)

        cmds, result = await self._send_group(".karmadice engine 优势判定", permission=1)

        assert any(word in result for word in ["引擎", "优势", "精确", "engine", "切换"])

    async def test_karmadice__engine_invalid(self):
        """.karmadice engine unknown returns invalid hint."""
        cmds, result = await self._send_group(".karmadice engine unknown", permission=1)

        assert any(word in result for word in ["未知", "invalid", "引擎"])

    async def test_karmadice__reset_group(self):
        """.karmadice reset (admin) clears group history."""
        await self._send_group(".karmadice on", permission=1)

        cmds, result = await self._send_group(".karmadice reset", permission=1)

        assert any(word in result for word in ["清空", "reset", "清"])

    async def test_karmadice__reset_self(self):
        """.karmadice reset me clears personal history without admin."""
        await self._send_group(".karmadice on", permission=1)

        cmds, result = await self._send_group(".karmadice reset me")

        assert any(word in result for word in ["清空", "reset", "清"])


# ── Appended from tests/core/command/test_roll_dice.py ───────────────────

@pytest.mark.integration
class TestRollDice:
    async def test_basic_roll(self, h):
        await h.send_group(".r", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)
        await h.send_group(".rd", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)
        await h.send_group(".rd20", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)

    async def test_multi_roll(self, h):
        await h.send_group(".r2#d20", checker=lambda s: "2次" in s and "1D20" in s)
        await h.send_group(".r2#d20+1", checker=lambda s: "2次" in s and "1D20+1" in s)
        await h.send_group(".r2#d20+5", checker=lambda s: "1D20+5" in s and "2次" in s)

    async def test_roll_with_reason(self, h):
        await h.send_group(".rd20 Attack",
                           checker=lambda s: "测试用户 为 attack 进行的掷骰结果为 1D20=" in s)
        await h.send_group(".r2#d20 Attack Twice",
                           checker=lambda s: "attack twice" in s and "2次" in s and "1D20" in s)
        await h.send_group(".rd8原因",
                           checker=lambda s: "测试用户 为 原因 进行的掷骰结果为 1D8=" in s)
        await h.send_group(".r原因",
                           checker=lambda s: "测试用户 为 原因 进行的掷骰结果为 1D20=" in s)

    async def test_hidden_roll(self, h):
        await h.send_group(".rh", checker=lambda s: "|Group: group|" in s and "|Private: user|" in s)
        await h.send_private(".rh", checker=lambda s: "|Group: group|" not in s and "|Private: user|" in s)
        await h.send_group(".rh d20 原因",
                           checker=lambda s: "测试用户 为 原因 进行的暗骰结果为" in s and
                           "1D20=" in s and "测试用户 进行了一次暗骰" in s)

    async def test_hidden_roll_target_split(self, h):
        await h.send_group(".rh", group_id="test_group_hidden", user_id="test_user_hidden",
                           target_checker=lambda cmds: len(cmds) >= 2 and
                           any("test_group_hidden" in str(c) for c in cmds) and
                           any("test_user_hidden" in str(c) for c in cmds))

    async def test_show_info_off(self, h):
        await h.send_group(".rsd20+5", checker=lambda s: "1D20+5=" in s and s.count("=") == 1)
        await h.send_group(".rs10D20cs>5",
                           checker=lambda s: "10D20CS>5=" in s and s.count("=") == 1 and "{" not in s)

    async def test_private_roll(self, h):
        await h.send_private(".r", checker=lambda s: "掷骰结果为 1D20=" in s)
        await h.send_private(".rd20", checker=lambda s: "掷骰结果为 1D20=" in s)

    async def test_error_expressions(self, h):
        await h.send_group(".r(1+1)d6", checker=lambda s: "语法错误" in s)
        await h.send_group(".r2(DK3)", checker=lambda s: "语法错误" in s)
        await h.send_group(".rh()", checker=lambda s: "语法错误" in s)

    async def test_expectation_mode(self, h):
        await h.send_private(".r exp 2d20k1", checker=lambda s: "期望" in s)

