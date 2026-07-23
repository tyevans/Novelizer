from __future__ import annotations
from substrate import EventTypeRegistry, EventTypeSpec, GatingTier

RESEARCH_TIER_ORDER = ["auto", "reviewed"]


def build_research_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(
        EventTypeSpec(name="claim.proposed", gating_tier=GatingTier.tiered, tier_level="auto")
    )
    registry.register(EventTypeSpec(name="source.corroborated", gating_tier=GatingTier.never))
    registry.register(EventTypeSpec(name="claim.refuted", gating_tier=GatingTier.always))
    registry.register(
        EventTypeSpec(name="claim.corrected", gating_tier=GatingTier.tiered, tier_level="reviewed")
    )
    return registry
