import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.retconner import Retconner
from novelizer.agents.schemas import RetconAmendments, WorldEntryDraft
from novelizer.store.models import WorldEntry, RetconRequest, RetconStatus


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


async def test_resolves_retcon_and_supersedes_entry(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Suns", body="Two suns."))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="two vs one", conflicting_entry_ids=["w1"], proposed_resolution="one sun"))
    await proj.catch_up()
    out = RetconAmendments(amended_entries=[WorldEntryDraft(title="Suns", body="One sun.", supersedes_id="w1")])
    agent = Retconner(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    # old entry superseded (gone from active list), new entry present
    active_entries = await read.list_world_entries()
    assert "w1" not in {e.id for e in active_entries}
    matching = [e for e in active_entries if e.body == "One sun."]
    assert len(matching) == 1
    assert matching[0].supersedes_id == "w1"
    # retcon marked resolved
    assert await read.list_retcon_requests(status=RetconStatus.open) == []
    assert len(await read.list_retcon_requests(status=RetconStatus.resolved)) == 1


async def test_noop_when_no_open_retcons(stack):
    events, proj, read, committer = stack
    agent = Retconner(FakeRunner(RetconAmendments()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_retcon_requests() == []
