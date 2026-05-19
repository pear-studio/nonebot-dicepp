"""
单元测试: ContextBuilder 世界书集成 / 消息组装 / Debug / 分段引导 / 配置迁移
"""

import pytest
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.character.models import Character, CharacterBook, LoreEntry
from plugins.DicePP.module.persona.data.models import UserProfile
from plugins.DicePP.module.persona.chat.context import ContextBuilder

# 今天日期，用于构造同日时间戳（format_timestamp 同日返回 HH:MM）
_TODAY = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)


def _dt(minute: int = 0) -> datetime:
    return _TODAY.replace(minute=minute)


# ═══════════════════════════════════════════════════════════════════
# 世界书集成
# ═══════════════════════════════════════════════════════════════════

class TestContextBuilderCharacterBook:

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
            formatted_history=[{"role": "user", "content": "墨墨今天吃了什么？"}],
            history_dicts=[
                {"role": "user", "content": "墨墨今天吃了什么？"},
                {"role": "user", "content": "墨墨在睡觉"},
            ],
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
            formatted_history=[{"role": "user", "content": "今天又在加班"}],
            history_dicts=[
                {"role": "user", "content": "今天又在加班"},
                {"role": "user", "content": "好累啊"},
            ],
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
            formatted_history=[],
            history_dicts=[{"role": "user", "content": "今天天气不错"}],
        )
        system_content = messages[0]["content"]
        assert "【世界书】" not in system_content

    def test_lore_deduplicated(self):
        entry = LoreEntry(keys=["墨墨", "橘猫"], content="苏晓的猫叫墨墨。")
        char = self._make_character([entry])
        builder = ContextBuilder(char, lore_token_budget=300)
        messages = builder.build(
            formatted_history=[
                {"role": "user", "content": "墨墨好可爱"},
                {"role": "assistant", "content": "橘猫确实很可爱"},
            ],
            history_dicts=[
                {"role": "user", "content": "墨墨好可爱"},
                {"role": "assistant", "content": "橘猫确实很可爱"},
                {"role": "user", "content": "墨墨在睡觉"},
            ],
        )
        system_content = messages[0]["content"]
        assert system_content.count("苏晓的猫叫墨墨。") == 1

    def test_token_budget_truncation(self):
        char = self._make_character([
            LoreEntry(keys=["a"], content="x" * 100),
            LoreEntry(keys=["b"], content="x" * 200),
        ])
        builder = ContextBuilder(char, lore_token_budget=30)
        messages = builder.build(
            formatted_history=[],
            history_dicts=[{"role": "user", "content": "a and b"}],
        )
        system_content = messages[0]["content"]
        assert "【世界书】" in system_content
        assert system_content.count("x" * 100) == 1
        assert "x" * 200 not in system_content

    def test_lore_format_as_bullets(self):
        char = self._make_character([
            LoreEntry(keys=["出版社"], content="出版社在中关村。"),
            LoreEntry(keys=["猫"], content="苏晓的猫叫墨墨。"),
        ])
        builder = ContextBuilder(char, lore_token_budget=300)
        messages = builder.build(
            formatted_history=[],
            history_dicts=[{"role": "user", "content": "出版社和猫"}],
        )
        system_content = messages[0]["content"]
        assert "【世界书】\n- 出版社在中关村。\n- 苏晓的猫叫墨墨。" in system_content

    def test_token_budget_respects_order_priority(self):
        char = self._make_character([
            LoreEntry(keys=["a"], content="x" * 100, order=10),
            LoreEntry(keys=["b"], content="y" * 20, order=200),
        ])
        builder = ContextBuilder(char, lore_token_budget=10)
        messages = builder.build(
            formatted_history=[],
            history_dicts=[{"role": "user", "content": "a and b"}],
        )
        system_content = messages[0]["content"]
        assert "【世界书】" in system_content
        assert "y" * 20 in system_content
        assert "x" * 100 not in system_content

    def test_budget_fits_all_entries(self):
        char = self._make_character([
            LoreEntry(keys=["a"], content="x" * 20),
            LoreEntry(keys=["b"], content="x" * 40),
            LoreEntry(keys=["c"], content="x" * 60),
        ])
        builder = ContextBuilder(char, lore_token_budget=50)
        messages = builder.build(
            formatted_history=[],
            history_dicts=[{"role": "user", "content": "a b c"}],
        )
        system_content = messages[0]["content"]
        assert "x" * 20 in system_content
        assert "x" * 40 in system_content
        assert "x" * 60 in system_content

    def test_lore_uses_history_dicts_for_scanning(self):
        """世界书扫描用 history_dicts（原始 content），不受格式化前缀干扰"""
        char = self._make_character([
            LoreEntry(keys=["墨墨"], content="苏晓的猫叫墨墨。"),
        ])
        builder = ContextBuilder(char, lore_token_budget=300)
        messages = builder.build(
            formatted_history=[{"role": "user", "content": "[14:30] 墨墨今天吃了什么？"}],
            history_dicts=[
                {"role": "user", "content": "墨墨今天吃了什么？"},
                {"role": "user", "content": "墨墨在睡觉"},
            ],
        )
        system_content = messages[0]["content"]
        assert "苏晓的猫叫墨墨。" in system_content



# ═══════════════════════════════════════════════════════════════════
# 6.4 build() 消息列表结构
# ═══════════════════════════════════════════════════════════════════

class TestBuildMessageStructure:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_system_message_first(self):
        builder = ContextBuilder(self._make_character())
        messages = builder.build(
            formatted_history=[{"role": "user", "content": "[14:00] 你好"}],
            history_dicts=[{"role": "user", "content": "你好"}],
        )
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "你好" in messages[1]["content"]

    def test_no_short_term_text_in_system(self):
        """system 消息中不含"近期对话"文本块"""
        builder = ContextBuilder(self._make_character())
        messages = builder.build(
            formatted_history=[
                {"role": "user", "content": "[14:30] 你好"},
                {"role": "assistant", "content": "[14:31] 你好呀"},
                {"role": "user", "content": "[14:32] 新消息"},
            ],
            history_dicts=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好呀"},
                {"role": "user", "content": "新消息"},
            ],
        )
        system_content = messages[0]["content"]
        assert "近期对话" not in system_content

    def test_formatted_history_appended_after_system(self):
        """格式化历史以独立消息对追加在 system 之后"""
        builder = ContextBuilder(self._make_character())
        messages = builder.build(
            formatted_history=[
                {"role": "user", "content": "[14:30] 你好"},
                {"role": "assistant", "content": "[14:31] 你好呀"},
                {"role": "user", "content": "[14:32] 新消息"},
            ],
            history_dicts=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好呀"},
                {"role": "user", "content": "新消息"},
            ],
        )
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "[14:30] 你好"}
        assert messages[2] == {"role": "assistant", "content": "[14:31] 你好呀"}
        assert messages[3] == {"role": "user", "content": "[14:32] 新消息"}

    def test_system_plus_single_history_entry(self):
        builder = ContextBuilder(self._make_character())
        messages = builder.build(
            formatted_history=[{"role": "user", "content": "[14:00] 你好"}],
            history_dicts=[{"role": "user", "content": "你好"}],
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"


# ═══════════════════════════════════════════════════════════════════
# R2: build_debug_info is_group 显式传入
# ═══════════════════════════════════════════════════════════════════

class TestBuildDebugInfo:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_short_term_chars_reflects_formatted_content(self):
        """传入已格式化的 truncated，short_term_chars 直接统计 content 长度"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "[14:30] hi"},
            {"role": "assistant", "content": "[14:31] hello"},
        ]
        info = builder.build_debug_info(short_term_history=history)
        assert info["short_term_chars"] == len("[14:30] hi") + len("[14:31] hello")

    def test_returned_message_count_includes_all_messages(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "[14:30] hi"},
        ]
        info = builder.build_debug_info(short_term_history=history)
        assert info["returned_message_count"] == 1 + len(history)  # 1 system + N history


# ═══════════════════════════════════════════════════════════════════
# 分段回复引导
# ═══════════════════════════════════════════════════════════════════

class TestContextBuilderSegmentGuide:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_segment_guide_injected_with_defaults(self):
        char = self._make_character()
        from plugins.DicePP.module.persona.chat.context import SegmentGuide
        builder = ContextBuilder(
            char,
            segment_guide=SegmentGuide(
                enabled=True, target_chars=30, max_chars=80, soft_limit=100, hard_limit=120
            ),
        )
        messages = builder.build(
            formatted_history=[{"role": "user", "content": "[14:00] hi"}],
            history_dicts=[{"role": "user", "content": "hi"}],
        )
        system = messages[0]["content"]
        assert "send_reply_segment" in system
        assert "30" in system
        assert "80" in system
        assert "100" in system
        assert "120" in system
        assert "delay_before" in system
        assert "【系统消息说明】" in system
        assert "[系统指令]" in system
        assert "不是用户输入" in system

    def test_segment_guide_reflects_custom_values(self):
        char = self._make_character()
        from plugins.DicePP.module.persona.chat.context import SegmentGuide
        builder = ContextBuilder(
            char,
            segment_guide=SegmentGuide(
                enabled=True, target_chars=50, max_chars=100, soft_limit=200, hard_limit=250
            ),
        )
        messages = builder.build(
            formatted_history=[{"role": "user", "content": "[14:00] hi"}],
            history_dicts=[{"role": "user", "content": "hi"}],
        )
        system = messages[0]["content"]
        assert "50" in system
        assert "100" in system
        assert "200" in system
        assert "250" in system

    def test_segment_guide_placed_after_character_info(self):
        char = self._make_character()
        from plugins.DicePP.module.persona.chat.context import SegmentGuide
        builder = ContextBuilder(
            char,
            segment_guide=SegmentGuide(
                enabled=True, target_chars=30, max_chars=80, soft_limit=100, hard_limit=120
            ),
        )
        messages = builder.build(
            formatted_history=[{"role": "user", "content": "[14:00] hi"}],
            history_dicts=[{"role": "user", "content": "hi"}],
        )
        system = messages[0]["content"]
        name_idx = system.index("苏晓")
        guide_idx = system.index("【回复规则】")
        remind_idx = system.index("请记住用户说过的话")
        assert name_idx < guide_idx < remind_idx

    def test_segment_guide_disabled_when_segment_enabled_false(self):
        char = self._make_character()
        builder = ContextBuilder(char, segment_guide=None)
        messages = builder.build(
            formatted_history=[{"role": "user", "content": "[14:00] hi"}],
            history_dicts=[{"role": "user", "content": "hi"}],
        )
        system = messages[0]["content"]
        assert "【回复规则】" not in system


# ═══════════════════════════════════════════════════════════════════
# 6.6 配置迁移兼容性
# ═══════════════════════════════════════════════════════════════════

class TestConfigMigration:

    def test_new_fields_assembled_correctly(self):
        """ChatConfig.from_persona() 装配新字段正确"""
        from plugins.DicePP.core.config.pydantic_models import PersonaConfig
        from plugins.DicePP.module.persona.chat.session import ChatConfig
        pc = PersonaConfig(
            max_history_turns=7,
            max_history_tokens=3000,
            max_diary_context_chars=600,
        )
        cc = ChatConfig.from_persona(pc)
        assert cc.max_history_turns == 7
        assert cc.max_history_tokens == 3000
        assert cc.max_diary_context_chars == 600


# ═══════════════════════════════════════════════════════════════════
# 6.7 Diary 截断独立配置
# ═══════════════════════════════════════════════════════════════════

class TestDiaryContextConfig:

    def test_diary_uses_max_diary_context_chars(self):
        """_build_diary_context 使用 max_diary_context_chars"""
        from plugins.DicePP.core.config.pydantic_models import PersonaConfig
        from plugins.DicePP.module.persona.chat.session import ChatConfig
        pc = PersonaConfig(
            max_diary_context_chars=300,
        )
        cc = ChatConfig.from_persona(pc)
        assert cc.max_diary_context_chars == 300


