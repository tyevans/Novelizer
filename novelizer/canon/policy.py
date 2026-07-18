from __future__ import annotations
from novelizer.canon.autonomy import AutonomyLevel
from novelizer.canon.events import EventType

_RETCON_EVENTS = {EventType.WORLD_ENTRY_SUPERSEDED, EventType.RETCON_REQUEST_RESOLVED}
_CANON_EVENTS = _RETCON_EVENTS | {
    EventType.WORLD_ENTRY_CREATED,
    EventType.CHARACTER_CREATED,
    EventType.CHARACTER_UPDATED,
    EventType.CHAPTER_CREATED,
    EventType.CHAPTER_STATUS_CHANGED,
}
_NEVER_GATED = {EventType.DIRECTOR_SIGNAL_CREATED, EventType.DIRECTOR_SIGNAL_CONSUMED}

_GATED_SETS: dict[AutonomyLevel, set[str]] = {
    AutonomyLevel.full_auto: set(),
    AutonomyLevel.gated_retcons: _RETCON_EVENTS,
    AutonomyLevel.gated_canon: _CANON_EVENTS,
    # gated_all is resolved dynamically in is_gated: everything not in _NEVER_GATED.
}


class AutonomyPolicy:
    """Reads the live AutonomyState from canon and decides what an agent may commit directly."""

    def __init__(self, read_store) -> None:
        self._read = read_store

    async def is_gated(self, agent_name: str, event_type: str) -> bool:
        if event_type in _NEVER_GATED:
            return False
        state = await self._read.get_autonomy_state()
        level = state.level_for(agent_name)
        if level == AutonomyLevel.gated_all:
            return True
        return event_type in _GATED_SETS.get(level, set())
