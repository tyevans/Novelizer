from novelizer.brain.completion import CompletionStatus, completion_status
from novelizer.store.models import ArcRecord, BeatRecord, BlueprintRecord, Chapter, PromiseRecord, PromiseState


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def _blueprint(target_chapter_count=10):
    return BlueprintRecord(id="bp1", framework="three_act", target_chapter_count=target_chapter_count)


def _beat(slug, name, fulfilled=False):
    return BeatRecord(
        id=slug, blueprint_id="bp1", slug=slug, name=name, ideal_pct=0.5, tolerance_pct=0.1,
        fulfilled_by_chapter_id="c0" if fulfilled else "",
    )


# --- blueprint None / beats empty -------------------------------------

def test_no_blueprint_returns_none():
    assert completion_status(None, [], [], [], _chapters(0)) is None


def test_empty_beats_never_complete():
    status = completion_status(_blueprint(), [], [], [], _chapters(0))
    assert status is not None
    assert status.complete is False
    assert status.beats_total == 0
    assert status.beats_fulfilled == 0
    assert status.blockers != []


# --- fully complete -----------------------------------------------------

def test_all_criteria_satisfied_is_complete_with_empty_blockers():
    beats = [_beat("open", "Opening", fulfilled=True), _beat("climax", "Climax", fulfilled=True)]
    promises = [PromiseRecord(id="p1", name="Ring", state=PromiseState.paid)]
    arcs = [ArcRecord(id="a1", character_id="mara", arc_type="positive", resolved=True)]
    status = completion_status(_blueprint(), beats, promises, arcs, _chapters(10))
    assert status.complete is True
    assert status.blockers == []
    assert status.beats_total == 2
    assert status.beats_fulfilled == 2
    assert status.promises_open == 0
    assert status.arcs_unresolved == 0
    assert status.chapters == 10
    assert status.target_chapters == 10


# --- each criterion independently blocks --------------------------------

def test_unfulfilled_beats_block_and_are_named():
    beats = [
        _beat("open", "Opening", fulfilled=True),
        _beat("midpoint", "Midpoint", fulfilled=False),
        _beat("climax", "Climax", fulfilled=False),
    ]
    status = completion_status(_blueprint(), beats, [], [], _chapters(10))
    assert status.complete is False
    assert status.beats_fulfilled == 1
    assert any("2 of 3 beats unfulfilled" in b for b in status.blockers)
    assert any("midpoint" in b and "climax" in b for b in status.blockers)


def test_open_promise_blocks():
    beats = [_beat("open", "Opening", fulfilled=True)]
    promises = [PromiseRecord(id="p1", name="Ring", state=PromiseState.open)]
    status = completion_status(_blueprint(), beats, promises, [], _chapters(10))
    assert status.complete is False
    assert status.promises_open == 1
    assert any("1 promise" in b or "1 promises" in b for b in status.blockers)


def test_released_promise_does_not_block():
    beats = [_beat("open", "Opening", fulfilled=True)]
    promises = [PromiseRecord(id="p1", name="Ring", state=PromiseState.released)]
    status = completion_status(_blueprint(), beats, promises, [], _chapters(10))
    assert status.complete is True
    assert status.promises_open == 0


def test_paid_promise_does_not_block():
    beats = [_beat("open", "Opening", fulfilled=True)]
    promises = [PromiseRecord(id="p1", name="Ring", state=PromiseState.paid)]
    status = completion_status(_blueprint(), beats, promises, [], _chapters(10))
    assert status.complete is True


def test_unresolved_active_arc_blocks():
    beats = [_beat("open", "Opening", fulfilled=True)]
    arcs = [ArcRecord(id="a1", character_id="mara", arc_type="positive", active=True, resolved=False)]
    status = completion_status(_blueprint(), beats, [], arcs, _chapters(10))
    assert status.complete is False
    assert status.arcs_unresolved == 1
    assert any("1 arc unresolved" in b for b in status.blockers)


def test_resolved_but_inactive_arc_ignored():
    beats = [_beat("open", "Opening", fulfilled=True)]
    arcs = [ArcRecord(id="a1", character_id="mara", arc_type="positive", active=False, resolved=False)]
    status = completion_status(_blueprint(), beats, [], arcs, _chapters(10))
    assert status.complete is True
    assert status.arcs_unresolved == 0


def test_resolved_active_arc_does_not_block():
    beats = [_beat("open", "Opening", fulfilled=True)]
    arcs = [ArcRecord(id="a1", character_id="mara", arc_type="positive", active=True, resolved=True)]
    status = completion_status(_blueprint(), beats, [], arcs, _chapters(10))
    assert status.complete is True


# --- counts reported correctly regardless of completion ------------------

def test_chapters_and_target_reported_never_gating():
    beats = [_beat("open", "Opening", fulfilled=True)]
    status = completion_status(_blueprint(target_chapter_count=20), beats, [], [], _chapters(3))
    assert status.complete is True
    assert status.chapters == 3
    assert status.target_chapters == 20


def test_multiple_blocker_categories_all_reported():
    beats = [_beat("open", "Opening", fulfilled=False)]
    promises = [PromiseRecord(id="p1", name="Ring", state=PromiseState.open)]
    arcs = [ArcRecord(id="a1", character_id="mara", arc_type="positive", active=True, resolved=False)]
    status = completion_status(_blueprint(), beats, promises, arcs, _chapters(10))
    assert status.complete is False
    assert len(status.blockers) == 3
