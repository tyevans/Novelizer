import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.schemas import WorldEntriesDraft, WorldEntryDraft


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


async def test_readiness_high_when_world_empty(stack):
    events, proj, read, committer = stack
    agent = WorldArchitect(FakeRunner(WorldEntriesDraft()), read, committer)
    assert await agent.readiness() == 1.0


async def test_run_once_creates_world_entries(stack):
    events, proj, read, committer = stack
    out = WorldEntriesDraft(entries=[
        WorldEntryDraft(title="The Brinemarsh", body="A salt flat.", domain="physical", tags=["geo"]),
        WorldEntryDraft(title="Salt Guild", body="Controls the trade.", domain="social"),
    ])
    agent = WorldArchitect(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    titles = {e.title for e in await read.list_world_entries()}
    assert {"The Brinemarsh", "Salt Guild"} <= titles
