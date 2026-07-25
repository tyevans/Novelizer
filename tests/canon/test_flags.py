import pytest

from novelizer.canon.flags import (
    FAILURE_ESCALATION_THRESHOLD,
    TERMINAL_FLAG_STATES,
    is_terminal,
    mark_declined,
    mark_dismissed,
    mark_escalated,
    mark_escalation_cleared,
    mark_resolved,
    mark_stale,
    may_clear_escalation,
    may_decide,
    may_escalate,
    should_escalate_after_failure,
)
from novelizer.store.models import Flag, FlagStatus


def _flag(**kw) -> Flag:
    return Flag(id="f1", category="contradiction", description="x", **kw)


def test_failure_escalation_threshold_is_three():
    assert FAILURE_ESCALATION_THRESHOLD == 3


def test_terminal_states_are_the_three_closed_statuses():
    assert TERMINAL_FLAG_STATES == {FlagStatus.resolved, FlagStatus.rejected, FlagStatus.stale}


@pytest.mark.parametrize("status", [FlagStatus.resolved, FlagStatus.rejected, FlagStatus.stale])
def test_closed_statuses_are_terminal(status):
    assert is_terminal(_flag(status=status)) is True


def test_open_is_not_terminal():
    assert is_terminal(_flag()) is False


def test_may_decide_only_while_open():
    assert may_decide(_flag()) is True
    assert may_decide(_flag(status=FlagStatus.resolved)) is False


def test_may_escalate_only_refuses_a_double_raise():
    assert may_escalate(_flag()) is True
    assert may_escalate(_flag(escalated=True)) is False


def test_may_escalate_holds_for_a_rejected_flag():
    # The repeated-failure trigger escalates the flag it has just rejected, so
    # escalation is orthogonal to status.
    assert may_escalate(_flag(status=FlagStatus.rejected)) is True


def test_may_clear_escalation_requires_an_active_escalation():
    assert may_clear_escalation(_flag(escalated=True)) is True
    assert may_clear_escalation(_flag()) is False


def test_may_clear_escalation_holds_for_a_resolved_but_still_escalated_flag():
    # Resolution and escalation are independent axes: the owning agent resolves
    # first, then clears, so a terminal status must not block the clear.
    assert may_clear_escalation(_flag(status=FlagStatus.resolved, escalated=True)) is True


def test_should_escalate_after_failure_at_and_above_threshold():
    assert should_escalate_after_failure(_flag(failed_attempts=2)) is False
    assert should_escalate_after_failure(_flag(failed_attempts=3)) is True
    assert should_escalate_after_failure(_flag(failed_attempts=4)) is True


def test_should_escalate_after_failure_is_false_when_already_escalated():
    assert should_escalate_after_failure(_flag(failed_attempts=3, escalated=True)) is False


def test_mark_declined_rejects_bumps_attempts_and_brackets_the_resolution():
    out = mark_declined(_flag(failed_attempts=1), by="retconner",
                        resolution="cannot_reproduce", reason="no evidence")
    assert out.status == FlagStatus.rejected
    assert out.resolved_by == "retconner"
    assert out.failed_attempts == 2
    assert out.proposed_resolution == "[cannot_reproduce] no evidence"


def test_mark_declined_omits_an_empty_reason():
    out = mark_declined(_flag(), by="curator", resolution="out_of_lane", reason="")
    assert out.proposed_resolution == "[out_of_lane]"


def test_mark_dismissed_rejects_without_counting_a_failed_attempt():
    out = mark_dismissed(_flag(failed_attempts=1), by="triage")
    assert out.status == FlagStatus.rejected
    assert out.resolved_by == "triage"
    assert out.failed_attempts == 1


def test_mark_resolved_sets_status_and_resolver():
    out = mark_resolved(_flag(), by="curator")
    assert out.status == FlagStatus.resolved
    assert out.resolved_by == "curator"


def test_mark_stale_closes_the_flag_and_records_the_pass_count():
    out = mark_stale(_flag(), by="triage", triage_passes=5)
    assert out.status == FlagStatus.stale
    assert out.resolved_by == "triage"
    assert out.triage_passes == 5


def test_mark_escalated_sets_the_flag():
    assert mark_escalated(_flag()).escalated is True


def test_mark_escalation_cleared_has_one_shape_for_agent_and_human():
    by_agent = mark_escalation_cleared(_flag(escalated=True), by="agent")
    assert (by_agent.escalated, by_agent.escalation_cleared_by,
            by_agent.escalation_clear_note) == (False, "agent", None)
    by_human = mark_escalation_cleared(_flag(escalated=True), by="human", note="did it myself")
    assert (by_human.escalated, by_human.escalation_cleared_by,
            by_human.escalation_clear_note) == (False, "human", "did it myself")


@pytest.mark.parametrize("transition", [
    lambda f: mark_declined(f, by="a", resolution="r", reason=""),
    lambda f: mark_dismissed(f, by="a"),
    lambda f: mark_resolved(f, by="a"),
    lambda f: mark_stale(f, by="a", triage_passes=5),
])
def test_deciding_an_already_closed_flag_raises(transition):
    with pytest.raises(ValueError):
        transition(_flag(status=FlagStatus.resolved))


def test_escalating_an_already_escalated_flag_raises():
    with pytest.raises(ValueError):
        mark_escalated(_flag(escalated=True))


def test_clearing_an_unescalated_flag_raises():
    with pytest.raises(ValueError):
        mark_escalation_cleared(_flag(), by="human")
