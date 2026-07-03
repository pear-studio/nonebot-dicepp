"""create_persona 成功路径 smoke 测试

覆盖 Round 4 R1 第 7 步：工具注册表在组装时不抛异常，
且工具（read_history / search_history / roll_dice 等）正确注册到 chat 域。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.factory import create_persona, _startup_summary
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.tools.registry import ToolDomain


def _make_bot() -> MagicMock:
    """构造可完整走通 create_persona 的最小 Bot mock"""
    bot = MagicMock()
    cfg = MagicMock()
    cfg.enabled = True
    cfg.character_path = "/tmp/chars"

    # 使用新 providers 结构
    provider = MagicMock()
    provider.api_key = "sk-test"
    provider.base_url = "https://api.example.com"
    provider.max_concurrent = None
    model = MagicMock()
    model.name = "test-model"
    model.category = "llm"
    model.capabilities = ["text", "tool_calls"]
    model.quality = 0.9
    model.cost = 0.5
    model.circuit_breaker = None
    provider.models = [model]
    cfg.providers = {"openai": provider}

    cfg.max_concurrent_requests = 2
    cfg.chat_llm_timeout_seconds = 30
    cfg.daily_limit = 20
    cfg.quota_check_enabled = False
    cfg.trace_enabled = False
    cfg.trace_max_age_days = 7
    cfg.max_short_term_chars = 1500
    cfg.timezone = "Asia/Shanghai"
    cfg.lore_token_budget = 300
    cfg.group_activity_decay_per_day = 10.0
    cfg.group_activity_floor_whitelist = 50.0
    cfg.group_max_messages = 40
    cfg.search_max_chars = 2000
    cfg.character_life_enabled = True
    cfg.character_life_jitter_minutes = 15
    cfg.character_life_min_event_interval_minutes = 5
    cfg.character_life_chain_max_depth = 3
    cfg.character_life_chain_force_extend_once_prob = 0.0
    cfg.character_life_default_energy = 50
    cfg.character_life_default_mood = 50
    cfg.character_life_default_health = 50
    cfg.character_life_recovery_energy = 20
    cfg.character_life_diary_time = "23:30"
    cfg.proactive_coordinator_max_failures = 3
    cfg.proactive_coordinator_max_iterations = 5
    cfg.proactive_event_share_threshold = 0.6
    cfg.proactive_event_share_delay_min = 1
    cfg.proactive_event_share_delay_max = 5
    cfg.proactive_share_max_retries = 2
    cfg.suggest_action_min_relationship = 40
    cfg.suggest_action_evaluation_timeout = 30
    cfg.proactive_always_send_users = []
    cfg.proactive_always_send_groups = []
    cfg.group_activity_min_threshold = 0.0
    cfg.decay_enabled = True
    cfg.decay_grace_period_hours = 8
    cfg.decay_rate_per_hour = 0.5
    cfg.decay_daily_cap = 5.0
    bot.config.persona_ai = cfg
    bot.config.persona = "test"
    bot.account = "test_bot"
    bot.db = MagicMock()
    bot.db._db = MagicMock()  # 满足 PersonaDataStore 的 core_db 参数
    return bot


@pytest.mark.asyncio
async def test_create_persona_success_registers_tools(monkeypatch):
    """create_persona 成功组装后，chat 域命中工具定义"""
    bot = _make_bot()

    character = Character(
        name="小骰",
        description="测试角色",
        extensions=PersonaExtensions(),
    )

    class FakeLoader:
        def __init__(self, path):
            pass

        def load(self, name):
            return character

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLoader",
        FakeLoader,
    )

    mock_router_cls = MagicMock()
    mock_router_cls.return_value.probe_all_models = AsyncMock(return_value={})
    mock_router_cls.return_value.all_providers_disabled = MagicMock(return_value=False)
    mock_router_cls.return_value.start_probe_task = MagicMock()
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.LLMRouter",
        mock_router_cls,
    )

    class FakeStore:
        def __init__(self, persona_db_path, core_db, **kwargs):
            self._persona_db_path = persona_db_path
            self._core_db = core_db
            self._persona_db = None

        async def open(self):
            pass

        async def ensure_tables(self):
            pass

        async def get_setting(self, key):
            return None

        async def get_global_setting(self, key):
            return None

        async def add_message_stream(self, **kwargs):
            pass

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.PersonaDataStore",
        FakeStore,
    )

    # 跳过持久化状态加载（不需要真实数据库）
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.ProactiveScheduler.load_persistent_state",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLife.load_persistent_state",
        AsyncMock(),
    )

    app = await create_persona(bot)

    assert app.get_character().name == "小骰"

    definitions = app.chat.tool_registry.get_definitions_for(ToolDomain.CHAT)
    names = {d["function"]["name"] for d in definitions}

    assert "read_history" in names, f"缺失 read_history，实际注册: {names}"
    assert "search_history" in names, f"缺失 search_history，实际注册: {names}"
    assert "read_profile" in names, f"缺失 read_profile，实际注册: {names}"
    assert "read_diary" in names, f"缺失 read_diary，实际注册: {names}"
    assert "search_diary" in names, f"缺失 search_diary，实际注册: {names}"
    assert "roll_dice" in names, f"缺失 roll_dice，实际注册: {names}"
    assert "send_reply_segment" in names, f"缺失 send_reply_segment，实际注册: {names}"
    assert "suggest_action" in names, f"缺失 suggest_action，实际注册: {names}"

    # R6: 跨组件引用一致性
    assert app.chat._response_handler.port is app.port
    assert app.life.scheduler.character.name == "小骰"
    assert app.life.character_life.character.name == "小骰"


@pytest.mark.asyncio
async def test_startup_summary_disabled_models_show_off_not_fail(monkeypatch):
    """disabled 的 provider / model 在启动报告里标记 [OFF]，不污染 [FAIL]。

    背景：probe_results 不会包含 disabled key（_build_providers 已过滤），
    启动报告必须基于 enabled 状态主动判定 DISABLED，而非把缺失等同于 FAIL。
    """
    character = Character(name="小骰", description="测试", extensions=PersonaExtensions())

    # provider A：完全 enabled
    p_a = MagicMock()
    p_a.enabled = True
    m_a1 = MagicMock(); m_a1.name = "m-on"; m_a1.category = "llm"
    m_a1.capabilities = ["text"]; m_a1.thinking = False; m_a1.enabled = True
    m_a2 = MagicMock(); m_a2.name = "m-off"; m_a2.category = "llm"
    m_a2.capabilities = ["text"]; m_a2.thinking = False; m_a2.enabled = False
    p_a.models = [m_a1, m_a2]

    # provider B：整 provider 禁用
    p_b = MagicMock()
    p_b.enabled = False
    m_b = MagicMock(); m_b.name = "m-b"; m_b.category = "llm"
    m_b.capabilities = ["text"]; m_b.thinking = False; m_b.enabled = True
    p_b.models = [m_b]

    # provider C：含一个图像生成模型（disabled）
    p_c = MagicMock()
    p_c.enabled = True
    m_c1 = MagicMock(); m_c1.name = "g-on"; m_c1.category = "gen"
    m_c1.capabilities = ["image"]; m_c1.thinking = False; m_c1.enabled = True
    m_c2 = MagicMock(); m_c2.name = "g-off"; m_c2.category = "gen"
    m_c2.capabilities = ["image"]; m_c2.thinking = False; m_c2.enabled = False
    p_c.models = [m_c1, m_c2]

    providers = {"A": p_a, "B": p_b, "C": p_c}
    # probe_results 不含 disabled key（与 _build_providers 行为一致）
    probe_results = {("A", "m-on"): True, ("C", "g-on"): True}

    infra = MagicMock()
    bot = MagicMock()
    bot.config.master = ["master-1"]

    sent_messages: list = []
    infra.port.send = AsyncMock(side_effect=lambda *a, **kw: sent_messages.append(a))

    log_lines: list = []
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.logger.info",
        lambda msg, *a, **kw: log_lines.append(msg),
    )

    await _startup_summary(character, providers, probe_results, infra, bot)
    log_text = "\n".join(log_lines)

    # 文本符号替换：不应再出现 emoji
    assert "✅" not in log_text, f"启动报告残留 emoji: {log_text}"
    assert "❌" not in log_text, f"启动报告残留 emoji: {log_text}"

    # enabled 模型 probe 成功 → [OK]
    assert "[OK] A/m-on" in log_text
    assert "[OK] C/g-on" in log_text

    # disabled 模型 → [OFF]，绝不能误标 [FAIL]
    assert "[OFF] A/m-off" in log_text
    assert "[OFF] B/m-b" in log_text
    assert "[OFF] C/g-off" in log_text
    assert "[FAIL]" not in log_text, (
        f"disabled 模型被误标为 FAIL，日志:\n{log_text}"
    )

    # 计数分母：(X 可用 / Y 失败 / Z 总计) 三列
    assert "LLM 模型 (1 可用 / 0 失败 / 3 总计):" in log_text
    assert "图像生成模型 (1 可用 / 0 失败 / 2 总计):" in log_text

    # master 消息：只列 [OK]，不含 disabled
    assert len(sent_messages) == 1
    master_msg = sent_messages[0][2]
    assert "A/m-on" in master_msg
    assert "C/g-on" in master_msg
    assert "A/m-off" not in master_msg
    assert "B/m-b" not in master_msg
    assert "C/g-off" not in master_msg


@pytest.mark.asyncio
async def test_startup_summary_probe_failure_marked_fail_not_off(monkeypatch):
    """enabled 但 probe 失败的模型必须显示 [FAIL]，避免 [OFF] 误用掩盖真实故障。"""
    character = Character(name="小骰", description="测试", extensions=PersonaExtensions())

    p = MagicMock()
    p.enabled = True
    m = MagicMock(); m.name = "m-fail"; m.category = "llm"
    m.capabilities = ["text"]; m.thinking = False; m.enabled = True
    p.models = [m]

    providers = {"A": p}
    probe_results = {("A", "m-fail"): False}

    infra = MagicMock()
    bot = MagicMock()
    bot.config.master = ["master-1"]
    sent_messages: list = []
    infra.port.send = AsyncMock(side_effect=lambda *a, **kw: sent_messages.append(a))

    log_lines: list = []
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.logger.info",
        lambda msg, *a, **kw: log_lines.append(msg),
    )

    await _startup_summary(character, providers, probe_results, infra, bot)
    log_text = "\n".join(log_lines)

    assert "[FAIL] A/m-fail" in log_text
    assert "[OFF]" not in log_text
    assert "LLM 模型 (0 可用 / 1 失败 / 1 总计):" in log_text

    assert len(sent_messages) == 1
    master_msg = sent_messages[0][2]
    # 失败模型不进 master 可用列表
    available_line = next(
        (line for line in master_msg.splitlines() if line.startswith("可用 LLM:")), ""
    )
    assert "A/m-fail" not in available_line
    # 但进 FAIL 列表
    assert "不可用 (probe 失败): A/m-fail" in master_msg
    # 全失败告警
    assert "[ALERT] 所有 1 个 LLM 模型 probe 失败" in master_msg


@pytest.mark.asyncio
async def test_startup_summary_mixed_ok_fail_disabled(monkeypatch):
    """三态混合：OK + FAIL + DISABLED 同时出现，验证计数与 master 分段正确。"""
    character = Character(name="小骰", description="测试", extensions=PersonaExtensions())

    # provider A：1 OK + 1 model 禁用
    p_a = MagicMock()
    p_a.enabled = True
    m_a_ok = MagicMock(); m_a_ok.name = "m-ok"; m_a_ok.category = "llm"
    m_a_ok.capabilities = ["text"]; m_a_ok.thinking = False; m_a_ok.enabled = True
    m_a_off = MagicMock(); m_a_off.name = "m-off"; m_a_off.category = "llm"
    m_a_off.capabilities = ["text"]; m_a_off.thinking = False; m_a_off.enabled = False
    p_a.models = [m_a_ok, m_a_off]

    # provider B：1 FAIL
    p_b = MagicMock()
    p_b.enabled = True
    m_b = MagicMock(); m_b.name = "m-fail"; m_b.category = "llm"
    m_b.capabilities = ["text"]; m_b.thinking = False; m_b.enabled = True
    p_b.models = [m_b]

    # provider C：整 provider 禁用（包含一个 enabled model）
    p_c = MagicMock()
    p_c.enabled = False
    m_c = MagicMock(); m_c.name = "m-c"; m_c.category = "llm"
    m_c.capabilities = ["text"]; m_c.thinking = False; m_c.enabled = True
    p_c.models = [m_c]

    # provider D：gen 1 OK + 1 model 禁用
    p_d = MagicMock()
    p_d.enabled = True
    m_d_ok = MagicMock(); m_d_ok.name = "g-ok"; m_d_ok.category = "gen"
    m_d_ok.capabilities = ["image"]; m_d_ok.thinking = False; m_d_ok.enabled = True
    m_d_off = MagicMock(); m_d_off.name = "g-off"; m_d_off.category = "gen"
    m_d_off.capabilities = ["image"]; m_d_off.thinking = False; m_d_off.enabled = False
    p_d.models = [m_d_ok, m_d_off]

    providers = {"A": p_a, "B": p_b, "C": p_c, "D": p_d}
    probe_results = {("A", "m-ok"): True, ("B", "m-fail"): False, ("D", "g-ok"): True}

    infra = MagicMock()
    bot = MagicMock()
    bot.config.master = ["master-1"]
    sent_messages: list = []
    infra.port.send = AsyncMock(side_effect=lambda *a, **kw: sent_messages.append(a))

    log_lines: list = []
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.logger.info",
        lambda msg, *a, **kw: log_lines.append(msg),
    )

    await _startup_summary(character, providers, probe_results, infra, bot)
    log_text = "\n".join(log_lines)

    # 三种前缀同时出现
    assert "[OK] A/m-ok" in log_text
    assert "[FAIL] B/m-fail" in log_text
    assert "[OFF] A/m-off" in log_text
    assert "[OFF] C/m-c" in log_text
    assert "[OK] D/g-ok" in log_text
    assert "[OFF] D/g-off" in log_text

    # LLM 计数：1 OK + 1 FAIL + 2 DISABLED（total 4）
    assert "LLM 模型 (1 可用 / 1 失败 / 4 总计):" in log_text
    # gen 计数：1 OK + 0 FAIL + 1 DISABLED（total 2）
    assert "图像生成模型 (1 可用 / 0 失败 / 2 总计):" in log_text

    assert len(sent_messages) == 1
    master_msg = sent_messages[0][2]
    # 可用列表只含 OK
    assert "可用 LLM: A/m-ok" in master_msg
    assert "可用图像生成: D/g-ok" in master_msg
    # FAIL 列表
    assert "不可用 (probe 失败): B/m-fail" in master_msg
    # disabled 不进 master
    assert "A/m-off" not in master_msg
    assert "C/m-c" not in master_msg
    assert "D/g-off" not in master_msg
    # 还有可用模型，不发全失败告警
    assert "[ALERT]" not in master_msg


@pytest.mark.asyncio
async def test_startup_summary_no_master_skips_send(monkeypatch):
    """master_ids 为空时早退，infra.port.send 调用 0 次。"""
    character = Character(name="小骰", description="测试", extensions=PersonaExtensions())

    p = MagicMock()
    p.enabled = True
    m = MagicMock(); m.name = "m"; m.category = "llm"
    m.capabilities = ["text"]; m.thinking = False; m.enabled = True
    p.models = [m]
    providers = {"A": p}
    probe_results = {("A", "m"): True}

    infra = MagicMock()
    bot = MagicMock()
    bot.config.master = []  # 无 master
    infra.port.send = AsyncMock()

    log_lines: list = []
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.logger.info",
        lambda msg, *a, **kw: log_lines.append(msg),
    )

    await _startup_summary(character, providers, probe_results, infra, bot)

    # 日志照常输出（与 master 消息解耦）
    assert any("Persona AI 启动报告" in line for line in log_lines)
    # 但 master send 0 次
    infra.port.send.assert_not_called()


@pytest.mark.asyncio
async def test_startup_summary_extra_probe_results_keys_ignored(monkeypatch):
    """probe_results 含 enabled model 不存在的多余 key 时，不影响 OK 状态判定。"""
    character = Character(name="小骰", description="测试", extensions=PersonaExtensions())

    p = MagicMock()
    p.enabled = True
    m = MagicMock(); m.name = "m"; m.category = "llm"
    m.capabilities = ["text"]; m.thinking = False; m.enabled = True
    p.models = [m]
    providers = {"A": p}

    # 多余的 key 来自陈旧配置 / 之前版本残留
    probe_results = {
        ("A", "m"): True,
        ("A", "deleted-model"): False,
        ("OLD_PROVIDER", "old-model"): False,
    }

    infra = MagicMock()
    bot = MagicMock()
    bot.config.master = ["master-1"]
    sent_messages: list = []
    infra.port.send = AsyncMock(side_effect=lambda *a, **kw: sent_messages.append(a))

    log_lines: list = []
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.logger.info",
        lambda msg, *a, **kw: log_lines.append(msg),
    )

    await _startup_summary(character, providers, probe_results, infra, bot)
    log_text = "\n".join(log_lines)

    # A/m 仍为 OK
    assert "[OK] A/m" in log_text
    assert "LLM 模型 (1 可用 / 0 失败 / 1 总计):" in log_text
    # 错配 key 不影响
    assert "deleted-model" not in log_text
    assert "OLD_PROVIDER" not in log_text
