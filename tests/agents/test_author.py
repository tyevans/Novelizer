import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.agents.author import Author, ChapterDraft
from novelizer.store.models import Chapter, DirectorSignal, SignalKind


class FakeRunner:
    def __init__(self, draft): self._draft = draft; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._draft}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_drops_with_draft_backlog(stack):
    events, proj, read = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    author = Author(FakeRunner(None), read, events)
    assert await author.readiness() == 0.0


async def test_run_once_appends_and_projects_a_chapter(stack):
    events, proj, read = stack
    draft = ChapterDraft(title="The Salt Road", prose="The road held its salt like a grudge.")
    author = Author(FakeRunner(draft), read, events)
    await author.run_once()
    await proj.catch_up()
    titles = [c.title for c in await read.list_chapters()]
    assert "The Salt Road" in titles


async def test_run_once_consumes_targeted_signals(stack):
    events, proj, read = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="a storm is coming"))
    await proj.catch_up()
    author = Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, events)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_unconsumed_signals(target_agent="author") == []


async def test_work_returns_none_passes_through_to_noop_commit(stack):
    events, proj, read = stack
    author = Author(FakeRunner(None), read, events)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_chapters() == []
