"""
单元测试: ContextBuilder 世界书集成
"""

import pytest

from plugins.DicePP.module.persona.character.models import Character, CharacterBook, LoreEntry
from plugins.DicePP.module.persona.data.models import UserProfile
from plugins.DicePP.module.persona.memory.context_builder import ContextBuilder


class TestContextBuilderCharacterBook:
    """测试 ContextBuilder 中的世界书注入"""

    def _make_character(self, entries):
        return Character(
            name="苏晓",
            description="一个温柔的AI伴侣",
            character_book=CharacterBook(entries=entries),
        )

    def test_lore_injected_into_system_prompt(self):
        char = self._make_character([
            LoreEntry(keys=["墨墨"], content="苏晓的猫叫墨墨。"),
        ])
        builder = ContextBuilder(char, lore_token_budget=300)
        profile = UserProfile(user_id="u1", facts={"name": "小明"})
        messages = builder.build(
            short_term_history=[{"role": "user", "content": "墨墨今天吃了什么？"}],
            current_message="墨墨在睡觉",
            user_profile=profile,
        )
        system_content = messages[0]["content"]
        assert "【世界书】" in system_content
        assert "苏晓的猫叫墨墨。" in system_content
        assert system_content.index("【你对用户的了解") < system_content.index("【世界书】")

    def test_lore_position_before_diary(self):
        char = self._make_character([
            LoreEntry(keys=["加班"], content="出版社经常加班。"),
        ])
        builder = ContextBuilder(char, lore_token_budget=300)
        messages = builder.build(
            short_term_history=[{"role": "user", "content": "今天又在加班"}],
            current_message="好累啊",
            diary_context="今天写了日记",
        )
        system_content = messages[0]["content"]
        assert "【世界书】" in system_content
        assert "【今天发生的事】" in system_content
        assert system_content.index("【世界书】") < system_content.index("【今天发生的事】")

    def test_no_lore_when_no_match(self):
        char = self._make_character([
            LoreEntry(keys=["墨墨"], content="苏晓的猫叫墨墨。"),
        ])
        builder = ContextBuilder(char, lore_token_budget=300)
        messages = builder.build(
            short_term_history=[],
            current_message="今天天气不错",
        )
        system_content = messages[0]["content"]
        assert "【世界书】" not in system_content

    def test_lore_deduplicated(self):
        entry = LoreEntry(keys=["墨墨", "橘猫"], content="苏晓的猫叫墨墨。")
        char = self._make_character([entry])
        builder = ContextBuilder(char, lore_token_budget=300)
        messages = builder.build(
            short_term_history=[
                {"role": "user", "content": "墨墨好可爱"},
                {"role": "assistant", "content": "橘猫确实很可爱"},
            ],
            current_message="墨墨在睡觉",
        )
        system_content = messages[0]["content"]
        # 只出现一次
        assert system_content.count("苏晓的猫叫墨墨。") == 1

    def test_token_budget_truncation(self):
        char = self._make_character([
            LoreEntry(keys=["a"], content="x" * 100),   # cost = 25
            LoreEntry(keys=["b"], content="x" * 200),   # cost = 50, would exceed budget=30
        ])
        builder = ContextBuilder(char, lore_token_budget=30)
        messages = builder.build(
            short_term_history=[],
            current_message="a and b",
        )
        system_content = messages[0]["content"]
        assert "【世界书】" in system_content
        # 只有第一条能注入
        assert system_content.count("x" * 100) == 1
        assert "x" * 200 not in system_content

    def test_lore_format_as_bullets(self):
        char = self._make_character([
            LoreEntry(keys=["出版社"], content="出版社在中关村。"),
            LoreEntry(keys=["猫"], content="苏晓的猫叫墨墨。"),
        ])
        builder = ContextBuilder(char, lore_token_budget=300)
        messages = builder.build(
            short_term_history=[],
            current_message="出版社和猫",
        )
        system_content = messages[0]["content"]
        assert "【世界书】\n- 出版社在中关村。\n- 苏晓的猫叫墨墨。" in system_content

    def test_token_budget_respects_order_priority(self):
        """高 order 条目优先保留，即使它在列表中排在后面"""
        char = self._make_character([
            LoreEntry(keys=["a"], content="x" * 100, order=10),   # cost = 25, low priority
            LoreEntry(keys=["b"], content="y" * 20, order=200),   # cost = 5, high priority
        ])
        builder = ContextBuilder(char, lore_token_budget=10)
        messages = builder.build(
            short_term_history=[],
            current_message="a and b",
        )
        system_content = messages[0]["content"]
        assert "【世界书】" in system_content
        # 高优先级的短条目应被保留，低优先级的长条目应被截断
        assert "y" * 20 in system_content
        assert "x" * 100 not in system_content

    def test_budget_fits_all_entries(self):
        char = self._make_character([
            LoreEntry(keys=["a"], content="x" * 20),   # cost = 5
            LoreEntry(keys=["b"], content="x" * 40),   # cost = 10
            LoreEntry(keys=["c"], content="x" * 60),   # cost = 15
        ])
        builder = ContextBuilder(char, lore_token_budget=50)
        messages = builder.build(
            short_term_history=[],
            current_message="a b c",
        )
        system_content = messages[0]["content"]
        assert "x" * 20 in system_content
        assert "x" * 40 in system_content
        assert "x" * 60 in system_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
