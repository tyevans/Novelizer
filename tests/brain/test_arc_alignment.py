import pytest

from novelizer.brain.arc_alignment import ARC_CONSISTENT_OUTCOMES, ArcFinding, arc_findings
from novelizer.store.models import ArcPivot, ArcRecord, BeatRecord, BlueprintRecord, Chapter


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def _blueprint(target_chapter_count=10):
    return BlueprintRecord(id="bp1", framework="three_act", target_chapter_count=target_chapter_count)


# --- contradiction: consistency table ---------------------------------

CONSISTENT_ROWS = [(t, next(iter(outs))) for t, outs in ARC_CONSISTENT_OUTCOMES.items()]
CONTRADICTORY_ROWS = [(t, "some_other_outcome") for t in ARC_CONSISTENT_OUTCOMES]


@pytest.mark.parametrize("arc_type,outcome", CONSISTENT_ROWS)
def test_consistent_outcome_no_contradiction(arc_type, outcome):
    arc = ArcRecord(id="a1", character_id="mara", arc_type=arc_type, resolved=True, outcome=outcome)
    findings = arc_findings([arc], [], _chapters(1), [], None)
    assert findings == []


@pytest.mark.parametrize("arc_type,outcome", CONTRADICTORY_ROWS)
def test_contradictory_outcome_flagged(arc_type, outcome):
    arc = ArcRecord(id="a1", character_id="mara", arc_type=arc_type, resolved=True, outcome=outcome)
    findings = arc_findings([arc], [], _chapters(1), [], None)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "contradiction"
    assert f.arc_id == "a1"
    assert f.character_id == "mara"
    assert f.detail == f"{arc_type} arc resolved {outcome}"


def test_unknown_arc_type_is_quiet():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="mystery_type", resolved=True, outcome="anything")
    assert arc_findings([arc], [], _chapters(1), [], None) == []


def test_blank_outcome_is_quiet():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="fall", resolved=True, outcome="")
    assert arc_findings([arc], [], _chapters(1), [], None) == []


# --- stagnant ------------------------------------------------------------

def test_stagnant_fires_at_threshold_boundary():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="positive", last_chapter_id="c0")
    chapters = _chapters(5)  # elapsed since c0 = 5 - 1 - 0 = 4
    findings = arc_findings([arc], [], chapters, [], None, stagnation_chapters=4)
    assert len(findings) == 1
    assert findings[0].kind == "stagnant"


def test_stagnant_quiet_below_threshold():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="positive", last_chapter_id="c2")
    chapters = _chapters(5)  # elapsed = 5 - 1 - 2 = 2
    assert arc_findings([arc], [], chapters, [], None, stagnation_chapters=4) == []


def test_resolved_arc_never_stagnant():
    arc = ArcRecord(
        id="a1", character_id="mara", arc_type="positive", resolved=True,
        outcome="truth_embraced", last_chapter_id="c0",
    )
    findings = arc_findings([arc], [], _chapters(20), [], None, stagnation_chapters=4)
    assert findings == []


def test_never_advanced_arc_stagnant_once_chapter_count_reaches_threshold():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="positive", last_chapter_id="")
    assert arc_findings([arc], [], _chapters(3), [], None, stagnation_chapters=4) == []
    findings = arc_findings([arc], [], _chapters(4), [], None, stagnation_chapters=4)
    assert len(findings) == 1
    assert findings[0].kind == "stagnant"


def test_inactive_unresolved_arc_never_flagged():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="positive", active=False, last_chapter_id="")
    assert arc_findings([arc], [], _chapters(10), [], None, stagnation_chapters=4) == []


# --- pivot_missed ----------------------------------------------------------

def _beat():
    return BeatRecord(id="bp1-midpoint", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1)


def test_pivot_missed_when_window_closed_and_last_advance_before_window():
    # window for ideal_pct=0.5, tol=0.1, target=10 -> (4, 6)
    arc = ArcRecord(
        id="a1", character_id="mara", arc_type="positive", last_chapter_id="c1",
        pivots=[ArcPivot(beat_id="bp1-midpoint")],
    )
    chapters = _chapters(7)  # now=7 > hi=6; last advance ordinal for c1 = 2 < lo=4
    findings = arc_findings([arc], [], chapters, [_beat()], _blueprint(), stagnation_chapters=100)
    kinds = [f.kind for f in findings]
    assert "pivot_missed" in kinds


def test_pivot_missed_quiet_when_advance_lands_inside_window():
    arc = ArcRecord(
        id="a1", character_id="mara", arc_type="positive", last_chapter_id="c4",
        pivots=[ArcPivot(beat_id="bp1-midpoint")],
    )
    chapters = _chapters(9)  # now=9 > hi=6, but advance ordinal for c4 = 5, inside [4,6]
    findings = arc_findings([arc], [], chapters, [_beat()], _blueprint(), stagnation_chapters=100)
    assert [f for f in findings if f.kind == "pivot_missed"] == []


def test_pivot_missed_quiet_without_blueprint():
    arc = ArcRecord(
        id="a1", character_id="mara", arc_type="positive", last_chapter_id="",
        pivots=[ArcPivot(beat_id="bp1-midpoint")],
    )
    findings = arc_findings([arc], [], _chapters(20), [_beat()], None, stagnation_chapters=100)
    assert [f for f in findings if f.kind == "pivot_missed"] == []


def test_pivot_missed_quiet_when_window_still_open():
    arc = ArcRecord(
        id="a1", character_id="mara", arc_type="positive", last_chapter_id="",
        pivots=[ArcPivot(beat_id="bp1-midpoint")],
    )
    findings = arc_findings([arc], [], _chapters(5), [_beat()], _blueprint(), stagnation_chapters=100)
    assert [f for f in findings if f.kind == "pivot_missed"] == []
