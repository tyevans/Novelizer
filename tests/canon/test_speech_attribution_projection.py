import os
import tempfile
import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import AttributedSegment, ChapterAttributed, ChapterRevised, EventType
from novelizer.canon.policy import _NEVER_GATED
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _stores(path):
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    return events, proj, read


def _segments():
    return [
        AttributedSegment(index=0, kind="narration", character_id=None,
                          character_name="", start_offset=0, end_offset=4, text="He. "),
        AttributedSegment(index=1, kind="speech", character_id="mira",
                          character_name="Mira", start_offset=4, end_offset=9, text='"Hi."'),
    ]


@pytest.mark.asyncio
async def test_attribution_replaces_prose_and_stores_segments(db_path):
    events, proj, read = await _stores(db_path)
    try:
        chapter = Chapter(id="ch1", title="One", prose='He. <speech char="Mira">"Hi."</speech>')
        await events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
        await events.append(
            EventType.CHAPTER_ATTRIBUTED, "ch1",
            ChapterAttributed(chapter_id="ch1", prose='He. "Hi."', segments=_segments(), problems=[]),
        )
        await proj.catch_up()

        stored = await read.get_chapter("ch1")
        assert stored.prose == 'He. "Hi."'

        segments = await read.list_speech_segments("ch1")
        assert [s.index for s in segments] == [0, 1]
        assert segments[1].character_id == "mira"
        assert stored.prose[segments[1].start_offset:segments[1].end_offset] == '"Hi."'
    finally:
        await read.close(); await proj.close(); await events.close()


@pytest.mark.asyncio
async def test_replaying_the_same_event_twice_is_idempotent(db_path):
    events, proj, read = await _stores(db_path)
    try:
        chapter = Chapter(id="ch1", title="One", prose='He. <speech char="Mira">"Hi."</speech>')
        await events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
        payload = ChapterAttributed(chapter_id="ch1", prose='He. "Hi."', segments=_segments(), problems=[])
        await events.append(EventType.CHAPTER_ATTRIBUTED, "ch1", payload)
        await events.append(EventType.CHAPTER_ATTRIBUTED, "ch1", payload)
        await proj.catch_up()

        segments = await read.list_speech_segments("ch1")
        assert len(segments) == 2, "handler must replace this chapter's rows, not append"

        # The property that actually matters for a projection: a from-zero
        # rebuild over the same log lands on the same table state, not just
        # "the live handler didn't append twice."
        await proj._reset_state()
        await proj.catch_up()
        rebuilt = await read.list_speech_segments("ch1")
        assert [(s.index, s.character_id, s.text) for s in rebuilt] == \
            [(s.index, s.character_id, s.text) for s in segments]
    finally:
        await read.close(); await proj.close(); await events.close()


@pytest.mark.asyncio
async def test_re_attribution_replaces_the_previous_segment_set(db_path):
    events, proj, read = await _stores(db_path)
    try:
        chapter = Chapter(id="ch1", title="One", prose="x")
        await events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
        await events.append(
            EventType.CHAPTER_ATTRIBUTED, "ch1",
            ChapterAttributed(chapter_id="ch1", prose='He. "Hi."', segments=_segments(), problems=[]),
        )
        await events.append(
            EventType.CHAPTER_ATTRIBUTED, "ch1",
            ChapterAttributed(
                chapter_id="ch1", prose="Only narration.",
                segments=[AttributedSegment(index=0, kind="narration", character_id=None,
                                            character_name="", start_offset=0, end_offset=15,
                                            text="Only narration.")],
                problems=[],
            ),
        )
        await proj.catch_up()

        segments = await read.list_speech_segments("ch1")
        assert len(segments) == 1
        assert segments[0].kind == "narration"
    finally:
        await read.close(); await proj.close(); await events.close()


def test_attribution_is_never_gated():
    assert EventType.CHAPTER_ATTRIBUTED in _NEVER_GATED


@pytest.mark.asyncio
async def test_revision_invalidates_prior_attribution(db_path):
    events, proj, read = await _stores(db_path)
    try:
        chapter = Chapter(id="ch1", title="One", prose='He. <speech char="Mira">"Hi."</speech>')
        await events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
        await events.append(
            EventType.CHAPTER_ATTRIBUTED, "ch1",
            ChapterAttributed(chapter_id="ch1", prose='He. "Hi."', segments=_segments(), problems=[]),
        )
        await events.append(
            EventType.CHAPTER_REVISED, "ch1",
            ChapterRevised(chapter_id="ch1", prose="A wholly rewritten scene."),
        )
        await proj.catch_up()

        assert await read.list_speech_segments("ch1") == [], (
            "a revision must drop the prior chapter's attribution -- stale offsets "
            "would otherwise pair old speaker assignments with new prose"
        )
    finally:
        await read.close(); await proj.close(); await events.close()


@pytest.mark.asyncio
async def test_attribution_for_unknown_chapter_stores_no_segments(db_path):
    events, proj, read = await _stores(db_path)
    try:
        # No chapter.created for "ghost" -- the no-op branch in the handler
        # must not leave orphaned segment rows behind either.
        await events.append(
            EventType.CHAPTER_ATTRIBUTED, "ghost",
            ChapterAttributed(chapter_id="ghost", prose="x", segments=_segments(), problems=[]),
        )
        await proj.catch_up()

        assert await read.get_chapter("ghost") is None
        assert await read.list_speech_segments("ghost") == []
    finally:
        await read.close(); await proj.close(); await events.close()
