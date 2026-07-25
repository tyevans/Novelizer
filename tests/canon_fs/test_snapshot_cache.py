"""The canon backends rebuilt their whole snapshot on EVERY call.

Measured on stories/death-becomes-her: 3,599 tool calls against 117 canon
events, and only 43 distinct replay generations were ever current while a tool
call was in flight -- so 98.8% of those snapshot builds re-derived a view of
canon that had not changed. Each build is 7 full-table reads plus a path-index
rebuild, against a SQLite file the projector is writing to and thirteen agents
are reading from concurrently; the same read that costs 0.8ms on an idle copy
shows a 100ms median and a 1.3s p90 in the live telemetry.

The invalidation key is the projector's own replay cursor
(projector_state.last_sequence), which the Projector advances in the SAME
transaction as each projection write -- "the read model includes everything
through sequence N" is one fact, not two. Canon is append-only, so a cursor
that has not moved is a proof that no projection has changed. A cache keyed on
it cannot serve content from a different generation than the one it claims.
"""
import os
import tempfile

import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterRevised, EventType, ThreadPlanted
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon_fs.backend import CanonBackend
from novelizer.store.models import Chapter


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


class CountingRead:
    """Wraps a ReadStore and counts the list_* calls a snapshot build makes."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.list_calls = 0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not name.startswith("list_") and name != "knowledge_matrix":
            return attr

        async def counted(*args, **kwargs):
            self.list_calls += 1
            return await attr(*args, **kwargs)

        return counted


async def _seed_chapter(events, proj, chapter_id, title, prose):
    await events.append(
        EventType.CHAPTER_CREATED, chapter_id,
        Chapter(id=chapter_id, title=title, prose=prose),
    )
    await proj.catch_up()


async def test_projection_generation_is_the_projector_replay_cursor(stack):
    events, proj, read = stack
    assert await read.projection_generation() == 0
    await _seed_chapter(events, proj, "ch1", "One", "p")
    first = await read.projection_generation()
    assert first > 0
    await _seed_chapter(events, proj, "ch2", "Two", "p")
    assert await read.projection_generation() > first


async def test_repeated_reads_at_one_generation_build_the_snapshot_once(stack):
    events, proj, read = stack
    await _seed_chapter(events, proj, "ch1", "One", "prose one")
    counting = CountingRead(read)
    backend = CanonBackend(counting)

    await backend.aread("/chapters/001-one.md")
    after_first = counting.list_calls
    assert after_first > 0, "the first read must actually build a snapshot"

    for _ in range(5):
        await backend.aread("/chapters/001-one.md")
    assert counting.list_calls == after_first, (
        "canon did not change, so five further reads must reuse the cached "
        "snapshot instead of re-running the list queries"
    )


async def test_ls_grep_and_glob_share_the_cached_snapshot(stack):
    """The waste is not limited to repeated reads: ls/grep/glob each rebuilt
    the same snapshot too, and in the live story they are 1,190 of the calls."""
    events, proj, read = stack
    await _seed_chapter(events, proj, "ch1", "One", "prose one")
    counting = CountingRead(read)
    backend = CanonBackend(counting)

    await backend.als("/chapters")
    after_first = counting.list_calls
    await backend.agrep("prose", "/chapters")
    await backend.aglob("*.md", "/chapters")
    await backend.aread("/chapters/001-one.md")
    assert counting.list_calls == after_first


async def test_a_new_canon_event_invalidates_the_cache(stack):
    """The staleness guard, and the whole reason the key is the replay cursor:
    a revision committed after a cached read must be visible to the next one."""
    events, proj, read = stack
    await _seed_chapter(events, proj, "ch1", "One", "the original prose")
    backend = CanonBackend(read)
    first = await backend.aread("/chapters/001-one.md")
    assert "the original prose" in first.file_data["content"]

    await events.append(
        EventType.CHAPTER_REVISED, "ch1",
        ChapterRevised(chapter_id="ch1", prose="the revised prose"),
    )
    await proj.catch_up()

    second = await backend.aread("/chapters/001-one.md")
    assert "the revised prose" in second.file_data["content"]
    assert "the original prose" not in second.file_data["content"]


async def test_a_record_appearing_later_is_visible_not_masked_by_the_cache(stack):
    """A cached path index must not hide a record added after it was built."""
    events, proj, read = stack
    await _seed_chapter(events, proj, "ch1", "One", "p")
    backend = CanonBackend(read)
    assert (await backend.aread("/threads/late-thread.md")).error is not None

    await events.append(EventType.THREAD_PLANTED, "late",
                        ThreadPlanted(id="late", name="Late Thread"))
    await proj.catch_up()

    assert (await backend.aread("/threads/late-thread.md")).error is None


async def test_a_snapshot_torn_by_a_concurrent_commit_is_not_cached(stack):
    """Safety rule: the generation is read before AND after the build, and the
    result is only cached when it did not move. Otherwise a build that
    straddled a commit -- its seven queries are not one transaction -- would be
    stored under the older generation while holding some newer content, and
    every later call at that generation would be served provably wrong bytes.
    """
    events, proj, read = stack
    await _seed_chapter(events, proj, "ch1", "One", "p")
    counting = CountingRead(read)
    backend = CanonBackend(counting)

    original = backend._snapshot_cache._build

    async def build_then_commit():
        snap = await original()
        # A commit landing mid-build is exactly the race being guarded.
        await events.append(EventType.THREAD_PLANTED, "mid",
                            ThreadPlanted(id="mid", name="Mid Thread"))
        await proj.catch_up()
        return snap

    backend._snapshot_cache._build = build_then_commit
    await backend.aread("/chapters/001-one.md")
    backend._snapshot_cache._build = original

    calls_before = counting.list_calls
    await backend.aread("/chapters/001-one.md")
    assert counting.list_calls > calls_before, (
        "the torn snapshot must not have been cached; the next read has to "
        "rebuild rather than trust it"
    )
