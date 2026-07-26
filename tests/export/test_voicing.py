import json

import pytest
from hypothesis import given, strategies as st

from novelizer.canon.events import AttributedSegment
from novelizer.export.voicing import build_voicing_export, render_annotated, render_json
from novelizer.store.models import Chapter


def _chapter(cid="ch1"):
    return Chapter(id=cid, title="One", prose="")


def _seg(index, kind, cid, name, text):
    return AttributedSegment(index=index, kind=kind, character_id=cid, character_name=name,
                             start_offset=0, end_offset=len(text), text=text)


def _mixed():
    return {"ch1": [
        _seg(0, "narration", None, "", "He waited. "),
        _seg(1, "speech", "mira", "Mira", '"One."'),
        _seg(2, "speech", "mira", "Mira", '"Two."'),
        _seg(3, "speech", "jon", "Jon", '"Three."'),
    ]}


def test_segment_mode_emits_one_chunk_per_segment():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    assert len(chunks) == 4
    assert chunks[1].character_id == "mira"


def test_chapter_mode_emits_one_chunk_per_chapter():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="chapter", chunk_size=0)
    assert len(chunks) == 1
    assert chunks[0].segment_indexes == [0, 1, 2, 3]


def test_budget_mode_packs_same_voice_segments():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="budget", chunk_size=100)
    # narration | mira("One." + "Two.") | jon
    assert len(chunks) == 3
    assert chunks[1].text == '"One.""Two."'
    assert chunks[1].character_id == "mira"


def test_budget_mode_never_merges_across_a_speaker_change():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="budget", chunk_size=10_000)
    speakers = [c.character_id for c in chunks]
    assert speakers == [None, "mira", "jon"]


def test_budget_mode_never_merges_across_a_chapter_boundary():
    chapters = [_chapter("ch1"), _chapter("ch2")]
    segments = {
        "ch1": [_seg(0, "speech", "mira", "Mira", '"A."')],
        "ch2": [_seg(0, "speech", "mira", "Mira", '"B."')],
    }
    chunks = build_voicing_export(chapters, segments, chunk_by="budget", chunk_size=10_000)
    assert len(chunks) == 2
    assert [c.chapter_id for c in chunks] == ["ch1", "ch2"]


def test_chapter_ordinal_follows_chapter_order():
    chapters = [_chapter("ch1"), _chapter("ch2")]
    segments = {"ch1": [_seg(0, "narration", None, "", "a")],
                "ch2": [_seg(0, "narration", None, "", "b")]}
    chunks = build_voicing_export(chapters, segments, chunk_by="segment", chunk_size=0)
    assert [c.chapter_ordinal for c in chunks] == [1, 2]


def test_an_oversized_single_segment_is_not_dropped():
    segments = {"ch1": [_seg(0, "speech", "mira", "Mira", "x" * 500)]}
    chunks = build_voicing_export([_chapter()], segments, chunk_by="budget", chunk_size=10)
    assert len(chunks) == 1
    assert len(chunks[0].text) == 500


def test_unknown_chunk_mode_is_rejected():
    with pytest.raises(ValueError):
        build_voicing_export([_chapter()], _mixed(), chunk_by="nonsense", chunk_size=0)


def test_render_annotated_rebuilds_the_marked_prose():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    assert render_annotated(chunks) == (
        'He waited. '
        '<speech char="Mira">"One."</speech>'
        '<speech char="Mira">"Two."</speech>'
        '<speech char="Jon">"Three."</speech>'
    )


def test_render_annotated_refuses_chapter_chunks():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="chapter", chunk_size=0)
    with pytest.raises(ValueError):
        render_annotated(chunks)


def test_render_annotated_leaves_narration_bare():
    segments = {"ch1": [_seg(0, "narration", None, "", "Just prose.")]}
    chunks = build_voicing_export([_chapter()], segments, chunk_by="segment", chunk_size=0)
    assert render_annotated(chunks) == "Just prose."


def test_render_annotated_round_trips_through_the_parser():
    """The rendering and the parser are the two halves of one contract."""
    from novelizer.speech.markers import parse_markers

    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    reparsed = parse_markers(render_annotated(chunks))
    assert reparsed.problems == []
    assert reparsed.clean_prose == "".join(s.text for s in _mixed()["ch1"])
    assert [s.char_name for s in reparsed.spans] == ["Mira", "Mira", "Jon"]


def test_render_json_round_trips():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    data = json.loads(render_json(chunks, title="Book"))
    assert data["title"] == "Book"
    assert len(data["chunks"]) == 4
    assert data["chunks"][1]["character_name"] == "Mira"


@given(st.integers(min_value=1, max_value=200))
def test_every_chunk_is_within_budget_or_a_single_segment(size):
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="budget", chunk_size=size)
    for chunk in chunks:
        assert len(chunk.text) <= size or len(chunk.segment_indexes) == 1


@given(st.sampled_from(["segment", "chapter", "budget"]), st.integers(min_value=1, max_value=200))
def test_chunking_never_loses_or_reorders_text(mode, size):
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by=mode, chunk_size=size)
    expected = "".join(s.text for s in _mixed()["ch1"])
    assert "".join(c.text for c in chunks) == expected
