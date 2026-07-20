import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import KeeperOutput, CharacterUpdate, NewCharacter, RetconDraft, KnowledgeIntent
from novelizer.canon.events import SecretCreated
from novelizer.store.models import Character, Chapter, RetconStatus
from novelizer.agents.base import DEFAULT_PASS_REMARK


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


async def test_updates_character_arc_and_files_retcon(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", traits="stoic", arc_status="wary"))
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira wept openly."))
    await proj.catch_up()
    out = KeeperOutput(
        updated_characters=[CharacterUpdate(id="c1", arc_status="cracking")],
        retcon_requests=[RetconDraft(description="stoic vs weeping", conflicting_entry_ids=["c1", "ch1"], proposed_resolution="show restraint")],
    )
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.arc_status == "cracking" and mira.name == "Mira" and mira.traits == "stoic"
    assert len(await read.list_retcon_requests(status=RetconStatus.open)) == 1


async def test_noop_when_no_characters(stack):
    events, proj, read, committer = stack
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mara", traits="wary"))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, personality="A protective, watchful presence.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A protective, watchful presence." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    out = KeeperOutput(feed_note="Mara's arc is bending toward trust.")
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.commit(out, {"characters": [], "recent": []})
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Mara's arc is bending toward trust."


async def test_updates_character_voice_and_leaves_unset_voice_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(
        EventType.CHARACTER_CREATED, "c1",
        Character(id="c1", name="Mira", traits="stoic", arc_status="wary", voice="Speaks in short, clipped sentences."),
    )
    await proj.catch_up()

    # First update: voice is set explicitly and should change.
    out = KeeperOutput(updated_characters=[
        CharacterUpdate(id="c1", voice="Now trails off mid-sentence when scared."),
    ])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.voice == "Now trails off mid-sentence when scared."
    assert mira.traits == "stoic"  # untouched field unaffected

    # Second update: voice left None should not clobber the existing voice.
    out2 = KeeperOutput(updated_characters=[CharacterUpdate(id="c1", arc_status="cracking")])
    agent2 = CharacterKeeper(FakeRunner(out2), read, committer)
    await agent2.run_once()
    await proj.catch_up()
    mira2 = await read.get_character("c1")
    assert mira2.voice == "Now trails off mid-sentence when scared."
    assert mira2.arc_status == "cracking"


async def test_character_keeper_commit_learn_commits_secret_learned(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    out = KeeperOutput(
        knowledge_intents=[KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara", note="pieced it together")],
    )
    keeper = CharacterKeeper(FakeRunner(out), read, committer)
    await keeper.run_once()
    await proj.catch_up()
    matrix = await read.knowledge_matrix()
    assert "mara" in matrix["the-heir-lives"]["known_by"]


async def test_character_keeper_commit_drops_non_learn_actions(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await proj.catch_up()
    out = KeeperOutput(knowledge_intents=[KnowledgeIntent(action="plant", title="Should Not Commit")])
    keeper = CharacterKeeper(FakeRunner(out), read, committer)
    await keeper.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []


async def test_character_keeper_commit_with_no_knowledge_intents_emits_no_secret_events(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await proj.catch_up()
    out = KeeperOutput()
    keeper = CharacterKeeper(FakeRunner(out), read, committer)
    await keeper.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []


from novelizer.store.models import RetconRequest


async def _seed_keeper_scene(events, proj):
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", traits="stoic", arc_status="wary"))
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira wept openly."))
    await proj.catch_up()


async def _seed_open_retcon(events, proj, description="stoic vs weeping"):
    req = RetconRequest(description=description, conflicting_entry_ids=["c1"], proposed_resolution="show restraint")
    await events.append(EventType.RETCON_REQUEST_CREATED, req.id, req)
    await proj.catch_up()
    return req


async def test_poll_includes_open_retcons(stack):
    events, proj, read, committer = stack
    await _seed_open_retcon(events, proj)
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer)
    ctx = await agent.poll()
    assert [r.description for r in ctx["open_retcons"]] == ["stoic vs weeping"]


async def test_work_prompt_lists_open_retcons(stack):
    events, proj, read, committer = stack
    await _seed_keeper_scene(events, proj)
    await _seed_open_retcon(events, proj)
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "already filed (do not re-report these)" in sent
    assert "stoic vs weeping" in sent


async def test_work_prompt_omits_retcon_block_when_queue_empty(stack):
    events, proj, read, committer = stack
    await _seed_keeper_scene(events, proj)
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "already filed" not in sent


async def test_creates_characters_mined_from_chapters(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Silas Vane met Mrs. Gable."))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[
        NewCharacter(name="Silas Vane", traits="haunted", arc_status="arriving"),
        NewCharacter(name="Mrs. Gable", motivations="keep the building's peace"),
    ])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    silas = await read.get_character("silas-vane")
    assert silas.name == "Silas Vane" and silas.traits == "haunted" and silas.arc_status == "arriving"
    gable = await read.get_character("mrs-gable")
    assert gable.name == "Mrs. Gable" and gable.motivations == "keep the building's peace"
    log = await events.events_since(0)
    created = [e for e in log if e.event_type == EventType.CHARACTER_CREATED]
    assert {e.aggregate_id for e in created} == {"silas-vane", "mrs-gable"}


async def test_work_invokes_runner_when_cast_empty_but_chapters_exist(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="The Listening Wall", prose="Silas listened."))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer)
    ctx = await agent.poll()
    out = await agent.work(ctx)
    assert out is not None
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "The Listening Wall" in sent
    assert "None yet" in sent


async def test_work_prompt_includes_characters_introduced_late_in_a_chapter(stack):
    events, proj, read, committer = stack
    filler = "The rain kept falling on the empty street outside the chapel. " * 20
    prose = filler + "Silas Vane stepped out of the shadows and spoke."
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose=prose))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Silas Vane" in sent


async def test_work_prompt_caps_prose_at_configured_prose_chars(stack):
    events, proj, read, committer = stack
    prose = ("x" * 150) + " Silas Vane arrived."
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose=prose))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, prose_chars=100)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Silas Vane" not in sent


async def test_new_character_colliding_with_existing_id_is_not_recreated(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "silas-vane", Character(id="silas-vane", name="Silas Vane", traits="stoic"))
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Silas Vane returned."))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[NewCharacter(name="Silas Vane!", traits="grim")])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    silas = await read.get_character("silas-vane")
    assert silas.traits == "stoic"  # existing record not clobbered by a re-create
    log = await events.events_since(0)
    created = [e for e in log if e.event_type == EventType.CHARACTER_CREATED and e.aggregate_id == "silas-vane"]
    assert len(created) == 1  # only the seed event


async def test_new_character_with_blank_name_is_dropped(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Someone was there."))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[NewCharacter(name="   ")])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []


async def test_duplicate_new_characters_in_one_output_create_once(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Maeve. Maeve!"))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[
        NewCharacter(name="Maeve", traits="first"),
        NewCharacter(name="MAEVE", traits="second"),
    ])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    maeve = await read.get_character("maeve")
    assert maeve.traits == "first"  # first mention wins, duplicate dropped
    log = await events.events_since(0)
    assert len([e for e in log if e.event_type == EventType.CHARACTER_CREATED]) == 1


async def test_readiness_prioritizes_bootstrap_when_chapters_but_no_cast(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Silas."))
    await proj.catch_up()
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer)
    assert await agent.readiness() == 0.8


async def test_retcon_matching_open_description_is_not_refiled(stack):
    events, proj, read, committer = stack
    await _seed_keeper_scene(events, proj)
    await _seed_open_retcon(events, proj)
    out = KeeperOutput(retcon_requests=[RetconDraft(
        description="stoic vs weeping", conflicting_entry_ids=["c1"], proposed_resolution="show restraint")])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert len([r for r in open_reqs if r.description == "stoic vs weeping"]) == 1


class BoomRunner:
    async def ainvoke(self, inputs):
        raise RuntimeError("boom")


async def test_keeper_readiness_zero_when_state_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira arrives."))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[NewCharacter(name="Mira")])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    assert await agent.readiness() > 0.0
    await agent.run_once()
    await proj.catch_up()
    # Its own minted character must not re-trigger it; no new external state.
    assert await agent.readiness() == 0.0
    await events.append(EventType.CHAPTER_CREATED, "ch2", Chapter(id="ch2", title="Two", prose="More."))
    await proj.catch_up()
    assert await agent.readiness() > 0.0


async def test_keeper_failed_run_leaves_watermark_unset(stack):
    events, proj, read, committer = stack
    # Seed BOTH a chapter and a character so readiness takes the gated 0.5
    # path, not the ungated 0.8 cast-bootstrap branch — otherwise this test
    # would pass even if a failed run wrongly recorded the watermark.
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira"))
    await proj.catch_up()
    agent = CharacterKeeper(BoomRunner(), read, committer)
    with pytest.raises(RuntimeError):
        await agent.run_once()
    assert await agent.readiness() == 0.5


async def test_keeper_no_action_pass_commits_nothing_and_backs_off(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    out = KeeperOutput(no_action=True, new_characters=[NewCharacter(name="Ghost")],
                       feed_note="All quiet on the cast front — write on.")
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []          # populated list ignored on a pass
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert [e.payload["note"] for e in remarks] == ["All quiet on the cast front — write on."]
    import time
    assert agent.seconds_until_ready(time.monotonic()) > agent.interval


async def test_keeper_pass_uses_default_remark_when_feed_note_empty(stack):
    events, proj, read, committer = stack
    agent = CharacterKeeper(FakeRunner(KeeperOutput(no_action=True)), read, committer)
    await agent.commit(KeeperOutput(no_action=True), {"characters": [], "recent": [], "secrets": [], "hands": []})
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert [e.payload["note"] for e in remarks] == [DEFAULT_PASS_REMARK]


class ChapterCommittingRunner(FakeRunner):
    """Simulates the Author committing a chapter while the Keeper's LLM call
    is in flight."""

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


async def test_keeper_midrun_chapter_is_not_absorbed_by_watermark(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira"))
    await proj.catch_up()
    agent = CharacterKeeper(ChapterCommittingRunner(KeeperOutput(), events, proj), read, committer)
    await agent.run_once()
    # The mid-run chapter was never analyzed: the watermark must stay clear.
    assert await agent.readiness() > 0.0


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_character_keeper_runner_without_backend_stays_constructible():
    from novelizer.agents.character_keeper import build_character_keeper_runner

    runner = build_character_keeper_runner(_FakeSettings())
    assert runner is not None


def test_build_character_keeper_runner_with_backend_uses_retrieval_note_base():
    from novelizer.agents.character_keeper import build_character_keeper_runner, SYSTEM_PROMPT
    from novelizer.agents.author import RETRIEVAL_NOTE_BASE
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_character_keeper_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner is not None
    assert "chapter list below" not in RETRIEVAL_NOTE_BASE
    assert (SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)


def test_build_character_keeper_runner_with_backend_bounds_recursion():
    from novelizer.agents.character_keeper import build_character_keeper_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_character_keeper_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 100
