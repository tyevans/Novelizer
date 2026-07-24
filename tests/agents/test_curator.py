import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.curator import Curator
from novelizer.agents.schemas import CurationDecision, WorldEntryDraft
from novelizer.store.models import WorldEntry, Flag, FlagStatus


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


async def test_revise_supersedes_and_resolves(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Tavern", body="Bloated prose."))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_craft", description="prose is bloated",
                             related_entry_ids=["w1"], proposed_resolution="tighten"))
    await proj.catch_up()
    out = CurationDecision(action="revise",
                           entry=WorldEntryDraft(title="Tavern", body="Tight prose.", supersedes_id="w1"))
    agent = Curator(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()

    active = await read.list_world_entries()
    assert "w1" not in {e.id for e in active}
    assert [e for e in active if e.body == "Tight prose."]
    assert await read.list_flags(category="world_craft", status=FlagStatus.open) == []
    assert len(await read.list_flags(category="world_craft", status=FlagStatus.resolved)) == 1


async def test_merge_supersedes_primary_and_retires_others(stack):
    events, proj, read, committer = stack
    for wid, body in (("w1", "Tavern A."), ("w2", "Tavern B."), ("w3", "Tavern C.")):
        await events.append(EventType.WORLD_ENTRY_CREATED, wid, WorldEntry(id=wid, title="Tavern", body=body))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_redundancy", description="three tavern entries",
                             related_entry_ids=["w1", "w2", "w3"], proposed_resolution="merge"))
    await proj.catch_up()
    out = CurationDecision(
        action="merge",
        entry=WorldEntryDraft(title="The Tavern", body="One consolidated tavern.", supersedes_id="w1"),
        retire_ids=["w2", "w3"],
    )
    await Curator(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()

    active_ids = {e.id for e in await read.list_world_entries()}
    assert active_ids.isdisjoint({"w1", "w2", "w3"})
    assert [e for e in await read.list_world_entries() if e.body == "One consolidated tavern."]
    assert len(await read.list_flags(category="world_redundancy", status=FlagStatus.resolved)) == 1


async def test_retire_tombstones_and_resolves(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Junk", body="Irrelevant."))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_relevance", description="no longer serves the story",
                             related_entry_ids=["w1"], proposed_resolution="retire"))
    await proj.catch_up()
    out = CurationDecision(action="retire", retire_ids=["w1"], reason="no longer serves the story")
    await Curator(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()

    assert "w1" not in {e.id for e in await read.list_world_entries()}
    assert len(await read.list_flags(category="world_relevance", status=FlagStatus.resolved)) == 1


async def test_reject_declines_and_counts_attempt(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Keep", body="Load-bearing."))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_relevance", description="maybe stale?",
                             related_entry_ids=["w1"], proposed_resolution="retire"))
    await proj.catch_up()
    out = CurationDecision(action="reject", reason="entry is load-bearing; keep it")
    await Curator(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()

    assert "w1" in {e.id for e in await read.list_world_entries()}
    rejected = await read.list_flags(category="world_relevance", status=FlagStatus.rejected)
    assert len(rejected) == 1 and rejected[0].failed_attempts == 1


async def test_lane_guard_declines_non_world_target(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_relevance", description="about a character",
                             related_entry_ids=["char-mara"], proposed_resolution="retire"))
    await proj.catch_up()

    class BoomRunner:
        async def ainvoke(self, inputs):
            raise AssertionError("LLM must not be called when lane guard trips")

    await Curator(BoomRunner(), read, committer).run_once()
    await proj.catch_up()
    rejected = await read.list_flags(category="world_relevance", status=FlagStatus.rejected)
    assert len(rejected) == 1 and "out_of_lane" in rejected[0].proposed_resolution
