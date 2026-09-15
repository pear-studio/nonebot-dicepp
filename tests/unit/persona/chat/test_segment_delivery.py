"""Chat 正文单出口、完成协议与真实投递回执的离线回归。"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.module.persona.agent.llm_gateway import LLMGatewayResult
from plugins.DicePP.module.persona.agent.loop import AgentLoop
from plugins.DicePP.module.persona.agent.message_buffer import MessageBuffer
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.agent.runtime_types import FinishReplyArgs, ToolSpec, ToolResult
from plugins.DicePP.module.persona.chat.chat_agent import ChatAgent
from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
from plugins.DicePP.module.persona.chat.delivery_queue import DeliveryItem, DeliveryQueue
from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult
from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope


def segment(sid, text):
    return ("send_reply_segment", {"segment_id": sid, "content": text})


def finish(**args):
    return ("finish_reply", args)


@pytest.fixture
def run_reply(monkeypatch):
    monkeypatch.setattr("plugins.DicePP.module.persona.chat.delivery_queue._MIN_JITTER", 0)
    monkeypatch.setattr("plugins.DicePP.module.persona.chat.delivery_queue._MAX_JITTER", 0)
    monkeypatch.setattr("plugins.DicePP.module.persona.chat.delivery_queue._ORDERING_GRACE", 0)

    async def run(turns, *, results=None, max_chars=80):
        store = MagicMock(add_message_stream=AsyncMock(side_effect=range(1, 30)))
        port = MagicMock(send=AsyncMock(side_effect=results, return_value=True))
        delivery = DeliveryQueue(port=port, store=store)
        conv = MagicMock(spec=Conversation)
        conv.append_ref = AsyncMock()
        conv.save = AsyncMock()
        conv.get_messages.return_value = []
        life = MagicMock()
        life.config.enabled = False
        config = ChatConfig(timezone="Asia/Shanghai", max_history_turns=20,
                            max_history_tokens=8000, lore_token_budget=1000,
                            segment_max_chars=max_chars)
        agent = ChatAgent(
            scope=ConversationScope.for_group("g1"), conversation=conv,
            store=store, client=MagicMock(), character=MagicMock(character_id="test", name="七七"),
            config=config, context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            action_evaluator=MagicMock(), character_life=life, make_delivery=lambda: delivery,
        )
        responses = [LLMGatewayResult(
            content="", tool_calls=[
                {"id": f"call-{r}-{i}", "name": name, "arguments": json.dumps(args)}
                for i, (name, args) in enumerate(calls)
            ], usage={}, provider="fake", model="fake", finish_reason="tool_calls",
        ) for r, calls in enumerate(turns)]
        gateway = MagicMock(complete=AsyncMock(side_effect=responses))
        loop = AgentLoop(llm_gateway=gateway)
        loop_results = []

        async def conv_run(**kwargs):
            iid = kwargs["interaction_id"]
            kwargs["tools"].tools["lookup"] = ToolSpec(
                name="lookup", description="查询", args_schema=FinishReplyArgs,
                handler=AsyncMock(return_value=ToolResult(observation="33")),
            )
            result = await loop.run(
                buffer=MessageBuffer(), state=AgentRunState("run1", iid, "u1", "g1"),
                toolkit=kwargs["tools"], output_spec=kwargs["output"],
                limits=kwargs["limits"], interaction_id=iid,
            )
            loop_results.append(result)
            return ConversationRunResult(
                run_id=result.run_id, completion_kind=result.completion.kind,
                final_reason=result.completion.code,
                output_arguments=dict(result.output.arguments) if result.output else None,
                output_call_index=result.output.call_index if result.output else None,
            )

        conv.run = AsyncMock(side_effect=conv_run)
        outcome = await agent.execute_turn("u1", "g1", ".jrrp")
        recorded = [c.kwargs["content"] for c in store.add_message_stream.await_args_list]
        assert [c.args for c in conv.append_ref.await_args_list] == [
            (i, "assistant") for i in range(1, len(recorded) + 1)
        ]
        assert delivery.sent_contents == recorded
        assert delivery._worker_task is None or delivery._worker_task.done()
        return outcome, recorded, port, loop_results[0], store
    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_output", [finish(content="今天是33。祝你好运。"),
                                        ("send_reply", {"content": "今天是33。祝你好运。"})])
async def test_aggregate_output_is_rejected_without_resending(run_reply, bad_output):
    outcome, recorded, port, result, _ = await run_reply([
        [("lookup", {}), segment("s1", "今天是33。"), segment("s2", "祝你好运。")],
        [bad_output], [finish()],
    ])
    assert recorded == ["今天是33。", "祝你好运。"]
    assert (outcome.status, outcome.sent_count) == ("sent", 2)
    assert port.send.await_count == 2
    assert result.output.arguments == {}


@pytest.mark.asyncio
async def test_legitimate_prefix_and_repeated_text_are_preserved(run_reply):
    outcome, recorded, _, _, store = await run_reply([
        [("lookup", {}), segment("s1", "好")],
        [segment("s2", "好吧，我们继续"), segment("s3", "好"), finish()],
    ])
    assert recorded == ["好", "好吧，我们继续", "好"]
    assert outcome.status == "sent"
    assert [c.kwargs["segment_index"] for c in store.add_message_stream.await_args_list] == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry, expected_status, expected", [
    (False, "partial_sent", ["A", "C"]),
    (True, "sent", ["A", "C", "B"]),
])
async def test_partial_failure_retries_only_failed_identity(run_reply, retry, expected_status, expected):
    turns = [[segment("a", "A"), segment("b", "B"), segment("c", "C")]]
    turns += [[segment("b", "B"), segment("c", "C"), finish()]] if retry else [[finish()]]
    outcome, recorded, port, _, _ = await run_reply(turns, results=[True, False, True, True])
    assert recorded == expected
    assert outcome.status == expected_status
    assert [c.kwargs["content"] for c in port.send.await_args_list] == (["A", "B", "C", "B"] if retry else ["A", "B", "C"])


@pytest.mark.asyncio
@pytest.mark.parametrize("calls, results, expected_status, expected", [
    ([segment("s1", "一次回复"), finish()], [True], "sent", ["一次回复"]),
    ([segment("s1", "一次回复"), finish()], [False], "failed", []),
    ([segment("s1", "一次回复"), finish()], [None], "delivery_unknown", []),
    ([segment("s1", "x" * 81), finish()], [], "failed", []),
    ([finish()], [], "empty", []),
])
async def test_finish_does_not_mask_delivery_result(run_reply, calls, results, expected_status, expected):
    outcome, recorded, _, _, _ = await run_reply([calls], results=results)
    assert (outcome.status, recorded) == (expected_status, expected)


@pytest.mark.asyncio
async def test_queue_timeout_is_a_failed_receipt_and_cannot_send_later(monkeypatch):
    monkeypatch.setattr("plugins.DicePP.module.persona.chat.delivery_queue._SEND_TIMEOUT", 0.01)
    attempted = asyncio.Event()
    released = asyncio.Event()
    delivered = []
    async def send(**kwargs):
        attempted.set()
        await released.wait()
        delivered.append(kwargs["content"])
        return True
    store = MagicMock(add_message_stream=AsyncMock())
    queue = DeliveryQueue(port=MagicMock(send=send), store=store)
    receipt = queue.enqueue(DeliveryItem("A", "i1", 0, "interim", "u1", "g1"))
    await attempted.wait()
    assert await receipt is None
    await queue.drain()
    await queue.shutdown()
    released.set()
    await asyncio.sleep(0)
    assert delivered == []
    assert queue.failed_count == 1
    store.add_message_stream.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    ("send_reply_segment", {"content": "缺少 id"}),
    segment("s2", "x" * 81),
])
async def test_finish_waits_for_tool_error_correction(run_reply, invalid):
    outcome, recorded, _, result, _ = await run_reply([
        [segment("s1", "A"), invalid, finish()],
        [segment("s2", "已纠正"), finish()],
    ])
    assert (outcome.status, recorded) == ("sent", ["A", "已纠正"])
    finish_observations = [m["content"] for m in result.message_delta
                           if m.get("role") == "tool" and m.get("tool_call_id") == "call-0-2"]
    assert len(finish_observations) == 1
    assert "完成信号未接受" in finish_observations[0]


@pytest.mark.asyncio
async def test_unknown_delivery_does_not_repeat_at_remote_on_same_id_retry(monkeypatch):
    from plugins.DicePP.module.persona.chat.reply_session import ReplySession
    monkeypatch.setattr("plugins.DicePP.module.persona.chat.delivery_queue._SEND_TIMEOUT", 0.01)
    remote = []
    async def send(**kwargs):
        remote.append(kwargs["content"])
        await asyncio.Event().wait()  # 已送达，但回执未返回
    queue = DeliveryQueue(port=MagicMock(send=send), store=MagicMock())
    session = ReplySession(queue, 2)
    try:
        first = await session.send("s1", DeliveryItem("A", "i1", 0, "interim", "u1", "g1"))
        retry = await session.send("s1", DeliveryItem("A", "i1", 1, "interim", "u1", "g1"))
        assert (first.status, retry.status, remote) == ("error", "error", ["A"])
        assert session.failed_count == 1
    finally:
        await queue.shutdown()


@pytest.mark.asyncio
async def test_drain_timeout_closes_pending_delivery(monkeypatch):
    monkeypatch.setattr("plugins.DicePP.module.persona.chat.delivery_queue._DRAIN_TIMEOUT", 0.01)
    entered = asyncio.Event()
    async def send(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    queue = DeliveryQueue(port=MagicMock(send=send), store=MagicMock())
    receipt = queue.enqueue(DeliveryItem("A", "i1", 0, "interim", "u1", "g1"))
    await entered.wait()
    with pytest.raises(TimeoutError, match="队列已关闭"):
        await queue.drain()
    assert await receipt is None  # 发送中取消，不能误认为肯定未发出
    assert queue._worker_task.done()
    assert await queue.enqueue(DeliveryItem("B", "i1", 1, "interim", "u1", "g1")) is False


@pytest.mark.asyncio
async def test_finalize_settles_unknown_receipt_after_waiter_cancellation(monkeypatch):
    from plugins.DicePP.module.persona.chat.reply_session import ReplySession
    monkeypatch.setattr("plugins.DicePP.module.persona.chat.delivery_queue._DRAIN_TIMEOUT", 0.01)
    entered = asyncio.Event()
    async def send(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    queue = DeliveryQueue(port=MagicMock(send=send), store=MagicMock())
    session = ReplySession(queue, 2)
    waiter = asyncio.create_task(session.send("s1", DeliveryItem("A", "i1", 0, "interim", "u1", "g1")))
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    outcome = await ChatAgent._finalize_turn(
        MagicMock(), conv=MagicMock(), delivery=queue, reply_session=session,
        result=ConversationRunResult(completion_kind="failed", final_reason="cancelled"),
    )
    assert outcome.status == "delivery_unknown"
    assert session.uncertain_count == 1
    assert queue._worker_task.done()
