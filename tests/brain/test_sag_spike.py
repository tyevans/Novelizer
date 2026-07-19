from hypothesis import given, settings, strategies as st
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.store.models import StructureScore


def test_flat_chapter_amid_high_tension_is_flagged_sag():
    scores = [
        StructureScore(chapter_id="c1", tension=0.8, pacing_label="rising"),
        StructureScore(chapter_id="c2", tension=0.1, pacing_label="flat"),
        StructureScore(chapter_id="c3", tension=0.85, pacing_label="climax"),
    ]
    flags = detect_sag_spike(scores)
    assert flags["c2"] == "sag"
    assert "c1" not in flags and "c3" not in flags


def test_spike_amid_low_tension_is_flagged_spike():
    scores = [
        StructureScore(chapter_id="c1", tension=0.1, pacing_label="lull"),
        StructureScore(chapter_id="c2", tension=0.95, pacing_label="climax"),
        StructureScore(chapter_id="c3", tension=0.15, pacing_label="lull"),
    ]
    flags = detect_sag_spike(scores)
    assert flags["c2"] == "spike"
    assert "c1" not in flags and "c3" not in flags


def test_uniform_tension_flags_nothing():
    scores = [StructureScore(chapter_id=f"c{i}", tension=0.5, pacing_label="steady") for i in range(4)]
    assert detect_sag_spike(scores) == {}


def test_fewer_than_two_scores_flags_nothing():
    assert detect_sag_spike([]) == {}
    assert detect_sag_spike([StructureScore(chapter_id="c1", tension=0.9, pacing_label="climax")]) == {}


@given(
    tensions=st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=2, max_size=15),
)
@settings(max_examples=50)
def test_flag_membership_matches_the_deviation_invariant(tensions):
    """For any list of scores, a chapter is flagged iff its tension deviates
    from the mean of all given scores by at least SAG_SPIKE_DELTA, and the
    direction of the flag (sag vs spike) matches the sign of that deviation."""
    scores = [StructureScore(chapter_id=f"c{i}", tension=t, pacing_label="") for i, t in enumerate(tensions)]
    mean = sum(tensions) / len(tensions)
    flags = detect_sag_spike(scores)
    for s in scores:
        diff = s.tension - mean
        if diff <= -SAG_SPIKE_DELTA:
            assert flags.get(s.chapter_id) == "sag"
        elif diff >= SAG_SPIKE_DELTA:
            assert flags.get(s.chapter_id) == "spike"
        else:
            assert s.chapter_id not in flags

def test_detect_sag_spike_respects_explicit_delta():
    scores = [
        StructureScore(chapter_id="c1", tension=0.5, pacing_label="steady"),
        StructureScore(chapter_id="c2", tension=0.65, pacing_label="steady"),
    ]
    assert detect_sag_spike(scores, delta=0.3) == {}
    assert detect_sag_spike(scores, delta=0.05) == {"c1": "sag", "c2": "spike"}
