"""
单元测试: ContextBuilder 世界书集成 / 消息组装 / Debug / 分段引导 / 配置迁移
"""

import pytest
from datetime import datetime, timedelta
from plugins.DicePP.utils.time import wall_now

from plugins.DicePP.module.persona.character.models import Character, CharacterBook, LoreEntry
from plugins.DicePP.module.persona.data.models import UserProfile
from plugins.DicePP.module.persona.chat.context import ContextBuilder

# 今天日期，用于构造同日时间戳（format_timestamp 同日返回 HH:MM）
_TODAY = wall_now().replace(hour=14, minute=0, second=0, microsecond=0)


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
        assert "send_reply" in system
        assert "80" in system
        assert "120" in system
        assert "delay_before" not in system

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
        assert "100" in system
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
# proactive prompt 输出协议
# ═══════════════════════════════════════════════════════════════════

class TestContextBuilderProactivePrompt:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_proactive_prompt_contains_send_reply_protocol(self):
        """proactive prompt 包含 send_reply 调用协议"""
        char = self._make_character()
        builder = ContextBuilder(char, segment_guide=None)
        prompt = builder.build_static_prompt_proactive()
        assert "send_reply" in prompt
        assert "不要直接输出文本" in prompt

    def test_proactive_prompt_excludes_segment_params(self):
        """proactive prompt 不含分段参数（send_reply_segment / 单段上限 / 总字数硬上限）"""
        char = self._make_character()
        builder = ContextBuilder(char, segment_guide=None)
        prompt = builder.build_static_prompt_proactive()
        assert "send_reply_segment" not in prompt
        assert "单段上限" not in prompt
        assert "总字数硬上限" not in prompt


# ═══════════════════════════════════════════════════════════════════
# 6.6 配置迁移兼容性
# ═══════════════════════════════════════════════════════════════════

class TestConfigMigration:

    def test_new_fields_assembled_correctly(self):
        """ChatConfig.from_persona() 装配新字段正确"""
        from plugins.DicePP.core.config.pydantic_models import PersonaConfig
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
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
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        pc = PersonaConfig(
            max_diary_context_chars=300,
        )
        cc = ChatConfig.from_persona(pc)
        assert cc.max_diary_context_chars == 300


# ═══════════════════════════════════════════════════════════════════
# 6.1 私聊格式化 _format_private_history
# ═══════════════════════════════════════════════════════════════════

class TestFormatPrivateHistory:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_role_direct_mapping(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "你好", "created_at": _dt(30)},
            {"role": "assistant", "content": "你好呀", "created_at": _dt(31)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_timestamp_prefix(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.chat.context.wall_now",
            lambda tz: _TODAY,
        )
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "你好", "created_at": _dt(30)},
        ]
        result = builder._format_private_history(history)
        assert result[0]["content"].startswith("[14:30] ")

    def test_empty_list(self):
        builder = ContextBuilder(self._make_character())
        result = builder._format_private_history([])
        assert result == []

    def test_merge_consecutive_user_messages(self):
        """连续 user 消息合并为单条，保证 user/assistant 交替"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "消息A", "created_at": _dt(30)},
            {"role": "user", "content": "消息B", "created_at": _dt(31)},
            {"role": "assistant", "content": "回复", "created_at": _dt(32)},
            {"role": "user", "content": "消息C", "created_at": _dt(33)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert "消息A" in result[0]["content"]
        assert "消息B" in result[0]["content"]
        assert "\n" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert "消息C" in result[2]["content"]

    def test_merge_all_user_messages(self):
        """全部为 user 消息时合并为单条"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "A", "created_at": _dt(30)},
            {"role": "user", "content": "B", "created_at": _dt(31)},
            {"role": "user", "content": "C", "created_at": _dt(32)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "A" in result[0]["content"]
        assert "B" in result[0]["content"]
        assert "C" in result[0]["content"]

    def test_all_assistant_messages(self):
        """纯 assistant 消息不触发 buffer flush，逐条输出"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "assistant", "content": "回复1", "created_at": _dt(30)},
            {"role": "assistant", "content": "回复2", "created_at": _dt(31)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "assistant"

    def test_nonstandard_role_treated_as_user(self):
        """非 assistant 的 role（如 system）被缓冲为 user"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "system", "content": "系统消息", "created_at": _dt(30)},
            {"role": "user", "content": "用户消息", "created_at": _dt(31)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "系统消息" in result[0]["content"]
        assert "用户消息" in result[0]["content"]

    def test_three_consecutive_users_then_assistant(self):
        """3 条连续 user 后接 assistant，user 全部合并"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "A", "created_at": _dt(30)},
            {"role": "user", "content": "B", "created_at": _dt(31)},
            {"role": "user", "content": "C", "created_at": _dt(32)},
            {"role": "assistant", "content": "回复", "created_at": _dt(33)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert "A" in result[0]["content"]
        assert "B" in result[0]["content"]
        assert "C" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert "回复" in result[1]["content"]


# ═══════════════════════════════════════════════════════════════════
# 6.2 群聊格式化 _format_group_history
# ═══════════════════════════════════════════════════════════════════

class TestFormatGroupHistory:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_merge_consecutive_non_assistant(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "你好", "speaker_name": "A",
             "created_at": _dt(30)},
            {"role": "user", "content": "嗨", "speaker_name": "B",
             "created_at": _dt(31)},
            {"role": "assistant", "content": "你们好呀",
             "created_at": _dt(31)},
        ]
        result = builder._format_group_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert "[A] 你好" in result[0]["content"]
        assert "[B] 嗨" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert "你们好呀" in result[1]["content"]

    def test_mixed_speakers_order(self):
        """A, B, assistant, A → merged(A+B), assistant, user(A)"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "msg1", "speaker_name": "A",
             "created_at": _dt(30)},
            {"role": "user", "content": "msg2", "speaker_name": "B",
             "created_at": _dt(31)},
            {"role": "assistant", "content": "reply",
             "created_at": _dt(32)},
            {"role": "user", "content": "msg3", "speaker_name": "A",
             "created_at": _dt(33)},
        ]
        result = builder._format_group_history(history)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert "msg1" in result[0]["content"] and "msg2" in result[0]["content"]
        assert "reply" in result[1]["content"]
        assert "msg3" in result[2]["content"]

    def test_consecutive_assistants_not_merged(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "assistant", "content": "reply1",
             "created_at": _dt(30)},
            {"role": "assistant", "content": "reply2",
             "created_at": _dt(31)},
        ]
        result = builder._format_group_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "assistant"

    def test_speaker_name_fallback_to_system(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "test",
             "created_at": _dt(30)},
        ]
        result = builder._format_group_history(history)
        assert "[系统] test" in result[0]["content"]

    def test_empty_list(self):
        builder = ContextBuilder(self._make_character())
        result = builder._format_group_history([])
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# 6.3 轮次 + token 双重截断 truncate_by_turns
# ═══════════════════════════════════════════════════════════════════

class TestTruncateByTurns:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_truncate_by_max_turns(self):
        """12 轮，max_turns=10 → 保留最近 10 轮"""
        builder = ContextBuilder(self._make_character())
        history = []
        for i in range(12):
            history.append({"role": "user", "content": f"u{i}"})
            history.append({"role": "assistant", "content": f"a{i}"})
        result = builder.truncate_by_turns(history, max_turns=10, max_tokens=10000)
        assert len(result) == 20
        assert result[0]["content"] == "u2"
        assert result[-1]["content"] == "a11"

    def test_truncate_by_token_budget(self):
        """token 预算超过时触发兜底"""
        builder = ContextBuilder(self._make_character())
        history = []
        for i in range(8):
            history.append({"role": "user", "content": "x" * 100})
            history.append({"role": "assistant", "content": "y" * 100})
        result = builder.truncate_by_turns(history, max_turns=10, max_tokens=200)
        assert len(result) >= 2
        assert len(result) <= 16

    def test_both_limits_token_wins(self):
        """token 早于 turns 触发截断"""
        builder = ContextBuilder(self._make_character())
        history = []
        for i in range(12):
            history.append({"role": "user", "content": "x" * 100})
            history.append({"role": "assistant", "content": "y" * 100})
        result = builder.truncate_by_turns(history, max_turns=10, max_tokens=150)
        assert len(result) < 20

    def test_full_turns_preserved(self):
        """不拆散 user/assistant 对"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = builder.truncate_by_turns(history, max_turns=1, max_tokens=10000)
        assert len(result) == 2
        assert result[0]["role"] == "user" and result[0]["content"] == "u2"
        assert result[1]["role"] == "assistant" and result[1]["content"] == "a2"

    def test_orphan_user_preserved(self):
        """末尾孤立 user 保留"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        result = builder.truncate_by_turns(history, max_turns=2, max_tokens=10000)
        assert result[-1]["role"] == "user"
        assert result[-1]["content"] == "u2"

    def test_empty_history(self):
        builder = ContextBuilder(self._make_character())
        result = builder.truncate_by_turns([], max_turns=10, max_tokens=4000)
        assert result == []

    def test_leading_assistant_stripped_and_preserved(self):
        """R1: 历史以 assistant 开头时剥离并在截断后 prepend"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "assistant", "content": "leading_a"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        result = builder.truncate_by_turns(history, max_turns=2, max_tokens=10000)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "leading_a"
        assert len(result) == 3

    def test_trailing_orphan_assistant_preserved(self):
        """R1: work 长度奇数时末尾孤立 assistant 被兜底保留"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "assistant", "content": "orphan_a"},
        ]
        result = builder.truncate_by_turns(history, max_turns=5, max_tokens=10000)
        assert result[-1]["role"] == "assistant"
        assert result[-1]["content"] == "orphan_a"


# ═══════════════════════════════════════════════════════════════════
# format_history 统一入口
# ═══════════════════════════════════════════════════════════════════

class TestFormatHistory:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_is_group_false_dispatches_private(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.chat.context.wall_now",
            lambda tz: _TODAY,
        )
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "hi", "created_at": _dt(30)},
        ]
        result = builder.format_history(history, is_group=False)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"].startswith("[14:30] ")

    def test_is_group_true_dispatches_group(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "hi", "speaker_name": "A",
             "created_at": _dt(30)},
        ]
        result = builder.format_history(history, is_group=True)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "[A]" in result[0]["content"]


# ═══════════════════════════════════════════════════════════════════
# merge_same_run_segments
# ═══════════════════════════════════════════════════════════════════

class TestMergeSameRunSegments:
    """merge_same_run_segments — 聚合同 run assistant 消息"""

    def test_no_agent_run_id_passthrough(self):
        """没有 agent_run_id 的 entry 原样输出"""
        formatted = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert result == formatted

    def test_same_run_merged(self):
        """同 run 的连续 assistant 段合并"""
        formatted = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "part1", "agent_run_id": "run_1"},
            {"role": "assistant", "content": "part2", "agent_run_id": "run_1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 2  # user + merged assistant
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "part1\npart2"
        assert "agent_run_id" not in result[1]

    def test_three_segments_merged(self):
        """三个同 run 段合并为一条"""
        formatted = [
            {"role": "assistant", "content": "a", "agent_run_id": "r1"},
            {"role": "assistant", "content": "b", "agent_run_id": "r1"},
            {"role": "assistant", "content": "c", "agent_run_id": "r1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 1
        assert result[0]["content"] == "a\nb\nc"

    def test_different_runs_not_merged(self):
        """不同 run_id 不合并"""
        formatted = [
            {"role": "assistant", "content": "reply1", "agent_run_id": "run_1"},
            {"role": "assistant", "content": "reply2", "agent_run_id": "run_2"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 2
        assert result[0]["content"] == "reply1"
        assert result[1]["content"] == "reply2"

    def test_user_message_breaks_merge(self):
        """user 消息插入在两个同 run assistant 之间 → 不合并"""
        formatted = [
            {"role": "assistant", "content": "reply1", "agent_run_id": "run_1"},
            {"role": "user", "content": "interrupt"},
            {"role": "assistant", "content": "reply2", "agent_run_id": "run_1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 3
        assert result[0]["content"] == "reply1"
        assert result[1]["content"] == "interrupt"
        assert result[2]["content"] == "reply2"

    def test_mixed_run_and_no_run_id(self):
        """带 run_id 和不带 run_id 的 assistant 消息相邻 → 不合并"""
        formatted = [
            {"role": "assistant", "content": "old", "agent_run_id": "run_1"},
            {"role": "assistant", "content": "legacy"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 2

    def test_empty_input(self):
        assert ContextBuilder.merge_same_run_segments([]) == []

    def test_single_assistant_with_run_id(self):
        """单条 assistant + run_id → 原样输出（没有多条不需要合并）"""
        formatted = [
            {"role": "assistant", "content": "only", "agent_run_id": "r1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 1
        assert result[0]["content"] == "only"

    def test_interleaved_runs(self):
        """交替不同 run 的 assistant 不合并"""
        formatted = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "agent_run_id": "r1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2", "agent_run_id": "r2"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 4
        assert result[1]["content"] == "a1"
        assert result[3]["content"] == "a2"

    def test_no_index_no_timestamp(self):
        """合并后的消息不应包含 segment_index/timestamp 等字段"""
        formatted = [
            {"role": "assistant", "content": "a", "agent_run_id": "r1", "segment_index": 0},
            {"role": "assistant", "content": "b", "agent_run_id": "r1", "segment_index": 1},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert "agent_run_id" not in result[0]
        assert "segment_index" not in result[0]

    def test_only_user_messages(self):
        """只有 user 消息 → 原样输出"""
        formatted = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "again"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert result == formatted


# ═══════════════════════════════════════════════════════════════════
# Q86: _build_image_markers 图片标记构建
# ═══════════════════════════════════════════════════════════════════

class TestBuildImageMarkers:
    """_build_image_markers 图片标记构建测试"""

    def test_normal_image_marker(self):
        from plugins.DicePP.module.persona.chat.context import _build_image_markers

        msg = {"image_meta": [{"image_hash": "abc123", "sub_type": "0"}]}
        result = _build_image_markers(msg)
        assert result == "[图片 abc123] "

    def test_emoticon_marker(self):
        from plugins.DicePP.module.persona.chat.context import _build_image_markers

        msg = {"image_meta": [{"image_hash": "def456", "sub_type": "1"}]}
        result = _build_image_markers(msg)
        assert result == "[表情 def456] "

    def test_default_subtype_is_image(self):
        from plugins.DicePP.module.persona.chat.context import _build_image_markers

        msg = {"image_meta": [{"image_hash": "xyz789"}]}
        result = _build_image_markers(msg)
        assert result == "[图片 xyz789] "

    def test_empty_image_meta_returns_empty(self):
        from plugins.DicePP.module.persona.chat.context import _build_image_markers

        assert _build_image_markers({}) == ""
        assert _build_image_markers({"image_meta": None}) == ""
        assert _build_image_markers({"image_meta": []}) == ""

    def test_missing_hash_falls_back_to_compute_image_hash(self, monkeypatch):
        from plugins.DicePP.module.persona.chat.context import _build_image_markers
        from plugins.DicePP.module.persona.image_cache import ImageCache

        monkeypatch.setattr(
            ImageCache, "compute_image_hash",
            lambda entry: "computed_hash",
        )
        msg = {"image_meta": [{"url": "http://example.com/img.png"}]}
        result = _build_image_markers(msg)
        assert result == "[图片 computed_hash] "

    def test_missing_hash_and_no_url_skips_entry(self, monkeypatch):
        from plugins.DicePP.module.persona.chat.context import _build_image_markers
        from plugins.DicePP.module.persona.image_cache import ImageCache

        monkeypatch.setattr(
            ImageCache, "compute_image_hash",
            lambda entry: None,
        )
        msg = {"image_meta": [{"unknown_field": "foo"}]}
        result = _build_image_markers(msg)
        # 空 markers 时返回 " "（空列表 join + 尾随空格）
        assert result == " "

    def test_multiple_image_markers(self):
        from plugins.DicePP.module.persona.chat.context import _build_image_markers

        msg = {
            "image_meta": [
                {"image_hash": "h1", "sub_type": "0"},
                {"image_hash": "h2", "sub_type": "1"},
            ]
        }
        result = _build_image_markers(msg)
        # 标记之间无空格分隔，仅尾部有空格
        assert result == "[图片 h1][表情 h2] "


# ═══════════════════════════════════════════════════════════════════
# 阶段 2：身份锚定禁止约束
# ═══════════════════════════════════════════════════════════════════

class TestAntiAnchorConstraint:
    def test_static_prompt_contains_speaker_anti_anchor(self):
        char = Character(name="苏晓", description="温柔的伙伴")
        builder = ContextBuilder(char)
        prompt = builder.build_static_prompt()
        # 约束当前说话者以本轮 name 为准，禁止从历史名字误认
        assert "name" in prompt
        assert "当前说话者" in prompt
        assert "误认" in prompt
