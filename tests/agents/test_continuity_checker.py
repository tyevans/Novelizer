import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.schemas import ContinuityOutput, RetconDraft, MinedFactsOutput, MinedSecretFact, MinedRevealFact, MinedThreadFact, MinedCausalFact
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


class SequencedFakeRunner:
    """Returns successive structured responses from `outs`, one per call,
    popped in order. Used where a test needs the contradiction-pass and
    mining-pass calls to return different structured responses."""

    def __init__(self, outs):
        self._outs = list(outs)
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._outs.pop(0)}


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
    agent = ContinuityChecker(FakeRunner(out), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    assert len(await read.list_retcon_requests(status=RetconStatus.open)) == 1


async def test_no_contradictions_is_noop(stack):
    events, proj, read, committer = stack
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_retcon_requests() == []


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ContinuityOutput())
    agent = ContinuityChecker(runner, FakeRunner(MinedFactsOutput()), read, committer, events, personality="A dry, pedantic fact-checker.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A dry, pedantic fact-checker." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    out = ContinuityOutput(feed_note="Two suns again. Nobody else noticed.")
    agent = ContinuityChecker(FakeRunner(out), FakeRunner(MinedFactsOutput()), read, committer, events)
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
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1
    assert "the-heir-lives" in leak_reqs[0].description and "mara" in leak_reqs[0].description


async def test_leak_is_not_refiled_on_a_second_cycle(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
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
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
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
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    paradox_reqs = [r for r in open_reqs if r.description.startswith(PARADOX_SOURCE_TAG)]
    assert len(paradox_reqs) == 1


async def test_llm_and_deterministic_findings_coexist_in_one_cycle(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    llm_out = ContinuityOutput(retcon_requests=[RetconDraft(description="two suns vs one", conflicting_entry_ids=["w1"], proposed_resolution="pick one")])
    agent = ContinuityChecker(FakeRunner(llm_out), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert len(open_reqs) == 2
    assert any(r.description == "two suns vs one" for r in open_reqs)
    assert any(r.description.startswith(LEAK_SOURCE_TAG) for r in open_reqs)


async def test_poll_includes_knowledge_and_causal_data(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
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
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)

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
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
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


from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.store.models import Character


async def test_mining_commits_a_secret_referenced_event_tagged_mined(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    contradiction_runner = FakeRunner(ContinuityOutput())
    mining_runner = FakeRunner(MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-heir-lives", character_id="mara", chapter_id="c1"),
    ]))
    agent = ContinuityChecker(contradiction_runner, mining_runner, read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    mined_refs = [e for e in log if e.event_type == EventType.SECRET_REFERENCED and e.payload.get("source") == "mined"]
    assert len(mined_refs) == 1
    assert mined_refs[0].payload["character_id"] == "mara"

    from novelizer.brain.leaks import find_leaks
    matrix = await read.knowledge_matrix()
    refs = await read.list_secret_references()
    leaks = find_leaks(refs, matrix)
    assert any(l.secret_id == "the-heir-lives" and l.character_id == "mara" for l in leaks)


async def test_mining_does_not_recommit_on_a_second_run_once(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-heir-lives", character_id="mara", chapter_id="c1"),
    ])
    agent = ContinuityChecker(
        FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events,
    )
    await agent.run_once()
    await proj.catch_up()

    agent2 = ContinuityChecker(
        FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events,
    )
    await agent2.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    mined_refs = [e for e in log if e.event_type == EventType.SECRET_REFERENCED and e.payload.get("source") == "mined"]
    assert len(mined_refs) == 1
    mined_markers = [e for e in log if e.event_type == EventType.CHAPTER_MINED and e.payload["chapter_id"] == "c1"]
    assert len(mined_markers) == 1


async def test_mining_ambiguous_secret_fact_files_a_tagged_retcon_not_an_event(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-heir-lives", character_id="mara", chapter_id="c1", known_id=False),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    assert [e for e in log if e.event_type in (EventType.SECRET_REFERENCED, EventType.SECRET_LEARNED)] == []
    retcons = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED]
    assert any(e.payload["description"].startswith(MINED_SOURCE_TAG) for e in retcons)


async def test_mining_reveal_fact_always_escalates_never_auto_commits(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(reveal_facts=[
        MinedRevealFact(id="the-heir-lives", chapter_id="c1"),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.SECRET_REVEALED] == []
    retcons = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED]
    assert any(e.payload["description"].startswith(MINED_SOURCE_TAG) for e in retcons)


async def test_mining_causal_fact_dedups_against_exact_triple_match(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2",
                        CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c2"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(causal_facts=[
        MinedCausalFact(cause_chapter_id="c1", effect_chapter_id="c2"),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    edges = await read.list_causal_edges()
    matching = [e for e in edges if e.cause_chapter_id == "c1" and e.effect_chapter_id == "c2"]
    assert len(matching) == 1


async def test_mining_thread_fact_dedups_against_raw_log_scan(stack):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched

    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-lost-heir", ThreadPlanted(id="the-lost-heir", name="The Lost Heir"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.THREAD_TOUCHED, "the-lost-heir", ThreadTouched(id="the-lost-heir", chapter_id="c1"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(thread_facts=[
        MinedThreadFact(action="touch", id="the-lost-heir", chapter_id="c1"),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    touches = [e for e in log if e.event_type == EventType.THREAD_TOUCHED and e.payload["chapter_id"] == "c1"]
    assert len(touches) == 1


async def test_mining_runs_only_for_chapters_without_a_mined_marker(stack):
    from novelizer.canon.events import ChapterMined

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(EventType.CHAPTER_MINED, "c1", ChapterMined(chapter_id="c1"))
    await proj.catch_up()

    mining_runner = FakeRunner(MinedFactsOutput())
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), mining_runner, read, committer, events)

    ctx = await agent.poll()
    mined_ids = {c.id for c in ctx["mined_chapters"]}
    assert mined_ids == {"c2"}

    await agent.run_once()
    assert len(mining_runner.calls) == 1


async def test_poll_includes_threads_secrets_and_mined_chapters(stack):
    from novelizer.canon.events import ThreadPlanted

    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.THREAD_PLANTED, "the-lost-heir",
                        ThreadPlanted(id="the-lost-heir", name="The Lost Heir"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
    ctx = await agent.poll()

    assert {s.id for s in ctx["secrets"]} == {"the-heir-lives"}
    assert {t.id for t in ctx["threads"]} == {"the-lost-heir"}
    assert {c.id for c in ctx["mined_chapters"]} == {"c1"}
