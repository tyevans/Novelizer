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
from novelizer.store.models import WorldEntry, RetconStatus, Chapter
from novelizer.canon.events import SecretCreated, SecretReferenced
from novelizer.brain.leaks import LEAK_SOURCE_TAG


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


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ContinuityOutput())
    agent = ContinuityChecker(runner, read, committer, personality="A dry, pedantic fact-checker.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A dry, pedantic fact-checker." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    out = ContinuityOutput(feed_note="Two suns again. Nobody else noticed.")
    agent = ContinuityChecker(FakeRunner(out), read, committer)
    await agent.commit(out, {})
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Two suns again. Nobody else noticed."


from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG
from novelizer.canon.events import SecretCreated, SecretReferenced, CausalEdgeDeclared
from novelizer.store.models import Chapter


async def _seed_leak(events, proj):
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c1"))
    await proj.catch_up()


async def test_leak_is_filed_as_a_tagged_retcon_request(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1
    assert "the-heir-lives" in leak_reqs[0].description and "mara" in leak_reqs[0].description


async def test_leak_is_not_refiled_on_a_second_cycle(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1


async def test_learned_reference_does_not_get_flagged(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import SecretLearned
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara", chapter_id="c1"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c1"))
    await proj.catch_up()
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)] == []


async def test_paradox_is_filed_as_a_tagged_retcon_request(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                        CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
    await proj.catch_up()
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    paradox_reqs = [r for r in open_reqs if r.description.startswith(PARADOX_SOURCE_TAG)]
    assert len(paradox_reqs) == 1


async def test_llm_and_deterministic_findings_coexist_in_one_cycle(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    llm_out = ContinuityOutput(retcon_requests=[RetconDraft(description="two suns vs one", conflicting_entry_ids=["w1"], proposed_resolution="pick one")])
    agent = ContinuityChecker(FakeRunner(llm_out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert len(open_reqs) == 2
    assert any(r.description == "two suns vs one" for r in open_reqs)
    assert any(r.description.startswith(LEAK_SOURCE_TAG) for r in open_reqs)


async def test_poll_includes_knowledge_and_causal_data(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    ctx = await agent.poll()
    assert "the-heir-lives" in ctx["knowledge_matrix"]
    assert ctx["secret_references"][0].character_id == "mara"
    assert ctx["chapter_order"] == ["c1"]
    assert ctx["causal_edges"] == []


async def test_m4_2_done_when_leak_fixture_reaches_the_open_retcon_queue(stack):
    """M4.2 done-when (mechanical half): seed a secret.referenced event with
    no covering learn/reveal, run ContinuityChecker.run_once() with a
    FakeRunner that finds nothing on its own, and confirm a
    retcon_request.created event lands via the Committer with a
    LEAK_SOURCE_TAG-prefixed description, visible in
    list_retcon_requests(status=open)."""
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)

    await agent.run_once()
    await proj.catch_up()

    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1
    assert leak_reqs[0].status == RetconStatus.open

    log = await events.events_since(0)
    created = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED
               and e.payload["description"].startswith(LEAK_SOURCE_TAG)]
    assert len(created) == 1


async def test_m4_3_done_when_mechanical_chain_leak_flagged_and_widget_still_shows_unknown(stack):
    """The M4.3 done-when, part (a): seed a secret (secret.created), a
    character who has NOT learned it, and a committed secret.referenced
    event naming that character using the secret in a chapter -> assert
    LeakDetector (M4.2) flags it -> drive ContinuityChecker.run_once() with
    a FakeRunner preset to return no LLM-found contradictions -> assert the
    resulting retcon_request.created event lands via the Committer, its
    description starting with LEAK_SOURCE_TAG -> assert it appears in
    list_retcon_requests(status=open) -> assert the Who-Knows-What widget's
    render-time helper still shows the character as not-having-learned the
    secret (the leak is flagged, not silently resolved). No live model call.
    """
    from novelizer.tui.widgets.who_knows_what import who_knows_what_line
    from novelizer.store.models import Character

    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(id="kestrel", name="Kestrel"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="kestrel", chapter_id="c1"))
    await proj.catch_up()

    # Step 1: LeakDetector flags it deterministically (no LLM), via the same
    # poll() the Continuity Checker uses.
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    ctx = await agent.poll()
    from novelizer.brain.leaks import find_leaks
    leaks = find_leaks(ctx["secret_references"], ctx["knowledge_matrix"])
    assert len(leaks) == 1 and leaks[0].character_id == "kestrel"

    # Step 2: run_once() with a FakeRunner that finds nothing on its own
    # still files a tagged retcon request via the Committer.
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1
    assert "the-heir-lives" in leak_reqs[0].description and "kestrel" in leak_reqs[0].description

    # Step 3: it's visible in the open retcon queue.
    assert leak_reqs[0].status == RetconStatus.open

    # Step 4: the Who-Knows-What widget's render-time helper still shows
    # Kestrel as not having learned the secret -- the leak is flagged, not
    # silently resolved.
    secret = await read.get_secret("the-heir-lives")
    characters = await read.list_characters()
    matrix = await read.knowledge_matrix()
    line = who_knows_what_line(secret, characters, matrix)
    assert "Kestrel" not in line
    assert "known to no one" in line
