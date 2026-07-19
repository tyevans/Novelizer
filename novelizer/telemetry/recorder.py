from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.events import TelemetryEventType, TokenDelta

logger = logging.getLogger(__name__)

_RUN_END_TYPES = {TelemetryEventType.AGENT_RUN_FINISHED, TelemetryEventType.AGENT_RUN_FAILED}


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
