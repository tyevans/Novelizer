import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import KeeperOutput, CharacterUpdate, NewCharacter, FlagDraft, KnowledgeIntent, ArcIntent
from novelizer.agents.character_keeper import SYSTEM_PROMPT
from novelizer.canon.events import SecretCreated, BlueprintAdopted, ArcDeclared, ChapterProcessed
from novelizer.store.models import Character, Chapter, FlagStatus
from novelizer.agents.prompts import DEFAULT_PASS_REMARK


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
        flags=[FlagDraft(category="contradiction", description="stoic vs weeping", related_entry_ids=["c1", "ch1"], proposed_resolution="show restraint")],
    )
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.arc_status == "cracking" and mira.name == "Mira" and mira.traits == "stoic"
    assert len(await read.list_flags(category="contradiction", status=FlagStatus.open)) == 1


async def test_noop_when_no_characters(stack):
    events, proj, read, committer = stack
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mara", traits="wary"))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events, personality="A protective, watchful presence.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A protective, watchful presence." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    out = KeeperOutput(feed_note="Mara's arc is bending toward trust.")
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
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
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.voice == "Now trails off mid-sentence when scared."
    assert mira.traits == "stoic"  # untouched field unaffected

    # Second update: voice left None should not clobber the existing voice.
    out2 = KeeperOutput(updated_characters=[CharacterUpdate(id="c1", arc_status="cracking")])
    agent2 = CharacterKeeper(FakeRunner(out2), read, committer, events)
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
    keeper = CharacterKeeper(FakeRunner(out), read, committer, events)
    await keeper.run_once()
    await proj.catch_up()
    matrix = await read.knowledge_matrix()
    assert "mara" in matrix["the-heir-lives"]["known_by"]


async def test_character_keeper_commit_drops_non_learn_actions(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await proj.catch_up()
    out = KeeperOutput(knowledge_intents=[KnowledgeIntent(action="plant", title="Should Not Commit")])
    keeper = CharacterKeeper(FakeRunner(out), read, committer, events)
    await keeper.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []


async def test_character_keeper_commit_with_no_knowledge_intents_emits_no_secret_events(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await proj.catch_up()
    out = KeeperOutput()
    keeper = CharacterKeeper(FakeRunner(out), read, committer, events)
    await keeper.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []


from novelizer.store.models import Flag


async def _seed_keeper_scene(events, proj):
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", traits="stoic", arc_status="wary"))
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira wept openly."))
    await proj.catch_up()


async def _seed_open_retcon(events, proj, description="stoic vs weeping"):
    req = Flag(category="contradiction", description=description, related_entry_ids=["c1"], proposed_resolution="show restraint")
    await events.append(EventType.FLAG_CREATED, req.id, req)
    await proj.catch_up()
    return req


async def test_poll_includes_open_retcons(stack):
    events, proj, read, committer = stack
    await _seed_open_retcon(events, proj)
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer, events)
    ctx = await agent.poll()
    assert [r.description for r in ctx["open_retcons"]] == ["stoic vs weeping"]


async def test_work_prompt_lists_open_retcons(stack):
    events, proj, read, committer = stack
    await _seed_keeper_scene(events, proj)
    await _seed_open_retcon(events, proj)
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "already filed (do not re-report these)" in sent
    assert "stoic vs weeping" in sent


async def test_work_prompt_omits_retcon_block_when_queue_empty(stack):
    events, proj, read, committer = stack
    await _seed_keeper_scene(events, proj)
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events)
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
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
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
    agent = CharacterKeeper(runner, read, committer, events)
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
    agent = CharacterKeeper(runner, read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Silas Vane" in sent


async def test_new_character_colliding_with_existing_id_is_not_recreated(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "silas-vane", Character(id="silas-vane", name="Silas Vane", traits="stoic"))
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Silas Vane returned."))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[NewCharacter(name="Silas Vane!", traits="grim")])
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
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
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
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
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
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
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer, events)
    assert await agent.readiness() == 0.8


async def test_retcon_matching_open_description_is_not_refiled(stack):
    events, proj, read, committer = stack
    await _seed_keeper_scene(events, proj)
    await _seed_open_retcon(events, proj)
    out = KeeperOutput(flags=[FlagDraft(
        category="contradiction", description="stoic vs weeping", related_entry_ids=["c1"], proposed_resolution="show restraint")])
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_flags(category="contradiction", status=FlagStatus.open)
    assert len([r for r in open_reqs if r.description == "stoic vs weeping"]) == 1


class BoomRunner:
    async def ainvoke(self, inputs):
        raise RuntimeError("boom")


async def test_keeper_readiness_zero_when_state_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira arrives."))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[NewCharacter(name="Mira")])
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
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
    agent = CharacterKeeper(BoomRunner(), read, committer, events)
    with pytest.raises(RuntimeError):
        await agent.run_once()
    assert await agent.readiness() == 0.5


async def test_keeper_no_action_pass_commits_nothing_and_backs_off(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    out = KeeperOutput(no_action=True, new_characters=[NewCharacter(name="Ghost")],
                       feed_note="All quiet on the cast front — write on.")
    agent = CharacterKeeper(FakeRunner(out), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []          # populated list ignored on a pass
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert [e.payload["note"] for e in remarks] == ["All quiet on the cast front — write on."]
    import time
    assert agent._idle_streak == 1
    assert not agent.ready(time.monotonic())


async def test_keeper_pass_uses_default_remark_when_feed_note_empty(stack):
    events, proj, read, committer = stack
    agent = CharacterKeeper(FakeRunner(KeeperOutput(no_action=True)), read, committer, events)
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
    agent = CharacterKeeper(ChapterCommittingRunner(KeeperOutput(), events, proj), read, committer, events)
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
    assert runner.config.get("recursion_limit") == 200


def test_build_character_keeper_runner_tooled_branch_passes_keeper_skills(monkeypatch):
    from novelizer.agents import character_keeper as keeper_mod
    from novelizer.canon_fs.backend import CanonBackend

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, skills=None, middleware=None, subagents=None):
        captured["skills"] = skills
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    keeper_mod.build_character_keeper_runner(_FakeSettings(), backend=backend, tools=[])
    from novelizer.canon_fs.skills_route import CRAFT_SKILLS
    assert captured["skills"] == CRAFT_SKILLS
    assert captured["skills"] == ["/skills"]


def test_build_character_keeper_runner_tooled_branch_passes_subagents_through(monkeypatch):
    from novelizer.agents import character_keeper as keeper_mod
    from novelizer.canon_fs.backend import CanonBackend

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None,
                                tools=None, skills=None, middleware=None, subagents=None):
        captured["subagents"] = subagents
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    researcher = {"name": "researcher", "description": "d", "system_prompt": "p"}
    keeper_mod.build_character_keeper_runner(_FakeSettings(), backend=backend, tools=[],
                                              subagents=[researcher])
    assert captured["subagents"] == [researcher]


def test_construct_reads_subagent_enabled_setting():
    from novelizer.agents.character_keeper import _construct
    from novelizer.agents.registry_types import AgentContext

    seen = {}

    def fake_tooled(builder, enabled, subagent_enabled=False, subagent_agent_name=""):
        seen["enabled"] = enabled
        seen["subagent_enabled"] = subagent_enabled
        seen["subagent_agent_name"] = subagent_agent_name
        return builder

    class _Settings(_FakeSettings):
        character_keeper_tools_enabled = True
        character_keeper_subagent_enabled = True
        default_agent_interval = 120
        keeper_prose_chars = 6000
        extractor_token_budget = 24000

    ctx = AgentContext(
        read=None, committer=None, events=None, settings=_Settings(),
        casting_note="", personalities={}, provenance={}, tooled=fake_tooled,
        runner_for=lambda name, builder, fallback_name=None: builder(_Settings()),
    )
    _construct(ctx)
    assert seen == {"enabled": True, "subagent_enabled": True, "subagent_agent_name": "character_keeper"}


def test_spec_carries_subagent_grant():
    from novelizer.agents.character_keeper import SPEC
    assert SPEC.subagent_grant.enabled_setting == "character_keeper_subagent_enabled"


def test_build_character_keeper_runner_bare_branch_carries_no_skills_kwarg(monkeypatch):
    from novelizer.agents import character_keeper as keeper_mod

    captured = {}

    class FakeGraph:
        pass

    def fake_create_deep_agent(*, model, system_prompt, response_format):
        captured["called"] = True
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    keeper_mod.build_character_keeper_runner(_FakeSettings())
    assert captured["called"]


def test_system_prompt_mentions_arc_task():
    assert "arc" in SYSTEM_PROMPT.lower()


async def test_work_prompt_includes_arc_note_when_arc_stagnant(stack):
    events, proj, read, committer = stack
    for i in range(5):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(
        EventType.ARC_DECLARED, "arc1",
        ArcDeclared(arc_id="arc1", character_id="mara", arc_type="positive", lie="I am alone"),
    )
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Arc alignment:" in sent
    assert "route Mara into the next brief" in sent


async def test_work_prompt_omits_arc_note_when_quiet(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Arc alignment:" not in sent


async def test_declare_arc_intent_projects_active_arc_for_character(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await proj.catch_up()
    out = KeeperOutput(
        arc_intents=[ArcIntent(action="declare", character_id="mara", arc_type="positive", lie="I am alone")],
    )
    keeper = CharacterKeeper(FakeRunner(out), read, committer, events)
    await keeper.run_once()
    await proj.catch_up()
    arcs = await read.list_arcs(active_only=True)
    assert len(arcs) == 1
    assert arcs[0].character_id == "mara"
    assert arcs[0].arc_type == "positive"
    assert arcs[0].lie == "I am alone"


async def test_advance_arc_intent_carries_latest_analyzed_chapter(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(
        EventType.ARC_DECLARED, "arc1",
        ArcDeclared(arc_id="arc1", character_id="mara", arc_type="positive", lie="I am alone"),
    )
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mara grew."))
    await proj.catch_up()
    out = KeeperOutput(arc_intents=[ArcIntent(action="advance", id="arc1", note="grew")])
    keeper = CharacterKeeper(FakeRunner(out), read, committer, events)
    await keeper.run_once()
    await proj.catch_up()
    arc = await read.get_active_arc_for_character("mara")
    assert arc.last_chapter_id == "ch1"


async def test_new_character_and_declare_arc_intent_in_same_pass(stack):
    events, proj, read, committer = stack
    out = KeeperOutput(
        new_characters=[NewCharacter(name="Mara")],
        arc_intents=[ArcIntent(action="declare", character_id="mara", arc_type="positive", lie="I am alone")],
    )
    keeper = CharacterKeeper(FakeRunner(out), read, committer, events)
    await keeper.commit(out, {"characters": [], "recent": []})
    await proj.catch_up()
    arc = await read.get_active_arc_for_character("mara")
    assert arc is not None
    assert arc.arc_type == "positive"
    assert arc.lie == "I am alone"


async def test_resolve_arc_intent_citing_unknown_id_is_dropped(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await proj.catch_up()
    out = KeeperOutput(
        arc_intents=[ArcIntent(action="resolve", id="nonexistent", outcome="truth_embraced")],
    )
    keeper = CharacterKeeper(FakeRunner(out), read, committer, events)
    await keeper.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("arc.")] == []


async def test_work_prompt_includes_active_arcs_block(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(
        EventType.ARC_DECLARED, "arc1",
        ArcDeclared(arc_id="arc1", character_id="mara", arc_type="positive", lie="I am alone"),
    )
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Mara: positive arc (id:arc1) lie='I am alone' advances=0" in sent


async def test_work_prompt_annotates_resolved_arc_with_outcome(stack):
    from novelizer.canon.events import ArcResolved
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(
        EventType.ARC_DECLARED, "arc1",
        ArcDeclared(arc_id="arc1", character_id="mara", arc_type="positive", lie="I am alone"),
    )
    await events.append(
        EventType.ARC_RESOLVED, "arc1",
        ArcResolved(arc_id="arc1", chapter_id="ch1", outcome="truth_embraced"),
    )
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "[resolved:truth_embraced]" in sent


async def test_work_prompt_includes_available_beat_ids_when_beats_exist(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(
        EventType.ARC_DECLARED, "arc1",
        ArcDeclared(arc_id="arc1", character_id="mara", arc_type="positive"),
    )
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=10,
            beats=[
                {
                    "beat_id": "bp1-midpoint", "slug": "midpoint", "name": "Midpoint",
                    "ideal_pct": 0.5, "tolerance_pct": 0.1, "expected_polarity": "flip",
                },
            ],
        ),
    )
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Available beat ids for pivots: bp1-midpoint" in sent


class SequenceRunner:
    """Returns a distinct structured_response per call, in order."""

    def __init__(self):
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        idx = len(self.calls)
        return {"structured_response": KeeperOutput(new_characters=[NewCharacter(name=f"Char{idx}")])}


async def test_sweep_stamps_processed_and_drains_backlog(stack):
    events, proj, read, committer = stack
    # ~150 chars each -> ~38 tokens (CharHeuristicEstimator, chars/4); with a
    # 100-token budget the first two chapters fit together (76) but the third
    # would push spent to 114 > 100 and so waits for the next run.
    prose = "word " * 30
    for i in (1, 2, 3):
        await events.append(EventType.CHAPTER_CREATED, f"ch{i}", Chapter(id=f"ch{i}", title=f"Ch{i}", prose=prose))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, events, extractor_token_budget=100)

    await agent.run_once()
    await proj.catch_up()
    processed = await events.events_since(0, event_types=[EventType.CHAPTER_PROCESSED])
    assert {e.payload["chapter_id"] for e in processed} == {"ch1", "ch2"}
    assert len(runner.calls) == 1

    await agent.run_once()
    await proj.catch_up()
    processed = await events.events_since(0, event_types=[EventType.CHAPTER_PROCESSED])
    assert {e.payload["chapter_id"] for e in processed} == {"ch1", "ch2", "ch3"}
    assert len(runner.calls) == 2

    await agent.run_once()
    await proj.catch_up()
    processed = await events.events_since(0, event_types=[EventType.CHAPTER_PROCESSED])
    assert {e.payload["chapter_id"] for e in processed} == {"ch1", "ch2", "ch3"}
    assert len(runner.calls) == 2  # backlog drained: zero new LLM calls


async def test_revised_chapter_is_reswept(stack):
    from novelizer.canon.events import ChapterRevised

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer, events)

    await events.append(
        EventType.CHAPTER_PROCESSED, "ch1", ChapterProcessed(agent="character_keeper", chapter_id="ch1"),
    )
    await proj.catch_up()
    ctx = await agent.poll()
    assert ctx["unmined"] == []

    await events.append(
        EventType.CHAPTER_REVISED, "ch1", ChapterRevised(chapter_id="ch1", prose="revised text"),
    )
    await proj.catch_up()
    ctx = await agent.poll()
    assert [c.id for c in ctx["unmined"]] == ["ch1"]


async def test_oversize_chapter_windows_merge(stack):
    events, proj, read, committer = stack
    prose = "word " * 2000  # far beyond a 50-token budget: multiple windows
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose=prose))
    await proj.catch_up()
    runner = SequenceRunner()
    agent = CharacterKeeper(runner, read, committer, events, extractor_token_budget=50)

    await agent.run_once()
    await proj.catch_up()

    assert len(runner.calls) > 1  # sanity: the chapter really was windowed
    chars = await read.list_characters()
    assert {c.name for c in chars} == {f"Char{i}" for i in range(1, len(runner.calls) + 1)}
    processed = await events.events_since(0, event_types=[EventType.CHAPTER_PROCESSED])
    assert len(processed) == 1
    assert processed[0].payload["chapter_id"] == "ch1"


async def test_prompt_contains_full_prose_not_slice(stack):
    events, proj, read, committer = stack
    prose = "filler " * 2000 + "LATE_ARRIVAL_SENTINEL"
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose=prose))
    await proj.catch_up()

    push_runner = FakeRunner(KeeperOutput())
    push_agent = CharacterKeeper(push_runner, read, committer, events)
    ctx = await push_agent.poll()
    await push_agent.work(ctx)
    sent = push_runner.calls[-1]["messages"][0]["content"]
    assert "LATE_ARRIVAL_SENTINEL" in sent

    pull_runner = FakeRunner(KeeperOutput())
    pull_agent = CharacterKeeper(pull_runner, read, committer, events, pull_mode=True)
    ctx = await pull_agent.poll()
    await pull_agent.work(ctx)
    sent = pull_runner.calls[-1]["messages"][0]["content"]
    assert "LATE_ARRIVAL_SENTINEL" in sent
    assert "Unread chapters (full prose — mine these now):" in sent


async def test_readiness_stays_open_until_backlog_drained(stack):
    """Regression: with an existing cast (the gated readiness path), the
    watermark must not be recorded while budgeted backlog remains — the
    scheduler otherwise never dispatches the rest of the sweep."""
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", traits="stoic"))
    prose = "word " * 30  # ~38 tokens; budget 100 fits two per run
    for i in (1, 2, 3):
        await events.append(EventType.CHAPTER_CREATED, f"ch{i}", Chapter(id=f"ch{i}", title=f"Ch{i}", prose=prose))
    await proj.catch_up()
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer, events, extractor_token_budget=100)

    await agent.run_once()
    assert await agent.readiness() > 0.0  # ch3 still unmined: gate stays open

    await agent.run_once()
    assert await agent.readiness() == 0.0  # drained: watermark recorded

    await events.append(EventType.CHAPTER_CREATED, "ch4", Chapter(id="ch4", title="Ch4", prose=prose))
    await proj.catch_up()
    assert await agent.readiness() > 0.0  # new prose reopens the gate


def test_keeper_prompt_tells_it_what_to_do_when_no_secrets_exist():
    """The Keeper's only secret action is `learn`, which needs an id from a
    citation aid that is empty until a secret exists. Silence in that state
    invites an invented id; the prompt has to name the empty case."""
    from novelizer.agents.character_keeper import SYSTEM_PROMPT

    # The prompt is hard-wrapped, so match on collapsed whitespace.
    flat = " ".join(SYSTEM_PROMPT.split()).lower()
    assert "lists no active secrets" in flat
    assert "never invent one" in flat
