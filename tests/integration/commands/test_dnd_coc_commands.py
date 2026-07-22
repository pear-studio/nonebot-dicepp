"""
misc 模块测试
- 集成测试：.jrrp / .dnd / .coc 指令行为
"""
import re
import os

import pytest
import pytest_asyncio


# ─────────────────────────── 集成测试辅助 ───────────────────────────

async def _send_group(bot, msg: str, user_id: str = "user1", group_id: str = "group1"):
    from core.communication import MessageMetaData, MessageSender
    meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), group_id, False)
    return await bot.process_message(msg, meta)


def _cleanup_bot(bot):
    """清理测试 Bot 产生的数据目录"""
    test_path = bot.data_path
    if os.path.exists(test_path):
        for root, dirs, files in os.walk(test_path, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(test_path)


@pytest_asyncio.fixture
async def dnd_bot():
    from core.bot import Bot
    bot = Bot("test_dnd_misc_bot")
    bot.config.master = ["test_master"]
    await bot.delay_init_command()
    yield bot
    await bot.shutdown_async()
    _cleanup_bot(bot)


@pytest_asyncio.fixture
async def coc_bot():
    from core.bot import Bot
    bot = Bot("test_coc_misc_bot")
    bot.config.master = ["test_master"]
    await bot.delay_init_command()
    yield bot
    await bot.shutdown_async()
    _cleanup_bot(bot)


# ─────────────────────────── DND 集成测试 ───────────────────────────

class TestDndCommandIntegration:
    """UtilsDNDCommand (.dnd) 集成测试"""

    async def test_dnd_contains_stats(self, dnd_bot):
        """dnd 属性生成结果应包含 DND 关键词和 6 项属性数值"""
        cmds = await _send_group(dnd_bot, ".dnd")
        result = "\n".join([str(c) for c in cmds])
        assert "DND人物作成" in result, f".dnd 应包含 DND人物作成，实际输出：{result}"
        numbers = re.findall(r'\d+', result)
        assert len(numbers) >= 6, f".dnd 应生成 6 项属性值，实际输出：{result}"

    async def test_dnd_multiple_times(self, dnd_bot):
        cmds = await _send_group(dnd_bot, ".dnd 3")
        result = "\n".join([str(c) for c in cmds])
        numbers = re.findall(r'\d+', result)
        assert len(numbers) >= 18, f".dnd 3 应返回 3 次生成结果（>=18 个数字），实际输出：{result}"

    async def test_dnd_with_reason(self, dnd_bot):
        cmds = await _send_group(dnd_bot, ".dnd 1 为了勇者")
        result = "\n".join([str(c) for c in cmds])
        assert "为了勇者" in result, ".dnd 含原因时原因应出现在结果中"
        assert "DND人物作成" in result


# ─────────────────────────── COC misc 集成测试 ───────────────────────────

class TestCocMiscCommandIntegration:
    """UtilsCOCCommand (.coc misc) 集成测试"""

    async def test_coc_contains_stats(self, coc_bot):
        """coc 属性生成结果应包含 COC 关键词和 9 项基础属性"""
        cmds = await _send_group(coc_bot, ".coc")
        result = "\n".join([str(c) for c in cmds])
        assert "COC人物作成" in result, f".coc 应包含 COC人物作成，实际输出：{result}"
        # 验证具体属性名称出现在输出中
        assert "力量" in result, f".coc 输出应含力量，实际输出：{result}"
        assert "敏捷" in result, f".coc 输出应含敏捷，实际输出：{result}"
        assert "智力" in result
        numbers = re.findall(r'\d+', result)
        assert len(numbers) >= 9, f".coc 应生成 9 项属性值，实际输出：{result}"

    async def test_coc_multiple_times(self, coc_bot):
        cmds = await _send_group(coc_bot, ".coc 2")
        result = "\n".join([str(c) for c in cmds])
        assert "COC人物作成" in result
        assert result.count("合计") == 2, f".coc 2 应返回两次 COC 作成结果，实际输出：{result}"
        assert "选到了" not in result, ".coc 不应被 .c 随机选择指令处理"

    async def test_coc_with_reason(self, coc_bot):
        """原因参数应出现在返回消息中"""
        cmds = await _send_group(coc_bot, ".coc 侦探角色扮演")
        result = "\n".join([str(c) for c in cmds])
        assert "COC人物作成" in result
        assert "侦探角色扮演" in result
        assert "选到了" not in result, ".coc 不应被 .c 随机选择指令处理"


# ── Miscellaneous command coverage ──────────────────────────────────────

class TestDNDCOC:
    async def test_dnd_single(self, h):
        await h.send_group(".dnd", checker=lambda s: "DND人物作成" in s and s.count("\n") == 1)

    async def test_dnd_multiple_no_space(self, h):
        await h.send_group(".dnd3", checker=lambda s: "DND人物作成" in s and s.count("\n") == 3)

    async def test_dnd_multiple_with_space(self, h):
        await h.send_group(".dnd 3", checker=lambda s: "DND人物作成" in s and s.count("\n") == 3)

    async def test_dnd_with_reason_no_space(self, h):
        await h.send_group(".dnd3 foo", checker=lambda s: "DND人物作成——foo:\n" in s and s.count("\n") == 3)

    async def test_dnd_with_reason_and_space(self, h):
        await h.send_group(".dnd 3   foo", checker=lambda s: "DND人物作成——foo:\n" in s and s.count("\n") == 3)


class TestJRRP:
    async def test_jrrp(self, h):
        await h.send_group(".jrrp", checker=lambda s: "测试用户的今日人品是:" in s)


class TestStats:
    async def test_personal_stats(self, h):
        await h.send_group(".统计",
                           checker=lambda s: "今日收到信息:" in s and "今日指令记录:" in s and "今日掷骰次数:" in s)

    async def test_group_stats(self, h):
        await h.send_group(".统计群聊", checker=lambda s: "今日收到信息:" in s and "今日指令记录:" in s)

    async def test_all_users_denied(self, h):
        await h.send_group(".统计所有用户", checker=lambda s: "权限不足" in s)

    async def test_all_users_master(self, h):
        await h.send_group(".统计所有用户", user_id="test_master",
                           checker=lambda s: "权限不足" not in s and "今日收到信息:" in s and "今日指令记录:" in s)

    async def test_all_groups_master(self, h):
        await h.send_group(".统计所有群聊", user_id="test_master",
                           checker=lambda s: "权限不足" not in s and "条群组信息" in s)

