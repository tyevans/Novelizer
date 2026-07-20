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
from novelizer.store.models import WorldEntry, RetconRequest, RetconStatus, Domain


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


class ScriptedRunner:
    """Returns (or raises) each entry of `script` in order, one per call."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return {"structured_response": step}


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


async def test_run_once_survives_llm_inventing_a_domain(stack):
    # Regression: live retconner wedged in a ValidationError loop because the
    # LLM answered domain="character" for a voice retcon; commit() must land
    # the entry as Domain.other and still resolve the request.
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Voice", body="clipped."))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="voice drift", conflicting_entry_ids=["w1"], proposed_resolution=""))
    await proj.catch_up()
    out = RetconAmendments.model_validate({
        "amended_entries": [{"title": "Voice", "body": "v2", "domain": "character", "supersedes_id": "w1"}]
    })
    agent = Retconner(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_retcon_requests(status=RetconStatus.open) == []
    new = [e for e in await read.list_world_entries() if e.body == "v2"]
    assert len(new) == 1
    assert new[0].domain == Domain.other


async def test_failing_head_request_does_not_block_the_queue(stack):
    # Regression: poll() always took open_reqs[0], so one poisoned request
    # froze the whole queue while new retcons stacked behind it.
    events, proj, read, committer = stack
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="poisoned head", conflicting_entry_ids=[], proposed_resolution=""))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r2",
                        RetconRequest(id="r2", description="healthy follower", conflicting_entry_ids=[], proposed_resolution=""))
    await proj.catch_up()
    runner = ScriptedRunner([RuntimeError("provider exploded"), RetconAmendments()])
    agent = Retconner(runner, read, committer)
    with pytest.raises(RuntimeError):
        await agent.run_once()
    await agent.run_once()
    await proj.catch_up()
    assert "healthy follower" in runner.calls[-1]["messages"][0]["content"]
    open_ids = {r.id for r in await read.list_retcon_requests(status=RetconStatus.open)}
    assert open_ids == {"r1"}


async def test_none_output_defers_head_request(stack):
    # A structured_response of None used to leave the head request open and
    # silently retry it forever — it must be deferred like a failure.
    events, proj, read, committer = stack
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="gives nothing", conflicting_entry_ids=[], proposed_resolution=""))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r2",
                        RetconRequest(id="r2", description="healthy follower", conflicting_entry_ids=[], proposed_resolution=""))
    await proj.catch_up()
    runner = ScriptedRunner([None, RetconAmendments()])
    agent = Retconner(runner, read, committer)
    await agent.run_once()
    await agent.run_once()
    await proj.catch_up()
    assert "healthy follower" in runner.calls[-1]["messages"][0]["content"]
    open_ids = {r.id for r in await read.list_retcon_requests(status=RetconStatus.open)}
    assert open_ids == {"r1"}


async def test_deferral_resets_once_every_open_request_has_failed(stack):
    # Deferral must not become a dead stop: with the whole queue deferred the
    # retconner would no-op forever while readiness stays 1.0. A fresh pass
    # starts instead.
    events, proj, read, committer = stack
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="only one", conflicting_entry_ids=[], proposed_resolution=""))
    await proj.catch_up()
    runner = ScriptedRunner([None, None])
    agent = Retconner(runner, read, committer)
    await agent.run_once()
    await agent.run_once()
    assert len(runner.calls) == 2


async def test_noop_when_no_open_retcons(stack):
    events, proj, read, committer = stack
    agent = Retconner(FakeRunner(RetconAmendments()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_retcon_requests() == []


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import RetconRequest
    req = RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="")
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", req)
    await proj.catch_up()
    runner = FakeRunner(RetconAmendments())
    agent = Retconner(runner, read, committer, personality="A calm, surgical fixer.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A calm, surgical fixer." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import RetconRequest
    req = RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="")
    out = RetconAmendments(feed_note="Tidied up. No drama needed.")
    agent = Retconner(FakeRunner(out), read, committer)
    await agent.commit(out, {"target": req, "world": []})
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Tidied up. No drama needed."


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_retconner_runner_without_backend_stays_constructible():
    from novelizer.agents.retconner import build_retconner_runner

    runner = build_retconner_runner(_FakeSettings())
    assert runner is not None


def test_build_retconner_runner_with_backend_uses_retrieval_note_base():
    from novelizer.agents.retconner import build_retconner_runner, SYSTEM_PROMPT
    from novelizer.agents.author import RETRIEVAL_NOTE_BASE
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_retconner_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner is not None
    assert "chapter list below" not in RETRIEVAL_NOTE_BASE
    assert (SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)


def test_build_retconner_runner_with_backend_bounds_recursion():
    from novelizer.agents.retconner import build_retconner_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_retconner_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 50
