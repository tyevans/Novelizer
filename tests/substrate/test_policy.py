from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated


def _registry():
    registry = EventTypeRegistry()
    registry.register(EventTypeSpec(name="always.event", gating_tier=GatingTier.always))
    registry.register(EventTypeSpec(name="never.event", gating_tier=GatingTier.never))
    registry.register(EventTypeSpec(name="retcon.event", gating_tier=GatingTier.tiered, tier_level="retcons"))
    registry.register(EventTypeSpec(name="canon.event", gating_tier=GatingTier.tiered, tier_level="canon"))
    return registry


TIER_ORDER = ["full_auto", "retcons", "canon", "all"]


def test_always_gated_regardless_of_tier_index():
    registry = _registry()
    assert is_gated("always.event", registry, TIER_ORDER, current_tier_index=0) is True
    assert is_gated("always.event", registry, TIER_ORDER, current_tier_index=3) is True


def test_never_gated_regardless_of_tier_index():
    registry = _registry()
    assert is_gated("never.event", registry, TIER_ORDER, current_tier_index=3) is False


def test_tiered_event_gated_once_current_index_reaches_its_tier():
    registry = _registry()
    # current_tier_index=0 is "full_auto" -- nothing tiered is gated yet
    assert is_gated("retcon.event", registry, TIER_ORDER, current_tier_index=0) is False
    # current_tier_index=1 is "retcons" -- retcon.event's own tier is now active
    assert is_gated("retcon.event", registry, TIER_ORDER, current_tier_index=1) is True
    # canon.event's tier ("canon") is index 2, not yet active at index 1
    assert is_gated("canon.event", registry, TIER_ORDER, current_tier_index=1) is False
    assert is_gated("canon.event", registry, TIER_ORDER, current_tier_index=2) is True


def test_tiered_event_gated_at_max_tier_index():
    registry = _registry()
    assert is_gated("retcon.event", registry, TIER_ORDER, current_tier_index=3) is True
    assert is_gated("canon.event", registry, TIER_ORDER, current_tier_index=3) is True
