"""Pure data functions for the Escalations review screen. No Textual
imports — screens call these and render the results."""
from __future__ import annotations
from dataclasses import dataclass

from novelizer.canon.event_store import EventStore
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Flag


@dataclass(frozen=True)
class TimelineEntry:
    event_type: str
    created_at: str
    summary: str


_SUMMARIES = {
    "flag.created": "Flag filed",
    "flag.resolved": "Resolved",
    "flag.rejected": "Rejected",
    "flag.escalated": "Escalated",
    "flag.escalation_cleared": "Escalation cleared",
}


async def escalated_flags(read: ReadStore) -> list[Flag]:
    return await read.list_flags(escalated=True)


async def escalation_timeline(events: EventStore, flag_id: str) -> list[TimelineEntry]:
    stored = await events.events_for_aggregate(flag_id)
    return [
        TimelineEntry(
            event_type=e.event_type,
            created_at=e.created_at,
            summary=_SUMMARIES.get(e.event_type, e.event_type),
        )
        for e in stored
    ]
