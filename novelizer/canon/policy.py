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
    EventType.CHAPTER_REVISED,
    EventType.SECRET_REVEALED,
}
_ALWAYS_GATED = {EventType.BLUEPRINT_ADOPTED}
_NEVER_GATED = {
    EventType.BLUEPRINT_RETARGETED,
    EventType.BEAT_FULFILLED,
    EventType.CHAPTER_BRIEF_DRAFTED,
    EventType.CHAPTER_BRIEF_SUPERSEDED,
    EventType.CHAPTER_BRIEF_FULFILLED,
    EventType.DIRECTOR_SIGNAL_CREATED,
    EventType.DIRECTOR_SIGNAL_CONSUMED,
    EventType.AGENT_REMARKED,
    EventType.CHAT_USER_MESSAGED,
    EventType.CHAT_AGENT_REPLIED,
    EventType.THREAD_PLANTED,
    EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF,
    EventType.THREAD_ABANDONED,
    EventType.ANNOTATION_STRUCTURE_SCORED,
    EventType.SECRET_CREATED,
    EventType.SECRET_LEARNED,
    EventType.SECRET_REFERENCED,
    EventType.CAUSAL_EDGE_DECLARED,
    EventType.CHAPTER_MINED,
    EventType.THEME_INTRODUCED,
    EventType.THEME_DEVELOPED,
    # mechanical bookkeeping from a deterministic no-LLM agent, same class as chapter.mined
    EventType.INSPIRATION_DRAWN,
    EventType.INSPIRATION_HAND_CONSUMED,
    EventType.INSPIRATION_HAND_SUPERSEDED,
    EventType.INSPIRATION_UPTAKE_RECORDED,
    EventType.PROMISE_MADE,
    EventType.PROMISE_PROGRESSED,
    EventType.PROMISE_PAID,
    EventType.PROMISE_RELEASED,
    EventType.THREAD_RESOLUTION_PLANNED,
    EventType.SECRET_REVEAL_PLANNED,
}

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
        if event_type in _ALWAYS_GATED:
            # adopting a shape re-frames the whole book — the Director signs off
            # at every autonomy level.
            return True
        if event_type in _NEVER_GATED:
            return False
        state = await self._read.get_autonomy_state()
        level = state.level_for(agent_name)
        if level == AutonomyLevel.gated_all:
            return True
        return event_type in _GATED_SETS.get(level, set())
