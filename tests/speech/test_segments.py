from hypothesis import given, strategies as st

from novelizer.speech.markers import parse_markers
from novelizer.speech.segments import NARRATION, segment_prose


def _segments(marked):
    parsed = parse_markers(marked)
    return parsed.clean_prose, segment_prose(parsed.clean_prose, parsed.spans)


def test_fills_narration_between_spans():
    clean, segs = _segments(
        'He waited. <speech char="Mira">"Twenty."</speech> Rain fell.'
    )
    assert [s.kind for s in segs] == ["narration", "speech", "narration"]
    assert segs[0].text == "He waited. "
    assert segs[1].char_name == "Mira"
    assert segs[2].text == " Rain fell."


def test_indexes_are_dense_and_ordered():
    _, segs = _segments(
        '<speech char="A">"One."</speech> mid <speech char="B">"Two."</speech>'
    )
    assert [s.index for s in segs] == list(range(len(segs)))


def test_untagged_prose_is_one_narration_segment():
    _, segs = _segments("Just prose.")
    assert len(segs) == 1
    assert segs[0].kind == NARRATION
    assert segs[0].char_name == ""


def test_empty_prose_yields_no_segments():
    assert segment_prose("", []) == []


def test_adjacent_spans_produce_no_empty_narration():
    _, segs = _segments(
        '<speech char="A">"One."</speech><speech char="B">"Two."</speech>'
    )
    assert [s.kind for s in segs] == ["speech", "speech"]


@given(st.text(alphabet=st.characters(blacklist_characters="<>\"", min_codepoint=32), max_size=60))
def test_segments_concatenate_to_the_clean_prose(text):
    clean, segs = _segments(text)
    assert "".join(s.text for s in segs) == clean
