"""
Tests for jrrp persona interception paths.

Covers:
- PersonaCommand.can_process_msg .jrrp branching (is_awake, whitelist, config toggle)
- PersonaCommand._handle_jrrp with mocked compute_jrrp and mocked app.chat.chat
- skip_scoring=True propagation through ChatSession.chat
- PersonaApp.is_awake() delegation to sleep_gate
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from core.communication import MessageMetaData


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
        config = MagicMock()
        config.jrrp_persona_enabled = True
        config.whitelist_enabled = False
        config.group_activity_enabled = False
        config.character_name = "test_char"
    cmd.config = config

    # _check_whitelist 使用 self.bot.config.persona_ai，而非 self.config，
    # 因此需同步配置 bot mock
    cmd.bot.config.persona_ai = config

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

    async def test_character_sleeping_returns_false(self):
        """角色睡眠时 .jrrp 返回 False"""
        app = MagicMock()
        app.is_awake = AsyncMock(return_value=False)
        cmd = _make_cmd(app=app)
        result = await self._call_can_process(cmd)
        assert result is False

    async def test_character_awake_returns_true(self):
        """角色清醒时 .jrrp 返回 True"""
        app = MagicMock()
        app.is_awake = AsyncMock(return_value=True)
        cmd = _make_cmd(app=app)
        result = await self._call_can_process(cmd)
        assert result is True

    async def test_whitelist_enabled_and_not_whitelisted_returns_false(self):
        """白名单启用但用户不在白名单时返回 False"""
        config = MagicMock()
        config.jrrp_persona_enabled = True
        config.whitelist_enabled = True
        config.group_activity_enabled = False

        app = MagicMock()
        app.is_awake = AsyncMock(return_value=True)

        data_store = MagicMock()
        data_store.get_global_setting = AsyncMock(return_value="some_code")
        data_store.is_user_whitelisted = AsyncMock(return_value=False)

        cmd = _make_cmd(app=app, data_store=data_store, config=config)
        result = await self._call_can_process(cmd, group_id="")
        assert result is False

    async def test_whitelist_enabled_and_whitelisted_returns_true(self):
        """白名单启用且用户在白名单时返回 True"""
        config = MagicMock()
        config.jrrp_persona_enabled = True
        config.whitelist_enabled = True
        config.group_activity_enabled = False

        app = MagicMock()
        app.is_awake = AsyncMock(return_value=True)

        data_store = MagicMock()
        data_store.get_global_setting = AsyncMock(return_value="some_code")
        data_store.is_user_whitelisted = AsyncMock(return_value=True)

        cmd = _make_cmd(app=app, data_store=data_store, config=config)
        result = await self._call_can_process(cmd, group_id="")
        assert result is True

    async def test_jrrp_persona_disabled_returns_false(self):
        """jrrp_persona_enabled=False 时 .jrrp 返回 False"""
        config = MagicMock()
        config.jrrp_persona_enabled = False
        config.whitelist_enabled = False
        config.group_activity_enabled = False

        app = MagicMock()
        app.is_awake = AsyncMock(return_value=True)

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
        app.is_awake = AsyncMock(return_value=True)
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
        """正常路径：写入 event_msg → LLM 评语（不发模板数值行）"""
        app = MagicMock()
        app.is_awake = AsyncMock(return_value=True)
        app.chat.chat = AsyncMock(return_value="运气不错呢！")

        data_store = MagicMock()
        data_store.add_message_stream = AsyncMock()

        cmd = _make_cmd(app=app, data_store=data_store)
        cmd._send = AsyncMock()

        meta = _make_meta()
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # event_msg 以 user 角色写入 message_stream
        user_calls = [c for c in data_store.add_message_stream.await_args_list
                      if c.kwargs.get("role") == "user"]
        assert len(user_calls) == 1, \
            f"event_msg 应在 chat() 前以 user 角色写入，实际调用: {data_store.add_message_stream.await_args_list}"
        # 只发了 LLM 评语，没有模板数值行
        assert cmd._send.await_count == 1
        cmd._send.assert_any_call("U123", "", "运气不错呢！")

    async def test_fallback_when_llm_raises(self, mock_jrrp_result):
        """LLM 异常时回退到 format_jrrp_text（数值 + 趋势，和原版 JrrpCommand 一致）"""
        app = MagicMock()
        app.chat.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()
        cmd.data_store = MagicMock()
        cmd.data_store.add_message_stream = AsyncMock()

        meta = _make_meta()
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                with patch("module.misc.jrrp_utils.format_jrrp_text",
                           return_value="test_user的今日人品是:75\n人品比昨天上升了25.0%！"):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # event_msg 仍在 chat() 前以 user 角色写入
        user_calls = [c for c in cmd.data_store.add_message_stream.await_args_list
                      if c.kwargs.get("role") == "user"]
        assert len(user_calls) == 1
        # 回退时发送完整模板文本（数值 + 趋势），仅发送一次
        cmd._send.assert_any_call("U123", "",
                                  "test_user的今日人品是:75\n人品比昨天上升了25.0%！")
        assert cmd._send.await_count == 1

    async def test_commentary_empty_segment_mode_no_fallback(self, mock_jrrp_result):
        """segment 模式 LLM 返回空串时不额外回退（dispatcher 已发送）"""
        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="")
        app.segment_dispatcher = MagicMock()  # 存在 → segment 模式

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()
        cmd.data_store = MagicMock()
        cmd.data_store.add_message_stream = AsyncMock()

        meta = _make_meta()
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # segment 模式下 dispatcher 已发送，不应额外回退
        assert cmd._send.await_count == 0

    async def test_commentary_empty_non_segment_fallback(self, mock_jrrp_result):
        """非 segment 模式 LLM 返回空串时回退到 format_jrrp_text"""
        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="")
        app.segment_dispatcher = None  # 非 segment 模式

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()
        cmd.data_store = MagicMock()
        cmd.data_store.add_message_stream = AsyncMock()

        meta = _make_meta()
        fallback_text = "test_user的今日人品是:75\n人品比昨天上升了25.0%！"
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                with patch("module.misc.jrrp_utils.format_jrrp_text",
                           return_value=fallback_text):
                    result = await cmd._handle_jrrp("U123", "", meta)

        assert result == []
        # 非 segment 模式下 LLM 返回空串，应回退到模板确保用户可见
        assert cmd._send.await_count == 1
        cmd._send.assert_called_once_with("U123", "", fallback_text)

    async def test_fallback_when_event_not_persisted(self, mock_jrrp_result):
        """event_msg 持久化失败时回退到 format_jrrp_text"""
        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="不会调到这里")
        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()

        meta = _make_meta()
        fallback_text = "test_user的今日人品是:75\n人品比昨天上升了25.0%！"

        # 子用例 A：data_store=None → event_persisted=False
        cmd.data_store = None
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                with patch("module.misc.jrrp_utils.format_jrrp_text",
                           return_value=fallback_text):
                    result_a = await cmd._handle_jrrp("U123", "", meta)
        assert result_a == []
        cmd._send.assert_called_once_with("U123", "", fallback_text)

        # 子用例 B：add_message_stream 抛异常 → event_persisted=False
        cmd._send.reset_mock()
        cmd.data_store = MagicMock()
        cmd.data_store.add_message_stream = AsyncMock(side_effect=RuntimeError("DB down"))
        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_jrrp_result):
            with patch("utils.time.get_current_date_raw"):
                with patch("module.misc.jrrp_utils.format_jrrp_text",
                           return_value=fallback_text):
                    with patch("module.persona.command.logger") as mock_logger:
                        result_b = await cmd._handle_jrrp("U123", "", meta)
        assert result_b == []
        cmd._send.assert_called_once_with("U123", "", fallback_text)
        warning_calls = [c[0][0] for c in mock_logger.warning.call_args_list]
        assert any("event_msg 持久化失败" in msg for msg in warning_calls), \
            f"应有持久化失败 warning，实际: {warning_calls}"


# ── Test: skip_scoring=True propagation ────────────────────────────────────

class TestSkipScoringPropagation:
    """skip_scoring=True 从 command.py 传播到 ChatSession.chat"""

    async def test_handle_jrrp_calls_chat_with_skip_scoring(self):
        """_handle_jrrp 通过 app.chat.chat 传入 skip_scoring=True"""
        from module.misc.jrrp_utils import JrrpResult

        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="nice")

        cmd = _make_cmd(app=app)
        cmd._send = AsyncMock()
        cmd.data_store = MagicMock()
        cmd.data_store.add_message_stream = AsyncMock()

        meta = _make_meta()
        mock_result = JrrpResult(jrrp=50, zrrp=50, delta=0, delta_percent=0.0,
                                 direction='same', is_min=False, is_max=False)

        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_result):
            with patch("utils.time.get_current_date_raw"):
                await cmd._handle_jrrp("U123", "", meta)

        # 验证 chat.chat 被调用且 skip_scoring=True
        app.chat.chat.assert_awaited_once()
        assert app.chat.chat.await_args is not None
        assert app.chat.chat.await_args.kwargs.get("skip_scoring") is True


    async def test_chat_session_skip_scoring_skips_sleep_gate(self):
        """skip_scoring=True 时绕过睡眠门控"""
        from module.persona.chat.session import ChatSession

        store = MagicMock()
        router = MagicMock()
        tool_registry = MagicMock()
        coordinator = MagicMock()
        character = MagicMock()
        config = MagicMock()

        sleep_gate = MagicMock()
        sleep_gate.is_awake = AsyncMock(return_value=False)

        scoring_trigger = MagicMock()
        response_handler = MagicMock()
        context_builder = MagicMock()

        session = ChatSession(
            store=store, router=router, tool_registry=tool_registry,
            coordinator=coordinator, character=character, config=config,
            scoring_trigger=scoring_trigger, response_handler=response_handler,
            context_builder=context_builder, sleep_gate=sleep_gate,
        )
        session._chat_via_coordinator = AsyncMock(return_value="jrrp response")

        # skip_scoring=True 时即使角色睡眠也应放行
        result = await session.chat(
            user_id="U123", group_id="", message=".jrrp", skip_scoring=True,
        )
        assert result == "jrrp response"
        # 睡眠门控不应被触发
        sleep_gate.is_awake.assert_not_awaited()


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
        tool_registry = MagicMock()
        coordinator = MagicMock()
        character = MagicMock()
        config = MagicMock()
        scoring_trigger = MagicMock()
        response_handler = MagicMock()
        context_builder = MagicMock()

        session = ChatSession(
            store=store, router=router, tool_registry=tool_registry,
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
            store=MagicMock(), router=MagicMock(), tool_registry=MagicMock(),
            coordinator=MagicMock(), character=MagicMock(), config=MagicMock(),
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
            store=MagicMock(), router=MagicMock(), tool_registry=MagicMock(),
            coordinator=MagicMock(), character=MagicMock(), config=MagicMock(),
            scoring_trigger=MagicMock(), response_handler=MagicMock(),
            context_builder=MagicMock(), sleep_gate=sleep_gate,
        )

        result = await session.is_awake()
        assert result is False


# ── Test: event_msg → LLM context 管道验证 ─────────────────────────────────

class TestJrrpLLMContext:
    """验证 _handle_jrrp 的 event_msg 真正进入 LLM 上下文（防 R1 同类回归）"""

    async def test_event_msg_persisted_as_user_before_chat(self):
        """event_msg 以 user 角色先于 chat() 写入 message_stream"""
        from module.misc.jrrp_utils import JrrpResult

        app = MagicMock()
        app.chat.chat = AsyncMock(return_value="运气不错！")

        data_store = MagicMock()
        data_store.add_message_stream = AsyncMock()

        cmd = _make_cmd(app=app, data_store=data_store)
        cmd._send = AsyncMock()

        meta = _make_meta()
        mock_result = JrrpResult(jrrp=75, zrrp=60, delta=15, delta_percent=25.0,
                                 direction='up', is_min=False, is_max=False)

        with patch("module.misc.jrrp_utils.compute_jrrp", return_value=mock_result):
            with patch("utils.time.get_current_date_raw"):
                await cmd._handle_jrrp("U123", "", meta)

        # 收集所有 add_message_stream 调用
        calls = data_store.add_message_stream.await_args_list
        call_kwargs = [c.kwargs for c in calls]

        # 验证存在一条 role="user" 且 content 包含 event_msg 核心内容
        user_calls = [k for k in call_kwargs if k.get("role") == "user"]
        assert len(user_calls) >= 1, f"应有至少一次 user 角色持久化，实际: {call_kwargs}"

        user_content = user_calls[0]["content"]
        assert "[事件]" in user_content, \
            f"event_msg 应包含 [事件] 前缀，实际: {user_content[:80]}..."
        assert "test_user 查询了今日运势" in user_content, \
            f"event_msg 应包含用户和运势信息，实际: {user_content[:80]}..."
        assert "今日: 75/100" in user_content, \
            f"event_msg 应包含今日运势值，实际: {user_content[:80]}..."
        assert "昨日: 60/100" in user_content, \
            f"event_msg 应包含昨日运势值，实际: {user_content[:80]}..."
        assert "上涨 25.0%" in user_content, \
            f"event_msg 应包含趋势方向，实际: {user_content[:80]}..."
        assert "请以角色身份就此说一两句话" in user_content, \
            f"event_msg 应包含角色指令，实际: {user_content[:80]}..."

        # 验证 chat() 在 event_msg 持久化之后调用（通过检查调用顺序）
        app.chat.chat.assert_awaited_once()
        assert app.chat.chat.await_args.kwargs.get("skip_scoring") is True

