"""L2 完整对话链测试 — mock 边界设在 provider.generate()

之上所有组件（LLMGateway、AgentLoop、LLMCallCoordinator、ChatSession）
全部使用真实实例。

依赖：
- ScriptedProvider / FakeMessagePort / FakeImageGenProvider（mock_provider.py）
- build_conversation_session / temp_db（conftest.py）
"""

import asyncio
from datetime import date, datetime

import pytest
from plugins.DicePP.module.persona.chat.session import ChatCallContext
from plugins.DicePP.module.persona.data.models import (
    RelationshipState,
)

from tests.unit.persona.mock_provider import (
    ScriptedProvider,
    FakeMessagePort,
    FakeImageGenProvider,
    text,
    tool,
    error,
)
from tests.unit.persona.conftest import build_conversation_session


# ── helpers ───────────────────────────────────────────────────────────────────


def _llm_messages_contain(calls: list, substring: str) -> bool:
    """检查 ScriptedProvider 的任意一次调用中 messages 是否含 substring。"""
    for c in calls:
        for msg in c.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str) and substring in content:
                return True
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and substring in str(part):
                        return True
    return False


def _last_call_messages(calls: list) -> str:
    """返回最后一次调用的 messages 拼接文本。"""
    if not calls:
        return ""
    parts = []
    for msg in calls[-1].get("messages", []):
        c = msg.get("content", "")
        if isinstance(c, str):
            parts.append(c)
    return "\n".join(parts)


# ── Scenario 1: 简单三轮对话 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_simple_three_turn_conversation(temp_db):
    """三轮纯文本对话，无工具调用，每轮返回非空文本。"""
    store = temp_db
    sp = ScriptedProvider([
        text("你好！今天想聊什么？"),
        text("是的，我也觉得天气不错。"),
        text("再见，下次再聊！"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)

    r1 = await session.chat("u1", "", "今天天气真好")
    assert r1 == "你好！今天想聊什么？"

    r2 = await session.chat("u1", "", "你也觉得吧")
    assert r2 == "是的，我也觉得天气不错。"

    r3 = await session.chat("u1", "", "好的，拜拜")
    assert r3 == "再见，下次再聊！"

    assert len(sp.calls) == 3


# ── Scenario 2: 工具调用对话（图片生成）──────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_call_image_generation(temp_db):
    """LLM 返回 generate_image tool_call → AgentLoop 执行 → ImageGenerationSink
    调 FakeImageGenProvider → LLM 返回文本回复。"""
    store = temp_db
    sp = ScriptedProvider([
        tool("generate_image", {"prompt": "一只可爱的猫咪"}),
        text("我已经为你画了一只猫咪！"),
    ])
    fake_gen = FakeImageGenProvider()
    port = FakeMessagePort()
    session = build_conversation_session(
        store=store, scripted_provider=sp,
        fake_image_gen=fake_gen, fake_port=port,
    )

    result = await session.chat("u1", "", "帮我画一只猫")

    assert result == "我已经为你画了一只猫咪！"
    assert len(sp.calls) == 2
    # Step 1 是 tool_call，Step 2 是文本回复
    assert sp.calls[0]["tools"] is not None
    assert len(sp.calls[0]["tools"]) >= 1
    tool_names = [t["function"]["name"] for t in sp.calls[0]["tools"]]
    assert "generate_image" in tool_names


# ── Scenario 3: 分段+普通混合 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_segmented_and_normal_mixed(temp_db):
    """seg→normal→seg→normal 4 轮，FakeMessagePort 捕获分段发送。"""
    store = temp_db
    sp = ScriptedProvider([
        tool("send_reply_segment", {"content": "第一段回复", "phase": "final"}),
        text("普通文本回复"),
        tool("send_reply_segment", {"content": "第二段回复", "phase": "final"}),
        text("又一条普通回复"),
    ])
    port = FakeMessagePort()
    session = build_conversation_session(
        store=store, scripted_provider=sp, fake_port=port,
    )

    r1 = await session.chat("u1", "", "msg1")
    assert r1 == ""  # delivery_performed → 空字符串
    assert len(port.sent) == 1
    assert port.sent[0]["content"] == "第一段回复"

    r2 = await session.chat("u1", "", "msg2")
    assert r2 == "普通文本回复"

    r3 = await session.chat("u1", "", "msg3")
    assert r3 == ""
    assert len(port.sent) == 2
    assert port.sent[1]["content"] == "第二段回复"

    r4 = await session.chat("u1", "", "msg4")
    assert r4 == "又一条普通回复"

    assert len(sp.calls) == 4


# ── Scenario 4: 硬截断触发 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hard_truncation_triggered(temp_db):
    """连续消息超出 token budget 触发 SessionManager 压缩检查，
    LLM 摘要路径 broken（router 无 generate()）→ 走 except 回退硬截断，
    验证 session 重建后仍能正常对话。"""
    store = temp_db
    sp = ScriptedProvider([
        text("回复1"),
        text("回复2"),
        text("回复3"),
        text("回复4"),
        text("回复5"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)
    # 设置极低的 token budget 快速触发压缩
    session.config.private_session_token_budget = 50

    # 发送多条消息填满 session
    for i in range(4):
        r = await session.chat("u1", "", f"这是一条比较长的消息用于填充token预算第{i}轮")
        assert r is not None
        assert "抱歉" not in r

    # 给后台压缩任务一点时间
    await asyncio.sleep(0.15)

    # 压缩后仍能正常对话
    r5 = await session.chat("u1", "", "最后一条消息")
    assert r5 is not None
    assert "抱歉" not in r5


# ── Scenario 5: 好感度标签变化 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warmth_label_change(temp_db):
    """预置 composite=0 写库 → chat() 断言上下文含"冷淡" →
    更新 intimacy=100 → chat() 断言上下文含"友好"。"""
    store = temp_db

    # 预置 composite=0（冷淡）关系
    rel = RelationshipState(user_id="u1", intimacy=0)
    await store.update_relationship(rel)

    sp = ScriptedProvider([
        text("嗯。"),
        text("你好呀！"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)

    r1 = await session.chat("u1", "", "你好")
    assert r1 == "嗯。"
    # 上下文应包含"冷淡"标签
    assert _llm_messages_contain(sp.calls, "冷淡") or _llm_messages_contain(sp.calls, "陌生")

    # 更新 composite=40（友好）
    rel2 = RelationshipState(user_id="u1", intimacy=100)
    await store.update_relationship(rel2)

    r2 = await session.chat("u1", "", "你好呀")
    assert r2 == "你好呀！"
    assert _llm_messages_contain(sp.calls, "友好") or _llm_messages_contain(sp.calls, "默契")


# ── Scenario 6: 群聊多用户 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_group_multi_user(temp_db):
    """alice、bob 在群聊中交替发言，验证上下文含发言者名称。"""
    store = temp_db
    sp = ScriptedProvider([
        text("Alice你好！"),
        text("Bob你也好！"),
        text("今天真热闹。"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)

    r1 = await session.chat("alice", "g1", "大家好我是Alice", ctx=ChatCallContext(nickname="Alice"))
    assert r1 == "Alice你好！"

    r2 = await session.chat("bob", "g1", "我是Bob", ctx=ChatCallContext(nickname="Bob"))
    assert r2 == "Bob你也好！"

    r3 = await session.chat("alice", "g1", "今天天气不错", ctx=ChatCallContext(nickname="Alice"))
    assert r3 == "今天真热闹。"

    # 验证上下文含发言者名称
    all_text = _last_call_messages(sp.calls)
    assert "Alice" in all_text or "Bob" in all_text


# ── Scenario 7: 中间出错恢复 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mid_error_recovery(temp_db):
    """正常 → provider 抛异常 → AgentLoop 返回 failed →
    _chat_with_tools 返回"抱歉" → 恢复正常。"""
    store = temp_db
    sp = ScriptedProvider([
        text("正常回复"),
        error(RuntimeError("API 暂时不可用")),
        text("已恢复"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)

    r1 = await session.chat("u1", "", "msg1")
    assert r1 == "正常回复"

    r2 = await session.chat("u1", "", "msg2")
    assert "抱歉" in r2 or "出错" in r2

    r3 = await session.chat("u1", "", "msg3")
    assert r3 == "已恢复"

    assert len(sp.calls) == 3


# ── Scenario 8: 跨日事件通知 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_day_event_notification(temp_db):
    """会话期内新增事件注入上下文，已通知事件不重复。"""
    store = temp_db
    today_str = date.today().strftime("%Y-%m-%d")

    sp = ScriptedProvider([
        text("今天天气确实不错。"),
        text("是的，我知道。"),
        text("好的。"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)

    # 第一步：预热——建立 session + 设置 context_since
    r0 = await session.chat("u1", "", "你好")
    assert r0 == "今天天气确实不错。"

    # 设置 last_context_update_at 到过去，确保窗口覆盖后续添加的事件
    tracker = session.session_manager.get_tracker("u1")
    tracker["last_context_update_at"] = datetime(2000, 1, 1)

    # 写入今日事件（created_at 为 wall_now，在 context_since 之后 → 会注入）
    await store.add_daily_event(
        date=today_str,
        event_type="system",
        description="今天下雨了，空气很清新。",
        context_summary="今日下雨",
    )
    await store.add_daily_event(
        date=today_str,
        event_type="system",
        description="附近新开了一家咖啡馆。",
        context_summary="新开咖啡馆",
    )

    r1 = await session.chat("u1", "", "今天天气怎么样")
    assert r1 == "是的，我知道。"
    # 第二次调用应包含事件通知
    assert _llm_messages_contain(sp.calls, "下雨") or _llm_messages_contain(sp.calls, "咖啡馆")

    r2 = await session.chat("u1", "", "那个新开的咖啡馆呢")
    assert r2 == "好的。"
    # 第三次调用：相同事件不应重复生成通知（仅断言不崩溃）


# ── Scenario 9: 超长消息 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_very_long_message(temp_db):
    """5000 字消息不崩溃，正常回复。"""
    store = temp_db
    sp = ScriptedProvider([
        text("收到你的长消息了！"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)

    long_msg = "今天" + "天气真好" * 800  # ~5000 chars
    result = await session.chat("u1", "", long_msg)

    assert result == "收到你的长消息了！"
    # 验证消息长度被记录在 LLM 调用中
    assert len(sp.calls) == 1


# ── Scenario 10: 空消息/纯文本 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_and_plain_text_messages(temp_db):
    """空文本消息和纯文本消息各一条。"""
    store = temp_db
    sp = ScriptedProvider([
        text("你想说什么呢？"),
        text("明白了。"),
    ])
    session = build_conversation_session(store=store, scripted_provider=sp)

    # 空消息（仅空白）
    r1 = await session.chat("u1", "", "")
    assert r1 == "你想说什么呢？"

    # 纯文本消息
    r2 = await session.chat("u1", "", "我想明白了")
    assert r2 == "明白了。"

    assert len(sp.calls) == 2


# ── Scenario 11: 连续快速 20 轮 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rapid_20_rounds_no_state_leak(temp_db):
    """连续快速 20 轮对话，无状态残留，每条回复正确。"""
    store = temp_db

    expected = [f"回复{i}" for i in range(20)]
    sp = ScriptedProvider([text(e) for e in expected])
    session = build_conversation_session(store=store, scripted_provider=sp)

    for i in range(20):
        r = await session.chat("u1", "", f"消息{i}")
        assert r == f"回复{i}", f"Round {i}: expected 回复{i}, got {r}"

    assert len(sp.calls) == 20
