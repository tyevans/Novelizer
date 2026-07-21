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
from novelizer.store.models import Chapter


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


async def test_flags_from_structured_output_are_filed(stack):
    events, proj, read, committer = stack
    from novelizer.agents.schemas import FlagDraft

    out = WorldEntriesDraft(flags=[
        FlagDraft(category="worldbuilding", description="Two entries claim the same capital city",
                  related_entry_ids=[], proposed_resolution="merge or rename one"),
    ])
    agent = WorldArchitect(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="worldbuilding", status="open")
    assert len(flags) == 1
    assert flags[0].description == "Two entries claim the same capital city"
    assert flags[0].filed_by == "world_architect"


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


async def test_architect_readiness_ignores_watermark_when_seed_pending(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import DirectorSignal, SignalKind
    agent = WorldArchitect(FakeRunner(WorldEntriesDraft()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    # Unchanged world -- watermark alone would gate this to 0.0.
    assert await agent.readiness() == 0.0
    sig = DirectorSignal(kind=SignalKind.seed, body="a drowned city", target_agent="world_architect")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
    await proj.catch_up()
    # A pending seed must wake the Architect even though the fingerprint
    # (chapters only) hasn't moved -- it isn't tracked by the watermark.
    assert await agent.readiness() == 1.0


async def test_architect_readiness_floor_when_state_changed(stack):
    events, proj, read, committer = stack
    agent = WorldArchitect(FakeRunner(WorldEntriesDraft()), read, committer)
    await agent.run_once()
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    assert await agent.readiness() >= 0.2


async def test_architect_readiness_zero_when_state_unchanged(stack):
    events, proj, read, committer = stack
    out = WorldEntriesDraft(entries=[
        WorldEntryDraft(title="The Brinemarsh", body="A salt flat.", domain="physical", tags=["geo"]),
    ])
    agent = WorldArchitect(FakeRunner(out), read, committer)
    assert await agent.readiness() == 1.0
    await agent.run_once()
    await proj.catch_up()
    # Its own minted entry must not re-trigger it; no new external state.
    assert await agent.readiness() == 0.0
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    assert await agent.readiness() > 0.0


class BoomRunner:
    async def ainvoke(self, inputs):
        raise RuntimeError("boom")


async def test_architect_failed_run_leaves_watermark_unset(stack):
    events, proj, read, committer = stack
    agent = WorldArchitect(BoomRunner(), read, committer)
    with pytest.raises(RuntimeError):
        await agent.run_once()
    assert await agent.readiness() == 1.0


class ChapterCommittingRunner(FakeRunner):
    """Simulates the Author committing a chapter while the Architect's LLM
    call is in flight."""

    def __init__(self, out, events, proj):
        super().__init__(out)
        self._events = events
        self._proj = proj

    async def ainvoke(self, inputs):
        await self._events.append(
            EventType.CHAPTER_CREATED, "ch-midrun",
            Chapter(id="ch-midrun", title="Mid-run", prose="Arrived during the run."),
        )
        await self._proj.catch_up()
        return await super().ainvoke(inputs)


async def test_architect_midrun_chapter_is_not_absorbed_by_watermark(stack):
    events, proj, read, committer = stack
    agent = WorldArchitect(ChapterCommittingRunner(WorldEntriesDraft(), events, proj), read, committer)
    await agent.run_once()
    # The mid-run chapter was never seen by this run's worldbuilding: the
    # watermark must stay clear so the next tick re-dispatches.
    assert await agent.readiness() > 0.0


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_world_architect_runner_without_backend_stays_constructible():
    from novelizer.agents.world_architect import build_world_architect_runner

    runner = build_world_architect_runner(_FakeSettings())
    assert runner is not None


def test_build_world_architect_runner_with_backend_uses_retrieval_note_base():
    from novelizer.agents.world_architect import build_world_architect_runner, SYSTEM_PROMPT
    from novelizer.agents.author import RETRIEVAL_NOTE_BASE
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_world_architect_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner is not None
    assert RETRIEVAL_NOTE_BASE.strip() != ""
    assert "chapter list below" not in RETRIEVAL_NOTE_BASE
    assert (SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)


def test_build_world_architect_runner_with_backend_bounds_recursion():
    from novelizer.agents.world_architect import build_world_architect_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_world_architect_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 100


def test_spec_carries_subagent_grant():
    from novelizer.agents.world_architect import SPEC
    assert SPEC.subagent_grant.enabled_setting == "world_architect_subagent_enabled"
