"""Proves substrate.policy/event_registry are domain-neutral: a second,
independent domain can declare its own event types and gating tiers using
only the public substrate API, with zero changes to substrate/ itself."""
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated

# A synthetic research domain's tier order -- two tiers, not fiction's four,
# to prove the mechanism isn't secretly assuming a fixed tier count/shape.
RESEARCH_TIER_ORDER = ["auto", "reviewed"]


def _research_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(EventTypeSpec(name="source.corroborated", gating_tier=GatingTier.never))
    registry.register(EventTypeSpec(name="claim.refuted", gating_tier=GatingTier.always))
    registry.register(
        EventTypeSpec(name="claim.proposed", gating_tier=GatingTier.tiered, tier_level="auto")
    )
    registry.register(
        EventTypeSpec(name="claim.retracted", gating_tier=GatingTier.tiered, tier_level="reviewed")
    )
    return registry


def test_never_gated_research_event_is_never_gated():
    registry = _research_registry()
    assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is False
    assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is False


def test_always_gated_research_event_is_always_gated():
    registry = _research_registry()
    assert is_gated("claim.refuted", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True
    assert is_gated("claim.refuted", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is True


def test_tiered_research_event_gates_at_its_own_tier_not_before():
    registry = _research_registry()
    # "auto" is tier index 0 -- gated as soon as current_tier_index reaches 0
    assert is_gated("claim.proposed", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True
    # "reviewed" is tier index 1 -- NOT gated while current_tier_index is 0
    assert is_gated("claim.retracted", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is False
    assert is_gated("claim.retracted", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is True


def test_research_and_fiction_registries_are_fully_independent():
    """The same substrate.policy.is_gated call, handed two unrelated
    registries with two unrelated tier vocabularies, must not leak state
    between them -- proves EventTypeRegistry instances don't share module-
    level mutable state."""
    research = _research_registry()
    fiction = EventTypeRegistry()
    fiction.register(EventTypeSpec(name="claim.proposed", gating_tier=GatingTier.never))
    # Same event NAME, opposite gating tier, in a totally separate registry.
    assert is_gated("claim.proposed", research, RESEARCH_TIER_ORDER, current_tier_index=0) is True
    assert is_gated("claim.proposed", fiction, RESEARCH_TIER_ORDER, current_tier_index=0) is False
