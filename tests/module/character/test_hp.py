"""HP 管理集成测试。"""
import pytest


@pytest.mark.integration
class TestHPBasic:
    async def test_hp_not_found(self, h):
        await h.send_group(".hp", checker=lambda s: "找不到" in s and "的生命值信息" in s)

    async def test_hp_set_simple(self, h):
        await h.send_group(".hp 10", checker=lambda s: "HP=10\n当前HP:10" in s)

    async def test_hp_set_with_max(self, h):
        await h.send_group(".hp 30/20", checker=lambda s: "HP=30/20\n当前HP:20/20" in s)

    async def test_hp_set_with_temp(self, h):
        await h.send_group(".hp (5)", checker=lambda s: "临时HP=5\n当前HP:20/20 (5)" in s)

    async def test_hp_show_current(self, h):
        await h.send_group(".hp", checker=lambda s: "HP:20/20 (5)" in s)

    async def test_hp_damage(self, h):
        await h.send_group(".hp -10", checker=lambda s: "当前HP减少10\nHP:20/20 (5) -> HP:15/20" in s)

    async def test_hp_fatal_damage(self, h):
        await h.send_group(".hp -100", checker=lambda s: "当前HP减少100\nHP:15/20 -> HP:0/20 昏迷" in s)

    async def test_hp_heal(self, h):
        await h.send_group(".hp +1", checker=lambda s: "当前HP增加1\nHP:0/20 昏迷 -> HP:1/20" in s)

    async def test_hp_another_user(self, h):
        await h.send_group(".hp +1", user_id="123456", checker=lambda s: "当前HP增加1\n损失HP:0 -> 损失HP:0" in s)

    async def test_hp_list(self, h):
        await h.send_group(".hp list", checker=lambda s: "测试用户 HP:1/20" in s and "测试用户 损失HP:0" in s)

    async def test_hp_list_with_nickname(self, h):
        await h.send_group(".nn 法师", user_id="123456", checker=lambda s: "已将您的昵称设为法师" in s)
        await h.send_group(".hp list", checker=lambda s: "测试用户 HP:1/20" in s and "法师 损失HP:0" in s)

    async def test_hp_modify_other_user_by_name(self, h):
        await h.send_group(".hp 测试用户+1", user_id="123456",
                           checker=lambda s: "当前HP增加1\nHP:1/20 -> HP:2/20" in s)

    async def test_hp_modify_by_other_user(self, h):
        await h.send_group(".nn 战士", user_id="654321", checker=lambda s: "已将您的昵称设为战士" in s)
        await h.send_group(".hp 测试用户+100", user_id="654321",
                           checker=lambda s: "当前HP增加100\nHP:2/20 -> HP:20/20" in s)


@pytest.mark.integration
class TestHPComplex:
    async def test_hp_both_hp_and_max(self, h):
        await h.send_group(".hp +10/20", checker=lambda s: "最大HP增加20, 当前HP增加10\nHP:20/20 -> HP:30/40" in s)

    async def test_hp_all_three_values(self, h):
        await h.send_group(".hp +40/20 (10)",
                           checker=lambda s: "最大HP增加20, 当前HP增加40, 临时HP增加10\nHP:30/40 -> HP:60/60 (10)" in s)

    async def test_hp_damage_with_temp(self, h):
        await h.send_group(".hp -10 (15)",
                           checker=lambda s: "临时HP减少15, 当前HP减少10\nHP:60/60 (10) -> HP:50/60" in s)

    async def test_hp_max_down(self, h):
        await h.send_group(".hp -0/20", checker=lambda s: "最大HP减少20, 当前HP减少0\nHP:50/60 -> HP:40/40" in s)

    async def test_hp_damage_with_resistance(self, h):
        await h.send_group(".hp -4d6抗性", checker=lambda s: "当前HP减少" in s)

    async def test_hp_set_to_zero(self, h):
        await h.send_group(".hp =0", checker=lambda s: "测试用户: HP=0\n当前HP:0/40 昏迷" in s)

    async def test_hp_set_to_positive(self, h):
        await h.send_group(".hp =10", checker=lambda s: "测试用户: HP=10\n当前HP:10/40" in s and "昏迷" not in s)


@pytest.mark.integration
class TestHPDelete:
    async def test_hp_delete(self, h):
        await h.send_group(".hp del", checker=lambda s: "已删除测试用户的生命值信息" in s)
        await h.send_group(".hp", checker=lambda s: "找不到" in s and "的生命值信息" in s)

    async def test_hp_modify_nonexistent(self, h):
        await h.send_group(".hp 巨兽+2", checker=lambda s: "找不到巨兽的生命值信息" in s)


@pytest.mark.integration
class TestHPInitiativeIntegration:
    async def test_hp_on_initiative_actors(self, h):
        await h.send_group(".ri 3#哥布林",
                           checker=lambda s: s.count("哥布林") >= 3 and "哥布林a" in s.lower()
                           and "先攻值是 1D20=" in s)

    async def test_hp_damage_on_init_actor(self, h):
        await h.send_group(".hp 哥布林a-10",
                           checker=lambda s: "哥布林a: 当前HP减少10\n损失HP:0 -> 损失HP:10" in s)

    async def test_hp_heal_init_actor(self, h):
        await h.send_group(".hp 哥布林a+20",
                           checker=lambda s: "哥布林a: 当前HP增加20\n损失HP:10 -> 损失HP:0" in s)

    async def test_hp_temp_on_init_actor(self, h):
        await h.send_group(".hp a+(10)",
                           checker=lambda s: "哥布林a: 临时HP增加10\n损失HP:0 -> 损失HP:0 (10)" in s)

    async def test_hp_damage_past_temp_on_init_actor(self, h):
        await h.send_group(".hp a-20",
                           checker=lambda s: "哥布林a: 当前HP减少20\n损失HP:0 (10) -> 损失HP:10" in s)

    async def test_hp_dice_damage_on_init_actor(self, h):
        await h.send_group(".hp a-4d6+2",
                           checker=lambda s: "哥布林a: 当前HP减少" in s and "损失HP:10 -> 损失HP:" in s)

    async def test_hp_multi_target_damage(self, h):
        await h.send_group(".hp a;b;c-4d6",
                           checker=lambda s: s.count("哥布林") == 3 and s.count("\n") == 2)

    async def test_hp_list_shows_all(self, h):
        await h.send_group(".hp list",
                           checker=lambda s: s.count("哥布林") == 3 and s.count("\n") == 3
                           and "法师 损失HP:0" in s)

    async def test_hp_set_zero_on_init_actor(self, h):
        await h.send_group(".hp a=0", checker=lambda s: "哥布林a: HP=0" in s and "昏迷" not in s)

    async def test_cleanup(self, h):
        await h.send_group(".init clr", checker=lambda s: "已清除先攻列表" in s)
