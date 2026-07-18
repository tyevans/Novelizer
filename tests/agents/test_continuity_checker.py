import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.schemas import ContinuityOutput, RetconDraft
from novelizer.store.models import WorldEntry, RetconStatus


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


async def test_files_retcons_for_contradictions(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Sun", body="There are two suns."))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w2", WorldEntry(id="w2", title="Sky", body="The lone sun set."))
    await proj.catch_up()
    out = ContinuityOutput(retcon_requests=[RetconDraft(description="two suns vs one", conflicting_entry_ids=["w1", "w2"], proposed_resolution="pick one")])
    agent = ContinuityChecker(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert len(await read.list_retcon_requests(status=RetconStatus.open)) == 1


async def test_no_contradictions_is_noop(stack):
    events, proj, read, committer = stack
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_retcon_requests() == []
