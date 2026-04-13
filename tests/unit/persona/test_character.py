"""
单元测试: Persona 角色系统
"""

import pytest
import tempfile
import os


from plugins.DicePP.module.persona.character.models import (
    Character,
    CharacterBook,
    LoreEntry,
    PersonaExtensions,
)
from plugins.DicePP.module.persona.character.loader import CharacterLoader


class TestCharacter:
    """测试 Character 模型"""

    def test_basic_creation(self):
        """测试基本创建"""
        char = Character(name="测试角色")
        assert char.name == "测试角色"
        assert char.description == ""
        assert char.first_mes == ""

    def test_with_extensions(self):
        """测试带扩展的角色"""
        ext = PersonaExtensions(
            initial_relationship=50,
            warmth_labels=["陌生", "熟悉", "朋友", "好友", "挚友", "知己"]
        )
        char = Character(
            name="苏晓",
            description="一个温柔的AI伴侣",
            first_mes="你好呀~",
            extensions=ext
        )
        
        assert char.extensions.initial_relationship == 50
        assert char.get_warmth_labels()[0] == "陌生"

    def test_get_warmth_labels_default(self):
        """测试默认温暖度标签"""
        char = Character(name="测试")
        labels = char.get_warmth_labels()
        
        assert len(labels) == 6
        assert labels[0] == "厌倦"
        assert labels[5] == "亲密"

    def test_format_mes_example(self):
        """测试示例对话格式化"""
        char = Character(
            name="苏晓",
            mes_example="{{user}}: 你好\n{{char}}: 你好呀~"
        )
        
        formatted = char.format_mes_example("小明")
        assert "小明" in formatted
        assert "{{user}}" not in formatted
        assert "苏晓" in formatted


class TestPersonaExtensions:
    """测试 PersonaExtensions 事件时刻生成"""

    def test_generate_event_times_count(self):
        ext = PersonaExtensions(daily_events_count=5, event_jitter_minutes=0)
        times = ext.generate_event_times()
        assert len(times) == 5

    def test_generate_event_times_within_window(self):
        ext = PersonaExtensions(
            event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=30
        )
        times = ext.generate_event_times(count=5)
        assert all(8 * 60 <= t < 22 * 60 for t in times)

    def test_generate_event_times_sorted(self):
        ext = PersonaExtensions(event_jitter_minutes=60)
        times = ext.generate_event_times(count=6)
        assert times == sorted(times)

    def test_generate_event_times_no_jitter_even_spacing(self):
        ext = PersonaExtensions(
            daily_events_count=2,
            event_day_start_hour=8,
            event_day_end_hour=20,
            event_jitter_minutes=0,
        )
        times = ext.generate_event_times()
        assert len(times) == 2
        # window=720 min, interval=360 → bases at 8*60+180=660, 8*60+540=1020
        assert times[0] == 660
        assert times[1] == 1020

    def test_generate_event_times_custom_count(self):
        ext = PersonaExtensions(daily_events_count=5, event_jitter_minutes=0)
        times = ext.generate_event_times(count=3)
        assert len(times) == 3

    def test_generate_event_times_zero_count(self):
        ext = PersonaExtensions(daily_events_count=0)
        assert ext.generate_event_times() == []
        assert ext.generate_event_times(count=0) == []


class TestCharacterBook:
    """测试世界书"""

    def test_lore_entry(self):
        """测试 LoreEntry"""
        entry = LoreEntry(
            keys=["猫", "宠物"],
            content="用户养了一只橘猫叫咪咪",
            enabled=True
        )
        
        assert "猫" in entry.keys
        assert entry.content == "用户养了一只橘猫叫咪咪"

    def test_character_book(self):
        """测试 CharacterBook"""
        book = CharacterBook(entries=[
            LoreEntry(keys=["猫"], content="有只橘猫"),
            LoreEntry(keys=["工作"], content="程序员"),
        ])

        assert len(book.entries) == 2


class TestSearchLoreEntries:
    """测试 Character.search_lore_entries"""

    def test_direct_match(self):
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(keys=["墨墨", "橘猫"], content="苏晓的猫叫墨墨。"),
            ])
        )
        matched = char.search_lore_entries(["我今天看到了墨墨"])
        assert len(matched) == 1
        assert matched[0].content == "苏晓的猫叫墨墨。"

    def test_no_match(self):
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(keys=["墨墨"], content="苏晓的猫叫墨墨。"),
            ])
        )
        matched = char.search_lore_entries(["今天天气不错"])
        assert matched == []

    def test_selective_match(self):
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(
                    keys=["出版社"],
                    secondary_keys=["加班", "截稿"],
                    selective=True,
                    content="出版社在中关村。"
                ),
            ])
        )
        matched = char.search_lore_entries(["出版社又在加班了"])
        assert len(matched) == 1

    def test_selective_missing_secondary(self):
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(
                    keys=["出版社"],
                    secondary_keys=["加班", "截稿"],
                    selective=True,
                    content="出版社在中关村。"
                ),
            ])
        )
        matched = char.search_lore_entries(["我去出版社了"])
        assert matched == []

    def test_disabled_entry_ignored(self):
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(keys=["墨墨"], content="有只猫", enabled=False),
            ])
        )
        matched = char.search_lore_entries(["墨墨"])
        assert matched == []

    def test_multiple_keys_same_entry_dedup_not_applied_here(self):
        """search_lore_entries 扫描拼接后的文本，每个 entry 只会命中一次，无需额外去重"""
        entry = LoreEntry(keys=["墨墨", "橘猫"], content="苏晓的猫叫墨墨。")
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[entry]),
        )
        matched = char.search_lore_entries(["墨墨和橘猫都在"])
        assert len(matched) == 1

    def test_without_character_book(self):
        char = Character(name="测试")
        assert char.search_lore_entries(["任意文本"]) == []

    def test_exact_match_avoids_english_substring_false_positive(self):
        """exact_match=True 时，英文 key 不应在更长单词中误触发"""
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(keys=["cat"], exact_match=True, content="有一只猫。"),
            ])
        )
        # "cat" 在 "category" 中是子串，不应命中
        assert char.search_lore_entries(["this is a category"]) == []
        # 独立单词应命中
        matched = char.search_lore_entries(["I have a cat"])
        assert len(matched) == 1

    def test_exact_match_chinese_still_works(self):
        """exact_match 对中文按常规子串匹配处理，前后不要求非中文字符"""
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(keys=["加班"], exact_match=True, content="经常加班。"),
            ])
        )
        matched = char.search_lore_entries(["今天又在加班，好累"])
        assert len(matched) == 1

    def test_min_match_length_filters_short_keys(self):
        """min_match_length 可过滤过短的 key，减少误触"""
        char = Character(
            name="测试",
            character_book=CharacterBook(entries=[
                LoreEntry(keys=["猫"], min_match_length=2, content="有只猫。"),
            ])
        )
        # "猫" 长度 1，小于 min_match_length=2，不应命中
        assert char.search_lore_entries(["这里有猫"]) == []


class TestCharacterLoader:
    """测试角色卡加载器"""

    def test_load_from_yaml(self):
        """测试从 YAML 加载"""
        yaml_content = """
name: 测试角色
description: 这是一个测试角色
personality: 温柔、体贴
first_mes: 你好呀~
mes_example: |
  <START>
  {{user}}: 你好
  {{char}}: 你好呀~
extensions:
  persona:
    initial_relationship: 40
    warmth_labels:
      - 陌生
      - 熟悉
      - 朋友
      - 好友
      - 挚友
      - 知己
"""
        
        with tempfile.TemporaryDirectory() as tmpdir:
            char_file = os.path.join(tmpdir, "test_char.yaml")
            with open(char_file, "w", encoding="utf-8") as f:
                f.write(yaml_content)
            
            loader = CharacterLoader(tmpdir)
            char = loader.load("test_char")
            
            assert char is not None
            assert char.name == "测试角色"
            assert char.extensions.initial_relationship == 40

    def test_load_nonexistent(self):
        """测试加载不存在的角色"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = CharacterLoader(tmpdir)
            char = loader.load("nonexistent")
            
            assert char is None

    def test_load_default_character(self):
        """测试加载默认角色卡"""
        loader = CharacterLoader("content/characters")
        char = loader.load("default")
        
        if os.path.exists("content/characters/default.yaml"):
            assert char is not None
            assert char.name is not None
        else:
            pytest.skip("默认角色卡不存在")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
