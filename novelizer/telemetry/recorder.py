from __future__ import annotations
import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pydantic import BaseModel
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import StoredEvent
from agent_kit import current_agent_name, current_run_id
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.events import (
    AgentRunCancelled, AgentRunFailed, AgentRunFinished, AgentRunStarted,
    TelemetryEventType, TokenDelta,
)

logger = logging.getLogger(__name__)

_RUN_END_TYPES = {
    TelemetryEventType.AGENT_RUN_FINISHED,
    TelemetryEventType.AGENT_RUN_FAILED,
    # A cancelled run is as terminal as either of the others: leaving it out
    # would leak its per-run call bookkeeping for the process's lifetime.
    TelemetryEventType.AGENT_RUN_CANCELLED,
}


class TelemetryRecorder:
    """Fire-and-forget machinery recorder: persist to the telemetry log,
    mirror to the bus. A store failure warns and drops — it must never take
    down an agent run or the scheduler; the bus mirror still fires so the
    live view degrades gracefully (the trace just has a gap)."""

    def __init__(self, store: EventStore, bus: TelemetryBus) -> None:
        self._store = store
        self._bus = bus
        self._open_calls: set[str] = set()
        self._call_counts: dict[str, int] = {}

    async def emit(self, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        self._track(event_type, payload)
        try:
            stored = await self._store.append(event_type, aggregate_id, payload)
        except Exception:
            logger.warning("telemetry: dropped %s (store write failed)", event_type, exc_info=True)
            stored = StoredEvent(
                sequence=-1, id=str(uuid.uuid4()), event_type=event_type,
                aggregate_id=aggregate_id, payload=payload.model_dump(mode="json"),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        self._bus.publish(stored)

    def publish_token(self, delta: TokenDelta) -> None:
        self._bus.publish(delta)

    def in_llm_call(self, run_id: str) -> bool:
        return run_id in self._open_calls

    def next_call_index(self, run_id: str) -> int:
        idx = self._call_counts.get(run_id, 0) + 1
        self._call_counts[run_id] = idx
        return idx

    def _track(self, event_type: str, payload: BaseModel) -> None:
        run_id = getattr(payload, "run_id", None)
        if run_id is None:
            return
        if event_type == TelemetryEventType.LLM_CALL_STARTED:
            self._open_calls.add(run_id)
        elif event_type in (TelemetryEventType.LLM_CALL_FINISHED, TelemetryEventType.LLM_CALL_FAILED):
            self._open_calls.discard(run_id)
        elif event_type in _RUN_END_TYPES:
            self._open_calls.discard(run_id)
            self._call_counts.pop(run_id, None)


@asynccontextmanager
async def run_with_identity(telemetry, name: str):
    """Bracket a block of work with ambient run identity (current_run_id /
    current_agent_name) and AGENT_RUN_* telemetry — the same contract
    BaseAgent.run_once gives autonomous agents, reusable for call sites
    that aren't a BaseAgent (chat, research). `telemetry` may be None: the
    context vars are still set/reset, nothing is emitted."""
    run_id = str(uuid.uuid4())
    started = time.monotonic()
    rid_token = current_run_id.set(run_id)
    name_token = current_agent_name.set(name)
    if telemetry is not None:
        await telemetry.emit(
            TelemetryEventType.AGENT_RUN_STARTED, run_id,
            AgentRunStarted(run_id=run_id, agent_name=name),
        )
    try:
        yield run_id
    except asyncio.CancelledError:
        # Same BaseException leak BaseAgent.run_once had: a cancelled block
        # reached no terminal event at all. Distinct type, no error fields, and
        # re-raised — see agent_kit.base.run_once for the full reasoning.
        if telemetry is not None:
            await telemetry.emit(
                TelemetryEventType.AGENT_RUN_CANCELLED, run_id,
                AgentRunCancelled(
                    run_id=run_id, agent_name=name,
                    phase="llm_call" if telemetry.in_llm_call(run_id) else "agent",
                    duration_s=time.monotonic() - started,
                ),
            )
        raise
    except Exception as e:
        if telemetry is not None:
            phase = "llm_call" if telemetry.in_llm_call(run_id) else "agent"
            await telemetry.emit(
                TelemetryEventType.AGENT_RUN_FAILED, run_id,
                AgentRunFailed(
                    run_id=run_id, agent_name=name, error_type=type(e).__name__,
                    error_message=str(e), phase=phase, duration_s=time.monotonic() - started,
                ),
            )
        raise
    else:
        if telemetry is not None:
            await telemetry.emit(
                TelemetryEventType.AGENT_RUN_FINISHED, run_id,
                AgentRunFinished(run_id=run_id, agent_name=name, duration_s=time.monotonic() - started),
            )
    finally:
        current_run_id.reset(rid_token)
        current_agent_name.reset(name_token)
