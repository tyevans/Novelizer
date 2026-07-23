"""Property proof: chapter_summaries is an upsert-by-chapter_id projection of
chapter.summarized, and a from-zero projector rebuild reproduces it exactly."""
from __future__ import annotations
import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterSummarized, EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore


async def _run(script: list[tuple[str, str]]) -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()
        for chapter_id, gist in script:
            await events.append(
                EventType.CHAPTER_SUMMARIZED, chapter_id,
                ChapterSummarized(chapter_id=chapter_id, gist=gist, summary=f"para: {gist}"),
            )
        await proj.catch_up()
        expected = {}
        for chapter_id, gist in script:
            expected[chapter_id] = gist  # last event per chapter wins
        rows = await read.list_chapter_summaries()
        assert {r.chapter_id: r.gist for r in rows} == expected
        if script:
            last_id = script[-1][0]
            got = await read.get_chapter_summary(last_id)
            assert got is not None and got.chapter_id == last_id
        assert await read.get_chapter_summary("missing") is None

        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.list_chapter_summaries()
        assert {r.chapter_id: r.gist for r in rebuilt} == expected
        await proj2.close()
        await read.close()
        await proj.close()
        await events.close()
    finally:
        os.unlink(path)


@given(st.lists(st.tuples(st.sampled_from(["c1", "c2", "c3"]),
                          st.text(min_size=1, max_size=20)), max_size=12))
@settings(max_examples=25, deadline=None)
def test_upsert_and_replay_stable(script):
    asyncio.run(_run(script))
