from novelizer.brain.beat_drift import BeatDrift, beat_drifts, next_expected_beat
from novelizer.store.models import BeatRecord, BlueprintRecord, Chapter


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def _blueprint(target_chapter_count=10):
    return BlueprintRecord(id="bp1", framework="three_act", target_chapter_count=target_chapter_count)


def test_no_blueprint_returns_empty():
    beats = [BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                         ideal_pct=0.5, tolerance_pct=0.1)]
    assert beat_drifts(None, beats, _chapters(10)) == []


def test_unfulfilled_inside_window_no_drift():
    # window for ideal_pct=0.5, tol=0.1, target=10 -> center 5, tol 1 -> (4,6)
    beat = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1)
    assert beat_drifts(_blueprint(), [beat], _chapters(5)) == []


def test_unfulfilled_past_window_is_late():
    beat = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1)
    drifts = beat_drifts(_blueprint(), [beat], _chapters(7))
    assert len(drifts) == 1
    d = drifts[0]
    assert d.kind == "late"
    assert d.beat_id == "b1"
    assert d.window_lo == 4
    assert d.window_hi == 6
    assert "9" not in d.detail  # sanity: no bogus values
    assert "4" in d.detail and "6" in d.detail


def test_fulfilled_before_window_is_early():
    beat = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1, fulfilled_by_chapter_id="c2")
    chapters = _chapters(10)  # c2 is ordinal 3, window (4,6)
    drifts = beat_drifts(_blueprint(), [beat], chapters)
    assert len(drifts) == 1
    assert drifts[0].kind == "early"


def test_fulfilled_inside_window_no_drift():
    beat = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1, fulfilled_by_chapter_id="c4")
    chapters = _chapters(10)  # c4 is ordinal 5, window (4,6)
    assert beat_drifts(_blueprint(), [beat], chapters) == []


def test_fulfilled_after_window_is_off_window():
    beat = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1, fulfilled_by_chapter_id="c8")
    chapters = _chapters(10)  # c8 is ordinal 9, window (4,6)
    drifts = beat_drifts(_blueprint(), [beat], chapters)
    assert len(drifts) == 1
    assert drifts[0].kind == "off_window"


def test_fulfilled_by_unknown_chapter_is_off_window_with_note():
    beat = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1, fulfilled_by_chapter_id="ghost")
    chapters = _chapters(10)
    drifts = beat_drifts(_blueprint(), [beat], chapters)
    assert len(drifts) == 1
    assert drifts[0].kind == "off_window"
    assert "unknown" in drifts[0].detail.lower()


def test_next_expected_beat_orders_by_ideal_pct():
    b1 = BeatRecord(id="b1", blueprint_id="bp1", slug="climax", name="Climax",
                     ideal_pct=0.9, tolerance_pct=0.1)
    b2 = BeatRecord(id="b2", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                     ideal_pct=0.5, tolerance_pct=0.1)
    b3 = BeatRecord(id="b3", blueprint_id="bp1", slug="fulfilled", name="Fulfilled",
                     ideal_pct=0.2, tolerance_pct=0.1, fulfilled_by_chapter_id="c1")
    result = next_expected_beat(_blueprint(), [b1, b2, b3], _chapters(10))
    assert result is b2


def test_next_expected_beat_none_when_all_fulfilled():
    b1 = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                     ideal_pct=0.5, tolerance_pct=0.1, fulfilled_by_chapter_id="c1")
    assert next_expected_beat(_blueprint(), [b1], _chapters(10)) is None


def test_next_expected_beat_no_blueprint_is_none():
    beats = [BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                         ideal_pct=0.5, tolerance_pct=0.1)]
    assert next_expected_beat(None, beats, _chapters(10)) is None
