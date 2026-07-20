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
    list_retcon_requests(status=open) -> assert the Secrets matrix render-time helper (brain_model.secret_row)
    still shows the character as not-having-learned the
    secret (the leak is flagged, not silently resolved). No live model call.
    """
    from novelizer.tui.widgets.brain_model import secret_row
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

    # Step 4: the Secrets matrix render-time helper (brain_model.secret_row) still shows
    # Kestrel as not having learned the secret -- the leak is flagged, not
    # silently resolved.
    secret = await read.get_secret("the-heir-lives")
    characters = await read.list_characters()
    matrix = await read.knowledge_matrix()
    row = secret_row(secret, characters, matrix).plain
    assert "●" not in row          # no filled cell — Kestrel hasn't learned it
    assert "0/1" in row            # spread meter shows 0 knowers among 1 character


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


async def test_mining_duplicate_unknown_id_facts_file_one_retcon(stack):
    # Regression (a-dress-for-doug, events 12+13 and 67+68): the miner listed
    # the same unknown-id fact twice in one output and both were filed —
    # _file_mined_retcon had no in-batch dedup.
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    fact = dict(action="uses", id="the-invented", character_id="the-boy", chapter_id="c1", known_id=False)
    mining_out = MinedFactsOutput(secret_facts=[MinedSecretFact(**fact), MinedSecretFact(**fact)])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    mined_reqs = [r for r in await read.list_retcon_requests(status=RetconStatus.open)
                  if r.description.startswith(MINED_SOURCE_TAG)]
    assert len(mined_reqs) == 1


async def test_mined_retcon_not_refiled_when_already_open(stack):
    # A crash between retcon filing and the chapter.mined stamp re-mines the
    # chapter next cycle; the identical description must not be filed twice —
    # same open-queue dedup the leak/paradox paths already have.
    from novelizer.store.models import RetconRequest
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    desc = (f"{MINED_SOURCE_TAG} mined secret uses fact citing unrecognized/unknown secret id "
            f"'the-invented' for character 'the-boy' in chapter 'c1'")
    await events.append(EventType.RETCON_REQUEST_CREATED, "seed",
                        RetconRequest(id="seed", description=desc, conflicting_entry_ids=[], proposed_resolution=""))
    await proj.catch_up()
    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-invented", character_id="the-boy", chapter_id="c1", known_id=False),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    mined_reqs = [r for r in await read.list_retcon_requests(status=RetconStatus.open)
                  if r.description.startswith(MINED_SOURCE_TAG)]
    assert len(mined_reqs) == 1


async def test_mining_prompt_lists_active_secret_ids_for_citation(stack):
    # Regression: the miner cited thread ids and character names as secret ids.
    # The secret namespace was only implicit in the knowledge-matrix lines; the
    # prompt must name the legal secret ids outright, like the Editor's does.
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    mining_runner = FakeRunner(MinedFactsOutput())
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), mining_runner, read, committer, events)
    await agent.run_once()
    sent = mining_runner.calls[-1]["messages"][0]["content"]
    assert "Active secret ids" in sent
    assert "the-heir-lives" in sent


async def test_mining_secret_fact_citing_thread_id_redirects_to_thread_touch(stack):
    # Regression: 'the-boy-s-gift' and 'the-name-of-the-sea' were active THREAD
    # ids the miner filed as secret facts. A deterministic namespace check can
    # recover the intended meaning — the prose engages that thread — as a
    # mined touch (same downgrade precedent as plant-collision → touch), not
    # an unresolvable retcon.
    from novelizer.canon.events import ThreadPlanted
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-lost-heir", ThreadPlanted(id="the-lost-heir", name="The Lost Heir"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-lost-heir", character_id="mara", chapter_id="c1", known_id=False),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    touches = [e for e in log if e.event_type == EventType.THREAD_TOUCHED and e.payload.get("source") == "mined"]
    assert len(touches) == 1 and touches[0].payload["id"] == "the-lost-heir"
    mined_reqs = [r for r in await read.list_retcon_requests(status=RetconStatus.open)
                  if r.description.startswith(MINED_SOURCE_TAG)]
    assert mined_reqs == []


async def test_mining_secret_fact_citing_already_touched_thread_is_a_noop(stack):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-lost-heir", ThreadPlanted(id="the-lost-heir", name="The Lost Heir"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.THREAD_TOUCHED, "the-lost-heir", ThreadTouched(id="the-lost-heir", chapter_id="c1"))
    await proj.catch_up()
    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-lost-heir", character_id="mara", chapter_id="c1", known_id=False),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    touches = [e for e in log if e.event_type == EventType.THREAD_TOUCHED]
    assert len(touches) == 1  # only the seeded one
    mined_reqs = [r for r in await read.list_retcon_requests(status=RetconStatus.open)
                  if r.description.startswith(MINED_SOURCE_TAG)]
    assert mined_reqs == []


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


class RaisingThenFakeMiningRunner:
    """Raises on its first call (simulating a mining-pass failure for one
    chapter), then returns `out` for every subsequent call."""

    def __init__(self, out):
        self._out = out
        self._first_call = True
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        if self._first_call:
            self._first_call = False
            raise RuntimeError("mining pass exploded")
        return {"structured_response": self._out}


async def test_mining_exception_for_one_chapter_does_not_block_the_next(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-heir-lives", character_id="mara", chapter_id="c2"),
    ])
    mining_runner = RaisingThenFakeMiningRunner(mining_out)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), mining_runner, read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    assert len(mining_runner.calls) == 2  # both chapters attempted

    log = await events.events_since(0)
    mined_markers = {e.payload["chapter_id"] for e in log if e.event_type == EventType.CHAPTER_MINED}
    assert mined_markers == {"c2"}  # c1's failure left it unstamped, c2 succeeded

    mined_refs = [e for e in log if e.event_type == EventType.SECRET_REFERENCED and e.payload.get("source") == "mined"]
    assert len(mined_refs) == 1
    assert mined_refs[0].payload["chapter_id"] == "c2"


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


import asyncio
from hypothesis import given, settings as hyp_settings, strategies as st


async def _run_mining_idempotency(fact_count: int, run_twice: bool) -> tuple[int, int]:
    """Seeds `fact_count` distinct secrets, a chapter, and a character; covers
    the even-indexed secrets with a pre-existing secret.referenced event
    (source='declared') so only the odd-indexed ones are "new" for mining to
    find. Runs a FakeRunner mining pass citing all fact_count facts once (via
    run_once), then optionally a second run_once with a runner whose mining
    response would re-cite the same facts -- but since the chapter already
    carries a chapter.mined marker, poll() must exclude it and the mining
    runner must never be invoked again.

    Returns (chapter_mined_count, mined_sourced_secret_referenced_count).
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path); await events.init()
        proj = Projector(events, path); await proj.init()
        read = ReadStore(path); await read.init()
        committer = Committer(events)

        secret_ids = [f"s{i}" for i in range(fact_count)]
        uncovered_ids = []
        await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
        for i, sid in enumerate(secret_ids):
            await events.append(EventType.SECRET_CREATED, sid, SecretCreated(id=sid, title=sid))
            if i % 2 == 0:
                await events.append(EventType.SECRET_REFERENCED, sid,
                                    SecretReferenced(id=sid, character_id="mara", chapter_id="c1"))
            else:
                uncovered_ids.append(sid)
        await proj.catch_up()

        mining_out = MinedFactsOutput(secret_facts=[
            MinedSecretFact(action="uses", id=sid, character_id="mara", chapter_id="c1")
            for sid in secret_ids
        ])
        agent = ContinuityChecker(
            FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events,
        )
        await agent.run_once()
        await proj.catch_up()

        if run_twice:
            # A second run whose mining response, if ever invoked for this
            # chapter, would try to recommit the same facts -- poll()'s
            # mined_chapters exclusion must mean it's never called.
            second_mining_runner = FakeRunner(mining_out)
            agent2 = ContinuityChecker(
                FakeRunner(ContinuityOutput()), second_mining_runner, read, committer, events,
            )
            await agent2.run_once()
            await proj.catch_up()
            assert second_mining_runner.calls == []

        log = await events.events_since(0)
        mined_markers = [e for e in log if e.event_type == EventType.CHAPTER_MINED
                          and e.payload["chapter_id"] == "c1"]
        mined_refs = [e for e in log if e.event_type == EventType.SECRET_REFERENCED
                      and e.payload.get("source") == "mined"]
        return len(mined_markers), len(mined_refs)
    finally:
        await read.close(); await proj.close(); await events.close(); os.unlink(path)


@given(
    fact_count=st.integers(min_value=0, max_value=5),
    run_twice=st.booleans(),
)
@hyp_settings(max_examples=25, deadline=None)
def test_mining_the_same_chapter_twice_never_double_commits_idempotency(fact_count, run_twice):
    """Idempotency invariant (M5.1 Locked decision 2): running run_once()
    against the same un-mined chapter any number of times with arbitrary
    mined-fact counts commits each distinct fact at most once, and always
    ends with exactly one chapter.mined marker for that chapter -- the
    marker absorbs repeat mining attempts regardless of what the second
    run's FakeRunner would have returned.
    """
    uncovered_count = (fact_count + 1) // 2  # odd-indexed secrets, 0..fact_count-1
    mined_marker_count, mined_ref_count = asyncio.run(_run_mining_idempotency(fact_count, run_twice))
    assert mined_marker_count == 1
    assert mined_ref_count <= uncovered_count


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
    assert ctx["thread_touch_pairs"] == set()


async def test_m5_1_done_when_mechanical_chain(stack):
    """M5.1 done-when (a), traced clause by clause -- see
    docs/submilestones/M5-finish.md's M5.1 done-when cell and
    docs/superpowers/plans/2026-07-18-novelizer-m5.1-prose-mining.md Task 9."""
    from novelizer.canon.committer import GatingCommitter
    from novelizer.canon.policy import AutonomyPolicy
    from novelizer.canon.autonomy import AutonomyLevel
    from novelizer.brain.leaks import find_leaks

    events, proj, read, committer = stack

    # --- Clause 1+2: seed a chapter's prose with an undeclared secret use
    # (no secret.referenced event for it in the log) and a FakeRunner mining
    # response declaring that use; run run_once() -> the resulting
    # secret.referenced event exists, tagged source="mined".
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose="Mara knew the heir lived."))
    await proj.catch_up()

    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="the-heir-lives", character_id="mara", chapter_id="c1"),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    mined_refs = [e for e in log if e.event_type == EventType.SECRET_REFERENCED and e.payload.get("source") == "mined"]
    assert len(mined_refs) == 1
    assert mined_refs[0].payload["character_id"] == "mara"

    # --- Clause 3: find_leaks now flags it -- mining feeds the existing
    # deterministic detector, doesn't bypass it.
    matrix = await read.knowledge_matrix()
    refs = await read.list_secret_references()
    leaks = find_leaks(refs, matrix)
    assert any(l.secret_id == "the-heir-lives" and l.character_id == "mara" for l in leaks)

    # --- Clause 4: a second run_once() against the same chapter does not
    # re-commit the same mined fact (idempotency via chapter.mined).
    second_mining_runner = FakeRunner(mining_out)
    agent2 = ContinuityChecker(FakeRunner(ContinuityOutput()), second_mining_runner, read, committer, events)
    await agent2.run_once()
    await proj.catch_up()

    assert second_mining_runner.calls == []  # mined_chapters excluded c1

    log = await events.events_since(0)
    mined_refs = [e for e in log if e.event_type == EventType.SECRET_REFERENCED and e.payload.get("source") == "mined"]
    assert len(mined_refs) == 1
    mined_markers = [e for e in log if e.event_type == EventType.CHAPTER_MINED and e.payload["chapter_id"] == "c1"]
    assert len(mined_markers) == 1

    # --- Clause 5: an ambiguous-mining fixture (known_id=False, unknown id)
    # produces a retcon_request.created tagged MINED_SOURCE_TAG instead of a
    # bad event.
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="A stranger spoke of something unclear."))
    await proj.catch_up()

    ambiguous_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="some-unknown-secret", character_id="mara", chapter_id="c2", known_id=False),
    ])
    agent3 = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(ambiguous_out), read, committer, events)
    await agent3.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    bad_events = [e for e in log if e.event_type in (EventType.SECRET_REFERENCED, EventType.SECRET_LEARNED)
                  and e.payload.get("id") == "some-unknown-secret"]
    assert bad_events == []
    tagged_retcons = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED
                      and e.payload["description"].startswith(MINED_SOURCE_TAG)]
    assert any("some-unknown-secret" in e.payload["description"] for e in tagged_retcons)

    # --- Clause 6: a mined-reveal fixture produces a retcon_request.created
    # tagged MINED_SOURCE_TAG and NO secret.revealed event, at every
    # autonomy level -- mined reveals never auto-commit. First under a
    # plain Committer:
    await events.append(EventType.CHAPTER_CREATED, "c3", Chapter(id="c3", title="Three", prose="The heir's truth came out."))
    await proj.catch_up()

    reveal_out = MinedFactsOutput(reveal_facts=[
        MinedRevealFact(id="the-heir-lives", chapter_id="c3"),
    ])
    agent4 = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(reveal_out), read, committer, events)
    await agent4.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.SECRET_REVEALED] == []
    retcons_after_c3 = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED
                        and e.payload["description"].startswith(MINED_SOURCE_TAG)]
    assert any("the-heir-lives" in e.payload["description"] for e in retcons_after_c3)

    # And explicitly under a GatingCommitter at AutonomyLevel.full_auto too,
    # per the decomposition's literal "at every autonomy level" wording --
    # mined reveals never auto-commit even when full_auto would let every
    # other event type through ungated.
    autonomy_state = await read.get_autonomy_state()
    assert autonomy_state.global_level == AutonomyLevel.full_auto  # default; asserted, not set

    await events.append(EventType.CHAPTER_CREATED, "c4", Chapter(id="c4", title="Four", prose="Another reveal, unspoken."))
    await proj.catch_up()

    gating_committer = GatingCommitter(events, AutonomyPolicy(read))
    reveal_out_2 = MinedFactsOutput(reveal_facts=[
        MinedRevealFact(id="the-heir-lives", chapter_id="c4"),
    ])
    agent5 = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(reveal_out_2), read, gating_committer, events)
    await agent5.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.SECRET_REVEALED] == []
    tagged_retcons_after = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED
                            and e.payload["description"].startswith(MINED_SOURCE_TAG)]
    assert len(tagged_retcons_after) >= len(retcons_after_c3) + 1


from novelizer.store.models import RetconRequest


async def _seed_open_retcon(events, proj, description="two suns vs one"):
    req = RetconRequest(description=description, conflicting_entry_ids=["w1"], proposed_resolution="pick one")
    await events.append(EventType.RETCON_REQUEST_CREATED, req.id, req)
    await proj.catch_up()
    return req


async def test_poll_includes_open_retcons(stack):
    events, proj, read, committer = stack
    await _seed_open_retcon(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()), read, committer, events)
    ctx = await agent.poll()
    assert [r.description for r in ctx["open_retcons"]] == ["two suns vs one"]


async def test_work_prompt_lists_open_retcons(stack):
    events, proj, read, committer = stack
    await _seed_open_retcon(events, proj)
    runner = FakeRunner(ContinuityOutput())
    agent = ContinuityChecker(runner, FakeRunner(MinedFactsOutput()), read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "already filed (do not re-report these)" in sent
    assert "two suns vs one" in sent


async def test_work_prompt_omits_retcon_block_when_queue_empty(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ContinuityOutput())
    agent = ContinuityChecker(runner, FakeRunner(MinedFactsOutput()), read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "already filed" not in sent


async def test_llm_retcon_matching_open_description_is_not_refiled(stack):
    events, proj, read, committer = stack
    await _seed_open_retcon(events, proj)
    llm_out = ContinuityOutput(retcon_requests=[RetconDraft(
        description="two suns vs one", conflicting_entry_ids=["w1"], proposed_resolution="pick one")])
    agent = ContinuityChecker(FakeRunner(llm_out), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert len([r for r in open_reqs if r.description == "two suns vs one"]) == 1


async def test_llm_duplicate_descriptions_within_one_output_filed_once(stack):
    events, proj, read, committer = stack
    draft = RetconDraft(description="two suns vs one", conflicting_entry_ids=["w1"], proposed_resolution="pick one")
    llm_out = ContinuityOutput(retcon_requests=[draft, draft])
    agent = ContinuityChecker(FakeRunner(llm_out), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert len([r for r in open_reqs if r.description == "two suns vs one"]) == 1


async def test_continuity_readiness_zero_when_state_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()),
                              read, committer, events)
    assert await agent.readiness() > 0.0
    await agent.run_once()      # mines ch1, stamps chapter.mined
    await proj.catch_up()
    assert await agent.readiness() == 0.0
    await events.append(EventType.CHAPTER_CREATED, "ch2", Chapter(id="ch2", title="Two", prose="more"))
    await proj.catch_up()
    assert await agent.readiness() > 0.0


async def test_continuity_pass_skips_llm_retcons_but_still_mines(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    out = ContinuityOutput(no_action=True,
                           retcon_requests=[RetconDraft(description="phantom", proposed_resolution="x")])
    agent = ContinuityChecker(FakeRunner(out), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    # LLM retcon ignored on a pass...
    assert await read.list_retcon_requests(status=RetconStatus.open) == []
    # ...but the deterministic mining pass still ran and stamped the chapter.
    mined = await events.events_since(0, event_types=[EventType.CHAPTER_MINED])
    assert [e.payload["chapter_id"] for e in mined] == ["ch1"]
    # Mining WAS deterministic work, so no backoff this run.
    assert agent._backoff_until == 0.0


async def test_continuity_pass_backs_off_when_no_deterministic_work(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    quiet = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()),
                              read, committer, events)
    await quiet.run_once()      # first run mines ch1
    passing = ContinuityChecker(FakeRunner(ContinuityOutput(no_action=True, feed_note="All threads hold.")),
                                FakeRunner(MinedFactsOutput()), read, committer, events)
    await passing.run_once()    # nothing left to mine, no leaks/paradoxes
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert remarks[-1].payload["note"] == "All threads hold."
    import time
    assert passing.seconds_until_ready(time.monotonic()) > passing.interval


class NoneMiningRunner:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


async def test_continuity_failed_mining_keeps_gate_open(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), NoneMiningRunner(), read, committer, events)
    await agent.run_once()
    # ch1 was not stamped chapter.mined; the "retry next poll" contract
    # requires readiness to stay open, not gate to 0.0.
    mined = await events.events_since(0, event_types=[EventType.CHAPTER_MINED])
    assert mined == []
    assert await agent.readiness() > 0.0


async def test_checker_pull_mode_false_keeps_chapter_excerpt_block(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="secret prose text" * 20))
    await proj.catch_up()
    runner = FakeRunner(ContinuityOutput())
    agent = ContinuityChecker(runner, FakeRunner(MinedFactsOutput()), read, committer, events, pull_mode=False)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Recent chapters:" in sent
    assert "Chapter index:" not in sent
    assert "secret prose text" in sent


async def test_checker_pull_mode_true_replaces_excerpts_with_chapter_map(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="secret prose text"))
    await proj.catch_up()
    runner = FakeRunner(ContinuityOutput())
    agent = ContinuityChecker(runner, FakeRunner(MinedFactsOutput()), read, committer, events, pull_mode=True)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Chapter index:" in sent
    assert "Recent chapters:" not in sent
    assert "- ch001 'One' (draft) cast: none [id:c1]" in sent
    assert "secret prose text" not in sent


def test_build_continuity_checker_runner_without_backend_stays_constructible():
    from novelizer.agents.continuity_checker import build_continuity_checker_runner

    class FakeSettings:
        agent_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        agent_temperature = 0.7
        llm_max_tokens = None

    runner = build_continuity_checker_runner(FakeSettings())
    assert runner is not None


def test_build_continuity_checker_runner_with_canon_backend_builds():
    from novelizer.agents.continuity_checker import build_continuity_checker_runner
    from novelizer.canon_fs.backend import CanonBackend

    class FakeSettings:
        agent_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        agent_temperature = 0.7
        llm_max_tokens = None

    backend = CanonBackend(read_store=None)
    runner = build_continuity_checker_runner(FakeSettings(), backend=backend, tools=[])
    assert runner is not None


def test_build_continuity_checker_runner_with_backend_bounds_recursion():
    """Fix 3: pull-mode runners must cap the tool loop."""
    from novelizer.agents.continuity_checker import build_continuity_checker_runner
    from novelizer.canon_fs.backend import CanonBackend

    class FakeSettings:
        agent_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        agent_temperature = 0.7
        llm_max_tokens = None

    backend = CanonBackend(read_store=None)
    runner = build_continuity_checker_runner(FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 100


def test_build_continuity_checker_runner_binds_callbacks_at_graph_scope_not_model():
    """Fix 1: telemetry callbacks must be bound on the graph so ToolNode
    executions under invoke-time config see them."""
    from novelizer.agents.continuity_checker import build_continuity_checker_runner
    from novelizer.canon_fs.backend import CanonBackend
    from langchain_core.callbacks.base import BaseCallbackHandler

    class FakeSettings:
        agent_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        agent_temperature = 0.7
        llm_max_tokens = None

    handler = BaseCallbackHandler()
    backend = CanonBackend(read_store=None)
    runner = build_continuity_checker_runner(
        FakeSettings(), callbacks=[handler], backend=backend, tools=[],
    )
    assert handler in (runner.config.get("callbacks") or [])


def test_build_continuity_mining_runner_construction_unchanged():
    from novelizer.agents.continuity_checker import build_continuity_mining_runner

    class FakeSettings:
        agent_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        agent_temperature = 0.7
        llm_max_tokens = None

    runner = build_continuity_mining_runner(FakeSettings())
    assert runner is not None
