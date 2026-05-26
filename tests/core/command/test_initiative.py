"""先攻命令集成测试。"""
import pytest


@pytest.mark.integration
class TestInitiativeBasic:
    async def test_no_init_list(self, h):
        await h.send_group(".init", checker=lambda s: "没有找到先攻列表" in s)

    async def test_single_ri(self, h):
        await h.send_group(".nn 伊丽莎白")
        await h.send_group(".ri", checker=lambda s: "伊丽莎白的先攻值是 1D20=" in s)

    async def test_ri_with_fixed_value(self, h):
        await h.send_group(".ri8", checker=lambda s: "伊丽莎白的先攻值是 8" in s)

    async def test_ri_with_bonus(self, h):
        await h.send_group(".ri +1", checker=lambda s: "伊丽莎白的先攻值是 1D20+1" in s)

    async def test_ri_with_reason_and_custom_dice(self, h):
        await h.send_group(".ri d4+D20 大地精",
                           checker=lambda s: "大地精" in s and "先攻值是 1D4+1D20" in s)
        await h.send_group(".rid4+D20大地精",
                           checker=lambda s: "大地精" in s and "先攻值是 1D4+1D20" in s)
        await h.send_group(".ri+1大地精",
                           checker=lambda s: "大地精" in s and "先攻值是 1D20+1=" in s)

    async def test_init_list_has_all_entries(self, h):
        await h.send_group(".init", checker=lambda s: "伊丽莎白" in s and "大地精" in s)

    async def test_init_list_per_group(self, h):
        await h.send_group(".init", group_id="group2", checker=lambda s: "没有找到先攻列表" in s)

    async def test_nickname_change_affects_init_list(self, h):
        await h.send_group(".nn 雷电将军")
        await h.send_group(".init", checker=lambda s: "伊丽莎白" not in s and "雷电将军" in s and "大地精" in s)

    async def test_clear_init_list(self, h):
        await h.send_group(".init clr", checker=lambda s: "已清除先攻列表" in s)
        await h.send_group(".init", checker=lambda s: "没有找到先攻列表" in s)


@pytest.mark.integration
class TestInitiativeMulti:
    async def test_multi_ri_with_counter(self, h):
        await h.send_group(".ri 4#地精", checker=lambda s: s.count("地精") >= 4 and "地精a" in s)
        await h.send_group(".ri+4 4#地精", checker=lambda s: s.count("地精") >= 4 and "地精a" in s)

    async def test_multi_ri_with_slash_names(self, h):
        await h.send_group(".ri+4 大地精一号/大地精二号",
                           checker=lambda s: s.count("大地精") >= 2 and "大地精一号" in s)

    async def test_init_list_counts_all(self, h):
        await h.send_group(".init", checker=lambda s: s.count("大地精") >= 2 and s.count("地精") >= 6
                           and "大地精一号" in s)

    async def test_del_single_from_init(self, h):
        await h.send_group(".init del 地精a", checker=lambda s: "已从先攻列表中移除" in s and "地精a" in s)
        await h.send_group(".init", checker=lambda s: s.count("地精") >= 5 and "地精a" not in s)

    async def test_del_multi_from_init(self, h):
        await h.send_group(".init del 地精b/地精c",
                           checker=lambda s: "已从先攻列表中移除" in s and "地精b" in s and "地精c" in s)
        await h.send_group(".init", checker=lambda s: s.count("地精") >= 3 and "地精b" not in s and "地精c" not in s)


@pytest.mark.integration
class TestInitiativeAdvantage:
    async def test_ri_advantage(self, h):
        await h.send_group(".ri优势 地精", checker=lambda s: "2D20K1=" in s and "MAX" in s.upper())

    async def test_ri_advantage_with_bonus(self, h):
        await h.send_group(".ri优势+3 地精", checker=lambda s: "2D20K1+3=" in s and "MAX" in s.upper())

    async def test_ri_with_custom_name_and_bonus(self, h):
        await h.send_group(".ri+1 狗头人+1/大狗头人+2",
                           checker=lambda s: "狗头人的先攻值是 1D20+1+1=" in s and
                           "大狗头人的先攻值是 1D20+1+2=" in s)

    async def test_ri_with_name_advantage(self, h):
        await h.send_group(".ri+1 狗头人优势", checker=lambda s: "狗头人的先攻值是 2D20K1+1=" in s)

    async def test_ri_disadvantage_with_mixed_names(self, h):
        await h.send_group(".ri劣势+1 狗头人优势+1/大狗头人",
                           checker=lambda s: "狗头人的先攻值是 1D20+1+1=" in s and
                           "大狗头人的先攻值是 2D20KL1+1=" in s)


@pytest.mark.integration
class TestInitiativeErrors:
    async def test_ri_too_many_times(self, h):
        await h.send_group(".init clr", checker=lambda s: "已清除先攻列表" in s)
        await h.send_group(".ri 100000000000#地精", checker=lambda s: "不是一个有效的数字" in s)

    async def test_ri_too_many_dice(self, h):
        await h.send_group(".ri1000000D20 地精", checker=lambda s: "骰子数量不能大于100" in s)

    async def test_init_list_size_limit(self, h):
        from core.data.models import INIT_LIST_SIZE
        for i in range(INIT_LIST_SIZE):
            await h.send_group(f".ri 地精{i}", checker=lambda s: s.count("地精") >= 1)
        await h.send_group(".ri 地精-1", checker=lambda s: "先攻列表大小超出限制" in s)

    async def test_del_nonexistent(self, h):
        await h.send_group(".init del 炎魔", checker=lambda s: "先攻里没有炎魔" in s)

    async def test_cleanup(self, h):
        await h.send_group(".init clr", checker=lambda s: "已清除先攻列表" in s)
        await h.send_group(".nn")
