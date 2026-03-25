"""
Task 5.1: 解析层综合测试
- CommandTextParser 一致性测试
- CompatMapper 映射测试
- CqExtractor 提取测试
- CommandContext 快照一致性测试（mock）
"""
import pytest
from core.command.parse_result import CommandParseResult, MentionInfo, MessageSegment, ParseIssue
from core.command.text_parser import CommandTextParser
from core.command.compat_mapper import CommandCompatMapper, CompatRule, apply_compat
from core.command.cq_extractor import extract_segments, extract_mentions, enrich_parse_result


# ---------------------------------------------------------------------------
# CommandParseResult 数据结构测试
# ---------------------------------------------------------------------------

class TestCommandParseResult:
    def test_empty_result(self):
        r = CommandParseResult(command_name="r")
        assert r.command_name == "r"
        assert r.args == []
        assert r.flags == set()
        assert r.tail_text == ""
        assert not r.has_errors
        assert not r.has_warnings

    def test_add_warning(self):
        r = CommandParseResult()
        r.add_warning("TEST_WARN", "test warning")
        assert r.has_warnings
        assert not r.has_errors
        assert r.issues[0].code == "TEST_WARN"
        assert r.issues[0].recoverable is True

    def test_add_error_fatal(self):
        r = CommandParseResult()
        r.add_error("TEST_ERR", "test error", recoverable=False)
        assert r.has_errors
        assert r.issues[0].recoverable is False

    def test_add_error_recoverable(self):
        r = CommandParseResult()
        r.add_error("TEST_ERR", "test error", recoverable=True)
        assert not r.has_errors  # recoverable error 不触发 has_errors
        assert r.has_warnings is False

    def test_first_arg(self):
        r = CommandParseResult(args=["d20", "reason"])
        assert r.first_arg() == "d20"
        assert r.first_arg("default") == "d20"

    def test_first_arg_empty(self):
        r = CommandParseResult()
        assert r.first_arg() == ""
        assert r.first_arg("fallback") == "fallback"

    def test_has_flag(self):
        r = CommandParseResult(flags={"h", "s"})
        assert r.has_flag("h")
        assert r.has_flag("s")
        assert not r.has_flag("a")

    def test_get_kwarg(self):
        r = CommandParseResult(kwargs={"reason": "攻击"})
        assert r.get_kwarg("reason") == "攻击"
        assert r.get_kwarg("missing") is None
        assert r.get_kwarg("missing", "default") == "default"


# ---------------------------------------------------------------------------
# CommandTextParser 解析测试
# ---------------------------------------------------------------------------

class TestCommandTextParser:
    def setup_method(self):
        self.roll_parser = CommandTextParser(
            command_prefix="r",
            private_flags={"h", "s", "a", "n"},
        )
        self.mode_parser = CommandTextParser(
            command_prefix="mode",
            strip_prefix_len=5,
        )
        self.help_parser = CommandTextParser(command_prefix="help")

    def test_simple_prefix_strip(self):
        r = self.roll_parser.parse(".rd20")
        assert r.command_name == "r"
        assert r.raw == "d20"
        assert not r.has_errors

    def test_private_flag_prefix(self):
        r = self.roll_parser.parse(".rhd20")
        assert "h" in r.flags
        assert "d20" in r.args

    def test_multiple_private_flags(self):
        r = self.roll_parser.parse(".rhsd20")
        assert "h" in r.flags
        assert "s" in r.flags
        assert "d20" in r.args

    def test_tail_text_split(self):
        r = self.roll_parser.parse(".rd20 攻击地精")
        assert "d20" in r.args
        assert r.tail_text == "攻击地精"

    def test_no_tail_text(self):
        r = self.roll_parser.parse(".rd20+4")
        assert r.tail_text == ""

    def test_empty_command(self):
        r = self.roll_parser.parse(".r")
        assert r.command_name == "r"
        assert r.raw == ""
        assert r.args == []

    def test_prefix_mismatch(self):
        r = self.roll_parser.parse(".mode test")
        assert r.has_errors
        assert any(i.code == "PREFIX_MISMATCH" for i in r.issues)

    def test_global_flag_quiet(self):
        r = self.help_parser.parse(".help -q")
        assert "q" in r.flags

    def test_global_flag_help_long(self):
        r = self.help_parser.parse(".help --help")
        assert "help" in r.flags

    def test_mode_parse(self):
        r = self.mode_parser.parse(".mode COC7")
        assert r.command_name == "mode"
        assert r.first_arg() == "COC7"

    def test_mode_empty(self):
        r = self.mode_parser.parse(".mode")
        assert r.raw == ""
        assert r.args == []

    def test_parse_body(self):
        r = self.roll_parser.parse_body("hd20 攻击", command_name="r")
        assert "h" in r.flags
        assert "d20" in r.args
        assert r.tail_text == "攻击"


# ---------------------------------------------------------------------------
# CqExtractor 提取测试
# ---------------------------------------------------------------------------

class TestCqExtractor:
    def test_plain_text(self):
        segs = extract_segments("d20+4 攻击")
        assert len(segs) == 1
        assert segs[0].seg_type == "text"
        assert segs[0].data["text"] == "d20+4 攻击"

    def test_at_extraction(self):
        segs = extract_segments("[CQ:at,qq=12345]攻击地精")
        assert len(segs) == 2
        assert segs[0].seg_type == "at"
        assert segs[0].data["user_id"] == "12345"
        assert segs[1].seg_type == "text"

    def test_mentions_from_segments(self):
        segs = extract_segments("[CQ:at,qq=11111,name=Alice][CQ:at,qq=22222,name=Bob]")
        mentions = extract_mentions(segs)
        assert len(mentions) == 2
        assert mentions[0].user_id == "11111"
        assert mentions[1].user_id == "22222"

    def test_image_extraction(self):
        segs = extract_segments("[CQ:image,url=http://example.com/img.png]")
        assert len(segs) == 1
        assert segs[0].seg_type == "image"
        assert segs[0].data["url"] == "http://example.com/img.png"

    def test_no_cq_code(self):
        segs = extract_segments("普通文本消息")
        assert len(segs) == 1
        assert segs[0].seg_type == "text"

    def test_empty_message(self):
        segs = extract_segments("")
        assert segs == []

    def test_enrich_parse_result(self):
        r = CommandParseResult(command_name="r", raw="d20")
        enrich_parse_result(r, "[CQ:at,qq=99999]d20")
        assert len(r.segments) == 2
        assert len(r.mentions) == 1
        assert r.mentions[0].user_id == "99999"


# ---------------------------------------------------------------------------
# CompatMapper 映射测试
# ---------------------------------------------------------------------------

class TestCompatMapper:
    def test_global_quiet_rule(self):
        """--quiet 应在 parser 词法层直接归一为规范 key 'q'（不经过 compat mapper）"""
        # --quiet 的映射由 GLOBAL_FLAG_TABLE long 别名在 CommandTextParser 中处理，
        # compat mapper 不再保留冗余的 _rule_long_quiet 规则。
        from core.command.text_parser import CommandTextParser
        parser = CommandTextParser(command_prefix="r")
        r = parser.parse(".r --quiet")
        assert "q" in r.flags
        assert "quiet" not in r.flags

    def test_private_rule_registration(self):
        """注册命令私有兼容规则并验证执行"""
        mapper = CommandCompatMapper.get_or_create("test_cmd_5x")

        def _rule(result: CommandParseResult) -> bool:
            if "old_flag" in result.flags:
                result.flags.discard("old_flag")
                result.flags.add("new_flag")
                return True
            return False

        mapper.register(CompatRule(
            description="old_flag → new_flag",
            apply=_rule,
        ))

        r = CommandParseResult(command_name="test_cmd_5x", flags={"old_flag"})
        apply_compat(r)
        assert "new_flag" in r.flags
        assert "old_flag" not in r.flags

    def test_no_rules_no_side_effect(self):
        """无规则时 apply_compat 不修改结果"""
        r = CommandParseResult(command_name="unknown_cmd_xyz", flags={"h"})
        apply_compat(r)
        assert "h" in r.flags  # 原 flag 不受影响
