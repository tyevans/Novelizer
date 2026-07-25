"""Summarizer: event-sourced rolling chapter summaries — exactly-once per
revision, projection upsert, revision re-summarize."""
from __future__ import annotations
import os
import tempfile
import pytest
from novelizer.agents.schemas import SummarizerOutput
from novelizer.agents.summarizer import Summarizer
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterRevised, EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter


class CountingRunner:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, inputs):
        self.calls += 1
        return {"structured_response": SummarizerOutput(gist=f"gist {self.calls}",
                                                        summary=f"summary {self.calls}")}


async def _stores(path):
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    return events, proj, read


@pytest.mark.asyncio
async def test_summarizes_each_chapter_once_then_idles():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events, proj, read = await _stores(path)
        for i in (1, 2):
            await events.append(EventType.CHAPTER_CREATED, f"c{i}",
                                Chapter(id=f"c{i}", title=f"Ch {i}", prose="word " * 50))
        await proj.catch_up()
        runner = CountingRunner()
        agent = Summarizer(runner, read, Committer(events), events)
        await agent.run_once()
        await proj.catch_up()
        rows = await read.list_chapter_summaries()
        assert {r.chapter_id for r in rows} == {"c1", "c2"}
        calls_after_first = runner.calls
        await agent.run_once()  # nothing new: no further LLM calls, no new events
        assert runner.calls == calls_after_first
        log = await events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED])
        assert len(log) == 2
        await read.close(); await proj.close(); await events.close()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_revision_triggers_resummarize():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events, proj, read = await _stores(path)
        await events.append(EventType.CHAPTER_CREATED, "c1",
                            Chapter(id="c1", title="Ch 1", prose="old prose"))
        await proj.catch_up()
        agent = Summarizer(CountingRunner(), read, Committer(events), events)
        await agent.run_once()
        await events.append(EventType.CHAPTER_REVISED, "c1",
                            ChapterRevised(chapter_id="c1", prose="new prose"))
        await proj.catch_up()
        await agent.run_once()
        log = await events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED])
        assert len(log) == 2  # summarized once per revision
        await proj.catch_up()
        row = await read.get_chapter_summary("c1")
        assert row.gist == "gist 2"
        await read.close(); await proj.close(); await events.close()
    finally:
        os.unlink(path)


class NoneRunner:
    """Simulates a malformed structured response every call."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, inputs):
        self.calls += 1
        return {"structured_response": None}


@pytest.mark.asyncio
async def test_failed_summarize_keeps_readiness_open():
    """Regression: a failed call must leave the watermark clear so the
    chapter actually retries next poll under the scheduler."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events, proj, read = await _stores(path)
        await events.append(EventType.CHAPTER_CREATED, "c1",
                            Chapter(id="c1", title="Ch 1", prose="some prose"))
        await proj.catch_up()
        agent = Summarizer(NoneRunner(), read, Committer(events), events)
        await agent.run_once()
        assert await agent.readiness() > 0.0  # gate stays open for the retry
        log = await events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED])
        assert log == []
        agent._runner = CountingRunner()
        await agent.run_once()
        assert await agent.readiness() == 0.0  # summarized: watermark recorded
        await read.close(); await proj.close(); await events.close()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_empty_summary_is_treated_as_failure():
    """An empty summary must not be committed as canon (it would displace the
    verbatim fallback at every advisory site)."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events, proj, read = await _stores(path)
        await events.append(EventType.CHAPTER_CREATED, "c1",
                            Chapter(id="c1", title="Ch 1", prose="some prose"))
        await proj.catch_up()

        class EmptyRunner:
            async def ainvoke(self, inputs):
                return {"structured_response": SummarizerOutput(gist="g", summary="  ")}

        agent = Summarizer(EmptyRunner(), read, Committer(events), events)
        await agent.run_once()
        log = await events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED])
        assert log == []
        assert await agent.readiness() > 0.0
        await read.close(); await proj.close(); await events.close()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_readiness_is_proportional_to_the_unsummarized_backlog():
    """A flat 0.6 could not express "how much undone work do I have", and with
    max_concurrent_agents=2 it made the Summarizer dispatchable only when fewer
    than two agents scored above 0.6 -- nearly never. Measured consequence:
    world.db chapter_summaries had 0 rows, so every agent reading the `gists`
    block got an empty one.

    Backlog-proportional on chapters lacking a summary, matching the house
    min(1.0, n/3) convention (editor.py, curator.py, triage.py). The signal is
    the same list work() consumes, so the score cannot disagree with the work.
    """
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events, proj, read = await _stores(path)
        agent = Summarizer(CountingRunner(), read, Committer(events), events)
        assert await agent.readiness() == 0.0  # nothing written yet

        scores = []
        for i in range(1, 4):
            await events.append(EventType.CHAPTER_CREATED, f"c{i}",
                                Chapter(id=f"c{i}", title=f"Ch {i}", prose="prose"))
            await proj.catch_up()
            agent._clear_watermark()
            scores.append(await agent.readiness())
        assert scores == sorted(scores) and len(set(scores)) == 3, (
            f"readiness must rise with the backlog, got {scores}"
        )
        assert scores[0] == pytest.approx(1 / 3)
        assert scores[2] == pytest.approx(1.0)
        await read.close(); await proj.close(); await events.close()
    finally:
        os.unlink(path)
