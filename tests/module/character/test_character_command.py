"""
Character module command integration tests.

Tests cover:
- .角色卡 (character card creation/management)
- .状态 (character status)
- Ability/skill checks (.力量检定, etc.)
- Saving throws (.体质豁免, etc.)
- .hp (HP management)
- .长休 (long rest)

Command format notes (from char_command.py):
- .角色卡记录 [content] - Create/update character
- .角色卡清除 - Delete character
- .角色卡模板 - Show template
- .状态 - Show HP/status
- .[ability]检定[+mod] - Ability check
- .[skill]检定 - Skill check
- .[ability]豁免 - Saving throw
- .hp [+/-amount] - HP management
- .长休 - Long rest

Character card format:
$姓名$ [name]
$等级$ [level]
$生命值$ [cur]/[max] ([temp])
$生命骰$ [cur]/[max] D[type]
$属性$ [str]/[dex]/[con]/[int]/[wis]/[cha]
$熟练$ [skill1]/[skill2]/...
"""

import pytest
from typing import List, Tuple, Any
from unittest import IsolatedAsyncioTestCase

from core.bot import Bot
from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender
from tests.conftest import async_make_test_bot, async_teardown_test_bot
from tests.helpers.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime
from tests.helpers.assert_helpers import assert_contains_number


class _CharBotBase(IsolatedAsyncioTestCase):
    """Base test class for character command tests."""

    async def asyncSetUp(self):
        self.bot, self.proxy = await async_make_test_bot("char_test")
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

    def _create_char_cmd(self, name: str = "测试角色", level: int = 5,
                         abilities: dict = None, proficiencies: str = "") -> str:
        """Generate character creation command."""
        if abilities is None:
            abilities = {"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8}
        
        ability_str = "/".join([str(abilities.get(k, 10)) for k in ["str", "dex", "con", "int", "wis", "cha"]])
        
        char_content = f"""$姓名$ {name}
$等级$ {level}
$生命值$ 45/45
$生命骰$ 5/5 D10
$属性$ {ability_str}"""
        
        if proficiencies:
            char_content += f"\n$熟练$ {proficiencies}"
        
        return f".角色卡记录\n{char_content}"


@pytest.mark.integration
class TestCharacterCard(_CharBotBase):
    """Tests for character card management."""

    async def test_char__create_saves_to_db(self):
        """Create character and verify in DB."""
        cmd = self._create_char_cmd(name="勇者", level=5, abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        cmds, result = await self._send_group(cmd)

        assert len(cmds) > 0, "Should have response"
        assert "设置" in result or "成功" in result, f"角色卡创建应返回成功提示: {result}"

        # Verify in DB
        char = await self.bot.db.characters_dnd.get(self.group_id, self.user_id)
        assert char is not None
        assert char.ability_info.level == 5
        assert char.ability_info.ability[0] == 18  # str is first in ABILITY_LIST

    async def test_char__state_shows_level_and_ability(self):
        """.状态 shows level and ability values."""
        # Create character first
        cmd = self._create_char_cmd(name="勇者", level=5)
        await self._send_group(cmd)
        
        # Check status
        cmds, result = await self._send_group(".状态")
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 5)  # Level
        assert_contains_number(result, 45)  # HP

    async def test_char__overwrite_updates_db(self):
        """Overwrite character updates DB."""
        # Create first character
        cmd1 = self._create_char_cmd(name="勇者", level=5, abilities={"str": 18})
        await self._send_group(cmd1)

        # Overwrite with new stats
        cmd2 = self._create_char_cmd(name="勇者", level=10, abilities={"str": 20})
        cmds, result = await self._send_group(cmd2)

        # Verify updated in DB
        char = await self.bot.db.characters_dnd.get(self.group_id, self.user_id)
        assert char.ability_info.level == 10
        assert char.ability_info.ability[0] == 20  # str is first in ABILITY_LIST

    async def test_char__delete_then_state_shows_miss(self):
        """Delete character then .状态 shows missing hint."""
        # Create and delete
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        await self._send_group(".角色卡清除")
        
        # Check status
        cmds, result = await self._send_group(".状态")
        
        assert any(word in result for word in ["找不到", "不存在", "miss", "没有"])

    async def test_char__state_without_char_returns_miss(self):
        """.状态 without character returns missing hint."""
        cmds, result = await self._send_group(".状态")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["找不到", "不存在", "miss", "没有"])


@pytest.mark.integration
class TestCharacterChecks(_CharBotBase):
    """Tests for ability and skill checks."""

    async def test_check__strength_with_modifier(self):
        """Strength 18 (+4 mod), mock 10, verify result 14."""
        # Create character with STR 18 (+4 modifier)
        cmd = self._create_char_cmd(abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        await self._send_group(cmd)
        
        # Roll check with mock value 10
        cmds, result = await self._send_group(".力量检定", dice_values=[10])
        
        assert len(cmds) > 0, "Should have response"
        # Result should be 10 + 4 = 14
        assert_contains_number(result, 14)

    async def test_check__skill_with_proficiency(self):
        """Athletics proficient (+3 prof), STR 18 (+4), mock 10, verify 17."""
        # Create character with Athletics proficiency
        cmd = self._create_char_cmd(
            abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8},
            proficiencies="运动"
        )
        await self._send_group(cmd)
        
        # Roll athletics check: d20(10) + str_mod(4) + prof(3) = 17
        cmds, result = await self._send_group(".运动检定", dice_values=[10])
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 17)

    async def test_check__skill_without_proficiency(self):
        """Stealth not proficient, DEX 14 (+2), mock 10, verify 12."""
        # Create character without Stealth proficiency
        cmd = self._create_char_cmd(abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        await self._send_group(cmd)
        
        # Roll stealth check: d20(10) + dex_mod(2) = 12
        cmds, result = await self._send_group(".隐匿检定", dice_values=[10])
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 12)

    async def test_check__with_temp_modifier(self):
        """.力量检定+3 with mock 10, verify 17 (10+4+3)."""
        cmd = self._create_char_cmd(abilities={"str": 18})
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".力量检定+3", dice_values=[10])
        
        assert len(cmds) > 0, "Should have response"
        # 10 + 4 (STR mod) + 3 = 17
        assert_contains_number(result, 17)

    async def test_check__multiple_times(self):
        """.2#力量检定 mock [8,12], verify both results appear."""
        cmd = self._create_char_cmd(abilities={"str": 18})
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".2#力量检定", dice_values=[8, 12])
        
        assert len(cmds) > 0, "Should have response"
        # Results: 8+4=12 and 12+4=16
        assert_contains_number(result, 12)
        assert_contains_number(result, 16)

    async def test_check__without_char_returns_miss(self):
        """Check without character returns missing hint."""
        cmds, result = await self._send_group(".力量检定")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["找不到", "不存在", "角色卡"])

    async def test_check__invalid_ability_name(self):
        """Invalid ability name returns error or no command (not processed)."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)

        cmds, result = await self._send_group(".不存在属性检定")

        # Invalid ability name may not be processed by any command
        # Either empty response or error message is acceptable
        if len(cmds) > 0:
            assert any(word in result for word in ["找不到", "不存在", "未知", "无效", "错误"])


@pytest.mark.integration
class TestCharacterSaving(_CharBotBase):
    """Tests for saving throws."""

    async def test_saving__with_proficiency(self):
        """CON save proficient, CON 16 (+3), mock 10, verify 16 (10+3+3)."""
        cmd = self._create_char_cmd(
            abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8},
            proficiencies="体质豁免"
        )
        await self._send_group(cmd)
        
        # CON save: d20(10) + con_mod(3) + prof(3) = 16
        cmds, result = await self._send_group(".体质豁免", dice_values=[10])
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 16)

    async def test_saving__without_proficiency(self):
        """CHA save not proficient, CHA 8 (-1), mock 10, verify 9 (10-1)."""
        cmd = self._create_char_cmd(abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        await self._send_group(cmd)
        
        # CHA save: d20(10) + cha_mod(-1) = 9
        cmds, result = await self._send_group(".魅力豁免", dice_values=[10])
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 9)


@pytest.mark.integration
class TestCharacterHP(_CharBotBase):
    """Tests for HP management."""

    async def test_hp__show_current(self):
        """HP 45/45, .hp reply contains 45."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".hp")
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 45)

    async def test_hp__take_damage(self):
        """HP 45/45, -10, verify HP is 35."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".hp -10")
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 35)

    async def test_hp__heal(self):
        """HP 20/45, +10, verify HP is 30."""
        # Create character with low HP via DB seed would be better, but using command
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        # First damage
        await self._send_group(".hp -25")
        
        # Then heal
        cmds, result = await self._send_group(".hp +10")
        
        assert len(cmds) > 0, "Should have response"
        assert_contains_number(result, 30)

    async def test_hp__heal_capped_at_max(self):
        """HP 40/45, +20, verify HP capped at 45."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        # Damage first
        await self._send_group(".hp -5")
        
        # Over-heal
        cmds, result = await self._send_group(".hp +20")
        
        assert len(cmds) > 0, "Should have response"
        # Should be capped at max 45
        assert_contains_number(result, 45)

    async def test_hp__without_char_returns_miss(self):
        """.hp without character returns missing hint."""
        cmds, result = await self._send_group(".hp")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["找不到", "不存在", "角色卡"])


@pytest.mark.integration
class TestCharacterLongRest(_CharBotBase):
    """Tests for long rest."""

    async def test_long_rest__restores_full_hp(self):
        """Low HP long rest, verify HP is 45."""
        # Create character
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        # Take damage
        await self._send_group(".hp -30")
        
        # Long rest
        cmds, result = await self._send_group(".长休")
        
        assert len(cmds) > 0, "Should have response"
        # Verify HP restored
        char = await self.bot.db.characters_dnd.get(self.group_id, self.user_id)
        assert char.hp_info.hp_cur == 45

    async def test_long_rest__includes_hp_dice_info(self):
        """Long rest reply contains '生命骰' (hit dice)."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".长休")
        
        assert len(cmds) > 0, "Should have response"
        assert "生命骰" in result

    async def test_long_rest__without_char_returns_miss(self):
        """.长休 without character returns missing hint."""
        cmds, result = await self._send_group(".长休")
        
        assert len(cmds) > 0, "Should have response"
        assert any(word in result for word in ["找不到", "不存在", "角色卡"])
