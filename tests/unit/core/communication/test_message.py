"""Unit tests for MessageMetaData auto-correction invariants."""

from plugins.DicePP.core.communication.message import MessageMetaData, MessageSender


def test_private_message_auto_to_me():
    """私聊构造时传入 to_me=False，应被自动修正为 True；群聊则保持不变。"""
    # 场景 1: 私聊 + to_me=False → 自动修正为 True
    private_meta = MessageMetaData("msg", "msg", MessageSender("u", "n"), "", False)
    assert private_meta.to_me is True, "私聊消息 to_me 应被自动修正为 True"

    # 场景 2: 群聊 + to_me=False → 不修正，保持 False
    group_meta = MessageMetaData("msg", "msg", MessageSender("u", "n"), "g123", False)
    assert group_meta.to_me is False, "群聊消息 to_me=False 不应被修正"
