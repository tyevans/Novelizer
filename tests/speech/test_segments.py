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


_plain_run = st.text(
    alphabet=st.characters(blacklist_characters="<>\"", min_codepoint=32), max_size=15
)
_tag_body = _plain_run.filter(lambda s: s.strip() != "")
_tag_kind = st.sampled_from(["speech", "thought"])
_char_name = st.sampled_from(["Mira", "Jorin", "Q"])


def _make_tag(kind, name, body):
    return f'<{kind} char="{name}">{body}</{kind}>'


_tag_block = st.builds(_make_tag, _tag_kind, _char_name, _tag_body)
_block = st.one_of(_plain_run, _tag_block)


@given(st.lists(_block, min_size=0, max_size=8))
def test_segments_concatenate_to_the_clean_prose(blocks):
    marked = "".join(blocks)
    clean, segs = _segments(marked)
    assert "".join(s.text for s in segs) == clean
    assert [s.index for s in segs] == list(range(len(segs)))
    assert all(s.text for s in segs)
    assert all(clean[s.start:s.end] == s.text for s in segs)
