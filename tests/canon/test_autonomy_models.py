from novelizer.canon.autonomy import AutonomyLevel, AutonomyState, Proposal, ProposalStatus


def test_proposal_defaults():
    p = Proposal(
        proposing_agent="author",
        target_event_type="chapter.created",
        target_aggregate_id="c1",
        payload={"title": "One", "prose": "p"},
    )
    assert p.status == ProposalStatus.open
    assert p.id and p.created_at is not None


def test_autonomy_state_level_for_uses_global_by_default():
    st = AutonomyState(global_level=AutonomyLevel.gated_canon)
    assert st.level_for("author") == AutonomyLevel.gated_canon
    assert st.level_for("editor") == AutonomyLevel.gated_canon


def test_autonomy_state_level_for_prefers_override():
    st = AutonomyState(
        global_level=AutonomyLevel.full_auto,
        overrides={"retconner": AutonomyLevel.gated_all},
    )
    assert st.level_for("retconner") == AutonomyLevel.gated_all
    assert st.level_for("author") == AutonomyLevel.full_auto


def test_autonomy_state_default_is_full_auto():
    st = AutonomyState()
    assert st.global_level == AutonomyLevel.full_auto
    assert st.overrides == {}
