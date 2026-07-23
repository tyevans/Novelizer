from __future__ import annotations
from novelizer.canon.autonomy import AutonomyLevel
from novelizer.canon.events import EventType
from substrate import EventTypeRegistry, EventTypeSpec, GatingTier, is_gated as _substrate_is_gated

_RETCON_EVENTS = {EventType.WORLD_ENTRY_SUPERSEDED, EventType.FLAG_RESOLVED}
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
    EventType.ARC_DECLARED,
    EventType.ARC_PIVOT_PLANNED,
    EventType.ARC_ADVANCED,
    EventType.ARC_RESOLVED,
    EventType.BOOK_COMPLETED,
    # escalation is a visibility signal, not a routing gate — same class as
    # the other bookkeeping events above.
    EventType.FLAG_ESCALATED,
    EventType.FLAG_ESCALATION_CLEARED,
}

FICTION_TIER_ORDER = ["full_auto", "retcons", "canon", "all"]


def _build_fiction_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    all_known: set[str] = set()
    for name in _ALWAYS_GATED:
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.always))
        all_known.add(name)
    for name in _NEVER_GATED:
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.never))
        all_known.add(name)
    for name in _CANON_EVENTS:
        if name in all_known:
            continue
        tier = "retcons" if name in _RETCON_EVENTS else "canon"
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.tiered, tier_level=tier))
        all_known.add(name)
    # Every other EventType constant not explicitly bucketed above falls
    # under the dynamic gated_all catch-all the original module documented
    # ("gated_all is resolved dynamically in is_gated: everything not in
    # _NEVER_GATED"). Register the rest at tier_level="all" so they are
    # gated only once current_tier_index reaches gated_all.
    for name in vars(EventType).values():
        if not isinstance(name, str) or name in all_known:
            continue
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.tiered, tier_level="all"))
        all_known.add(name)
    return registry


_FICTION_REGISTRY = _build_fiction_registry()

_LEVEL_TO_TIER_INDEX = {
    AutonomyLevel.full_auto: 0,
    AutonomyLevel.gated_retcons: 1,
    AutonomyLevel.gated_canon: 2,
    AutonomyLevel.gated_all: 3,
}


class AutonomyPolicy:
    """Reads the live AutonomyState from canon and decides what an agent may commit directly."""

    def __init__(self, read_store) -> None:
        self._read = read_store

    async def is_gated(self, agent_name: str, event_type: str) -> bool:
        state = await self._read.get_autonomy_state()
        level = state.level_for(agent_name)
        return _substrate_is_gated(
            event_type, _FICTION_REGISTRY, FICTION_TIER_ORDER, _LEVEL_TO_TIER_INDEX[level]
        )
