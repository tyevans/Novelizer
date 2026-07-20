"""The World Architect has to see the story it is building a world for.

poll() read only existing world entries, so the agent's entire model of the
novel was 100 characters of each of 20 entries and no chapters at all. It could
not tell which places or factions the prose was leaning on, so "identify thin
areas and expand them" could only be free association. It also had no
watermark, so it re-fired on an unchanged story.

See docs/agent-prompting/proposal-world-architect.md §1, §3.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.agents.world_architect import WorldArchitect
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter, DirectorSignal, SignalKind, WorldEntry


class FakeRunner:
    def __init__(self, draft=None):
        self._draft = draft if draft is not None else WorldEntriesDraft()
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._draft}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


async def _prompt(read, committer, runner):
    architect = WorldArchitect(runner, read, committer)
    ctx = await architect.poll()
    await architect.work(ctx)
    return runner.calls[-1]["messages"][0]["content"]


class TestStoryAwareness:
    async def test_chapter_index_reaches_the_prompt(self, stack):
        """Lore should serve the chapters that exist, which requires knowing
        they exist."""
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1",
            Chapter(id="c1", title="The Salt Road", prose="p"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "The Salt Road" in sent
        assert "ch001" in sent

    async def test_no_chapter_block_before_any_prose_exists(self, stack):
        events, proj, read, committer = stack
        sent = await _prompt(read, committer, FakeRunner())
        assert "Chapter index" not in sent

    async def test_poll_reads_chapters(self, stack):
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"),
        )
        await proj.catch_up()
        architect = WorldArchitect(FakeRunner(), read, committer)
        assert [c.id for c in (await architect.poll())["chapters"]] == ["c1"]


class TestWatermark:
    async def test_readiness_is_gated_once_nothing_has_changed(self, stack):
        """Without a fingerprint the Architect re-fires on an unchanged story,
        padding the world with lore no chapter asked for."""
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"),
        )
        await proj.catch_up()
        architect = WorldArchitect(FakeRunner(), read, committer)
        assert await architect.readiness() > 0.0
        await architect._record_watermark()
        assert await architect.readiness() == 0.0

    async def test_a_new_chapter_reopens_readiness(self, stack):
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"),
        )
        await proj.catch_up()
        architect = WorldArchitect(FakeRunner(), read, committer)
        await architect._record_watermark()
        assert await architect.readiness() == 0.0
        await events.append(
            EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="T2", prose="p"),
        )
        await proj.catch_up()
        assert await architect.readiness() > 0.0

    async def test_a_director_seed_reopens_readiness(self, stack):
        """A seed is always the Architect's work, watermark or not."""
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"),
        )
        await proj.catch_up()
        architect = WorldArchitect(FakeRunner(), read, committer)
        await architect._record_watermark()
        assert await architect.readiness() == 0.0
        await events.append(
            EventType.DIRECTOR_SIGNAL_CREATED, "s1",
            DirectorSignal(id="s1", kind=SignalKind.seed, body="expand the salt flats",
                           target_agent="world_architect"),
        )
        await proj.catch_up()
        assert await architect.readiness() > 0.0
