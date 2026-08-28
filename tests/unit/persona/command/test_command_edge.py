"""
PersonaCommand 单元测试 — 边界/异常/录播/分段

从 test_command.py 拆分，保留独立的测试套件。

共享的 helper（make_group_meta/make_private_meta/make_mock_bot/make_cmd/
get_sent_content）由本目录 conftest.py 通过
autouse fixture 注入到 self 上，本文件直接使用 self.make_group_meta(...) 等
访问。
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, mock_open, patch
from unittest.async_case import IsolatedAsyncioTestCase

from plugins.DicePP.module.persona.command import PersonaCommand
from plugins.DicePP.module.persona.chat.orchestrator import ChatOutcome
from plugins.DicePP.module.persona.data.models import (
    RelationshipState,
    UserProfile,
    WhitelistEntry,
    GroupActivity,
    DiaryEntry,
    DailyEvent,
)
from plugins.DicePP.core.communication import MessageMetaData, MessageSender


class TestEdgeAndExceptionPaths(IsolatedAsyncioTestCase):
    """异常/边界路径（3个）"""

    async def asyncSetUp(self):
        self.bot = self.make_mock_bot()
        self.cmd = self.make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd._send = AsyncMock()

        # 新架构：mock PersonaApp
        self.cmd.app = MagicMock()
        self.cmd.app.chat = MagicMock()
        self.cmd.app.chat.character = MagicMock()
        self.cmd.app.chat.character.name = "TestChar"
        self.cmd.app.chat.character.description = "A test char"
        self.cmd.app.get_character.return_value = self.cmd.app.chat.character
        self.cmd.app.chat_with_user = AsyncMock(return_value="你好呀")

    async def test_quota_exceeded(self):
        from plugins.DicePP.module.persona.llm.router import QuotaExceeded
        self.cmd.app.chat_with_user = AsyncMock(side_effect=QuotaExceeded("配额超限"))
        meta = self.make_private_meta("你好")
        meta.to_me = True
        await self.cmd.process_msg("你好", meta, None)
        assert "配额超限" in self.get_sent_content(self.cmd)

    async def test_introduction_and_empty_command(self):
        meta = self.make_private_meta(".ai unknown")
        await self.cmd.process_msg(".ai unknown", meta, None)
        assert "你好，我是" in self.get_sent_content(self.cmd)

        meta2 = self.make_private_meta(".ai")
        await self.cmd.process_msg(".ai", meta2, None)
        assert "你好，我是" in self.get_sent_content(self.cmd)


class TestGroupChatRecorder(IsolatedAsyncioTestCase):
    """_group_chat_recorder 写库路径与边界"""

    async def asyncSetUp(self):
        self.bot = self.make_mock_bot()
        self.cmd = self.make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store

    async def test_records_to_store(self):
        from plugins.DicePP.core.communication import PostSendEvent

        await self.cmd._group_chat_recorder(PostSendEvent(
            group_id="g1",
            user_id="u1",
            role="user",
            message_type="chat",
            content="hello",
            display_name="小明",
            platform_message_id="platform-1",
            history_stream_id=None,
        ))
        self.store.add_message_stream.assert_awaited_once_with(
            user_id="u1",
            group_id="g1",
            role="user",
            type="chat",
            content="hello",
            display_name="小明",
        )

    async def test_silently_ignores_when_no_store(self):
        from plugins.DicePP.core.communication import PostSendEvent

        self.cmd.data_store = None
        # 不应抛异常；断言到此为止，data_store=None 时直接 return
        await self.cmd._group_chat_recorder(PostSendEvent(
            group_id="g1",
            user_id="u1",
            role="user",
            message_type="chat",
            content="hello",
            display_name="小明",
            platform_message_id="platform-1",
            history_stream_id=None,
        ))

    async def test_history_stream_id_skips_duplicate_persona_write(self):
        from plugins.DicePP.core.communication import PostSendEvent

        await self.cmd._group_chat_recorder(PostSendEvent(
            group_id="g1",
            user_id="u1",
            role="assistant",
            message_type="chat",
            content="hello",
            display_name="小明",
            platform_message_id="platform-1",
            history_stream_id=123,
        ))

        self.store.add_message_stream.assert_not_awaited()

    async def test_sender_managed_history_skips_duplicate_persona_write(self):
        from plugins.DicePP.core.communication import PostSendEvent

        await self.cmd._group_chat_recorder(PostSendEvent(
            group_id="g1",
            user_id="u1",
            role="assistant",
            message_type="chat",
            content="hello",
            display_name="小明",
            platform_message_id="platform-1",
            history_stream_id=None,
            history_managed_by_sender=True,
        ))

        self.store.add_message_stream.assert_not_awaited()


class TestSegmentedPathPreservesGroupActivity(IsolatedAsyncioTestCase):
    """R4 回归: 分段路径下 chat_with_user 返回空字符串（delivery 已由 runtime 完成），
    群活跃度仍需更新, 但 _send 不应被再次调用 (消息已通过 dispatcher 实时发出)
    """

    async def asyncSetUp(self):
        from plugins.DicePP.core.config.pydantic_models import PersonaConfig, ProviderConfig, ModelConfig
        # 与 default_persona_config() 同源, 但启用 group_activity
        persona = PersonaConfig(
            enabled=True,
            character_path="./content/characters",
            providers={
                "openai": ProviderConfig(
                    api_key="fake_key",
                    base_url="http://localhost",
                    models=[
                        ModelConfig(name="gpt-4o", category="llm", capabilities=["text", "tool_calls"], quality=0.9, cost=0.5)
                    ],
                ),
            },
            observe_group_enabled=False,
            group_activity_enabled=True,
            trace_enabled=False,
            whitelist_enabled=False,
            daily_limit=100,
            quota_check_enabled=False,
            relationship_refuse_enabled=False,
            decay_enabled=False,
            proactive_enabled=False,
            character_life_enabled=False,
            group_chat_enabled=False,
        )
        self.bot = self.make_mock_bot(persona)
        self.cmd = self.make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd._send = AsyncMock()

        self.store.is_group_whitelisted = AsyncMock(return_value=True)
        self.store.update_group_activity = AsyncMock()
        self.store.add_message_stream = AsyncMock(return_value=1)
        self.store._retain_message_stream = AsyncMock()

        self.cmd.app = MagicMock()
        self.cmd.app.chat_with_user = AsyncMock(
            return_value=ChatOutcome(
                status="sent",
                sent_count=1,
                reason="output_collected",
                counts_as_interaction=True,
            )
        )

    async def test_segmented_response_updates_activity_without_resend(self):
        meta = self.make_group_meta("hello", to_me=True)
        await self.cmd.process_msg("hello", meta, None)

        # @ 触发后 chat_with_user 走过一次
        self.cmd.app.chat_with_user.assert_awaited_once()

        # chat 层已通过 delivery 发送，command 只按 outcome 更新群活跃度
        self.store.update_group_activity.assert_awaited_once()

        # 消息已由 delivery 发出，_send 不应被再次调用
        self.cmd._send.assert_not_awaited()

    async def test_none_response_short_circuits_before_activity(self):
        """skipped outcome 应在 update_group_activity 之前早退"""
        self.cmd.app.chat_with_user = AsyncMock(
            return_value=ChatOutcome(status="skipped", reason="dedup")
        )
        meta = self.make_group_meta("hello", to_me=True)
        await self.cmd.process_msg("hello", meta, None)

        self.cmd.app.chat_with_user.assert_awaited_once()
        # skipped → 在群活跃度更新之前 return [],store 不被触达
        self.store.update_group_activity.assert_not_awaited()
        self.cmd._send.assert_not_awaited()


class TestEmojiAndImageOnlyMessages(IsolatedAsyncioTestCase):
    """B-260602-4d9e05 回归: 纯表情/纯图片下载失败时不应再回退到 _get_status"""

    async def asyncSetUp(self):
        self.bot = self.make_mock_bot()
        self.cmd = self.make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd._send = AsyncMock()

        self.cmd.app = MagicMock()
        self.cmd.app.chat_with_user = AsyncMock(return_value="收到表情包啦")

        # 保留真实图片解析/缓存控制流，但 mock 文件系统以维持 unit 隔离。
        from plugins.DicePP.module.persona.image_cache import ImageCache
        self.cmd.image_cache = ImageCache()
        mkdir_patch = patch('plugins.DicePP.module.persona.image_cache.os.makedirs')
        open_patch = patch(
            "builtins.open",
            mock_open(read_data="data:image/png;base64,iVBORw0KGgo="),
        )
        mkdir_patch.start()
        open_patch.start()
        self.addCleanup(mkdir_patch.stop)
        self.addCleanup(open_patch.stop)

    async def test_private_emoji_downloaded_and_passed_to_chat(self):
        """私聊纯表情 (sub_type=1) → force_emoji 下载后作为 data URL 传入 chat_with_user"""
        from unittest.mock import patch
        fake_content = b"\x89PNG\r\n"
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.content = fake_content
        fake_response.headers = {"content-type": "image/gif"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        raw = "[CQ:image,file=CA59D5,subType=1,url=http://x.com/e.png,file_size=98936]"
        meta = MessageMetaData(plain_msg="", raw_msg=raw,
                               sender=MessageSender("u1", "测试用户"),
                               group_id="", to_me=True)

        with patch('plugins.DicePP.module.persona.image_cache.httpx.AsyncClient', return_value=mock_client):
            await self.cmd.process_msg("", meta, None)

        self.cmd.app.chat_with_user.assert_awaited_once()
        call_kwargs = self.cmd.app.chat_with_user.await_args.kwargs
        assert call_kwargs["image_data_urls"] is not None
        assert len(call_kwargs["image_data_urls"]) == 1
        assert call_kwargs["image_data_urls"][0].startswith("data:image/")

    async def test_group_at_emoji_downloaded_and_passed_to_chat(self):
        """群 @bot + 表情 → force_emoji 下载后作为 data URL 传入 chat_with_user"""
        from unittest.mock import patch
        fake_content = b"\x89PNG\r\n"
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.content = fake_content
        fake_response.headers = {"content-type": "image/gif"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        raw = "[CQ:at,qq=bot] [CQ:image,file=CA59D5,subType=1,url=http://x.com/e.png,file_size=98936]"
        meta = MessageMetaData(plain_msg="", raw_msg=raw,
                               sender=MessageSender("u1", "测试用户"),
                               group_id="g1", to_me=True)
        meta.sender.card = "银月团长"
        self.bot.get_nickname = AsyncMock(return_value="银月游侠")

        with patch('plugins.DicePP.module.persona.image_cache.httpx.AsyncClient', return_value=mock_client):
            await self.cmd.process_msg("", meta, None)

        self.cmd.app.chat_with_user.assert_awaited_once()
        call_kwargs = self.cmd.app.chat_with_user.await_args.kwargs
        assert call_kwargs["image_data_urls"] is not None
        assert len(call_kwargs["image_data_urls"]) == 1
        assert call_kwargs["image_data_urls"][0].startswith("data:image/")
        assert call_kwargs["nickname"] == "银月游侠"

    async def test_private_regular_image_falls_back_to_chat(self):
        """私聊纯 sub_type=0 图片（下载成功）→ 正常进 chat_with_user，image_data_urls 非空"""
        from unittest.mock import patch
        fake_content = b"\x89PNG\r\n"
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.content = fake_content
        fake_response.headers = {"content-type": "image/png"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        raw = "[CQ:image,file=IMG,subType=0,url=http://x.com/i.png,file_size=1234]"
        meta = MessageMetaData(plain_msg="", raw_msg=raw,
                               sender=MessageSender("u1", "测试用户"),
                               group_id="", to_me=True)

        with patch('plugins.DicePP.module.persona.image_cache.httpx.AsyncClient', return_value=mock_client):
            await self.cmd.process_msg("", meta, None)

        self.cmd.app.chat_with_user.assert_awaited_once()
        call_kwargs = self.cmd.app.chat_with_user.await_args.kwargs
        # 正常路径：message 是用户原文(空)，image_data_urls 是真实 data URL
        assert call_kwargs["image_data_urls"] is not None
        assert len(call_kwargs["image_data_urls"]) == 1
        assert call_kwargs["image_data_urls"][0].startswith("data:image/")

    async def test_private_mixed_emoji_and_image_download_failed(self):
        """混合 emoji + 普通图片，全部下载失败 → [图片下载失败，请重试]"""
        from unittest.mock import patch
        fake_response = MagicMock()
        fake_response.status_code = 404
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        raw = (
            "[CQ:image,file=E,subType=1,url=http://x.com/e.png,file_size=98936]"
            "[CQ:image,file=I,subType=0,url=http://x.com/i.png,file_size=1234]"
        )
        meta = MessageMetaData(plain_msg="", raw_msg=raw,
                               sender=MessageSender("u1", "测试用户"),
                               group_id="", to_me=True)

        with patch('plugins.DicePP.module.persona.image_cache.httpx.AsyncClient', return_value=mock_client):
            await self.cmd.process_msg("", meta, None)

        self.cmd.app.chat_with_user.assert_awaited_once()
        call_kwargs = self.cmd.app.chat_with_user.await_args.kwargs
        assert call_kwargs["message"] == "[图片下载失败，请重试]"
        assert call_kwargs["image_data_urls"] is None

    async def test_private_truly_empty_message_still_returns_status(self):
        """私聊纯 @bot (raw_msg 无图片段) → 仍走 _get_status（无内容）"""
        meta = self.make_private_meta("", user_id="u1")
        meta.raw_msg = ""  # 显式置空，避免有内容
        await self.cmd.process_msg("", meta, None)

        self.cmd.app.chat_with_user.assert_not_awaited()
        # _send 被调用, 内容是 status 报告
        assert self.cmd._send.await_args is not None
