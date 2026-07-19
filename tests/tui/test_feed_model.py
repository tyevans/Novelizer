from hypothesis import given, strategies as st

from novelizer.agents.editor import VOICE_SOURCE_TAG
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG
from novelizer.tui.widgets.feed_model import SOURCE_BADGES, parse_source_badge

_ALL_TAGS = [VOICE_SOURCE_TAG, LEAK_SOURCE_TAG, PARADOX_SOURCE_TAG, MINED_SOURCE_TAG]


def test_every_source_tag_constant_has_a_badge():
    assert set(SOURCE_BADGES) == set(_ALL_TAGS)


def test_badges_are_the_spec_short_forms():
    assert SOURCE_BADGES[VOICE_SOURCE_TAG] == "[drift]"
    assert SOURCE_BADGES[LEAK_SOURCE_TAG] == "[leak]"
    assert SOURCE_BADGES[PARADOX_SOURCE_TAG] == "[paradox]"
    assert SOURCE_BADGES[MINED_SOURCE_TAG] == "[mined]"


@given(tag=st.sampled_from(_ALL_TAGS), rest=st.text(max_size=200))
def test_badge_parser_round_trips_every_source_tag(tag, rest):
    badge, remainder = parse_source_badge(f"{tag} {rest}")
    assert badge == SOURCE_BADGES[tag]
    assert remainder == rest.lstrip()


def test_untagged_description_passes_through_unbadged():
    assert parse_source_badge("scar mismatch") == (None, "scar mismatch")


def test_unknown_source_tag_is_left_intact_not_badged():
    desc = "[source: gremlin] something odd"
    assert parse_source_badge(desc) == (None, desc)


from novelizer.tui.widgets.feed_model import CLAMP_LINES, CLAMP_WIDTH, clamp_text, md_inline


def test_clamp_short_text_is_unchanged_and_not_truncated():
    assert clamp_text("a quiet line") == ("a quiet line", False)


def test_clamp_empty_text():
    assert clamp_text("") == ("", False)


def test_clamp_collapses_newlines_and_truncates_long_payloads():
    long = "line one\nline two\nline three\n" + "critique " * 200
    clamped, truncated = clamp_text(long)
    assert truncated is True
    lines = clamped.splitlines()
    assert len(lines) == CLAMP_LINES
    assert all(len(line) <= CLAMP_WIDTH for line in lines)
    assert clamped.startswith("line one line two line three")


@given(st.text(max_size=2000))
def test_clamp_never_exceeds_two_lines_of_width(s):
    clamped, _ = clamp_text(s)
    lines = clamped.splitlines()
    assert len(lines) <= CLAMP_LINES
    assert all(len(line) <= CLAMP_WIDTH for line in lines)


def test_md_inline_renders_bold_and_never_shows_raw_stars():
    text = md_inline("the **closing image** lands")
    assert text.plain == "the closing image lands"
    assert "**" not in text.plain
    bold_spans = [sp for sp in text.spans if "bold" in str(sp.style)]
    assert len(bold_spans) == 1
    assert text.plain[bold_spans[0].start:bold_spans[0].end] == "closing image"


def test_md_inline_leaves_a_lone_star_pair_literal():
    # only *paired* ** markers are markdown; a single ** is not eaten
    assert md_inline("2 ** 3 is eight").plain == "2 ** 3 is eight"
