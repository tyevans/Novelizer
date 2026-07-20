from novelizer.brain.tension_target import target_curve, tension_deviations
from novelizer.store.models import BeatRecord, BlueprintRecord, Chapter, StructureScore


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def _blueprint(target_chapter_count=20):
    return BlueprintRecord(id="bp1", framework="six-position", target_chapter_count=target_chapter_count)


def _six_position_beats():
    return [
        BeatRecord(id="b1", blueprint_id="bp1", slug="catalyst", name="Catalyst",
                   ideal_pct=0.10, tolerance_pct=0.05),
        BeatRecord(id="b2", blueprint_id="bp1", slug="threshold", name="Threshold",
                   ideal_pct=0.25, tolerance_pct=0.05),
        BeatRecord(id="b3", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                   ideal_pct=0.50, tolerance_pct=0.05, expected_polarity="flip"),
        BeatRecord(id="b4", blueprint_id="bp1", slug="low-point", name="Low Point",
                   ideal_pct=0.75, tolerance_pct=0.05, expected_polarity="down"),
        BeatRecord(id="b5", blueprint_id="bp1", slug="final-turn", name="Final Turn",
                   ideal_pct=0.80, tolerance_pct=0.05, expected_polarity="up"),
        BeatRecord(id="b6", blueprint_id="bp1", slug="climax", name="Climax",
                   ideal_pct=0.90, tolerance_pct=0.05, expected_polarity="up"),
    ]


# --- target_curve ---

def test_no_blueprint_returns_empty():
    assert target_curve(None, []) == []


def test_curve_length_matches_target_chapter_count():
    curve = target_curve(_blueprint(20), _six_position_beats())
    assert len(curve) == 20


def test_curve_starts_at_point_three_and_ends_at_point_five():
    curve = target_curve(_blueprint(20), [])
    assert curve[0] == 0.3
    assert curve[-1] == 0.5


def test_curve_rises_into_midpoint_flip_anchor():
    curve = target_curve(_blueprint(20), _six_position_beats())
    # midpoint ideal_pct=0.5 -> round(0.5*20)=10 -> index 9 (1-based ch 10)
    assert curve[9] == 0.85
    # monotonic rise from start into the flip anchor
    assert curve[0] < curve[4] < curve[9]


def test_climax_anchor_value():
    curve = target_curve(_blueprint(20), _six_position_beats())
    # climax ideal_pct=0.90 -> round(0.9*20)=18 -> index 17 (1-based ch 18)
    assert curve[17] == 0.75


def test_anchors_clamp_to_valid_ordinal_range():
    beats = [
        BeatRecord(id="b1", blueprint_id="bp1", slug="opening", name="Opening",
                   ideal_pct=0.0, tolerance_pct=0.0, expected_polarity="up"),
    ]
    curve = target_curve(_blueprint(10), beats)
    assert len(curve) == 10
    assert curve[0] == 0.75  # clamped to ordinal 1, anchor overrides implicit start


def test_kishotenketsu_single_flip_anchor_produces_full_curve():
    beats = [
        BeatRecord(id="b1", blueprint_id="bp1", slug="ki", name="Ki", ideal_pct=0.05, tolerance_pct=0.08),
        BeatRecord(id="b2", blueprint_id="bp1", slug="sho", name="Sho", ideal_pct=0.40, tolerance_pct=0.08),
        BeatRecord(id="b3", blueprint_id="bp1", slug="ten", name="Ten", ideal_pct=0.75, tolerance_pct=0.08,
                   expected_polarity="flip"),
        BeatRecord(id="b4", blueprint_id="bp1", slug="ketsu", name="Ketsu", ideal_pct=0.95, tolerance_pct=0.08),
    ]
    curve = target_curve(_blueprint(16), beats)
    assert len(curve) == 16
    assert curve[0] == 0.3
    assert curve[-1] == 0.5
    # ten anchor at round(0.75*16)=12 -> index 11
    assert curve[11] == 0.85


# --- tension_deviations ---

def _score(chapter_id, tension):
    return StructureScore(chapter_id=chapter_id, tension=tension)


def test_no_deviations_inside_delta_returns_empty():
    blueprint = _blueprint(4)
    beats = []
    chapters = _chapters(4)
    # curve: [0.3, ~0.367, ~0.433, 0.5]
    scores = [_score("c0", 0.3), _score("c1", 0.4), _score("c2", 0.45), _score("c3", 0.5)]
    assert tension_deviations(blueprint, beats, scores, chapters) == []


def test_deviation_outside_delta_returns_tuple_with_target():
    blueprint = _blueprint(4)
    beats = []
    chapters = _chapters(4)
    scores = [_score("c0", 0.95)]  # target at ordinal 1 = 0.3, |0.95-0.3|=0.65 > 0.25
    deviations = tension_deviations(blueprint, beats, scores, chapters)
    assert deviations == [("c0", 0.95, 0.3)]


def test_unscored_chapters_are_skipped():
    blueprint = _blueprint(4)
    chapters = _chapters(4)
    scores = [_score("c0", 0.95)]
    deviations = tension_deviations(blueprint, [], scores, chapters)
    ids = [d[0] for d in deviations]
    assert "c1" not in ids and "c2" not in ids and "c3" not in ids


def test_last_wins_by_chapter_uses_most_recent_score():
    blueprint = _blueprint(4)
    chapters = _chapters(4)
    scores = [_score("c0", 0.95), _score("c0", 0.3)]  # second overrides first, no deviation
    assert tension_deviations(blueprint, [], scores, chapters) == []


def test_chapter_beyond_target_length_uses_last_curve_value():
    blueprint = _blueprint(2)
    chapters = _chapters(3)
    scores = [_score("c2", 0.99)]  # ordinal 3, beyond n=2 -> uses curve[-1] == 0.5
    deviations = tension_deviations(blueprint, [], scores, chapters)
    assert deviations == [("c2", 0.99, 0.5)]


def test_no_blueprint_returns_empty_deviations():
    assert tension_deviations(None, [], [_score("c0", 0.99)], _chapters(1)) == []
