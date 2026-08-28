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

    def test_with_extensions(self):
        """测试带扩展的角色"""
        ext = PersonaExtensions(world="现代都市")
        char = Character(
            name="苏晓",
            description="一个温柔的AI伴侣",
            extensions=ext
        )

        assert char.extensions.world == "现代都市"

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

        default_formatted = char.format_mes_example()
        assert "玩家: 你好" in default_formatted


class TestPersonaExtensions:
    """测试 PersonaExtensions 事件时刻生成"""

    @pytest.mark.parametrize("count_param,expected", [(None, 5), (3, 3)])
    def test_generate_event_times_count(self, count_param, expected):
        ext = PersonaExtensions(daily_events_count=5, event_jitter_minutes=0)
        kwargs = {} if count_param is None else {"count": count_param}
        times = ext.generate_event_times(**kwargs)
        assert len(times) == expected

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

    def test_generate_event_times_zero_count(self):
        ext = PersonaExtensions(daily_events_count=0)
        assert ext.generate_event_times() == []
        assert ext.generate_event_times(count=0) == []

    @pytest.mark.parametrize("hour", [0, 1, 23, 24, 46, 47])
    def test_check_hour_range_valid(self, hour):
        ext = PersonaExtensions(event_day_start_hour=hour)
        assert ext.event_day_start_hour == hour

    @pytest.mark.parametrize("hour", [-1, 48, 100])
    def test_check_hour_range_start_invalid(self, hour):
        with pytest.raises(ValueError, match="event hour must be 0-47"):
            PersonaExtensions(event_day_start_hour=hour)

    @pytest.mark.parametrize("hour", [-1, 48, 100])
    def test_check_hour_range_end_invalid(self, hour):
        with pytest.raises(ValueError, match="event hour must be 0-47"):
            PersonaExtensions(event_day_end_hour=hour)


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
mes_example: |
  <START>
  {{user}}: 你好
  {{char}}: 你好呀~
extensions:
  persona:
    {}
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            char_dir = os.path.join(tmpdir, "test_char")
            os.makedirs(char_dir, exist_ok=True)
            char_file = os.path.join(char_dir, "character.yaml")
            with open(char_file, "w", encoding="utf-8") as f:
                f.write(yaml_content)

            loader = CharacterLoader(tmpdir)
            char = loader.load("test_char")

            assert char.name == "测试角色"

    def test_load_nonexistent(self):
        """测试加载不存在的角色"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = CharacterLoader(tmpdir)
            char = loader.load("nonexistent")
            
            assert char is None

    def test_load_default_character(self):
        """测试从临时目录加载角色卡"""
        yaml_content = "name: 默认角色\ndescription: 默认描述\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            char_dir = os.path.join(tmpdir, "default")
            os.makedirs(char_dir, exist_ok=True)
            with open(os.path.join(char_dir, "character.yaml"), "w", encoding="utf-8") as f:
                f.write(yaml_content)
            loader = CharacterLoader(tmpdir)
            char = loader.load("default")
            assert char.name == "默认角色"
            assert char.description == "默认描述"

    def test_list_characters(self):
        """测试列出所有角色"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["alice", "bob"]:
                char_dir = os.path.join(tmpdir, name)
                os.makedirs(char_dir, exist_ok=True)
                open(os.path.join(char_dir, "character.yaml"), "w").close()

            loader = CharacterLoader(tmpdir)
            chars = loader.list_characters()
            assert chars == ["alice", "bob"]

    def test_load_all_extensions_fields(self):
        """测试 PersonaExtensions 生活模拟与图片字段从 YAML 正确加载"""
        yaml_content = """
name: 全字段角色
extensions:
  persona:
    world: 现代都市
    daily_events_count: 8
    event_day_start_hour: 7
    event_day_end_hour: 23
    event_jitter_minutes: 45
    event_day_start_jitter_minutes: 15
    event_day_end_jitter_minutes: 20
    sleep_messages:
      - zzz
    image_gen_style: 水彩画风
    image_gen_appearance: 黑发、高挑、戴眼镜
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            char_dir = os.path.join(tmpdir, "full_fields")
            os.makedirs(char_dir, exist_ok=True)
            char_file = os.path.join(char_dir, "character.yaml")
            with open(char_file, "w", encoding="utf-8") as f:
                f.write(yaml_content)

            loader = CharacterLoader(tmpdir)
            char = loader.load("full_fields")

            assert char.name == "全字段角色"
            ext = char.extensions
            assert ext.world == "现代都市"
            assert ext.daily_events_count == 8
            assert ext.event_day_start_hour == 7
            assert ext.event_day_end_hour == 23
            assert ext.event_jitter_minutes == 45
            assert ext.event_day_start_jitter_minutes == 15
            assert ext.event_day_end_jitter_minutes == 20
            assert ext.sleep_messages == ["zzz"]
            assert ext.image_gen_style == "水彩画风"
            assert ext.image_gen_appearance == "黑发、高挑、戴眼镜"

    def test_list_characters_skips_invalid_dirs(self):
        """测试跳过无 character.yaml 的目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            char_dir = os.path.join(tmpdir, "valid")
            os.makedirs(char_dir, exist_ok=True)
            open(os.path.join(char_dir, "character.yaml"), "w").close()

            empty_dir = os.path.join(tmpdir, "no_character_yaml")
            os.makedirs(empty_dir, exist_ok=True)

            loader = CharacterLoader(tmpdir)
            chars = loader.list_characters()
            assert chars == ["valid"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
