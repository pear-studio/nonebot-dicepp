"""Scripted Chat runs that deliver fixture replies through the real segment tool."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunResult, RunOutput, ToolExecutionContext,
)
from plugins.DicePP.module.persona.life.conversation import ConversationRunResult


class CaptureDelivery:
    """Receipt-producing queue fake; optional stream ids model persistence."""

    def __init__(self, sent_stream_ids=None, *, succeeds=True):
        self.items = []
        self.sent_count = 0
        self.sent_contents = []
        self.sent_stream_ids = []
        self._stream_ids = list(sent_stream_ids or [])
        self._succeeds = succeeds

    def enqueue(self, item):
        self.items.append(item)
        if self._succeeds:
            self.sent_count += 1
            self.sent_contents.append(item.content)
            if self._stream_ids:
                self.sent_stream_ids.append(self._stream_ids.pop(0))
        receipt = asyncio.get_running_loop().create_future()
        receipt.set_result(self._succeeds)
        return receipt

    async def drain(self):
        pass

    async def shutdown(self):
        pass


class ChatRunMock(AsyncMock):
    """Treat result text as a scripted reply, then return a bodyless finish.

    Fixture return values describe what the model should say. They are never
    returned to ChatAgent with a content output: this mock executes the actual
    registered tool and preserves the fixture's completion/failure metadata.
    AsyncMock side effects and assertion APIs remain available to lifecycle tests.
    """

    async def _execute_mock_call(self, *args, **kwargs):
        result = await super()._execute_mock_call(*args, **kwargs)
        if isinstance(result, ConversationRunResult):
            completed = result.completion_kind == "completed"
            body = (result.output_arguments or {}).get("content", result.final_text)
            has_finish = result.output_arguments is not None
            toolkit = kwargs["tools"]
        elif isinstance(result, AgentRunResult):
            completed = result.completion.kind == "completed"
            body = ((result.output.arguments or {}).get("content", result.output.text)
                    if result.output else None)
            has_finish = result.output is not None
            toolkit = args[0].tools
        else:
            return result
        if completed and body:
            tool = toolkit.tools["send_reply_segment"]
            await tool.handler(
                tool.args_schema(content=body, segment_id="scripted-reply"),
                ToolExecutionContext(
                    run_id="scripted", tool_call_id="scripted-send",
                    call_index=0, same_name_index=0,
                ),
            )
        if isinstance(result, ConversationRunResult):
            return replace(result, final_text="", output_arguments={} if has_finish else None)
        return replace(result, output=RunOutput(arguments={}) if has_finish else None)
