import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.triage import Triage
from novelizer.agents.schemas import TriageVerdict
from novelizer.store.models import Flag, FlagStatus


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_verified_owned_flag_stays_open_for_owner(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="contradiction", description="two suns",
                             related_entry_ids=["w1"], proposed_resolution="", filed_by="continuity_checker"))
    await proj.catch_up()
    out = TriageVerdict(verdict="real")
    agent = Triage(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="contradiction", status=FlagStatus.open)
    assert len(flags) == 1 and flags[0].id == "f1"


async def test_dismissed_flag_is_rejected(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="pacing", description="false alarm",
                             related_entry_ids=[], proposed_resolution="", filed_by="structure_analyst"))
    await proj.catch_up()
    out = TriageVerdict(verdict="dismiss", reason="not actually a pacing problem")
    agent = Triage(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_flags(status=FlagStatus.open) == []
    rejected = await read.list_flags(status=FlagStatus.rejected)
    assert len(rejected) == 1 and rejected[0].resolved_by == "triage"


async def test_unowned_category_increments_triage_passes_then_goes_stale(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="mystery_gap", description="no owner for this",
                             related_entry_ids=[], proposed_resolution="", filed_by="author"))
    await proj.catch_up()
    out = TriageVerdict(verdict="real")
    agent = Triage(FakeRunner(out), read, committer, stale_after=2)
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="mystery_gap")
    assert flags[0].triage_passes == 1 and flags[0].status == FlagStatus.open
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="mystery_gap")
    assert flags[0].triage_passes == 2 and flags[0].status == FlagStatus.stale


async def test_owned_category_never_increments_triage_passes(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="worldbuilding", description="no map",
                             related_entry_ids=[], proposed_resolution="", filed_by="author"))
    await proj.catch_up()
    agent = Triage(FakeRunner(TriageVerdict(verdict="real")), read, committer, stale_after=1)
    await agent.run_once()
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="worldbuilding")
    assert flags[0].triage_passes == 0
    assert flags[0].status == FlagStatus.open


async def test_noop_when_no_open_flags(stack):
    events, proj, read, committer = stack
    agent = Triage(FakeRunner(TriageVerdict(verdict="real")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_flags() == []


def test_spec_carries_subagent_grant():
    from novelizer.agents.triage import SPEC
    assert SPEC.subagent_grant.enabled_setting == "triage_subagent_enabled"
