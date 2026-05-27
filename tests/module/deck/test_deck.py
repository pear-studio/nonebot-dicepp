import pytest
from unittest.mock import MagicMock
from module.deck.deck_command import DeckItem, ForceFinal, Deck


# ────────────────────── DeckItem ──────────────────────

@pytest.mark.unit
class TestDeckItem:
    def test_init_default(self):
        item = DeckItem("测试内容")
        assert item.content == "测试内容"
        assert item.weight == 1
        assert item.redraw
        assert item.final_type == 0

    def test_init_with_params(self):
        item = DeckItem("测试内容", weight=5, redraw=False, final_type=2)
        assert item.content == "测试内容"
        assert item.weight == 5
        assert not item.redraw
        assert item.final_type == 2

    def test_weight_minimum(self):
        item = DeckItem("测试", weight=0)
        assert item.weight == 1

    def test_weight_negative(self):
        item = DeckItem("测试", weight=-5)
        assert item.weight == 1

    def test_final_type_values(self):
        for ft in [0, 1, 2]:
            item = DeckItem("测试", final_type=ft)
            assert item.final_type == ft


# ────────────────────── ForceFinal ──────────────────────

@pytest.mark.unit
class TestForceFinal:
    def test_init(self):
        error = ForceFinal("测试错误")
        assert error.info == "测试错误"

    def test_str(self):
        error = ForceFinal("测试错误")
        assert str(error) == "测试错误"

    def test_is_exception(self):
        assert issubclass(ForceFinal, Exception)


# ────────────────────── Deck ──────────────────────

def _make_loc_helper():
    """创建最小化的 LocalizationManager mock"""
    loc = MagicMock()
    loc.format_loc_text = MagicMock(side_effect=lambda key, **kwargs: kwargs.get("content", kwargs.get("result", "")))
    return loc


@pytest.mark.unit
class TestDeckAddItem:
    def test_add_item_increases_weight_sum(self):
        deck = Deck("测试牌库", "/tmp")
        deck.add_item(DeckItem("A", weight=3))
        deck.add_item(DeckItem("B", weight=2))
        assert deck.weight_sum == 5
        assert len(deck.items) == 2

    def test_add_item_default_weight(self):
        deck = Deck("测试牌库", "/tmp")
        deck.add_item(DeckItem("A"))
        assert deck.weight_sum == 1


@pytest.mark.unit
class TestDeckDraw:
    def setup_method(self):
        self.loc = _make_loc_helper()
        self.deck = Deck("测试牌库", "/tmp")
        self.deck.add_item(DeckItem("卡牌A"))
        self.deck.add_item(DeckItem("卡牌B"))
        self.deck.add_item(DeckItem("卡牌C"))

    def test_draw_single_returns_content(self):
        result = self.deck.draw(1, [self.deck], self.loc)
        assert "卡牌" in result

    def test_draw_multiple_times(self):
        # loc mock 返回 content，多次抽取不应抛异常
        result = self.deck.draw(3, [self.deck], self.loc)
        assert result.count("卡牌") == 3

    def test_draw_no_redraw_exhausts_deck(self):
        """不放回抽取：在同一次 draw(times=2) 调用中第二次应触发空牌库提示"""
        deck = Deck("不放回牌库", "/tmp")
        deck.add_item(DeckItem("唯一卡牌", redraw=False))
        loc = _make_loc_helper()
        from module.deck.deck_command import LOC_DRAW_ERR_EMPTY_DECK
        # times=2：第一次抽到唯一卡，第二次牌库已空
        deck.draw(2, [deck], loc)
        loc.format_loc_text.assert_any_call(LOC_DRAW_ERR_EMPTY_DECK)

    def test_draw_final_type_2_raises_force_final(self):
        """final_type=2 的卡牌会抛出 ForceFinal"""
        deck = Deck("终止牌库", "/tmp")
        deck.add_item(DeckItem("终止卡", final_type=2))
        loc = _make_loc_helper()
        from module.deck.deck_command import LOC_DRAW_FIN_ALL
        loc.format_loc_text.side_effect = lambda key, **kwargs: (
            "抽取提前结束！（全部）" if key == LOC_DRAW_FIN_ALL else kwargs.get("content", "")
        )
        with pytest.raises(ForceFinal):
            deck.draw(1, [deck], loc)

    def test_draw_final_type_1_stops_inner(self):
        """final_type=1 的卡牌终止内层抽取，多次draw仍能执行"""
        deck = Deck("内层终止牌库", "/tmp")
        deck.add_item(DeckItem("内层终止卡", final_type=1))
        deck.add_item(DeckItem("普通卡"))
        loc = _make_loc_helper()
        from module.deck.deck_command import LOC_DRAW_FIN_INNER
        loc.format_loc_text.side_effect = lambda key, **kwargs: (
            "提前结束内层" if key == LOC_DRAW_FIN_INNER else kwargs.get("content", "")
        )
        # 抽2次，若第一张是 final_type=1，第二张不会被抽到
        # 不抛 ForceFinal 即为通过
        deck.draw(2, [deck], loc)

    def test_weighted_draw_respects_weight(self):
        """固定 random.randint 返回值，验证权重抽卡逻辑"""
        import random
        from unittest.mock import patch

        def _loc_passthrough(key, **kwargs):
            """透传 content/result 关键字参数"""
            return kwargs.get("content", kwargs.get("result", ""))

        # weight_sum=100, randint(1,100)→1 选中第一项(低权重卡)
        deck = Deck("权重牌库", "/tmp")
        deck.add_item(DeckItem("低权重卡", weight=1))
        deck.add_item(DeckItem("高权重卡", weight=99))
        loc = _make_loc_helper()
        loc.format_loc_text.side_effect = _loc_passthrough

        with patch.object(random, 'randint', return_value=1):
            result_low = deck.draw(1, [deck], loc)

        # 重新创建牌库，randint(1,100)→50 选中第二项(高权重卡)
        deck2 = Deck("权重牌库", "/tmp")
        deck2.add_item(DeckItem("低权重卡", weight=1))
        deck2.add_item(DeckItem("高权重卡", weight=99))
        loc2 = _make_loc_helper()
        loc2.format_loc_text.side_effect = _loc_passthrough

        with patch.object(random, 'randint', return_value=50):
            result_high = deck2.draw(1, [deck2], loc2)

        assert "低权重卡" in result_low, f"randint=1 应选中低权重卡: {result_low}"
        assert "高权重卡" in result_high, f"randint=50 应选中高权重卡: {result_high}"
