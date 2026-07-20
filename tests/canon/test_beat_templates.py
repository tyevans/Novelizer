from hypothesis import given
from hypothesis import strategies as st

from novelizer.canon.beat_templates import BEAT_TEMPLATES, beat_window


def test_six_position_template_shape():
    beats = BEAT_TEMPLATES["six-position"]
    assert [b.slug for b in beats] == [
        "catalyst", "threshold", "midpoint", "low-point", "final-turn", "climax"]
    assert beats[2].ideal_pct == 0.50 and beats[2].expected_polarity == "flip"
    assert all(0.0 < b.ideal_pct < 1.0 for b in beats)
    assert [b.ideal_pct for b in beats] == sorted(b.ideal_pct for b in beats)


def test_kishotenketsu_has_no_required_polarity_except_ten():
    beats = BEAT_TEMPLATES["kishotenketsu"]
    assert [b.slug for b in beats] == ["ki", "sho", "ten", "ketsu"]
    assert beats[2].expected_polarity == "flip"
    assert all(b.expected_polarity == "" for i, b in enumerate(beats) if i != 2)


def test_beat_window_basic():
    assert beat_window(0.50, 0.05, 20) == (9, 11)      # center 10, tol 1
    assert beat_window(0.90, 0.05, 20) == (17, 19)


def test_beat_window_clamps_to_book():
    lo, hi = beat_window(0.05, 0.05, 10)
    assert lo >= 1
    lo, hi = beat_window(0.95, 0.10, 10)
    assert hi <= 10


@given(st.floats(0.01, 0.99), st.floats(0.01, 0.2), st.integers(3, 200))
def test_beat_window_invariants(pct, tol, n):
    lo, hi = beat_window(pct, tol, n)
    assert 1 <= lo <= hi <= n
