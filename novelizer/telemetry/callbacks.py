from __future__ import annotations
import time
from typing import Any
from uuid import UUID
from langchain_core.callbacks import AsyncCallbackHandler
from novelizer.run_context import current_run_id, current_agent_name
from novelizer.telemetry.events import (
    TelemetryEventType, LlmCallStarted, LlmCallFinished, LlmCallFailed, TokenDelta,
)


def render_messages(messages) -> str:
    """Render LangChain chat batches to the trace's prompt text: one
    [role] header per message, content stringified."""
    parts = []
    for batch in messages:
        for m in batch:
            parts.append(f"[{m.type}]\n{m.content}")
    return "\n\n".join(parts)


class _CallState:
    __slots__ = ("novelizer_run_id", "agent_name", "call_index", "model", "started", "chunks")

    def __init__(self, novelizer_run_id: str, agent_name: str, call_index: int, model: str) -> None:
        self.novelizer_run_id = novelizer_run_id
        self.agent_name = agent_name
        self.call_index = call_index
        self.model = model
        self.started = time.monotonic()
        self.chunks = 0


class TelemetryCallbackHandler(AsyncCallbackHandler):
    """Bridges LangChain model callbacks to telemetry: call_started with the
    full rendered prompt, per-token bus deltas, call_finished/failed with
    vitals. LangChain's own run_id (a UUID per call) keys in-flight state;
    the novelizer run identity is read from run_context at call start."""

    def __init__(self, recorder) -> None:
        self._recorder = recorder
        self._calls: dict[UUID, _CallState] = {}

    async def on_chat_model_start(self, serialized: dict, messages, *, run_id: UUID, **kwargs: Any) -> None:
        await self._start(serialized, render_messages(messages), run_id)

    async def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        await self._start(serialized, "\n\n".join(prompts), run_id)

    async def _start(self, serialized: dict, prompt: str, lc_run_id: UUID) -> None:
        nrun = current_run_id.get() or ""
        skw = (serialized or {}).get("kwargs", {})
        model = skw.get("model_name") or skw.get("model") or ""
        state = _CallState(nrun, current_agent_name.get(),
                           self._recorder.next_call_index(nrun), model)
        self._calls[lc_run_id] = state
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_STARTED, nrun,
            LlmCallStarted(run_id=nrun, agent_name=state.agent_name,
                           call_index=state.call_index, model=model, prompt=prompt),
        )

    async def on_llm_new_token(self, token: str, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.get(run_id)
        if state is None:
            return
        state.chunks += 1
        self._recorder.publish_token(
            TokenDelta(run_id=state.novelizer_run_id, agent_name=state.agent_name, text=token))

    async def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.pop(run_id, None)
        if state is None:
            return
        tokens = self._usage_tokens(response)
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_FINISHED, state.novelizer_run_id,
            LlmCallFinished(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                            call_index=state.call_index, model=state.model,
                            duration_s=time.monotonic() - state.started,
                            output_tokens=tokens if tokens else state.chunks),
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.pop(run_id, None)
        if state is None:
            return
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_FAILED, state.novelizer_run_id,
            LlmCallFailed(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                          call_index=state.call_index, model=state.model,
                          duration_s=time.monotonic() - state.started,
                          error_type=type(error).__name__, error_message=str(error)),
        )

    @staticmethod
    def _usage_tokens(response) -> int:
        try:
            gen = response.generations[0][0]
            usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
            return int(usage.get("output_tokens", 0))
        except (IndexError, AttributeError, TypeError, ValueError):
            return 0
