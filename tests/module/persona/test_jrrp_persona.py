"""
Tests for jrrp persona interception paths.

Covers:
- PersonaCommand.can_process_msg .jrrp branching (is_awake, whitelist, config toggle)
- PersonaCommand._handle_jrrp with mocked compute_jrrp and mocked app.chat.chat
- is_command=True propagation through ChatSession.chat
- PersonaApp.is_awake() delegation to sleep_gate
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from core.communication import MessageMetaData
from plugins.DicePP.module.persona.chat.session import ChatCallContext
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
    from module.persona.command import PersonaCommand

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
        from module.misc.jrrp_utils import JrrpResult
        return JrrpResult(jrrp=75, zrrp=60, delta=15, delta_percent=25.0,
                          direction='up', is_min=False, is_max=False)

    async def test_sends_info_line_and_commentary(self, mock_jrrp_result):
        """正常路径：event_msg 通过 transient_message 传入 chat() → LLM 评语"""
        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="运气不错呢！")

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # event_msg 通过 transient_message 传入 chat()，不写入 message_stream
        app.chat.chat.assert_awaited_once()
        chat_kwargs = app.chat.chat.await_args.kwargs
        assert chat_kwargs.get("ctx").transient_message is not None, \
            "应传入 transient_message"
        assert "[事件] test_user 查询了今日运势" in chat_kwargs["ctx"].transient_message, \
            f"transient_message 应包含事件内容，实际: {chat_kwargs['transient_message'][:80]}..."
        assert "今日: 75/100" in chat_kwargs["ctx"].transient_message
        # 只发了 LLM 评语，没有模板数值行
        assert cmd._send.await_count == 1
        cmd._send.assert_any_call("U123", "", "运气不错呢！")

    async def test_fallback_when_llm_raises(self, mock_jrrp_result):
        """LLM 异常时回退到 format_jrrp_text（数值 + 趋势，和原版 JrrpCommand 一致）"""
        app = MagicMock()
        app.chat.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                with patch("module.misc.jrrp_utils.format_jrrp_text",
                           return_value="test_user的今日人品是:75\n人品比昨天上升了25.0%！"):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # chat() 被调用（携带 transient_message），但 LLM 抛出异常
        app.chat.chat.assert_awaited_once()
        chat_kwargs = app.chat.chat.await_args.kwargs
        assert chat_kwargs.get("ctx").transient_message is not None
        # 回退时发送完整模板文本（数值 + 趋势），仅发送一次
        cmd._send.assert_any_call("U123", "",
                                  "test_user的今日人品是:75\n人品比昨天上升了25.0%！")
        assert cmd._send.await_count == 1

    async def test_commentary_empty_fallback(self, mock_jrrp_result):
        """LLM 返回空串时回退到 format_jrrp_text，确保用户可见"""
        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="")

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        fallback_text = "test_user的今日人品是:75\n人品比昨天上升了25.0%！"
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                with patch("module.misc.jrrp_utils.format_jrrp_text",
                           return_value=fallback_text):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        cmd._send.assert_called_once_with("U123", "", fallback_text)

    async def test_commentary_empty_second_case_fallback(self, mock_jrrp_result):
        """LLM 返回空串时回退到 format_jrrp_text"""
        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="")

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        fallback_text = "test_user的今日人品是:75\n人品比昨天上升了25.0%！"
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                with patch("module.misc.jrrp_utils.format_jrrp_text",
                           return_value=fallback_text):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        assert cmd._send.await_count == 1
        cmd._send.assert_called_once_with("U123", "", fallback_text)

    async def test_works_without_data_store(self, mock_jrrp_result):
        """data_store=None 时 _handle_jrrp 仍能工作（transient_message 不依赖持久化）"""
        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="运气不错！")
        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()
        cmd.data_store = None

        meta = _make_meta()
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # data_store 为 None 不应阻塞 LLM 调用（transient_message 走旁路）
        app.chat.chat.assert_awaited_once()
        cmd._send.assert_called_once_with("U123", "", "运气不错！")


# ── Test: is_command=True propagation ────────────────────────────────────

class TestIsCommandPropagation:
    """is_command=True 从 command.py 传播到 ChatSession.chat"""

    async def test_handle_jrrp_calls_chat_with_is_command(self):
        """_handle_jrrp 通过 app.chat.chat 传入 is_command=True"""
        from module.misc.jrrp_utils import JrrpResult

        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="nice")

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        mock_result = JrrpResult(jrrp=50, zrrp=50, delta=0, delta_percent=0.0,
                                 direction='same', is_min=False, is_max=False)

        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_result):
            with patch("utils.time.get_current_date_raw"):
                await cmd._handle_jrrp("U123", "", meta)

        # 验证 chat.chat 被调用且 is_command=True
        app.chat.chat.assert_awaited_once()
        assert app.chat.chat.await_args is not None
        assert app.chat.chat.await_args.kwargs.get("ctx").is_command is True


# ── Test: PersonaApp.is_awake ──────────────────────────────────────────────

class TestPersonaAppIsAwake:
    """PersonaApp.is_awake() 委托行为测试"""

    async def test_delegates_to_chat_session(self):
        """is_awake 委托给 chat.is_awake()"""
        from module.persona.factory import PersonaApp

        chat = MagicMock()
        chat.is_awake = AsyncMock(return_value=True)

        app = PersonaApp(chat=chat, life=MagicMock(), store=MagicMock(), port=MagicMock())
        result = await app.is_awake()
        assert result is True
        chat.is_awake.assert_awaited_once()

    async def test_sleep_gate_none_returns_true(self):
        """ChatSession sleep_gate 为 None 时 is_awake 返回 True"""
        from module.persona.chat.session import ChatSession

        store = MagicMock()
        router = MagicMock()
        coordinator = MagicMock()
        character = MagicMock()
        config = PersonaConfig()
        scoring_trigger = MagicMock()
        response_handler = MagicMock()
        context_builder = MagicMock()

        session = ChatSession(
            store=store, router=router,
            coordinator=coordinator, character=character, config=config,
            scoring_trigger=scoring_trigger, response_handler=response_handler,
            context_builder=context_builder, sleep_gate=None,
        )

        result = await session.is_awake()
        assert result is True

    async def test_sleep_gate_awake_returns_true(self):
        """sleep_gate.is_awake() 返回 True 时 is_awake 返回 True"""
        from module.persona.chat.session import ChatSession

        sleep_gate = MagicMock()
        sleep_gate.is_awake = AsyncMock(return_value=True)

        session = ChatSession(
            store=MagicMock(), router=MagicMock(),
            coordinator=MagicMock(), character=MagicMock(), config=PersonaConfig(),
            scoring_trigger=MagicMock(), response_handler=MagicMock(),
            context_builder=MagicMock(), sleep_gate=sleep_gate,
        )

        result = await session.is_awake()
        assert result is True

    async def test_sleep_gate_not_awake_returns_false(self):
        """sleep_gate.is_awake() 返回 False 时 is_awake 返回 False"""
        from module.persona.chat.session import ChatSession

        sleep_gate = MagicMock()
        sleep_gate.is_awake = AsyncMock(return_value=False)

        session = ChatSession(
            store=MagicMock(), router=MagicMock(),
            coordinator=MagicMock(), character=MagicMock(), config=PersonaConfig(),
            scoring_trigger=MagicMock(), response_handler=MagicMock(),
            context_builder=MagicMock(), sleep_gate=sleep_gate,
        )

        result = await session.is_awake()
        assert result is False


# ── Test: event_msg → LLM context 管道验证 ─────────────────────────────────

class TestJrrpLLMContext:
    """验证 _handle_jrrp 的 event_msg 通过 transient_message 进入 LLM 上下文"""

    async def test_event_msg_passed_as_transient_message(self):
        """event_msg 以 transient_message 参数传入 chat()，不写入 message_stream"""
        from module.misc.jrrp_utils import JrrpResult

        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="运气不错！")

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        mock_result = JrrpResult(jrrp=75, zrrp=60, delta=15, delta_percent=25.0,
                                 direction='up', is_min=False, is_max=False)

        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_result):
            with patch("utils.time.get_current_date_raw"):
                await cmd._handle_jrrp("U123", "", meta)

        # 验证 chat() 被调用且 ctx.transient_message 包含完整 event 内容
        app.chat.chat.assert_awaited_once()
        ctx = app.chat.chat.await_args.kwargs.get("ctx")
        assert ctx is not None, "应传入 ctx (ChatCallContext)"
        tm = ctx.transient_message
        assert tm is not None, "ctx.transient_message 不应为 None"
        assert "[事件] test_user 查询了今日运势" in tm, \
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
        assert app.chat.chat.await_args.kwargs["message"] == ".jrrp", \
            f"message 应为 '.jrrp'（仅用于去重/缓冲），实际: {app.chat.chat.await_args.kwargs.get('message')}"

