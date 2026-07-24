import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.schemas import WorldEntriesDraft, FlagDraft
from novelizer.store.models import FlagStatus


class FakeRunner:
    def __init__(self, out):
        self._out = out
    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_world_architect_files_world_redundancy_with_category_intact(stack):
    events, proj, read, committer = stack
    draft = WorldEntriesDraft(
        entries=[],
        flags=[FlagDraft(category="world_redundancy",
                         description="two overlapping tavern entries",
                         related_entry_ids=["w1", "w2"], proposed_resolution="merge")],
    )
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_flags = await read.list_flags(category="world_redundancy", status=FlagStatus.open)
    assert len(open_flags) == 1
    assert open_flags[0].filed_by == "world_architect"
