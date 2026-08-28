"""
Tests for jrrp persona interception paths.

Covers:
- PersonaCommand.can_process_msg .jrrp branching (is_awake, whitelist, config toggle)
- PersonaCommand._handle_jrrp with mocked compute_jrrp and mocked app.chat.chat
- is_command=True propagation through ChatSession.chat
- PersonaApp.is_awake() delegation to sleep_gate
"""
import re
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from plugins.DicePP.core.communication import MessageMetaData
from plugins.DicePP.module.persona.chat.chat_shared import ChatCallContext
from plugins.DicePP.module.persona.chat.orchestrator import ChatOutcome
from plugins.DicePP.core.config.pydantic_models import PersonaConfig


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_meta(user_id: str = "U123", group_id: str = "", nickname: str = "test_user") -> MessageMetaData:
    """创建最小 MessageMetaData"""
    meta = MagicMock(spec=MessageMetaData)
    meta.user_id = user_id
    meta.group_id = group_id
    meta.nickname = nickname
    meta.sender = MagicMock()
    meta.sender.card = ""
    meta.sender.nickname = nickname
    meta.to_me = False
    meta.raw_msg = ""
    return meta


def _make_cmd(app=None, data_store=None, config=None, enabled=True):
    """创建 PersonaCommand 实例并注入 mock 依赖"""
    from plugins.DicePP.module.persona.command import PersonaCommand

    cmd = PersonaCommand.__new__(PersonaCommand)
    cmd.enabled = enabled
    cmd.app = app
    cmd.data_store = data_store
    cmd.bot = MagicMock()
    cmd.bot.get_nickname = AsyncMock(return_value="test_user")
    cmd.image_cache = MagicMock()
    cmd._admin_handlers = {}

    if config is None:
        config = PersonaConfig(
            jrrp_persona_enabled=True,
            whitelist_enabled=False,
            group_activity_enabled=False,
        )
    cmd.config = config

    # _check_whitelist 使用 self.bot.config.persona_ai，而非 self.config，
    # 因此需同步配置 bot mock
    cmd.bot.config.persona_ai = config
    cmd.bot.config.persona = None  # 匹配新默认值

    return cmd


# ── Test: can_process_msg .jrrp branching ──────────────────────────────────

class TestCanProcessMsgJrrpBranching:
    """PersonaCommand.can_process_msg 的 .jrrp 分支测试"""

    async def _call_can_process(self, cmd, msg=".jrrp", group_id=""):
        """辅助方法：调用 can_process_msg 并返回第一个元素 (bool)"""
        meta = _make_meta(group_id=group_id)
        result, _, _ = await cmd.can_process_msg(msg, meta)
        return result

    async def test_persona_disabled_returns_false(self):
        """模块禁用时 .jrrp 返回 False（回退到 JrrpCommand）"""
        cmd = _make_cmd(enabled=False)
        result = await self._call_can_process(cmd)
        assert result is False

    async def test_app_not_initialized_returns_false(self):
        """app 为 None 时 .jrrp 返回 False"""
        cmd = _make_cmd(app=None)
        result = await self._call_can_process(cmd)
        assert result is False

    async def test_jrrp_with_valid_app_returns_true(self):
        """角色清醒或睡眠时 .jrrp 均返回 True（is_awake 不参与 jrrp 路由决策）"""
        app = MagicMock()
        cmd = _make_cmd(app=app)
        result = await self._call_can_process(cmd)
        assert result is True

    async def test_whitelist_enabled_and_not_whitelisted_returns_false(self):
        """白名单启用但用户不在白名单时返回 False"""
        config = PersonaConfig(
            jrrp_persona_enabled=True,
            whitelist_enabled=True,
            group_activity_enabled=False,
        )

        app = MagicMock()

        data_store = MagicMock()
        data_store.get_global_setting = AsyncMock(return_value="some_code")
        data_store.is_user_whitelisted = AsyncMock(return_value=False)

        cmd = _make_cmd(app=app, data_store=data_store, config=config)
        result = await self._call_can_process(cmd, group_id="")
        assert result is False

    async def test_whitelist_enabled_and_whitelisted_returns_true(self):
        """白名单启用且用户在白名单时返回 True"""
        config = PersonaConfig(
            jrrp_persona_enabled=True,
            whitelist_enabled=True,
            group_activity_enabled=False,
        )

        app = MagicMock()

        data_store = MagicMock()
        data_store.get_global_setting = AsyncMock(return_value="some_code")
        data_store.is_user_whitelisted = AsyncMock(return_value=True)

        cmd = _make_cmd(app=app, data_store=data_store, config=config)
        result = await self._call_can_process(cmd, group_id="")
        assert result is True

    async def test_jrrp_persona_disabled_returns_false(self):
        """jrrp_persona_enabled=False 时 .jrrp 返回 False"""
        config = PersonaConfig(
            jrrp_persona_enabled=False,
            whitelist_enabled=False,
            group_activity_enabled=False,
        )

        app = MagicMock()

        cmd = _make_cmd(app=app, config=config)
        result = await self._call_can_process(cmd)
        assert result is False

    async def test_non_jrrp_command_not_intercepted(self):
        """非 .jrrp 的 . 命令不进入 jrrp 分支"""
        cmd = _make_cmd(enabled=True, app=None)
        meta = _make_meta()
        result, _, hint = await cmd.can_process_msg(".roll", meta)
        # 不以 .ai 开头的 . 命令 → False 且不进入 persona
        assert result is False

    async def test_chinese_period_jrrp_also_works(self):
        """中文句号「。jrrp」也能拦截"""
        app = MagicMock()
        cmd = _make_cmd(app=app)
        result = await self._call_can_process(cmd, msg="。jrrp")
        assert result is True


# ── Test: _handle_jrrp ─────────────────────────────────────────────────────

class TestHandleJrrp:
    """PersonaCommand._handle_jrrp 单元测试"""

    @pytest.fixture
    def mock_jrrp_result(self):
        from plugins.DicePP.module.misc.jrrp_utils import JrrpResult
        return JrrpResult(jrrp=75, zrrp=60, delta=15, delta_percent=25.0,
                          direction='up', is_min=False, is_max=False)

    async def test_sends_info_line_and_commentary(self, mock_jrrp_result):
        """正常路径：event_msg 通过 transient_message 传入 chat() → LLM 评语"""
        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="sent", sent_count=1)
        )

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # event_msg 通过 transient_message 传入 chat_command()，不写入 message_stream
        app.chat.chat_command.assert_awaited_once()
        chat_kwargs = app.chat.chat_command.await_args.kwargs
        assert chat_kwargs.get("ctx").transient_message is not None, \
            "应传入 transient_message"
        assert re.match(
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[事件\] ",
            chat_kwargs["ctx"].transient_message,
        )
        assert (
            "[事件] [uid: U123] [昵称: test_user] 查询了今日运势"
            in chat_kwargs["ctx"].transient_message
        )
        assert "今日: 75/100" in chat_kwargs["ctx"].transient_message
        # LLM 评语已由 chat delivery 发送，命令层不再二次 _send
        cmd._send.assert_not_awaited()

    async def test_group_context_uses_dicepp_resolved_nickname(
        self, mock_jrrp_result,
    ):
        """群 .jrrp 与普通聊天共用 DicePP 正式称呼，不让群名片覆盖角色名。"""
        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="sent", sent_count=1)
        )
        cmd = _make_cmd(app=app)
        cmd.bot.get_nickname = AsyncMock(return_value="银月游侠")
        cmd._send = AsyncMock()
        meta = _make_meta(group_id="G1", nickname="账号昵称")
        meta.sender.card = "银月团长"

        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                await cmd._handle_jrrp("U123", "G1", meta)

        ctx = app.chat.chat_command.await_args.kwargs["ctx"]
        assert ctx.nickname == "银月游侠"
        assert (
            "[事件] [uid: U123] [昵称: 银月游侠] 查询了今日运势"
            in ctx.transient_message
        )
        assert "银月团长" not in ctx.transient_message

    async def test_fallback_when_llm_raises(self, mock_jrrp_result):
        """LLM 异常时回退到 format_jrrp_text（数值 + 趋势，和原版 JrrpCommand 一致）"""
        app = MagicMock()
        app.chat.chat_command = AsyncMock(side_effect=RuntimeError("LLM down"))

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                with patch('plugins.DicePP.module.misc.jrrp_utils.format_jrrp_text',
                           return_value="test_user的今日人品是:75\n人品比昨天上升了25.0%！"):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # chat_command() 被调用（携带 transient_message），但 LLM 抛出异常
        app.chat.chat_command.assert_awaited_once()
        chat_kwargs = app.chat.chat_command.await_args.kwargs
        assert chat_kwargs.get("ctx").transient_message is not None
        # 回退时发送完整模板文本（数值 + 趋势），仅发送一次
        cmd._send.assert_any_call("U123", "",
                                  "test_user的今日人品是:75\n人品比昨天上升了25.0%！")
        assert cmd._send.await_count == 1

    async def test_commentary_empty_fallback(self, mock_jrrp_result):
        """LLM 返回空串时回退到 format_jrrp_text，确保用户可见"""
        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="empty", reason="empty_response")
        )

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        fallback_text = "test_user的今日人品是:75\n人品比昨天上升了25.0%！"
        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                with patch('plugins.DicePP.module.misc.jrrp_utils.format_jrrp_text',
                           return_value=fallback_text):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        cmd._send.assert_called_once_with("U123", "", fallback_text)

    async def test_commentary_empty_second_case_fallback(self, mock_jrrp_result):
        """LLM 返回空串时回退到 format_jrrp_text"""
        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="empty", reason="empty_response")
        )

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        fallback_text = "test_user的今日人品是:75\n人品比昨天上升了25.0%！"
        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                with patch('plugins.DicePP.module.misc.jrrp_utils.format_jrrp_text',
                           return_value=fallback_text):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        assert cmd._send.await_count == 1
        cmd._send.assert_called_once_with("U123", "", fallback_text)

    async def test_fallback_when_llm_returns_failed_status(self, mock_jrrp_result):
        """R3: chat_command 返回 status='failed' 时回退到 format_jrrp_text 模板发送。"""
        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="failed", reason="provider_error")
        )

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        fallback_text = "test_user的今日人品是:75\n人品比昨天上升了25.0%！"
        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                with patch('plugins.DicePP.module.misc.jrrp_utils.format_jrrp_text',
                           return_value=fallback_text):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # chat_command 被调用一次
        app.chat.chat_command.assert_awaited_once()
        # 回退时发送模板文本
        cmd._send.assert_called_once_with("U123", "", fallback_text)

    async def test_works_without_data_store(self, mock_jrrp_result):
        """data_store=None 时 _handle_jrrp 仍能工作（transient_message 不依赖持久化）"""
        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="sent", sent_count=1)
        )
        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()
        cmd.data_store = None

        meta = _make_meta()
        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # data_store 为 None 不应阻塞 LLM 调用（transient_message 走旁路）
        app.chat.chat_command.assert_awaited_once()
        cmd._send.assert_not_awaited()

    async def test_handle_jrrp_e2e_real_orchestrator(self, mock_jrrp_result):
        """e2e: _handle_jrrp → 真实 ChatOrchestrator.chat(ctx=...) 全链路签名验证

        使用真实 ChatOrchestrator（非裸 AsyncMock），确保 ctx=ChatCallContext
        传参不会被静默吞掉。裸 AsyncMock 接受任意 kwargs 是这个 bug 逃过测试的根因。
        """
        from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        from plugins.DicePP.module.persona.data.store import PersonaDataStore

        # 构造真实 ChatOrchestrator（依赖全部 mock，但 chat() 是真实方法）
        store = MagicMock(spec=PersonaDataStore)
        db = MagicMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        store._persona_db = db

        char = MagicMock()
        char.character_id = "test_e2e"
        char.get_relation_labels.return_value = ["陌生人"]
        char.extensions.sleep_messages = None
        char.extensions.refuse_messages = None
        char.personality = ""
        char.scenario = ""
        char.name = "TestBot"
        char.description = ""
        char.mes_example = ""
        char.tails = ""
        char.character_book = None

        config = ChatConfig(
            timezone="Asia/Shanghai",
            reputation_refuse_threshold=30,
            relationship_refuse_enabled=False,
            max_history_turns=20,
            max_history_tokens=8000,
            lore_token_budget=1000,
        )

        cb = MagicMock()
        cb.build_static_prompt.return_value = "you are a test bot"

        orch = ChatOrchestrator(
            store=store, client=MagicMock(), character=char,
            config=config, context_builder=cb,
        )

        # 短路真实 LLM turn，但保留 chat_command() 真实签名与 ctx 传递路径
        orch._ensure_conversation = AsyncMock(return_value=MagicMock())
        mock_agent = MagicMock()
        mock_agent.execute_turn = AsyncMock(
            return_value=ChatOutcome(status="sent", sent_count=1)
        )
        orch._ensure_agent = MagicMock(return_value=mock_agent)

        # 组装 PersonaCommand，app.chat 指向真实 ChatOrchestrator
        app = MagicMock()
        app.chat = orch
        app.send_message = AsyncMock()

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta(group_id="G1", nickname="账号昵称")
        meta.sender.card = "银月团长"
        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_jrrp_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                result = await cmd._handle_jrrp("U123", "G1", meta)

        # 不抛 TypeError 即为通过；LLM 评语由 delivery 发送，命令层不再 _send
        assert result == []
        cmd._send.assert_not_awaited()
        mock_agent.execute_turn.assert_awaited_once()
        turn_kwargs = mock_agent.execute_turn.await_args.kwargs
        assert turn_kwargs["speaker_name"] == "test_user"
        assert (
            "[事件] [uid: U123] [昵称: test_user] 查询了今日运势"
            in turn_kwargs["transient_message"]
        )


# ── Test: is_command=True propagation ────────────────────────────────────

class TestIsCommandPropagation:
    """is_command=True 从 command.py 传播到 ChatOrchestrator.chat_command"""

    async def test_handle_jrrp_calls_chat_with_is_command(self):
        """_handle_jrrp 通过 app.chat.chat_command 传入 is_command=True"""
        from plugins.DicePP.module.misc.jrrp_utils import JrrpResult

        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="sent", sent_count=1)
        )

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        mock_result = JrrpResult(jrrp=50, zrrp=50, delta=0, delta_percent=0.0,
                                 direction='same', is_min=False, is_max=False)

        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                await cmd._handle_jrrp("U123", "", meta)

        # 验证 chat_command 被调用且 is_command=True
        app.chat.chat_command.assert_awaited_once()
        assert app.chat.chat_command.await_args is not None
        assert app.chat.chat_command.await_args.kwargs.get("ctx").is_command is True


# ── Test: PersonaApp.is_awake ──────────────────────────────────────────────

class TestPersonaAppIsAwake:
    """PersonaApp.is_awake() 委托行为测试"""

    async def test_delegates_to_chat_session(self):
        """is_awake 委托给 chat.is_awake()"""
        from plugins.DicePP.module.persona.factory import PersonaApp

        chat = MagicMock()
        chat.is_awake = AsyncMock(return_value=True)

        app = PersonaApp(chat=chat, life=MagicMock(), store=MagicMock(), port=MagicMock())
        result = await app.is_awake()
        assert result is True
        chat.is_awake.assert_awaited_once()


# ── Test: event_msg → LLM context 管道验证 ─────────────────────────────────

class TestJrrpLLMContext:
    """验证 _handle_jrrp 的 event_msg 通过 transient_message 进入 LLM 上下文"""

    async def test_event_msg_passed_as_transient_message(self):
        """event_msg 以 transient_message 参数传入 chat()，不写入 message_stream"""
        from plugins.DicePP.module.misc.jrrp_utils import JrrpResult

        app = MagicMock()
        app.chat.chat_command = AsyncMock(
            return_value=ChatOutcome(status="sent", sent_count=1)
        )

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        mock_result = JrrpResult(jrrp=75, zrrp=60, delta=15, delta_percent=25.0,
                                 direction='up', is_min=False, is_max=False)

        with patch('plugins.DicePP.module.misc.jrrp_utils.compute_jrrp', return_value=mock_result):
            with patch('plugins.DicePP.utils.time.get_current_date_raw'):
                await cmd._handle_jrrp("U123", "", meta)

        # 验证 chat_command() 被调用且 ctx.transient_message 包含完整 event 内容
        app.chat.chat_command.assert_awaited_once()
        ctx = app.chat.chat_command.await_args.kwargs.get("ctx")
        assert ctx is not None, "应传入 ctx (ChatCallContext)"
        tm = ctx.transient_message
        assert tm is not None, "ctx.transient_message 不应为 None"
        assert "[事件] [uid: U123] [昵称: test_user] 查询了今日运势" in tm, \
            f"transient_message 应包含 [事件] 前缀，实际: {tm[:80]}..."
        assert "今日: 75/100" in tm, \
            f"transient_message 应包含今日运势值，实际: {tm[:80]}..."
        assert "昨日: 60/100" in tm, \
            f"transient_message 应包含昨日运势值，实际: {tm[:80]}..."
        assert "上涨 25.0%" in tm, \
            f"transient_message 应包含趋势方向，实际: {tm[:80]}..."
        assert "请以角色身份就此说一两句话" in tm, \
            f"transient_message 应包含角色指令，实际: {tm[:80]}..."

        # 验证 is_command=True, message=".jrrp"（R2: message 仅用于去重/缓冲）
        assert ctx.is_command is True
        assert app.chat.chat_command.await_args.kwargs["message"] == ".jrrp", \
            f"message 应为 '.jrrp'（仅用于去重/缓冲），实际: {app.chat.chat_command.await_args.kwargs.get('message')}"


# ── Test: ChatOrchestrator.chat 签名契约 ───────────────────────────────────

class TestChatSignatureContract:
    """防止 ChatOrchestrator.chat() 签名变更导致调用方静默失败。

    裸 AsyncMock 接受任意 kwargs，签名不匹配会被吞掉。
    本测试通过 inspect.signature 直接验证真实方法的参数契约，
    确保 ctx=ChatCallContext 传参方式始终被 chat() 支持。
    """

    def test_chat_accepts_ctx_parameter(self):
        """ChatOrchestrator.chat() 签名包含 ctx 参数"""
        import inspect
        from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator

        sig = inspect.signature(ChatOrchestrator.chat)
        params = dict(sig.parameters)
        assert "ctx" in params, (
            f"ChatOrchestrator.chat() 必须接受 ctx 参数，"
            f"当前签名: {sig}"
        )

    def test_chat_rejects_legacy_kwargs(self):
        """ChatOrchestrator.chat() 不接受已废弃的独立 keyword 参数"""
        import inspect
        from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator

        sig = inspect.signature(ChatOrchestrator.chat)
        params = dict(sig.parameters)
        legacy = ["is_command", "image_data_urls", "transient_message", "nickname"]
        for name in legacy:
            assert name not in params, (
                f"ChatOrchestrator.chat() 不应再接受独立参数 '{name}'，"
                f"应通过 ctx: ChatCallContext 传入。当前签名: {sig}"
            )

