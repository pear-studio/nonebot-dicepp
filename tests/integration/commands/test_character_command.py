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

from tests.support.bot_test_base import _CommandTestBase
from tests.support.assert_helpers import assert_contains_number


class _CharBotBase(_CommandTestBase):
    """Base test class for character command tests."""

    bot_name = "char_test"

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


class TestCharacterCard(_CharBotBase):
    """Tests for character card management."""

    async def test_char__create_saves_to_db(self):
        """Create character and verify in DB."""
        cmd = self._create_char_cmd(name="勇者", level=5, abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        cmds, result = await self._send_group(cmd)

        assert "设置" in result or "成功" in result, f"角色卡创建应返回成功提示: {result}"

        # Verify in DB
        char = await self.bot.db.characters_dnd.get(self.group_id, self.user_id)
        assert char.name == "勇者"
        assert char.ability_info.level == 5
        assert char.ability_info.ability[0] == 18  # str is first in ABILITY_LIST

    async def test_char__state_shows_level_and_ability(self):
        """.状态 shows level and ability values."""
        # Create character first
        cmd = self._create_char_cmd(name="勇者", level=5)
        await self._send_group(cmd)
        
        # Check status
        cmds, result = await self._send_group(".状态")
        
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
        
        assert any(word in result for word in ["找不到", "不存在", "miss", "没有"])


class TestCharacterChecks(_CharBotBase):
    """Tests for ability and skill checks."""

    async def test_check__strength_with_modifier(self):
        """Strength 18 (+4 mod), mock 10, verify result 14."""
        # Create character with STR 18 (+4 modifier)
        cmd = self._create_char_cmd(abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        await self._send_group(cmd)
        
        # Roll check with mock value 10
        cmds, result = await self._send_group(".力量检定", dice_values=[10])
        
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
        
        assert_contains_number(result, 17)

    async def test_check__skill_without_proficiency(self):
        """Stealth not proficient, DEX 14 (+2), mock 10, verify 12."""
        # Create character without Stealth proficiency
        cmd = self._create_char_cmd(abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        await self._send_group(cmd)
        
        # Roll stealth check: d20(10) + dex_mod(2) = 12
        cmds, result = await self._send_group(".隐匿检定", dice_values=[10])
        
        assert_contains_number(result, 12)

    async def test_check__with_temp_modifier(self):
        """.力量检定+3 with mock 10, verify 17 (10+4+3)."""
        cmd = self._create_char_cmd(abilities={"str": 18})
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".力量检定+3", dice_values=[10])
        
        # 10 + 4 (STR mod) + 3 = 17
        assert_contains_number(result, 17)

    async def test_check__multiple_times(self):
        """.2#力量检定 mock [8,12], verify both results appear."""
        cmd = self._create_char_cmd(abilities={"str": 18})
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".2#力量检定", dice_values=[8, 12])
        
        # Results: 8+4=12 and 12+4=16
        assert_contains_number(result, 12)
        assert_contains_number(result, 16)

    async def test_check__without_char_returns_miss(self):
        """Check without character returns missing hint."""
        cmds, result = await self._send_group(".力量检定")
        
        assert any(word in result for word in ["找不到", "不存在", "角色卡"])

    async def test_check__invalid_ability_name(self):
        """Invalid ability name returns error or no command (not processed)."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)

        cmds, result = await self._send_group(".不存在属性检定", require_response=False)

        # Invalid ability name may not be processed by any command
        # Either empty response or error message is acceptable
        if cmds:
            assert any(word in result for word in ["找不到", "不存在", "未知", "无效", "错误"])


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
        
        assert_contains_number(result, 16)

    async def test_saving__without_proficiency(self):
        """CHA save not proficient, CHA 8 (-1), mock 10, verify 9 (10-1)."""
        cmd = self._create_char_cmd(abilities={"str": 18, "dex": 14, "con": 16, "int": 10, "wis": 12, "cha": 8})
        await self._send_group(cmd)
        
        # CHA save: d20(10) + cha_mod(-1) = 9
        cmds, result = await self._send_group(".魅力豁免", dice_values=[10])
        
        assert_contains_number(result, 9)


class TestCharacterHP(_CharBotBase):
    """Tests for HP management."""

    async def test_hp__show_current(self):
        """HP 45/45, .hp reply contains 45."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".hp")
        
        assert_contains_number(result, 45)

    async def test_hp__take_damage(self):
        """HP 45/45, -10, verify HP is 35."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".hp -10")
        
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
        
        assert_contains_number(result, 30)

    async def test_hp__heal_capped_at_max(self):
        """HP 40/45, +20, verify HP capped at 45."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        # Damage first
        await self._send_group(".hp -5")
        
        # Over-heal
        cmds, result = await self._send_group(".hp +20")
        
        # Should be capped at max 45
        assert_contains_number(result, 45)

    async def test_hp__without_char_returns_miss(self):
        """.hp without character returns missing hint."""
        cmds, result = await self._send_group(".hp")
        
        assert any(word in result for word in ["找不到", "不存在", "角色卡"])


class TestCharacterHitDice(_CharBotBase):
    """Tests for .生命骰 (hit dice) command."""

    async def test_hp_dice__without_char_returns_miss(self):
        """.生命骰 without character returns missing hint."""
        cmds, result = await self._send_group(".生命骰")

        assert any(word in result for word in ["找不到", "不存在", "角色卡"])

    async def test_hp_dice__with_char_uses_one_dice(self):
        """.生命骰 with character, mock roll 6, verify 6+3=9 healing."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)

        cmds, result = await self._send_group(".生命骰", dice_values=[6])

        # D10 + CON(3) = 6 + 3 = 9
        assert "生命骰" in result
        assert "回复" in result or "恢复" in result
        assert "9" in result or "点生命值" in result

    async def test_hp_dice__multiple_dice_uses_specified_number(self):
        """.3#生命骰 uses 3 hit dice."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)

        cmds, result = await self._send_group(".3#生命骰", dice_values=[4, 5, 6])

        assert "3颗" in result or "3" in result
        assert "生命骰" in result

    async def test_hp_dice__empty_hp_dice_returns_not_set(self):
        """Character without hp_dice returns '尚未设置生命骰'."""
        # Create character without hp_dice line
        cmd = """$姓名$ 无骰角色
$等级$ 3
$生命值$ 30/30
$属性$ 10/10/10/10/10/10"""
        await self._send_group(f".角色卡记录\n{cmd}")

        cmds, result = await self._send_group(".生命骰")

        assert "尚未设置生命骰" in result


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
        
        # Verify HP restored
        char = await self.bot.db.characters_dnd.get(self.group_id, self.user_id)
        assert char.hp_info.hp_cur == 45

    async def test_long_rest__includes_hp_dice_info(self):
        """Long rest reply contains '生命骰' (hit dice)."""
        cmd = self._create_char_cmd()
        await self._send_group(cmd)
        
        cmds, result = await self._send_group(".长休")
        
        assert "生命骰" in result

    async def test_long_rest__without_char_returns_miss(self):
        """.长休 without character returns missing hint."""
        cmds, result = await self._send_group(".长休")
        
        assert any(word in result for word in ["找不到", "不存在", "角色卡"])


# ── Additional character command coverage ────────────────────────────────

class TestCharacterCardIntegration:
    async def test_no_card_found(self, h):
        await h.send_group(".角色卡", checker=lambda s: "找不到角色卡" in s)

    async def test_card_template(self, h):
        await h.send_group(".角色卡模板", checker=lambda s: "$等级$" in s and "$生命值$" in s)

    async def test_record_card(self, h):
        char_temp = """
                        $姓名$ 伊丽莎白
                        $等级$ 4
                        $生命值$ 20/30(5)
                        $生命骰$ 3/4 D8
                        $属性$ 10/15/12/13/8/11
                        $熟练$ 体操/2*隐匿/敏捷豁免/敏捷攻击
                        $额外加值$ 敏捷攻击:+1d4/魅力攻击:优势/豁免:+2/攻击:+1
                    """
        await h.send_group(f".角色卡记录\n{char_temp}", checker=lambda s: "角色卡已设置" in s)

    async def test_show_card(self, h):
        await h.send_group(".角色卡", checker=lambda s: "$等级$ 4" in s and "$生命值$ 20/30 (5)" in s)

    async def test_show_status(self, h):
        await h.send_group(".状态", checker=lambda s: "HP:20/30 (5)" in s and "生命骰:3/4 D8" in s)

    async def test_strength_check(self, h):
        await h.send_group(".力量检定",
                           checker=lambda s: "伊丽莎白 throw 力量检定" in s and "无熟练加值 力量调整值:0"
                           in s and "1D20=" in s)

    async def test_dexterity_check(self, h):
        await h.send_group(".敏捷检定",
                           checker=lambda s: "伊丽莎白 throw 敏捷检定" in s and "无熟练加值 敏捷调整值:2"
                           in s and "1D20+2=" in s)

    async def test_proficient_skill_check(self, h):
        await h.send_group(".体操检定",
                           checker=lambda s: "throw 体操检定" in s and "熟练加值:2 敏捷调整值:2"
                           in s and "1D20+2+2=" in s)

    async def test_expertise_check(self, h):
        await h.send_group(".隐匿检定",
                           checker=lambda s: "throw 隐匿检定" in s and "熟练加值:2*2 敏捷调整值:2"
                           in s and "1D20+4+2=" in s)

    async def test_skill_alias_check(self, h):
        await h.send_group(".躲藏检定",
                           checker=lambda s: "throw 躲藏检定" in s and "熟练加值:2*2 敏捷调整值:2"
                           in s and "1D20+4+2=" in s)

    async def test_wisdom_check(self, h):
        await h.send_group(".洞悉检定",
                           checker=lambda s: "throw 洞悉检定" in s and "无熟练加值 感知调整值:-1"
                           in s and "1D20-1=" in s)

    async def test_saving_throw_with_bonus(self, h):
        await h.send_group(".感知豁免",
                           checker=lambda s: "throw 感知豁免检定" in s and "无熟练加值 感知调整值:-1 额外加值:+2"
                           in s and "1D20-1+2=" in s)

    async def test_attack_with_extra_dice(self, h):
        await h.send_group(".敏捷攻击",
                           checker=lambda s: "throw 敏捷攻击检定" in s and "熟练加值:2 敏捷调整值:2 额外加值:+1d4+1"
                           in s and "1D20+2+2+1D4+1=" in s)

    async def test_strength_attack(self, h):
        await h.send_group(".力量攻击",
                           checker=lambda s: "throw 力量攻击检定" in s and "熟练加值:2 力量调整值:0 额外加值:+1"
                           in s and "1D20+2+1=" in s)

    async def test_multi_attack(self, h):
        await h.send_group(".2#敏捷攻击",
                           checker=lambda s: "throw 2次敏捷攻击检定" in s and "额外加值:+1d4+1"
                           in s and s.count("1D20+2+2+1D4+1=") == 2)

    async def test_charisma_attack_with_advantage(self, h):
        await h.send_group(".魅力攻击",
                           checker=lambda s: "throw 魅力攻击检定" in s and "熟练加值:2 魅力调整值:0 额外加值:+1 自带优势"
                           in s and "2D20K1+2+1=" in s)

    async def test_initiative_check(self, h):
        await h.send_group(".init", checker=lambda s: "伊丽莎白 先攻:" not in s)
        await h.send_group(".先攻检定",
                           checker=lambda s: "throw 先攻检定" in s and "无熟练加值 敏捷调整值:2"
                           in s and "先攻值是 1D20+2" in s)
        await h.send_group(".init",
                           checker=lambda s: "伊丽莎白 先攻:" in s and "HP:20/30 (5)" in s)

    async def test_hp_from_card(self, h):
        await h.send_group(".hp", checker=lambda s: "伊丽莎白: HP:20/30 (5)" in s)
        await h.send_group(".hp-8", checker=lambda s: "伊丽莎白: 当前HP减少8\nHP:20/30 (5) -> HP:17/30" in s)

    async def test_hit_dice(self, h):
        await h.send_group(".生命骰",
                           checker=lambda s: "伊丽莎白使用1颗D8生命骰, 体质调整值为1, 回复"
                           in s and "HP:17/30 -> HP:" in s)

    async def test_multi_hit_dice(self, h):
        await h.send_group(".2#生命骰",
                           checker=lambda s: "伊丽莎白使用2颗D8生命骰, 体质调整值为1, 回复"
                           in s and "HP:17/30" not in s)

    async def test_hit_dice_insufficient(self, h):
        await h.send_group(".10#生命骰",
                           checker=lambda s: "伊丽莎白生命骰数量不足, 还有0颗生命骰" in s)

    async def test_long_rest(self, h):
        await h.send_group(".长休",
                           checker=lambda s: "伊丽莎白进行了一次长休\n生命值回复至上限(30)\n回复2个生命骰, 当前拥有2/4个D8生命骰"
                           in s)

    async def test_delete_card(self, h):
        await h.send_group(".角色卡清除", checker=lambda s: "角色卡已删除" in s)
        await h.send_group(".角色卡", checker=lambda s: "找不到角色卡" in s)
        await h.send_group(".nn", checker=lambda s: "已将您的昵称从伊丽莎白重置为测试用户" in s)

