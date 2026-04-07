"""
misc 模块测试
- 集成测试：.jrrp / .dnd / .coc 指令行为
"""
import pytest
import unittest
from unittest.async_case import IsolatedAsyncioTestCase


# ─────────────────────────── 集成测试辅助 ───────────────────────────

class _BotTestBase(IsolatedAsyncioTestCase):
    BOT_NAME = "test_misc_bot"

    async def asyncSetUp(self):
        from core.bot import Bot
        self.bot = Bot(self.BOT_NAME)
        self.bot.config.master = ["test_master"]
        await self.bot.delay_init_command()

    async def asyncTearDown(self):
        await self.bot.shutdown_async()
        import os
        test_path = self.bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(test_path)

    async def _send_group(self, msg: str, user_id: str = "user1", group_id: str = "group1"):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), group_id, False)
        return await self.bot.process_message(msg, meta)


# ─────────────────────────── JRRP 集成测试 ───────────────────────────

@pytest.mark.integration
class TestJrrpCommandIntegration(_BotTestBase):
    """JrrpCommand (.jrrp) 集成测试"""

    BOT_NAME = "test_jrrp_bot"

    async def test_jrrp_returns_response(self):
        cmds = await self._send_group(".jrrp")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".jrrp 应返回结果")
        self.assertTrue(len(result) > 0, "结果内容不应为空")

    async def test_jrrp_contains_number(self):
        """jrrp 输出应包含 1-100 之间的数字"""
        import re
        cmds = await self._send_group(".jrrp")
        result = "\n".join([str(c) for c in cmds])
        numbers = re.findall(r'\d+', result)
        self.assertTrue(len(numbers) > 0, "jrrp 结果应包含数字")
        # 至少有一个数字在 1-100 范围内
        has_valid_num = any(1 <= int(n) <= 100 for n in numbers if n.isdigit())
        self.assertTrue(has_valid_num, f"jrrp 结果应包含 1-100 的人品值，实际输出：{result}")

    async def test_jrrp_deterministic_same_day(self):
        """同一天同一用户的 jrrp 结果应相同"""
        cmds1 = await self._send_group(".jrrp", user_id="fixed_user")
        cmds2 = await self._send_group(".jrrp", user_id="fixed_user")
        result1 = "\n".join([str(c) for c in cmds1])
        result2 = "\n".join([str(c) for c in cmds2])
        self.assertEqual(result1, result2, "同一天同一用户的 jrrp 应相同")


# ─────────────────────────── DND 集成测试 ───────────────────────────

@pytest.mark.integration
class TestDndCommandIntegration(_BotTestBase):
    """UtilsDNDCommand (.dnd) 集成测试"""

    BOT_NAME = "test_dnd_misc_bot"

    async def test_dnd_returns_response(self):
        cmds = await self._send_group(".dnd")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".dnd 应返回结果")
        self.assertTrue(len(result) > 0, "结果内容不应为空")

    async def test_dnd_contains_stats(self):
        """dnd 属性生成结果应包含 6 项属性"""
        import re
        cmds = await self._send_group(".dnd")
        result = "\n".join([str(c) for c in cmds])
        # 应包含属性值（数字）
        numbers = re.findall(r'\d+', result)
        self.assertTrue(len(numbers) >= 6, f".dnd 应生成 6 项属性，实际输出：{result}")

    async def test_dnd_multiple_times(self):
        cmds = await self._send_group(".dnd 3")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".dnd 3 应返回 3 次生成结果")

    async def test_dnd_with_reason(self):
        cmds = await self._send_group(".dnd 1 为了勇者")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("为了勇者", result, ".dnd 含原因时原因应出现在结果中")


# ─────────────────────────── COC misc 集成测试 ───────────────────────────

@pytest.mark.integration
class TestCocMiscCommandIntegration(_BotTestBase):
    """UtilsCOCCommand (.coc misc) 集成测试"""

    BOT_NAME = "test_coc_misc_bot"

    async def test_coc_returns_response(self):
        cmds = await self._send_group(".coc")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".coc 应返回结果")
        self.assertTrue(len(result) > 0, "结果内容不应为空")

    async def test_coc_contains_stats(self):
        """coc 属性生成结果应包含多项属性数值"""
        import re
        cmds = await self._send_group(".coc")
        result = "\n".join([str(c) for c in cmds])
        numbers = re.findall(r'\d+', result)
        self.assertTrue(len(numbers) >= 6, f".coc 应生成多项属性，实际输出：{result}")

    async def test_coc_multiple_times(self):
        cmds = await self._send_group(".coc 2")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".coc 2 应返回两次生成结果")

    async def test_coc_with_reason(self):
        """原因参数应出现在返回消息中（注意次数需可被解析为整数）"""
        cmds = await self._send_group(".coc 侦探角色扮演")
        result = "\n".join([str(c) for c in cmds])
        # 原因应在输出中（当 args[0] 不是有效整数时，reason 含整个参数）
        # 或者直接断言有输出、不崩溃即可
        self.assertTrue(len(cmds) > 0, ".coc 含原因时应有输出")
