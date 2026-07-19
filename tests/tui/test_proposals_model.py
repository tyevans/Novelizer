from hypothesis import given, strategies as st

from novelizer.tui.widgets.proposals_model import BANNER_STYLE, banner_line


def test_banner_line_singular():
    line = banner_line(1)
    assert line.plain == "▼ 1 proposal awaiting approval — press a"
    assert str(line.style) == BANNER_STYLE


def test_banner_line_plural_matches_spec_mockup():
    assert banner_line(2).plain == "▼ 2 proposals awaiting approval — press a"


@given(st.integers(min_value=1, max_value=99))
def test_banner_line_always_counts_and_always_high_contrast(n):
    line = banner_line(n)
    assert line.plain.startswith(f"▼ {n} proposal")
    assert line.plain.endswith("— press a")
    assert str(line.style) == BANNER_STYLE
