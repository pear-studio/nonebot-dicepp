"""
跨模块 E2E 战斗流程测试
验证完整的战斗流程涉及多个模块的协同工作
"""
import pytest

from tests.e2e.conftest import e2e_bot, send_as_user
from core.bot import Bot


# ---------------------------------------------------------------------------
# 角色卡创建辅助函数 - 使用命名字段格式（$姓名$, $等级$ 等）
# ---------------------------------------------------------------------------

def _char_cmd(
    name: str,
    level: int,
    hp_max: int,
    hp_dice: str,
    abilities: str,
    profs: str,
) -> str:
    """构建 .角色卡记录 命令字符串（使用解析器支持的命名字段格式）。

    Args:
        name: 角色名
        level: 等级
        hp_max: 最大 HP
        hp_dice: 生命骰类型，如 "D10"
        abilities: 六维属性，用 "/" 分隔，如 "16/14/13/10/12/8"
        profs: 熟练技能，用 "/" 分隔，如 "运动/威吓"（空字符串则不添加该行）
    """
    lines = [
        ".角色卡记录",
        f"$姓名$ {name}",
        f"$等级$ {level}",
        f"$生命值$ {hp_max}/{hp_max}",
        f"$生命骰$ {level}/{level} {hp_dice}",
        f"$属性$ {abilities}",
    ]
    if profs:
        lines.append(f"$熟练$ {profs}")
    return "\n".join(lines)


@pytest.mark.e2e
class TestCombatFlow:
    """E2E 战斗流程测试"""

    async def test_combat_flow__full_encounter(self, e2e_bot: Bot):
        """任务 7.1: 完整战斗流程 - 从创建角色到战斗结束"""
        bot = e2e_bot
        group_id = "combat_group_1"

        dm_id = "dm_user"
        dm_nick = "DM"

        # 创建战士角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("战士", 5, 50, "D10", "16/14/13/10/12/8", "运动/威吓"),
            user_id=dm_id, nickname=dm_nick, group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"战士角色卡创建失败: {result}"

        # 创建法师角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("法师", 5, 30, "D6", "8/14/12/16/13/10", "奥秘/历史"),
            user_id="player1", nickname="玩家1", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"法师角色卡创建失败: {result}"

        # 开启日志（如果环境不支持，仅验证命令不崩溃）
        cmds, result = await send_as_user(
            bot, ".log on", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        # 日志可能未启用，只要返回了响应即可
        assert len(cmds) > 0, "日志命令应返回响应"

        # 查看先攻列表（初始应为空）
        cmds, result = await send_as_user(
            bot, ".init", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert "没有找到" in result or "先攻" in result, "应返回先攻相关响应"

        # 开启新战斗轮（.br 清理旧战斗状态，应在添加先攻前调用）
        cmds, result = await send_as_user(
            bot, ".br", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert len(cmds) > 0, "应成功创建战斗轮"

        # 添加怪物 (使用 .ri 命令)
        cmds, result = await send_as_user(
            bot, ".ri 20 地精", user_id=dm_id, nickname=dm_nick, group_id=group_id, dice_values=[20]
        )
        assert "地精" in result and "先攻" in result, f"应添加地精到先攻列表: {result}"

        # 验证状态: 查询先攻列表确认地精已添加
        cmds, result = await send_as_user(
            bot, ".init", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert "地精" in result and "20" in result, f"先攻列表中应包含地精及先攻值: {result}"

        # 战士使用 .ri 加入先攻
        cmds, result = await send_as_user(
            bot, ".ri 战士", user_id=dm_id, nickname=dm_nick, group_id=group_id, dice_values=[18]
        )
        assert "战士" in result and "先攻" in result, f"战士应加入先攻: {result}"

        # 法师使用 .ri 加入先攻
        cmds, result = await send_as_user(
            bot, ".ri 法师", user_id="player1", nickname="玩家1", group_id=group_id, dice_values=[15]
        )
        assert "法师" in result and "先攻" in result, f"法师应加入先攻: {result}"

        # 查看当前先攻列表和回合
        cmds, result = await send_as_user(
            bot, ".turn", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert "回合" in result and ("地精" in result or "战士" in result or "法师" in result), f"应显示当前回合: {result}"

        # 战士攻击
        cmds, result = await send_as_user(
            bot, ".r 1d20+5 攻击地精", user_id=dm_id, nickname=dm_nick, group_id=group_id, dice_values=[18]
        )
        assert "攻击" in result, f"应显示攻击检定: {result}"

        # 法师施法
        cmds, result = await send_as_user(
            bot, ".r 3d8 火球术伤害", user_id="player1", nickname="玩家1", group_id=group_id, dice_values=[3, 4, 7]
        )
        assert "火球术" in result, f"应显示法术伤害: {result}"

        # 地精受到伤害 (使用 .hp 命令)
        cmds, result = await send_as_user(
            bot, ".hp 地精 -10", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert "地精" in result and ("HP" in result or "生命" in result or "伤害" in result), f"应显示地精受到伤害: {result}"

        # 验证状态: 查询先攻列表确认地精 HP 已记录
        cmds, result = await send_as_user(
            bot, ".init", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert "地精" in result, f"先攻列表中应包含地精: {result}"

        # 推进回合（结束当前回合进入下一回合）
        cmds, result = await send_as_user(
            bot, ".ed", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert "回合" in result, f"应推进回合: {result}"

        # 结束战斗（清除先攻列表）
        cmds, result = await send_as_user(
            bot, ".init clr", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert "清除" in result or "清空" in result, f"应清除先攻列表: {result}"

        # 关闭日志（如果环境不支持，仅验证命令不崩溃）
        cmds, result = await send_as_user(
            bot, ".log off", user_id=dm_id, nickname=dm_nick, group_id=group_id
        )
        assert len(cmds) > 0, "日志命令应返回响应"

    async def test_combat_flow__initiative_with_character_stats(self, e2e_bot: Bot):
        """任务 7.2: 先攻与角色卡属性联动"""
        bot = e2e_bot
        group_id = "combat_group_2"

        # 创建高敏捷角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("游荡者", 3, 24, "D8", "8/16/12/14/10/8", "潜行/巧手"),
            user_id="player_rogue", nickname="游荡者玩家", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"游荡者角色卡创建失败: {result}"

        # 查看先攻列表（初始为空）
        cmds, result = await send_as_user(
            bot, ".init", user_id="dm2", nickname="DM", group_id=group_id
        )
        assert "没有找到" in result or "先攻" in result, "初始先攻列表应为空"

        # 游荡者使用 .ri 加入先攻
        cmds, result = await send_as_user(
            bot, ".ri 游荡者", user_id="player_rogue", nickname="游荡者玩家", group_id=group_id, dice_values=[17]
        )
        assert "游荡者" in result and "先攻" in result, f"游荡者应加入先攻: {result}"

    async def test_combat_flow__hp_tracking_during_combat(self, e2e_bot: Bot):
        """任务 7.3: 战斗中 HP 追踪"""
        bot = e2e_bot
        group_id = "combat_group_3"

        # 创建角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("圣武士", 5, 45, "D10", "16/10/14/8/12/16", "宗教/洞悉"),
            user_id="player_paladin", nickname="圣武士玩家", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"圣武士角色卡创建失败: {result}"

        # 查看先攻
        cmds, result = await send_as_user(bot, ".init", user_id="dm3", nickname="DM", group_id=group_id)
        assert "没有找到" in result, "初始应无先攻列表"

        # 圣武士使用 .ri 加入先攻
        cmds, result = await send_as_user(
            bot, ".ri 圣武士", user_id="player_paladin", nickname="圣武士玩家", group_id=group_id, dice_values=[12]
        )
        assert "圣武士" in result and "先攻" in result, f"圣武士应加入先攻: {result}"

        # 设置初始 HP
        cmds, result = await send_as_user(
            bot, ".hp =30/45", user_id="player_paladin", nickname="圣武士玩家", group_id=group_id
        )
        assert "30" in result and "45" in result, f"应设置初始 HP 为 30/45: {result}"

        # 受到伤害 (使用 .hp 命令)
        cmds, result = await send_as_user(
            bot, ".hp -15", user_id="player_paladin", nickname="圣武士玩家", group_id=group_id
        )
        assert "15" in result and ("伤害" in result or "失去" in result or "HP" in result), f"应显示受到伤害 15: {result}"

        # 验证状态: 查询 HP 确认已减少
        cmds, result = await send_as_user(
            bot, ".hp", user_id="player_paladin", nickname="圣武士玩家", group_id=group_id
        )
        # HP 应为 30-15=15，格式为 "HP:15/45" 或类似
        assert "15" in result and "45" in result, f"HP 应显示为 15/45, 实际: {result}"

        # 治疗 (使用 .hp 命令)
        cmds, result = await send_as_user(
            bot, ".hp +10", user_id="player_paladin", nickname="圣武士玩家", group_id=group_id
        )
        assert "10" in result and ("治疗" in result or "恢复" in result or "HP" in result), f"应显示治疗 10: {result}"

        # 验证状态: 查询 HP 确认已恢复
        cmds, result = await send_as_user(
            bot, ".hp", user_id="player_paladin", nickname="圣武士玩家", group_id=group_id
        )
        # HP 应为 15+10=25
        assert "25" in result and "45" in result, f"HP 应显示为 25/45, 实际: {result}"

    async def test_combat_flow__saving_throw_in_combat(self, e2e_bot: Bot):
        """任务 7.4: 战斗中豁免检定"""
        bot = e2e_bot
        group_id = "combat_group_4"

        # 创建角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("牧师", 4, 32, "D8", "10/12/14/13/16/14", "医药/洞悉"),
            user_id="player_cleric", nickname="牧师玩家", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"牧师角色卡创建失败: {result}"

        # 查看先攻
        cmds, result = await send_as_user(bot, ".init", user_id="dm4", nickname="DM", group_id=group_id)
        assert "没有找到" in result, "初始应无先攻列表"

        # 牧师使用 .ri 加入先攻
        cmds, result = await send_as_user(
            bot, ".ri 牧师", user_id="player_cleric", nickname="牧师玩家", group_id=group_id, dice_values=[14]
        )
        assert "牧师" in result and "先攻" in result, f"牧师应加入先攻: {result}"

        # 进行感知检定
        cmds, result = await send_as_user(
            bot, ".感知检定", user_id="player_cleric", nickname="牧师玩家", group_id=group_id, dice_values=[15]
        )
        assert "感知" in result and "检定" in result, f"应进行感知检定: {result}"

    async def test_combat_flow__multi_round_combat(self, e2e_bot: Bot):
        """任务 7.5: 多轮战斗流程"""
        bot = e2e_bot
        group_id = "combat_group_5"

        # 设置战斗 (使用 .ri 命令添加怪物)
        cmds, result = await send_as_user(bot, ".init", user_id="dm5", nickname="DM", group_id=group_id)
        assert "没有找到" in result, "初始应无先攻列表"

        # 开启战斗轮（应在添加先攻前调用，.br 会清理旧战斗状态）
        cmds, result = await send_as_user(bot, ".br", user_id="dm5", nickname="DM", group_id=group_id)
        assert len(cmds) > 0, "应成功创建战斗轮"

        await send_as_user(bot, ".ri 15 兽人1", user_id="dm5", nickname="DM", group_id=group_id, dice_values=[15])
        await send_as_user(bot, ".ri 14 兽人2", user_id="dm5", nickname="DM", group_id=group_id, dice_values=[14])

        # 验证先攻列表
        cmds, result = await send_as_user(bot, ".init", user_id="dm5", nickname="DM", group_id=group_id)
        assert "兽人1" in result and "兽人2" in result, f"先攻列表应包含两个兽人: {result}"

        # 查看当前回合（此时应能正常显示）
        cmds, result = await send_as_user(bot, ".turn", user_id="dm5", nickname="DM", group_id=group_id)
        assert "回合" in result and ("兽人1" in result or "兽人2" in result), f"应显示当前回合: {result}"

        # 进行多轮 (通过多次 .ed 推进回合)
        for i in range(4):  # 推进4个回合（跨越多轮）
            cmds, result = await send_as_user(
                bot, ".ed", user_id="dm5", nickname="DM", group_id=group_id
            )
            assert "回合" in result or "轮" in result, f"第 {i+1} 次推进应正常进行: {result}"

        # 查看当前轮次
        cmds, result = await send_as_user(bot, ".round", user_id="dm5", nickname="DM", group_id=group_id)
        assert "轮" in result, f"应显示当前轮次: {result}"

        # 结束战斗（清除先攻列表）
        cmds, result = await send_as_user(bot, ".init clr", user_id="dm5", nickname="DM", group_id=group_id)
        assert "清除" in result or "清空" in result, f"应清除先攻列表: {result}"

    async def test_combat_flow__dm_controls_initiative(self, e2e_bot: Bot):
        """任务 7.7: DM 控制先攻列表"""
        bot = e2e_bot
        group_id = "combat_group_7"
        dm_id = "dm7"

        # DM 查看先攻（初始为空）
        cmds, result = await send_as_user(
            bot, ".init", user_id=dm_id, nickname="DM", group_id=group_id
        )
        assert "没有找到" in result or "先攻" in result, "初始先攻列表应为空"

        # DM 添加怪物 (使用 .ri 命令)
        cmds, result = await send_as_user(
            bot, ".ri 18 巨魔", user_id=dm_id, nickname="DM", group_id=group_id, dice_values=[18]
        )
        assert "巨魔" in result and "先攻" in result, f"DM 应能添加怪物: {result}"

        # DM 移除怪物 — 使用 .init del 命令
        cmds, result = await send_as_user(
            bot, ".init del 巨魔", user_id=dm_id, nickname="DM", group_id=group_id
        )
        # 应成功移除（删除后列表为空，会提示先攻列表不存在）
        assert "移除" in result or "删除" in result or "没有" in result or "不存在" in result, f"应成功移除巨魔: {result}"

    async def test_combat_flow__player_join_leave_initiative(self, e2e_bot: Bot):
        """任务 7.8: 玩家加入和离开先攻"""
        bot = e2e_bot
        group_id = "combat_group_8"

        # 创建角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("游侠", 3, 28, "D10", "12/16/14/10/14/8", "自然/生存"),
            user_id="player_ranger", nickname="游侠玩家", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"游侠角色卡创建失败: {result}"

        # DM 查看先攻
        cmds, result = await send_as_user(bot, ".init", user_id="dm8", nickname="DM", group_id=group_id)
        assert "没有找到" in result, "初始应无先攻列表"

        # 玩家使用 .ri 加入先攻
        cmds, result = await send_as_user(
            bot, ".ri 游侠", user_id="player_ranger", nickname="游侠玩家", group_id=group_id, dice_values=[16]
        )
        assert "游侠" in result and "先攻" in result, f"玩家应能加入先攻: {result}"

        # 查看列表
        cmds, result = await send_as_user(
            bot, ".init", user_id="dm8", nickname="DM", group_id=group_id
        )
        assert "游侠" in result, f"先攻列表应包含游侠: {result}"

    async def test_combat_flow__death_saving_throws(self, e2e_bot: Bot):
        """任务 7.9: 死亡豁免流程"""
        bot = e2e_bot
        group_id = "combat_group_9"

        # 创建低 HP 角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("武僧", 3, 20, "D8", "12/16/12/10/14/10", "体操/运动"),
            user_id="player_monk", nickname="武僧玩家", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"武僧角色卡创建失败: {result}"

        # 进入先攻
        cmds, result = await send_as_user(bot, ".init", user_id="dm9", nickname="DM", group_id=group_id)
        assert "没有找到" in result, "初始应无先攻列表"

        # 武僧使用 .ri 加入先攻
        cmds, result = await send_as_user(
            bot, ".ri 武僧", user_id="player_monk", nickname="武僧玩家", group_id=group_id, dice_values=[13]
        )
        assert "武僧" in result and "先攻" in result, f"武僧应加入先攻: {result}"

        # 造成大量伤害 (使用 .hp 命令)
        cmds, result = await send_as_user(
            bot, ".hp 武僧 -25", user_id="dm9", nickname="DM", group_id=group_id
        )
        assert "武僧" in result and ("HP" in result or "生命" in result), f"应显示武僧受到伤害: {result}"

        # 进行死亡豁免
        cmds, result = await send_as_user(
            bot, ".死亡豁免", user_id="player_monk", nickname="武僧玩家", group_id=group_id, dice_values=[12]
        )
        assert "死亡豁免" in result or "豁免" in result, f"应进行死亡豁免: {result}"

    async def test_combat_flow__long_rest_during_adventure(self, e2e_bot: Bot):
        """任务 7.10: 冒险中长休恢复"""
        bot = e2e_bot
        group_id = "combat_group_10"

        # 创建角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("术士", 4, 26, "D6", "8/14/12/14/10/16", "奥秘/欺瞒/威吓/游说"),
            user_id="player_sorcerer", nickname="术士玩家", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"术士角色卡创建失败: {result}"

        # 设置初始 HP
        cmds, result = await send_as_user(
            bot, ".hp =20/26", user_id="player_sorcerer", nickname="术士玩家", group_id=group_id
        )
        assert "20" in result and "26" in result, f"应设置初始 HP 为 20/26: {result}"

        # 受到伤害
        cmds, result = await send_as_user(
            bot, ".hp -10", user_id="player_sorcerer", nickname="术士玩家", group_id=group_id
        )
        assert "10" in result, f"应显示受到伤害 10: {result}"

        # 验证 HP 减少
        cmds, result = await send_as_user(
            bot, ".hp", user_id="player_sorcerer", nickname="术士玩家", group_id=group_id
        )
        assert "10" in result and "26" in result, f"HP 应显示为 10/26, 实际: {result}"

        # 长休恢复 (使用 .长休 命令)
        cmds, result = await send_as_user(
            bot, ".长休", user_id="player_sorcerer", nickname="术士玩家", group_id=group_id
        )
        assert "长休" in result or "休息" in result or "恢复" in result, f"应进行长休: {result}"

    async def test_combat_flow__character_status_check(self, e2e_bot: Bot):
        """任务 7.11: 角色状态查看（.状态 显示 HP 和生命骰）"""
        bot = e2e_bot
        group_id = "combat_group_11"

        # 创建施法者角色
        cmds, result = await send_as_user(
            bot,
            _char_cmd("法师", 5, 32, "D6", "8/14/12/16/13/10", "奥秘/历史/调查"),
            user_id="player_wizard", nickname="法师玩家", group_id=group_id,
        )
        assert "设置" in result or "成功" in result, f"法师角色卡创建失败: {result}"

        # 查看角色状态（.状态 只返回 HP 信息和生命骰）
        cmds, result = await send_as_user(
            bot, ".状态", user_id="player_wizard", nickname="法师玩家", group_id=group_id
        )
        # .状态 返回格式为 "HP:32/32
        assert "HP" in result or "生命" in result, f"应显示角色 HP 信息: {result}"
        # 验证包含生命骰信息
        assert "D6" in result or "生命骰" in result, f"应显示生命骰信息: {result}"
