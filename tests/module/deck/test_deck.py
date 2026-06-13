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

    @pytest.mark.parametrize("weight_input", [0, -5])
    def test_weight_minimum(self, weight_input):
        item = DeckItem("测试", weight=weight_input)
        assert item.weight == 1

    def test_final_type_values(self):
        for ft in [0, 1, 2]:
            item = DeckItem("测试", final_type=ft)
            assert item.final_type == ft

    # ── DeckItem.get_result ROLL handler (Q32) ─────────────────────────────────

    def test_get_result_roll_valid_expression(self):
        """ROLL() with valid dice expression should evaluate and return result."""
        from unittest.mock import patch, MagicMock
        item = DeckItem("ROLL(1D20)")
        loc = _make_loc_helper()
        source_deck = Deck("测试牌库", "/tmp")

        with patch('module.deck.deck_command.exec_roll_exp_unified') as mock_roll:
            mock_result = MagicMock()
            mock_result.get_complete_result.return_value = "1D20=15"
            mock_roll.return_value = mock_result
            result = item.get_result(source_deck, [], loc)

        assert "1D20=15" in result

    def test_get_result_roll_invalid_expression_ignore(self):
        """ROLL() with invalid expression and ignore=True returns original text."""
        item = DeckItem("ROLL(abc)")
        loc = _make_loc_helper()
        source_deck = Deck("测试牌库", "/tmp")

        result = item.get_result(source_deck, [], loc, ignore=True)

        assert "abc" in result or result == "abc"

    def test_get_result_roll_invalid_expression_raises(self):
        """ROLL() with invalid expression and ignore=False raises ValueError."""
        item = DeckItem("ROLL(abc)")
        loc = _make_loc_helper()
        source_deck = Deck("测试牌库", "/tmp")

        with pytest.raises(ValueError):
            item.get_result(source_deck, [], loc, ignore=False)

    # ── DeckItem.get_result IMG handler (Q34) ─────────────────────────────────

    def test_get_result_img_fallback(self):
        """IMG() with non-existing file should return the key as fallback."""
        from unittest.mock import patch
        item = DeckItem("IMG(test.png)")
        loc = _make_loc_helper()
        source_deck = Deck("测试牌库", "/tmp")

        with patch('pathlib.Path.exists', return_value=False):
            result = item.get_result(source_deck, [], loc)

        assert result == "test.png"

    def test_get_result_img_relative_path_fallback(self):
        """IMG() with relative subpath and non-existing file returns key."""
        from unittest.mock import patch
        item = DeckItem("IMG(subdir/img.jpg)")
        loc = _make_loc_helper()
        source_deck = Deck("测试牌库", "/tmp")

        with patch('pathlib.Path.exists', return_value=False):
            result = item.get_result(source_deck, [], loc)

        assert result == "subdir/img.jpg"

    # ── DeckItem.get_result DRAW handler (Q33) ─────────────────────────────────

    def test_get_result_draw_valid_simple_count(self):
        """DRAW() with simple integer count draws from target deck."""
        import random
        from unittest.mock import patch

        item = DeckItem("DRAW(目标牌库, 1)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")
        target_deck = Deck("目标牌库", "/tmp")
        target_deck.add_item(DeckItem("CardA"))

        with patch.object(random, "randint", return_value=1):
            result = item.get_result(source_deck, [target_deck], loc)

        assert "CardA" in result

    def test_get_result_draw_invalid_expression_ignore(self):
        """DRAW() with invalid expression and ignore=True returns fallback."""
        item = DeckItem("DRAW(t, abc)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        result = item.get_result(source_deck, [], loc, ignore=True)

        assert result == "t*ABC"

    def test_get_result_draw_invalid_expression_raises(self):
        """DRAW() with invalid expression and ignore=False raises ValueError."""
        item = DeckItem("DRAW(t, abc)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        with pytest.raises(ValueError, match="invalid roll expression"):
            item.get_result(source_deck, [], loc, ignore=False)

    def test_get_result_draw_deck_not_found_ignore(self):
        """DRAW() referencing non-existent deck with ignore=True returns fallback."""
        item = DeckItem("DRAW(缺失牌库, 2)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        result = item.get_result(source_deck, [], loc, ignore=True)

        assert result == "缺失牌库*2"

    def test_get_result_draw_deck_not_found_raises(self):
        """DRAW() referencing non-existent deck with ignore=False raises ValueError."""
        item = DeckItem("DRAW(缺失牌库, 2)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        with pytest.raises(ValueError, match="invalid deck"):
            item.get_result(source_deck, [], loc, ignore=False)

    def test_get_result_draw_times_zero_ignore(self):
        """DRAW() with draw_times=0 and ignore=True returns fallback."""
        item = DeckItem("DRAW(t, 0)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        result = item.get_result(source_deck, [], loc, ignore=True)

        assert result == "t*0"

    def test_get_result_draw_times_zero_raises(self):
        """DRAW() with draw_times=0 and ignore=False raises ValueError."""
        item = DeckItem("DRAW(t, 0)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        with pytest.raises(ValueError, match="invalid value"):
            item.get_result(source_deck, [], loc, ignore=False)

    def test_get_result_draw_times_exceed_limit_ignore(self):
        """DRAW() with draw_times>HLDL_DRAW_LIMIT and ignore=True returns fallback."""
        item = DeckItem("DRAW(t, 99)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        result = item.get_result(source_deck, [], loc, ignore=True)

        assert result == "t*99"

    def test_get_result_draw_times_exceed_limit_raises(self):
        """DRAW() with draw_times>HLDL_DRAW_LIMIT and ignore=False raises ValueError."""
        item = DeckItem("DRAW(t, 99)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")

        with pytest.raises(ValueError, match="invalid value"):
            item.get_result(source_deck, [], loc, ignore=False)

    def test_get_result_draw_dice_expression_count(self):
        """DRAW() with dice expression as count evaluates and draws correctly."""
        import random
        from unittest.mock import patch, MagicMock

        item = DeckItem("DRAW(目标牌库, 1D4)")
        loc = _make_loc_helper()
        source_deck = Deck("来源", "/tmp")
        target_deck = Deck("目标牌库", "/tmp")
        target_deck.add_item(DeckItem("CardX"))

        mock_result = MagicMock()
        mock_result.get_val.return_value = 3
        mock_result.get_complete_result.return_value = "1D4=3"

        with (
            patch("module.deck.deck_command.exec_roll_exp_unified", return_value=mock_result),
            patch.object(random, "randint", return_value=1),
        ):
            result = item.get_result(source_deck, [target_deck], loc)

        assert "CardX" in result


# ────────────────────── ForceFinal ──────────────────────

@pytest.mark.unit
class TestForceFinal:
    @pytest.mark.parametrize("attr,expected", [
        ("info", "测试错误"),
        ("__str__()", "测试错误"),
    ])
    def test_force_final(self, attr, expected):
        error = ForceFinal("测试错误")
        if attr == "__str__()":
            assert str(error) == expected
        else:
            assert getattr(error, attr) == expected

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
    @pytest.mark.parametrize("weight_input,expected_sum", [(3, 3), (None, 1)])
    def test_add_item_increases_weight_sum(self, weight_input, expected_sum):
        deck = Deck("测试牌库", "/tmp")
        kwargs = {"weight": weight_input} if weight_input is not None else {}
        deck.add_item(DeckItem("A", **kwargs))
        assert deck.weight_sum == expected_sum
        assert len(deck.items) == 1


@pytest.mark.unit
class TestDeckDraw:
    def setup_method(self):
        self.loc = _make_loc_helper()
        self.deck = Deck("测试牌库", "/tmp")
        self.deck.add_item(DeckItem("卡牌A"))
        self.deck.add_item(DeckItem("卡牌B"))
        self.deck.add_item(DeckItem("卡牌C"))

    @pytest.mark.parametrize("times", [1, 3])
    def test_draw_returns_content(self, times):
        result = self.deck.draw(times, [self.deck], self.loc)
        assert result.count("卡牌") == times
        for item_content in ["卡牌A", "卡牌B", "卡牌C"]:
            if item_content in result:
                break
        else:
            pytest.fail("No item content found in draw result")

    def test_draw_no_redraw_exhausts_deck(self):
        """不放回抽取：同一次 draw(times=2) 调用中第二次应触发空牌库提示"""
        deck = Deck("不放回牌库", "/tmp")
        deck.add_item(DeckItem("唯一卡牌", redraw=False))
        loc = _make_loc_helper()
        result = deck.draw(2, [deck], loc)
        assert "唯一卡牌" in result
        from module.deck.deck_command import LOC_DRAW_ERR_EMPTY_DECK
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
        import random
        from unittest.mock import patch

        deck = Deck("内层终止牌库", "/tmp")
        deck.add_item(DeckItem("内层终止卡", final_type=1))
        deck.add_item(DeckItem("普通卡"))
        loc = _make_loc_helper()
        from module.deck.deck_command import LOC_DRAW_FIN_INNER
        loc.format_loc_text.side_effect = lambda key, **kwargs: (
            "提前结束内层" if key == LOC_DRAW_FIN_INNER else kwargs.get("content", kwargs.get("result", ""))
        )
        with patch.object(random, "randint", return_value=1):
            result = deck.draw(2, [deck], loc)
        assert "提前结束内层" in result

    def test_draw_exhausted_multi_items(self):
        """所有条目不放回时抽完应触发空牌库提示."""
        deck = Deck("空牌库测试", "/tmp")
        deck.add_item(DeckItem("A", redraw=False))
        deck.add_item(DeckItem("B", redraw=False))
        loc = _make_loc_helper()
        result = deck.draw(3, [deck], loc)
        assert "A" in result
        assert "B" in result
        from module.deck.deck_command import LOC_DRAW_ERR_EMPTY_DECK
        loc.format_loc_text.assert_any_call(LOC_DRAW_ERR_EMPTY_DECK)

    def test_draw_empty_deck_no_items(self):
        """空牌库直接抽立即返回空牌库提示."""
        deck = Deck("空牌库", "/tmp")
        loc = _make_loc_helper()
        result = deck.draw(1, [deck], loc)
        from module.deck.deck_command import LOC_DRAW_ERR_EMPTY_DECK
        loc.format_loc_text.assert_any_call(LOC_DRAW_ERR_EMPTY_DECK)

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
