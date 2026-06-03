"""
共享测试 helper（autouse 注入）。

为何用 autouse fixture 把 helper 挂到 self 上：
1. 避免每个测试方法签名加 6 个 fixture 参数；
2. 与 IsolatedAsyncioTestCase 实例化模型兼容（unittest 风格历史继承）；
3. pytest 哲学倾向显式 fixture，但本目录测试历史继承自 unittest，autouse 注入是渐进过渡。

后续若 test_command.py / test_command_edge.py 改写为纯函数式 pytest 测试，
可移除 autouse 并改用显式 fixture 参数或基类继承方案 A。
"""

"""Shared fixtures for persona integration tests (PersonaCommand suite).

把 test_command.py / test_command_edge.py 中重复的 helper 抽到这里。

提供两种使用方式：
- 函数：直接调用 `make_group_meta(...)` 等模块级函数（脚本/裸函数测试用）
- fixture：通过 autouse fixture 注入到 IsolatedAsyncioTestCase 的 `self` 上
  （如 `self.make_group_meta(...)`），无需手动声明依赖
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.command import PersonaCommand
from core.communication import MessageMetaData, MessageSender


# --------------------------------------------------------------------------- #
# Helpers — module-level functions, also re-exported via autouse fixture below
# --------------------------------------------------------------------------- #

def make_group_meta(msg: str, user_id: str = "user", nickname: str = "测试用户",
                    group_id: str = "group", to_me: bool = False) -> MessageMetaData:
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)


def make_private_meta(msg: str, user_id: str = "user", nickname: str = "测试用户") -> MessageMetaData:
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)


def default_persona_config():
    from plugins.DicePP.core.config.pydantic_models import PersonaConfig, ProviderConfig, ModelConfig
    return PersonaConfig(
        enabled=True,
        character_name="test_char",
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
        group_activity_enabled=False,
        trace_enabled=False,
        whitelist_enabled=True,
        daily_limit=100,
        quota_check_enabled=False,
        relationship_refuse_enabled=False,
        decay_enabled=False,
        proactive_enabled=False,
        character_life_enabled=False,
        group_chat_enabled=False,
    )


def make_mock_bot(persona_config=None):
    bot = MagicMock()
    bot.config.persona_ai = persona_config or default_persona_config()
    bot.config.admin = []
    bot.config.master = ["master_user"]
    bot.account = "test_bot"
    return bot


def make_cmd(bot=None, enabled=True):
    bot = bot or make_mock_bot()
    cmd = PersonaCommand(bot)
    cmd.enabled = enabled
    cmd.config = bot.config.persona_ai
    cmd._register_admin_handlers()
    return cmd


def get_sent_content(cmd) -> str:
    """从 mock 的 _send 调用中提取发送的消息内容"""
    if cmd._send.call_args is None:
        return ""
    args = cmd._send.call_args[0]
    return args[2] if len(args) > 2 else ""


# --------------------------------------------------------------------------- #
# Autouse fixture: 把 helper 函数注入到 unittest 风格的 TestCase 实例上
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _inject_persona_helpers(request):
    """把 helper 函数挂到 IsolatedAsyncioTestCase 的 self 上。

    pure-pytest 风格的函数测试不受影响（request.instance 为 None 时直接返回）。

    注意：直接赋值普通函数而非 staticmethod —— 实例属性上的 staticmethod
    描述符不会通过描述符协议解析，而普通函数赋给实例属性时不会绑定 self，
    调用方式与直接调用模块级函数一致。
    """
    inst = getattr(request, "instance", None)
    if inst is None:
        return
    inst.make_group_meta = make_group_meta
    inst.make_private_meta = make_private_meta
    inst.default_persona_config = default_persona_config
    inst.make_mock_bot = make_mock_bot
    inst.make_cmd = make_cmd
    inst.get_sent_content = get_sent_content
