"""ChatSession 模块测试 — T6 重构后 ChatSession 仍存在但 ChatOrchestrator 已是主路径"""
import pytest
from unittest.mock import Mock


class TestChatSessionImports:
    """验证 ChatSession 模块可正常导入且关键类存在"""

    def test_chat_session_import(self):
        from plugins.DicePP.module.persona.chat.session import ChatSession
        assert ChatSession is not None

    def test_chat_config_import(self):
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        assert ChatConfig is not None

    def test_orchestrator_import(self):
        from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator
        assert ChatOrchestrator is not None

    def test_context_builder_import(self):
        from plugins.DicePP.module.persona.chat.context import ContextBuilder
        assert ContextBuilder is not None

    def test_delivery_queue_import(self):
        from plugins.DicePP.module.persona.chat.delivery_queue import DeliveryQueue
        assert DeliveryQueue is not None


class TestChatConfigDefaults:
    """ChatConfig 默认值测试"""

    def test_default_config_creation(self):
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        config = ChatConfig()
        assert config is not None
