"""Lightweight Persona LLM doubles shared through pytest fixtures."""

from __future__ import annotations

from plugins.DicePP.module.persona.llm.coordinator import SubmitResult


class MockCoordinator:
    def __init__(self, simulate_buffered: bool = False):
        self.simulate_buffered = simulate_buffered

    async def submit(
        self,
        key,
        message,
        call_fn,
        on_exhausted=None,
        on_result=None,
    ):
        del key, on_exhausted
        messages = [message]
        result = await call_fn(messages)
        if self.simulate_buffered and on_result:
            await on_result(result)
        return SubmitResult.success(result)
