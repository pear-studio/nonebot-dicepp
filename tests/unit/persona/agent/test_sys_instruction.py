"""测试 sys_instruction.py — 系统指令注入协议模块"""
import pytest

from plugins.DicePP.module.persona.agent.sys_instruction import (
    SYS_INSTRUCTION_PREFIX,
    SYS_INSTRUCTION_NOTICE,
    make_sys_msg,
    inject_sys_notice,
)


# ── make_sys_msg ────────────────────────────────────────

class TestMakeSysMsg:
    def test_format(self):
        """role="user"，content 以 [系统指令] 开头"""
        msg = make_sys_msg("请使用工具")
        assert msg["role"] == "user"
        assert msg["content"].startswith(SYS_INSTRUCTION_PREFIX)
        assert "请使用工具" in msg["content"]

    def test_content_embedded(self):
        """传入文本正确嵌入 content"""
        msg = make_sys_msg("你不要直接回复文本")
        assert msg["content"] == f"{SYS_INSTRUCTION_PREFIX} 你不要直接回复文本"


# ── inject_sys_notice ───────────────────────────────────

class TestInjectSysNotice:
    def test_adds_notice_to_system(self):
        """含 system 首消息且无说明时，追加 SYS_INSTRUCTION_NOTICE"""
        original = "你是角色扮演助手。"
        messages = [{"role": "system", "content": original}]
        inject_sys_notice(messages)
        assert SYS_INSTRUCTION_NOTICE in messages[0]["content"]
        assert original in messages[0]["content"]
        # 返回 None（原地修改）
        assert inject_sys_notice(messages) is None

    def test_idempotent(self):
        """已含 【系统消息说明】 时不重复追加"""
        messages = [{"role": "system", "content": f"基础提示\n\n{SYS_INSTRUCTION_NOTICE}"}]
        content_before = messages[0]["content"]
        inject_sys_notice(messages)
        assert messages[0]["content"] == content_before

    def test_empty_messages(self):
        """空列表不报错"""
        messages = []
        inject_sys_notice(messages)  # 不应抛异常
        assert messages == []

    def test_non_system_first(self):
        """首消息非 system 角色时跳过"""
        messages = [{"role": "user", "content": "你好"}]
        original = messages[0]["content"]
        inject_sys_notice(messages)
        assert messages[0]["content"] == original
        assert SYS_INSTRUCTION_NOTICE not in messages[0]["content"]

    def test_list_content(self):
        """content 为 list（多模态）时跳过"""
        messages = [{"role": "system", "content": [{"type": "text", "text": "system prompt"}]}]
        original = list(messages[0]["content"])
        inject_sys_notice(messages)
        assert messages[0]["content"] == original

    def test_preserves_other_content(self):
        """注入后不覆盖原有 system prompt 内容"""
        original = "你是一个角色扮演助手。请保持角色一致。"
        messages = [{"role": "system", "content": original}]
        inject_sys_notice(messages)
        assert messages[0]["content"].startswith(original)
        assert SYS_INSTRUCTION_NOTICE in messages[0]["content"]
