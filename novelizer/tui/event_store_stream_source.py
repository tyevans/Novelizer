"""EventStore-backed StreamSource: the production wiring.

Lives in novelizer/, not tui_kit/, because it knows about canon's
EventStore and novelizer's telemetry vocabulary. tui_kit only ever sees
the protocol.
"""
from __future__ import annotations
import logging
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.events import TelemetryEventType
from tui_kit.run_model import LiveRunState, StreamBlock, apply_bus_item

logger = logging.getLogger(__name__)

_STREAM_TYPES = [
    TelemetryEventType.LLM_CALL_STARTED,
    TelemetryEventType.LLM_CALL_FINISHED,
    TelemetryEventType.TOOL_CALL_STARTED,
    TelemetryEventType.TOOL_CALL_FINISHED,
    TelemetryEventType.TOOL_CALL_FAILED,
]


class EventStoreStreamSource:
    def __init__(self, store: EventStore, to_contract_event) -> None:
        self._store = store
        self._to_contract = to_contract_event

    async def page_before(self, sequence: int, limit: int) -> list[StreamBlock]:
        events = await self._store.events_before(sequence, limit, _STREAM_TYPES)
        # Fold per run: apply_bus_item ignores events whose run_id does not
        # match the state it is folding, so one shared state would silently
        # drop every run but the first in the page.
        by_run: dict[str, list] = {}
        for ev in events:
            contract = self._to_contract(ev)
            if contract is not None:
                by_run.setdefault(getattr(contract, "run_id", ""), []).append(contract)
        blocks: list[StreamBlock] = []
        for run_id, items in by_run.items():
            state = LiveRunState(status="running", run_id=run_id)
            for item in items:
                state = apply_bus_item(state, item, now=0.0)
            blocks.extend(state.blocks)
        return blocks

    async def fetch_output(self, sequence: int) -> str:
        try:
            page = await self._store.events_before(sequence + 1, 1)
        except Exception:
            logger.warning("stream: output fetch failed at seq %s", sequence, exc_info=True)
            return ""
        if not page or page[-1].sequence != sequence:
            return ""
        return str(page[-1].payload.get("output_summary", ""))
