from novelizer.speech.markers import parse_markers


def test_extracts_speech_span_and_strips_tags():
    marked = 'She turned. <speech char="Mira">"Twenty dollars."</speech> The rain kept on.'
    result = parse_markers(marked)
    assert result.clean_prose == 'She turned. "Twenty dollars." The rain kept on.'
    assert result.problems == []
    assert len(result.spans) == 1
    span = result.spans[0]
    assert span.kind == "speech"
    assert span.char_name == "Mira"
    assert span.text == '"Twenty dollars."'
    assert result.clean_prose[span.start:span.end] == span.text


def test_extracts_thought_spans_too():
    marked = '<thought char="Mira">Twenty. She had four.</thought>'
    result = parse_markers(marked)
    assert result.clean_prose == "Twenty. She had four."
    assert result.spans[0].kind == "thought"
    assert result.spans[0].char_name == "Mira"


def test_untagged_prose_yields_no_spans():
    result = parse_markers("The rain kept on.")
    assert result.clean_prose == "The rain kept on."
    assert result.spans == []
    assert result.problems == []


def test_unclosed_tag_is_reported_as_a_problem():
    result = parse_markers('<speech char="Mira">"Twenty dollars."')
    assert result.problems
    assert "unclosed" in result.problems[0].lower()


def test_nested_tag_is_reported_as_a_problem():
    marked = '<speech char="Mira">"He said <speech char="Jon">go</speech> and left."</speech>'
    result = parse_markers(marked)
    assert result.problems
    assert "nested" in result.problems[0].lower()


def test_multiple_spans_have_ascending_non_overlapping_offsets():
    marked = (
        '<speech char="A">"One."</speech> mid '
        '<speech char="B">"Two."</speech>'
    )
    result = parse_markers(marked)
    assert len(result.spans) == 2
    assert result.spans[0].end <= result.spans[1].start
    for span in result.spans:
        assert result.clean_prose[span.start:span.end] == span.text


from hypothesis import given, strategies as st

_plain = st.text(
    alphabet=st.characters(blacklist_characters="<>\"", min_codepoint=32),
    min_size=0, max_size=40,
)
_names = st.sampled_from(["Mira", "Jon", "The Warden"])


@st.composite
def _marked_prose(draw):
    parts = []
    for _ in range(draw(st.integers(min_value=0, max_value=5))):
        parts.append(draw(_plain))
        if draw(st.booleans()):
            kind = draw(st.sampled_from(["speech", "thought"]))
            name = draw(_names)
            body = draw(_plain)
            parts.append(f'<{kind} char="{name}">{body}</{kind}>')
    return "".join(parts)


@given(_marked_prose())
def test_offsets_always_address_their_own_text(marked):
    result = parse_markers(marked)
    for span in result.spans:
        assert result.clean_prose[span.start:span.end] == span.text


@given(_marked_prose())
def test_spans_never_overlap_and_are_ordered(marked):
    result = parse_markers(marked)
    previous_end = 0
    for span in result.spans:
        assert span.start >= previous_end
        previous_end = span.end
