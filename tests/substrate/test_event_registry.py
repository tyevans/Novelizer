import pytest
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier


def test_register_and_get_roundtrips():
    registry = EventTypeRegistry()
    spec = EventTypeSpec(name="thing.created", gating_tier=GatingTier.never)
    registry.register(spec)
    assert registry.get("thing.created") is spec


def test_get_unregistered_raises_keyerror():
    registry = EventTypeRegistry()
    with pytest.raises(KeyError):
        registry.get("nope.nope")


def test_all_returns_every_registered_spec_in_registration_order():
    registry = EventTypeRegistry()
    a = EventTypeSpec(name="a.created", gating_tier=GatingTier.always)
    b = EventTypeSpec(name="b.created", gating_tier=GatingTier.never)
    registry.register(a)
    registry.register(b)
    assert registry.all() == [a, b]


def test_tiered_spec_carries_tier_level():
    spec = EventTypeSpec(name="c.created", gating_tier=GatingTier.tiered, tier_level="canon")
    assert spec.tier_level == "canon"


def test_register_duplicate_name_raises_valueerror():
    registry = EventTypeRegistry()
    registry.register(EventTypeSpec(name="a.created", gating_tier=GatingTier.never))
    with pytest.raises(ValueError):
        registry.register(EventTypeSpec(name="a.created", gating_tier=GatingTier.always))
