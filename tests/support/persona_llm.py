"""Lightweight Persona LLM doubles shared through pytest fixtures."""

from __future__ import annotations

from module.persona.llm.coordinator import SubmitResult


class MockCoordinator:
    def __init__(self, simulate_buffered: bool = False):
        self.simulate_buffered = simulate_buffered

    async def submit(
        self,
        key,
        message,
        call_fn,
        continue_on_buffered=True,
        on_exhausted=None,
        on_result=None,
    ):
        del key, continue_on_buffered, on_exhausted
        messages = [] if message is None else [message]
        result = await call_fn(messages)
        if self.simulate_buffered and on_result:
            await on_result(result)
        return SubmitResult.success(result)
