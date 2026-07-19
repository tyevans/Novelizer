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
