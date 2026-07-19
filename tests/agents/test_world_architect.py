import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.schemas import WorldEntriesDraft, WorldEntryDraft


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


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(WorldEntriesDraft())
    agent = WorldArchitect(runner, read, committer, personality="A quietly obsessive worldbuilder.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A quietly obsessive worldbuilder." in sent
    assert "In character:" in sent


async def test_work_prompt_omits_personality_line_when_unset(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(WorldEntriesDraft())
    agent = WorldArchitect(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "In character:" not in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    draft = WorldEntriesDraft(feed_note="Another corner of the map, filled in.")
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["agent_name"] == "world_architect"
    assert remarks[0].payload["note"] == "Another corner of the map, filled in."


async def test_architect_no_action_pass_commits_nothing_and_backs_off(stack):
    events, proj, read, committer = stack
    draft = WorldEntriesDraft(no_action=True, feed_note="The world is rich enough — let the story breathe.")
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_world_entries() == []
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert [e.payload["note"] for e in remarks] == ["The world is rich enough — let the story breathe."]
    import time
    assert agent.seconds_until_ready(time.monotonic()) > agent.interval


async def test_architect_pass_ignored_when_director_seed_pending(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import DirectorSignal, SignalKind
    sig = DirectorSignal(kind=SignalKind.seed, body="a drowned city", target_agent="world_architect")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
    await proj.catch_up()
    draft = WorldEntriesDraft(no_action=True)
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    # The seed must not be silently dropped by a pass: normal path runs,
    # the signal is consumed, and no backoff is taken.
    assert await read.list_unconsumed_signals(target_agent="world_architect") == []
    assert agent._backoff_until == 0.0


async def test_architect_readiness_floor_unchanged(stack):
    events, proj, read, committer = stack
    agent = WorldArchitect(FakeRunner(WorldEntriesDraft()), read, committer)
    await agent.run_once()
    assert await agent.readiness() >= 0.2
