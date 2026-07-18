import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.editor import Editor
from novelizer.agents.schemas import EditorVerdict
from novelizer.store.models import Chapter, EditorialStatus


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_scales_with_drafts(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    assert await Editor(FakeRunner(None), read, committer).readiness() == 1.0


async def test_approve_promotes_to_reviewed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="approve", notes="clean")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    ch = await read.get_chapter("c1")
    assert ch.editorial_status == EditorialStatus.reviewed and ch.editor_notes == "clean"


async def test_revise_keeps_draft_and_notes_author(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="revise", notes="middle sags")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert (await read.get_chapter("c1")).editorial_status == EditorialStatus.draft
    notes = await read.list_unconsumed_signals(target_agent="author")
    assert any("middle sags" in s.body for s in notes)
