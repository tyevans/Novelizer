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
    assert runner.config.get("recursion_limit") == 50
