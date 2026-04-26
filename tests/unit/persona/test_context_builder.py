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


class TestContextBuilderSpeakerPrefix:
    """测试 _format_short_term 称呼前缀 (fix-persona-group-history-context)"""

    def _make_character(self):
        return Character(
            name="苏晓",
            description="一个温柔的AI伴侣",
        )

    def test_private_chat_speaker_prefix(self):
        """8.5: 私聊 formatting 使用 [你] 和 [我]"""
        char = self._make_character()
        builder = ContextBuilder(char)
        history = [
            {"role": "user", "content": "你好", "speaker_name": "你"},
            {"role": "assistant", "content": "你好呀", "speaker_name": "我"},
        ]
        text = builder._format_short_term(history)
        assert "[你] 你好" in text
        assert "[我] 你好呀" in text

    def test_group_chat_speaker_prefix(self):
        """8.5: 群聊 formatting 使用 [display_name]"""
        char = self._make_character()
        builder = ContextBuilder(char)
        history = [
            {"role": "user", "content": "大家好", "speaker_name": "小明"},
            {"role": "user", "content": "嗨", "speaker_name": "小红"},
            {"role": "assistant", "content": "你们好", "speaker_name": "我"},
        ]
        text = builder._format_short_term(history)
        assert "[小明] 大家好" in text
        assert "[小红] 嗨" in text
        assert "[我] 你们好" in text

    def test_group_chat_fallback_speaker_name(self):
        """8.5: 群聊 display_name 缺失时回退到 [群友]"""
        char = self._make_character()
        builder = ContextBuilder(char)
        history = [
            {"role": "user", "content": "test", "speaker_name": "群友"},
        ]
        text = builder._format_short_term(history)
        assert "[群友] test" in text

    def test_build_skips_truncate_when_disabled(self):
        """7.2: build 信任传入的 short_term_history，不做额外截断"""
        char = self._make_character()
        builder = ContextBuilder(char, max_short_term_chars=10)
        history = [
            {"role": "user", "content": "这是一段非常长的群聊消息内容", "speaker_name": "小明"},
            {"role": "assistant", "content": "回复", "speaker_name": "我"},
        ]
        messages = builder.build(
            short_term_history=history,
            current_message="新消息",
        )
        system_content = messages[0]["content"]
        # 传入的历史完整保留
        assert "这是一段非常长的群聊消息内容" in system_content

    def test_build_keeps_truncated_history(self):
        """传入已截断的历史，build 不做额外处理"""
        char = self._make_character()
        builder = ContextBuilder(char, max_short_term_chars=10)
        history = [
            {"role": "user", "content": "很长很长的私聊消息内容在这里", "speaker_name": "你"},
            {"role": "assistant", "content": "回复", "speaker_name": "我"},
        ]
        # 模拟 orchestrator 层预先截断
        truncated = builder._truncate_by_turns(history, max_chars=10)
        messages = builder.build(
            short_term_history=truncated,
            current_message="新消息",
        )
        system_content = messages[0]["content"]
        # 截断后的历史被正确格式化
        assert "[你]" in system_content or "[我]" in system_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
