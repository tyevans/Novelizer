from substrate.policy import is_gated
from research_domain.event_types import RESEARCH_TIER_ORDER, build_research_registry


def test_claim_proposed_is_gated_once_auto_tier_active():
    registry = build_research_registry()
    assert is_gated("claim.proposed", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True


def test_source_corroborated_is_never_gated():
    registry = build_research_registry()
    assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is False


def test_claim_refuted_is_always_gated():
    registry = build_research_registry()
    assert is_gated("claim.refuted", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True


def test_claim_corrected_gates_only_at_reviewed_tier():
    registry = build_research_registry()
    assert is_gated("claim.corrected", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is False
    assert is_gated("claim.corrected", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is True


def test_build_research_registry_returns_a_fresh_instance_each_call():
    a = build_research_registry()
    b = build_research_registry()
    assert a is not b
