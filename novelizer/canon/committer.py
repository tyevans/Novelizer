from __future__ import annotations
from pydantic import BaseModel
from novelizer.canon.event_store import EventStore


class Committer:
    """The single seam through which agents write canon.

    M1.1 appends the event directly (full-auto). M1.3 will introduce a gating
    subclass/replacement that may append a proposal instead, keyed on
    ``agent_name`` and ``event_type`` — without any agent changing.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._events = event_store

    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        await self._events.append(event_type, aggregate_id, payload)
