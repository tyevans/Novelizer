from __future__ import annotations
from substrate.event_registry import EventTypeRegistry, GatingTier


def is_gated(
    event_name: str,
    registry: EventTypeRegistry,
    tier_order: list[str],
    current_tier_index: int,
) -> bool:
    spec = registry.get(event_name)
    if spec.gating_tier == GatingTier.always:
        return True
    if spec.gating_tier == GatingTier.never:
        return False
    tier_index = tier_order.index(spec.tier_level)
    return current_tier_index >= tier_index
