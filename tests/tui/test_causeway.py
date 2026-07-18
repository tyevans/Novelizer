from novelizer.tui.widgets.causeway import causeway_line
from novelizer.store.models import CausalEdgeRecord


def test_ordinary_edge_line_has_no_marker():
    edge = CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2", note="sets up the reveal")
    line = causeway_line(edge, False)
    assert "c1" in line and "c2" in line and "sets up the reveal" in line
    assert "PARADOX" not in line


def test_paradox_edge_line_shows_marker():
    edge = CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1")
    line = causeway_line(edge, True)
    assert "PARADOX" in line
