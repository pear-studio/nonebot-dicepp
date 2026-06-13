"""角色卡集成测试。"""
import pytest


@pytest.mark.integration
class TestCharacterCard:
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
