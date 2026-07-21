from hypothesis import given, strategies as st

from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG
from novelizer.tui.widgets.feed_model import SOURCE_BADGES, parse_source_badge

_ALL_TAGS = [LEAK_SOURCE_TAG, PARADOX_SOURCE_TAG, MINED_SOURCE_TAG]


def test_every_source_tag_constant_has_a_badge():
    assert set(SOURCE_BADGES) == set(_ALL_TAGS)


def test_badges_are_the_spec_short_forms():
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


def test_render_remark_bold_pair_spanning_the_clamp_wrap_boundary_renders_bold():
    # A bold pair that starts near the wrap column so textwrap.wrap breaks the
    # line mid-phrase, inserting a newline between the ** markers before
    # md_inline ever sees the text. Confirm it actually wraps mid-pair first.
    note = ("x " * 34) + "**bold phrase that wraps**"
    clamped, _ = clamp_text(note)
    assert "\n" in clamped
    lines = clamped.splitlines()
    assert "**bold phrase that wraps**" not in lines[0]
    assert "**" in lines[0] or "**" in lines[1]

    text = render_event(_ev(EventType.AGENT_REMARKED, {"agent_name": "editor", "note": note}))
    assert "**" not in text.plain


from novelizer.canon.events import EventType, StoredEvent
from novelizer.tui.widgets.feed_model import (
    ALARM_STYLE, chapter_rule, render_event, welcome_lines, worker_error_line,
)


def _ev(event_type, payload, seq=1):
    return StoredEvent(sequence=seq, id=f"e{seq}", event_type=event_type,
                       aggregate_id="agg", payload=payload, created_at="t")


def test_render_chapter_created_has_speaker_column_and_chapter_chip():
    text = render_event(_ev(EventType.CHAPTER_CREATED, {"title": "The Salt Road"}))
    assert text.plain.startswith("✎ Author")
    assert 'drafted "The Salt Road"' in text.plain
    assert text.plain.rstrip().endswith("◆ chapter")


def test_render_world_entry_uses_architect_and_lore_chip():
    text = render_event(_ev(EventType.WORLD_ENTRY_CREATED, {"title": "Brinemarsh"}))
    assert text.plain.startswith("⌂ Architect")
    assert "Brinemarsh" in text.plain
    assert text.plain.rstrip().endswith("◆ lore")


def test_render_chapter_status_changed_speaks_as_editor():
    text = render_event(_ev(EventType.CHAPTER_STATUS_CHANGED,
                            {"title": "One", "editorial_status": "reviewed"}))
    assert text.plain.startswith("§ Editor")
    assert "One" in text.plain


def test_render_unmapped_canon_event_falls_back_to_payload_title_and_domain_chip():
    text = render_event(_ev(EventType.SECRET_CREATED, {"id": "s", "title": "The Heir Lives"}))
    assert text.plain.startswith("· System")
    assert "The Heir Lives" in text.plain
    assert text.plain.rstrip().endswith("◆ secret")


def test_render_remark_is_dim_italic_with_speech_glyph():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "character_keeper",
                             "note": "Elara wouldn't say it that plainly."}))
    assert text.plain.startswith("♥ Keeper")
    assert "💬" in text.plain
    assert "Elara wouldn't say it that plainly." in text.plain
    assert any("italic" in str(span.style) for span in text.spans)


def test_render_remark_unknown_agent_uses_title_case_fallback():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "mystery_agent", "note": "?"}))
    assert "Mystery Agent" in text.plain


def test_render_retcon_created_is_alarm_with_parsed_badge():
    from novelizer.brain.leaks import LEAK_SOURCE_TAG
    text = render_event(_ev(EventType.RETCON_REQUEST_CREATED,
                            {"description": f"{LEAK_SOURCE_TAG} clean and neutral violated"}))
    assert text.plain.startswith("↺ Retconner")
    assert "⚠" in text.plain
    assert "[leak]" in text.plain
    assert "[source: leak_detector]" not in text.plain
    assert any(str(span.style) == ALARM_STYLE for span in text.spans)


def test_render_retcon_without_tag_is_alarm_without_badge():
    text = render_event(_ev(EventType.RETCON_REQUEST_CREATED, {"description": "scar mismatch"}))
    assert "scar mismatch" in text.plain and "⚠" in text.plain
    assert "[" not in text.plain


def test_render_strips_markdown_bold_from_payload_text():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "editor", "note": "the **closing image** lands"}))
    assert "**" not in text.plain
    assert "closing image" in text.plain


def test_render_clamps_long_note_to_two_lines_with_dim_continuation():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "editor", "note": "critique " * 100}))
    assert len(text.plain.splitlines()) <= 2
    assert text.plain.rstrip().endswith("…")


def test_chapter_rule_is_a_dim_horizontal_rule():
    text = chapter_rule(4, "The Name in the Wind")
    assert text.plain == "── ch 4 · The Name in the Wind ──"
    assert str(text.style) == "dim"


def test_welcome_lines_are_the_spec_director_voice_verbatim():
    lines = welcome_lines()
    assert [t.plain for t in lines] == [
        "★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst.",
        "★ It's quiet. Give them a world:  :seed a lighthouse keeper who taxes the tide",
    ]
    assert all(str(t.style) == "bold" for t in lines)


def test_worker_error_line_is_a_plain_compatible_alarm():
    text = worker_error_line("feed", RuntimeError("boom"))
    assert text.plain == "⚠ feed error: boom"
    assert str(text.style) == ALARM_STYLE


def test_format_event_is_the_plain_rendering():
    from novelizer.tui.app import format_event
    ev = _ev(EventType.CHAPTER_CREATED, {"title": "One"})
    assert format_event(ev) == render_event(ev).plain


def test_render_remark_with_none_agent_name_and_note_does_not_raise():
    text = render_event(_ev(EventType.AGENT_REMARKED, {"agent_name": None, "note": None}))
    assert isinstance(text.plain, str)


def test_render_retcon_with_none_description_does_not_raise():
    text = render_event(_ev(EventType.RETCON_REQUEST_CREATED, {"description": None}))
    assert isinstance(text.plain, str)


def test_render_structure_scored_with_non_numeric_tension_does_not_raise():
    text = render_event(_ev(EventType.ANNOTATION_STRUCTURE_SCORED,
                            {"tension": "not-a-number", "pacing_label": "brisk"}))
    assert isinstance(text.plain, str)
