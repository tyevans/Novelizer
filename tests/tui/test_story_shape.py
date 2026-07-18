from novelizer.tui.widgets.story_shape import story_shape_line
from novelizer.store.models import StructureScore


def test_unflagged_score_line_has_no_marker():
    s = StructureScore(chapter_id="c1", tension=0.5, pacing_label="steady")
    line = story_shape_line(s, None)
    assert "c1" in line and "0.50" in line and "steady" in line
    assert "SAG" not in line and "SPIKE" not in line


def test_sag_flagged_score_line_shows_marker():
    s = StructureScore(chapter_id="c2", tension=0.1, pacing_label="flat")
    line = story_shape_line(s, "sag")
    assert "SAG" in line


def test_spike_flagged_score_line_shows_marker():
    s = StructureScore(chapter_id="c2", tension=0.95, pacing_label="climax")
    line = story_shape_line(s, "spike")
    assert "SPIKE" in line
