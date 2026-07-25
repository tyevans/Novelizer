import os
import tempfile
import time
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.canon.events import EventType
from novelizer.agents.author import ChapterDraft
from novelizer.store.models import DirectorSignal, SignalKind
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, CharacterUpdate,
    EditorVerdict, ContinuityOutput, RetconAmendments, FlagDraft,
)
from novelizer.agents.base import ChapterDraft
from novelizer.canon.committer import GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.proposal_service import ProposalService
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.agents.registry import AGENT_REGISTRY


class FakeRunner:
    def __init__(self, draft): self._draft = draft
    async def ainvoke(self, inputs):
        return {"structured_response": self._draft}


@pytest.fixture
def settings(tmp_path):
    # Per-test temp DIRECTORY, not a bare mkstemp file in shared /tmp. The
    # runtime derives sibling paths from db_path -- embed_cursor.json,
    # kg_cursor.json, the embeddings store -- via Path.with_name(), so a db at
    # /tmp/tmpXXXX.db puts every cursor at the FIXED /tmp/embed_cursor.json,
    # shared across every test and every parallel run. A stale cursor left at
    # seq N then makes a fresh room's lag() read 0, which the background-gate
    # tests assert against directly. An isolated dir gives each test its own
    # cursor namespace. (Production is unaffected: each story owns its dir.)
    return Settings(db_path=str(tmp_path / "world.db"))


@pytest.fixture
def runtime(settings):
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    rt._canon_backend, rt._canon_tools = object(), []
    return rt


async def test_start_wires_a_working_slice(settings):
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    await rt.start()
    try:
        await rt.events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                               DirectorSignal(id="s1", kind=SignalKind.seed, body="begin"))
        await rt.projector.catch_up()
        await rt.author.run_once()
        await rt.projector.catch_up()
        assert "Chapter One" in [c.title for c in await rt.read.list_chapters()]
    finally:
        await rt.close()


async def test_start_wires_a_shared_llm_pool_into_the_scheduler(settings):
    """Phase 3: Runtime.start() constructs one AdaptivePool sized from
    llm_pool_size and hands the SAME object to the scheduler. That single shared
    ceiling is the whole point -- the scheduler and (later) the KG drain must
    draw permits from one pool, not two independent budgets on one vLLM
    endpoint. Duck-typed on purpose: no import of agent_kit.pool here, so its
    absence reds only this test (AttributeError: no attribute 'pool'), never the
    whole file's collection."""
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    try:
        await rt.start()
        assert rt.pool is not None
        assert rt.pool.size == settings.llm_pool_size
        assert rt.scheduler._pool is rt.pool
    finally:
        await rt.close()


async def test_start_shares_the_llm_pool_with_both_background_drains(settings):
    """Phase 5: the SAME AdaptivePool handed to the scheduler is handed to both
    projectors, so agents and the two drains share ONE endpoint ceiling and one
    AIMD controller -- the property that does not hold today (independent LLM
    consumers, no shared limit, the 429-pileup source). Duck-typed on the `_pool`
    attribute so a missing wiring reds only this test, not the file's collection."""
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    try:
        await rt.start()
        assert rt.indexer._pool is rt.pool
        assert rt.kg_projector._pool is rt.pool
    finally:
        await rt.close()


async def test_start_wires_background_drain_concurrency_from_settings(settings):
    """Phase 5: start() passes background_drain_concurrency through to both
    projectors as their fan-out cap (`_drain_concurrency`)."""
    rt = Runtime(settings.model_copy(update={"background_drain_concurrency": 3}),
                 runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    try:
        await rt.start()
        assert rt.indexer._drain_concurrency == 3
        assert rt.kg_projector._drain_concurrency == 3
    finally:
        await rt.close()


async def test_runtime_wires_gating_committer_and_policy(settings):
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    try:
        await rt.start()
        assert isinstance(rt.committer, GatingCommitter)
        assert isinstance(rt.policy, AutonomyPolicy)
        assert isinstance(rt.proposals, ProposalService)
        assert rt.author._committer is rt.committer
        assert rt.world_architect._committer is rt.committer
    finally:
        await rt.close()


async def test_runtime_wires_continuity_checker_mining_runner_and_event_store(settings):
    """ContinuityChecker must receive the runtime's own EventStore instance and a
    non-None mining runner. When a fixture supplies a dedicated
    "continuity_checker_mining" fake, that dedicated fake -- not the plain
    "continuity_checker" fake -- must be the one actually held by the instance."""
    mining_fake = ScriptedRunner(ContinuityOutput())
    runners = {
        "world_architect": ScriptedRunner(WorldEntriesDraft(entries=[])),
        "author": ScriptedRunner(ChapterDraft(title="Chapter One", prose="It began.")),
        "character_keeper": ScriptedRunner(KeeperOutput()),
        "editor": ScriptedRunner(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": ScriptedRunner(ContinuityOutput()),
        "continuity_checker_mining": mining_fake,
        "retconner": ScriptedRunner(RetconAmendments()),
        "structure_analyst": _FakeAgentRunner(),
        "summarizer": _FakeAgentRunner(),
    }
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert rt.continuity_checker._events is rt.events
        assert rt.continuity_checker._mining_runner is not None
        assert rt.continuity_checker._mining_runner is mining_fake
    finally:
        await rt.close()


async def test_runtime_gating_end_to_end_via_scheduler(settings):
    """Set autonomy to gate chapters; author's output queues as a proposal, not a chapter.
    Approving it makes the chapter appear."""
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    try:
        await rt.start()
        await rt.events.append(EventType.AUTONOMY_CHANGED, "singleton",
                                AutonomyState(global_level=AutonomyLevel.gated_canon))
        await rt.projector.catch_up()
        from novelizer.store.models import Chapter
        ch = Chapter(id="c1", title="Gated One", prose="p")
        await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
        await rt.projector.catch_up()
        assert await rt.read.list_chapters() == []
        pending = await rt.read.list_proposals(status="open")
        assert len(pending) == 1 and pending[0].payload["title"] == "Gated One"

        await rt.proposals.approve(pending[0])
        await rt.projector.catch_up()
        chapters = await rt.read.list_chapters()
        assert len(chapters) == 1 and chapters[0].title == "Gated One"
    finally:
        await rt.close()


class ScriptedRunner:
    """Returns a fixed structured_response every call."""
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


async def test_full_pipeline_runs_under_runtime(settings):
    runners = {
        "world_architect": ScriptedRunner(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": ScriptedRunner(ChapterDraft(title="Chapter One", prose="It began on the salt flats.")),
        "character_keeper": ScriptedRunner(KeeperOutput()),
        "editor": ScriptedRunner(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": ScriptedRunner(ContinuityOutput()),
        "retconner": ScriptedRunner(RetconAmendments()),
        "structure_analyst": _FakeAgentRunner(),
        "plotter": _FakeAgentRunner(),
        "summarizer": _FakeAgentRunner(),
    }
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert {a.name for a in rt.agents} == {
            "world_architect", "author", "character_keeper", "editor", "continuity_checker",
            "retconner", "curator", "structure_analyst", "muse", "plotter", "triage", "summarizer",
            "flaglabeler",
        }
        # Drive each agent once directly (deterministic), projecting between.
        for name in ["world_architect", "author", "editor"]:
            agent = next(a for a in rt.agents if a.name == name)
            await agent.run_once()
            await rt.projector.catch_up()
        assert "Brinemarsh" in [e.title for e in await rt.read.list_world_entries()]
        chapters = await rt.read.list_chapters()
        assert chapters and chapters[0].title == "Chapter One"
        assert chapters[0].editorial_status.value == "reviewed"
    finally:
        await rt.close()


class ScriptedContinuityRunner:
    """Returns one FlagDraft referencing a known world-entry id on its first call,
    then empty ContinuityOutput on every subsequent call (so it doesn't keep piling
    up new retcons once the scheduler starts favoring it again)."""

    def __init__(self, first_out, later_out):
        self._first = first_out
        self._later = later_out
        self._calls = 0

    async def ainvoke(self, inputs):
        self._calls += 1
        out = self._first if self._calls == 1 else self._later
        return {"structured_response": out}


class AdvancingClock:
    """A fake monotonic clock that jumps well past any backoff ladder deadline
    on each call, so an agent that took a no-progress step back is eligible
    again by the next tick and the loop below cannot stall on it.

    Seeded from time.monotonic() rather than zero: the agents' own ladders are
    stamped with the real monotonic clock (novelizer's BaseAgent takes no clock
    injection), and a scheduler clock starting behind those deadlines would
    read every backed-off agent as still backed off."""

    def __init__(self, step: float = 10_000.0) -> None:
        self._t = time.monotonic()
        self._step = step

    def __call__(self) -> float:
        self._t += self._step
        return self._t


async def test_scheduler_drives_full_retcon_loop_end_to_end(settings):
    """The done-criterion for M1.1 is that the pipeline runs unattended in full-auto,
    driven by the Scheduler. This proves the Scheduler itself (not a hand-driven test)
    selects and runs ContinuityChecker to file a retcon and Retconner to resolve it,
    superseding a pre-existing world entry -- with only the LLM calls faked."""
    known_world_entry_id = "w1"
    retcon = ContinuityOutput(flags=[
        FlagDraft(
            category="contradiction",
            description="two suns vs one sun",
            related_entry_ids=[known_world_entry_id],
            proposed_resolution="there is only one sun",
        )
    ])
    # Every registered agent gets a fake; the scripted ones below are the
    # subject of the test. Any agent left out would fall back to a real,
    # network-bound runner (see _all_fake_runners).
    runners = _all_fake_runners(
        world_architect=ScriptedRunner(WorldEntriesDraft(entries=[])),
        author=ScriptedRunner(ChapterDraft(title="", prose="")),
        character_keeper=ScriptedRunner(KeeperOutput()),
        editor=ScriptedRunner(EditorVerdict(verdict="approve", notes="")),
        continuity_checker=ScriptedContinuityRunner(retcon, ContinuityOutput()),
        retconner=ScriptedRunner(RetconAmendments(amended_entries=[
            WorldEntryDraft(title="Suns", body="One sun.", supersedes_id=known_world_entry_id)
        ])),
    )
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        # Pre-seed a world entry with a KNOWN id so the continuity retcon and the
        # retconner's supersedes_id can both reference it deterministically -- ids
        # are normally generated at commit time, so we can't get one from a fake
        # WorldArchitect run without inspecting store state afterward.
        from novelizer.store.models import WorldEntry
        await rt.events.append(
            EventType.WORLD_ENTRY_CREATED, known_world_entry_id,
            WorldEntry(id=known_world_entry_id, title="Suns", body="Two suns burn overhead."),
        )
        await rt.projector.catch_up()
        assert known_world_entry_id in {e.id for e in await rt.read.list_world_entries()}

        # Give the scheduler an advancing clock so backoff deadlines never block
        # agent eligibility across many ticks.
        rt.scheduler._clock = AdvancingClock()

        # Neutralize the strict background gate: this test's subject is the
        # Scheduler driving the retcon loop, not background catch-up (which has
        # its own dedicated tests). The gate closes on any index/KG lag, and the
        # pre-seeded WORLD_ENTRY_CREATED raises it -- but there is no embed
        # endpoint here to drain it back to zero, so an active gate would freeze
        # the room forever. Disabling it isolates the behavior under test, the
        # same way the AdvancingClock above neutralizes backoff.
        rt.scheduler._gate_provider = None

        # Phase 1: only ContinuityChecker is eligible -- this forces the scheduler
        # to select it (deterministically) to file the retcon.
        for name in ("world_architect", "author", "character_keeper", "editor", "retconner"):
            rt.scheduler.pause_agent(name)
        for _ in range(10):
            ran = await rt.scheduler.tick()
            await rt.scheduler.drain_in_flight()
            await rt.projector.catch_up()
            if "continuity_checker" in ran and await rt.read.list_flags(category="contradiction", status="open"):
                break
        open_retcons = await rt.read.list_flags(category="contradiction", status="open")
        assert len(open_retcons) == 1, "scheduler did not drive ContinuityChecker to file a retcon"
        assert open_retcons[0].related_entry_ids == [known_world_entry_id]

        # Phase 2: pause ContinuityChecker, resume Retconner -- this forces the
        # scheduler to select Retconner (deterministically) to resolve the retcon.
        rt.scheduler.pause_agent("continuity_checker")
        rt.scheduler.resume_agent("retconner")
        for _ in range(10):
            ran = await rt.scheduler.tick()
            await rt.scheduler.drain_in_flight()
            await rt.projector.catch_up()
            if "retconner" in ran and await rt.read.list_flags(category="contradiction", status="resolved"):
                break

        # Real, non-vacuous assertions: the retcon actually resolved, and the world
        # entry it targeted was actually superseded (old id gone, new body active).
        resolved = await rt.read.list_flags(category="contradiction", status="resolved")
        assert len(resolved) >= 1
        assert await rt.read.list_flags(category="contradiction", status="open") == []
        active_ids = {e.id for e in await rt.read.list_world_entries()}
        assert known_world_entry_id not in active_ids
        active_bodies = {e.body for e in await rt.read.list_world_entries()}
        assert "One sun." in active_bodies
    finally:
        await rt.close()


class _FakeAgentRunner:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


def _all_fake_runners(**overrides):
    """A fake runner for EVERY registered agent, plus any caller overrides.

    Derived from AGENT_REGISTRY rather than a hand-written name list. Runtime's
    `_runner_for` falls back to BUILDING THE REAL RUNNER for any name a test did
    not inject, and a real runner talks to the LLM endpoint -- which no test has
    -- so each miss costs a full connect-and-retry ladder before failing. A
    hardcoded list silently acquires those misses as agents are registered: this
    list had drifted to 9 of 13 (muse, plotter, curator, triage, flaglabeler
    unfaked), and one scheduler test that ticks them ran for 143s of the suite's
    480s. Deriving the fleet from the registry makes a newly-registered agent
    faked by construction instead of quietly slow.
    """
    runners = {spec.name: _FakeAgentRunner() for spec in AGENT_REGISTRY}
    runners.update(overrides)
    return runners


async def test_runtime_wires_structure_analyst_as_a_seventh_agent():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path)
        runners = _all_fake_runners()
        runners["structure_analyst"] = _FakeAgentRunner()
        rt = Runtime(settings, runners=runners)
        await rt.start()
        assert {a.name for a in rt.agents} == {
            "world_architect", "author", "character_keeper", "editor",
            "continuity_checker", "retconner", "curator", "structure_analyst", "muse",
            "plotter", "triage", "summarizer", "flaglabeler",
        }
        assert rt.structure_analyst is not None
        assert rt.structure_analyst._committer is rt.committer
        assert rt.structure_analyst.interval == settings.structure_analyst_interval
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_wires_structure_analyst_personality_from_the_pack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path)
        runners = _all_fake_runners()
        runners["structure_analyst"] = _FakeAgentRunner()
        rt = Runtime(settings, runners=runners)
        await rt.start()
        assert rt.structure_analyst.personality == rt.voice_pack.agent_personalities.get("structure_analyst", "")
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_wires_active_prose_profile_into_author():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path, prose_profile="sparse")
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.active_prose_profile is not None
        assert rt.active_prose_profile.name == "sparse"
        assert rt.author._casting_note == rt.active_prose_profile.casting_note
        assert rt.editor._casting_note == rt.active_prose_profile.casting_note
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_unknown_profile_falls_back_to_empty_casting_note():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path, prose_profile="does-not-exist")
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.active_prose_profile is None
        assert rt.author._casting_note == ""
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_wires_each_agents_personality_from_the_pack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path)
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.author.personality == rt.voice_pack.agent_personalities["author"]
        assert rt.editor.personality == rt.voice_pack.agent_personalities["editor"]
        assert rt.world_architect.personality == rt.voice_pack.agent_personalities["world_architect"]
        assert rt.character_keeper.personality == rt.voice_pack.agent_personalities["character_keeper"]
        assert rt.continuity_checker.personality == rt.voice_pack.agent_personalities["continuity_checker"]
        assert rt.retconner.personality == rt.voice_pack.agent_personalities["retconner"]
        assert rt.author.personality != rt.editor.personality
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_missing_personality_falls_back_to_empty_string():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    custom_pack_path = path + ".pack.toml"
    with open(custom_pack_path, "w") as f:
        f.write('name = "sparse-pack"\n')
    try:
        settings = Settings(db_path=path, voice_pack=custom_pack_path)
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.author.personality == ""
        assert rt.retconner.personality == ""
        await rt.close()
    finally:
        os.unlink(path)
        os.unlink(custom_pack_path)


async def test_runtime_wires_telemetry_store_bus_and_recorder(tmp_path):
    from novelizer.settings import EffectiveSettings as Settings
    from novelizer.runtime import Runtime

    class _R:
        async def ainvoke(self, inputs):
            return {"structured_response": None}

    db = tmp_path / "world.db"
    settings = Settings(db_path=str(db))
    rt = Runtime(settings, runners={n: _R() for n in [
        "author", "world_architect", "character_keeper", "editor",
        "continuity_checker", "continuity_checker_mining", "retconner", "structure_analyst", "summarizer"]})
    await rt.start()
    try:
        assert rt.telemetry is not None and rt.telemetry_bus is not None
        # telemetry.db lands beside the domain db — never inside it
        assert (tmp_path / "telemetry.db").exists()
        # every agent got the recorder injected; scheduler too
        assert all(a.telemetry is rt.telemetry for a in rt.agents)
        assert rt.scheduler._telemetry is rt.telemetry
    finally:
        await rt.close()


async def test_agent_run_via_runtime_lands_run_events_in_telemetry_log(tmp_path):
    from novelizer.settings import EffectiveSettings as Settings
    from novelizer.runtime import Runtime
    from novelizer.telemetry.events import TelemetryEventType

    class _R:
        async def ainvoke(self, inputs):
            return {"structured_response": None}

    settings = Settings(db_path=str(tmp_path / "world.db"))
    rt = Runtime(settings, runners={n: _R() for n in [
        "author", "world_architect", "character_keeper", "editor",
        "continuity_checker", "continuity_checker_mining", "retconner", "structure_analyst", "summarizer"]})
    await rt.start()
    try:
        await rt.author.run_once()
        tel = await rt.telemetry_store.events_since(0)
        types = [e.event_type for e in tel]
        assert TelemetryEventType.AGENT_RUN_STARTED in types
        assert TelemetryEventType.AGENT_RUN_FINISHED in types
    finally:
        await rt.close()


async def test_runtime_backfills_and_ticks_indexer(tmp_path):
    """Injected EmbeddingStore seam: start() backfills any pre-existing events,
    index_catch_up() incrementally indexes new ones, and is safe to call twice."""
    from novelizer.store.embeddings import EmbeddingStore
    from novelizer.store.models import Chapter
    from tests.conftest import FakeEmbeddingFunction

    store = EmbeddingStore(str(tmp_path / "emb"), embedding_function=FakeEmbeddingFunction())
    settings = Settings(db_path=str(tmp_path / "world.db"))
    rt = Runtime(settings, runners=_all_fake_runners(), embedding_store=store)
    await rt.start()
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "ch1",
                               Chapter(id="ch1", title="One", prose="The bell rang."))
        await rt.projector.catch_up()
        await rt.index_catch_up()
        hits = await store.search("bell", kinds=["chapter"])
        assert [h.id for h in hits] == ["ch1"]
        await rt.index_catch_up()  # idempotent, never raises
    finally:
        await rt.close()


async def test_start_wires_kg_projector(settings):
    """Runtime.start() must wire kg_store/kg_projector the same way it wires
    the existing indexer, and kg_catch_up() must run cleanly."""
    rt = Runtime(settings, runners=_all_fake_runners())
    await rt.start()
    try:
        assert rt.kg_store is not None
        assert rt.kg_projector is not None
        await rt.kg_catch_up()  # idempotent, never raises
    finally:
        await rt.close()


async def test_mining_runner_falls_back_to_parent_checker_fake(settings):
    """Post-M5.3-merge fix: an injected runners dict WITHOUT a dedicated
    "continuity_checker_mining" key must reuse the parent "continuity_checker"
    fake for the mining role -- never silently build the real network-bound
    runner (TUI tests hung on live connection attempts whenever the checker
    actually ran)."""
    checker_fake = ScriptedRunner(ContinuityOutput())
    runners = _all_fake_runners()
    runners["continuity_checker"] = checker_fake
    runners.pop("continuity_checker_mining", None)
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert rt.continuity_checker._mining_runner is checker_fake
    finally:
        await rt.close()


async def test_runtime_flags_on_wire_pull_mode_for_author_and_checker(settings):
    settings = settings.model_copy(update={
        "author_tools_enabled": True, "checker_tools_enabled": True,
    })
    rt = Runtime(settings, runners=_all_fake_runners())
    await rt.start()
    try:
        assert rt.author.pull_mode is True
        assert rt.continuity_checker.pull_mode is True
    finally:
        await rt.close()


async def test_runtime_flags_off_leave_pull_mode_false(settings):
    settings = settings.model_copy(update={
        "author_tools_enabled": False, "checker_tools_enabled": False,
    })
    rt = Runtime(settings, runners=_all_fake_runners())
    await rt.start()
    try:
        assert rt.author.pull_mode is False
        assert rt.continuity_checker.pull_mode is False
    finally:
        await rt.close()


async def test_runtime_start_completes_with_flags_on_and_no_fake_author(settings):
    """Flags on, author NOT in the injected runners dict: start() must build
    the real author via the toolkit-wrapped closure without touching the
    network (builders construct lazily; nothing calls ainvoke here)."""
    settings = settings.model_copy(update={
        "author_tools_enabled": True, "checker_tools_enabled": True,
    })
    runners = _all_fake_runners()
    runners.pop("author", None)
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert rt.author is not None
        assert rt.author.pull_mode is True
    finally:
        await rt.close()


async def test_runtime_chat_pull_mode_on_wires_tooled_chat_runner(settings, monkeypatch):
    """CPT-M5: chat_tools_enabled=True must set ChatService.pull_mode True and
    build the real chat runner with the runtime's canon backend/tools."""
    settings = settings.model_copy(update={"chat_tools_enabled": True})
    runners = _all_fake_runners()
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert rt.chat.pull_mode is True

        seen_kwargs: list[dict] = []

        def _spy_build_chat_runner(settings, agent_name, callbacks=None, backend=None, tools=None):
            seen_kwargs.append({"callbacks": callbacks, "backend": backend, "tools": tools})
            return _FakeAgentRunner()

        monkeypatch.setattr("novelizer.runtime.build_chat_runner", _spy_build_chat_runner)

        rt._chat_runner_for("author")

        assert len(seen_kwargs) == 1
        assert seen_kwargs[0]["backend"] is rt._canon_backend
        assert seen_kwargs[0]["tools"] is rt._canon_tools
        assert seen_kwargs[0]["callbacks"] is rt._llm_callbacks
    finally:
        await rt.close()


async def test_runtime_chat_pull_mode_off_uses_bare_chat_runner(settings, monkeypatch):
    """CPT-M5: chat_tools_enabled=False must set ChatService.pull_mode False
    and build the chat runner via the bare legacy call (no backend/tools)."""
    settings = settings.model_copy(update={"chat_tools_enabled": False})
    runners = _all_fake_runners()
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert rt.chat.pull_mode is False

        seen_kwargs: list[dict] = []

        def _spy_build_chat_runner(settings, agent_name, callbacks=None, backend=None, tools=None):
            seen_kwargs.append({"callbacks": callbacks, "backend": backend, "tools": tools})
            return _FakeAgentRunner()

        monkeypatch.setattr("novelizer.runtime.build_chat_runner", _spy_build_chat_runner)

        rt._chat_runner_for("author")

        assert len(seen_kwargs) == 1
        assert seen_kwargs[0]["backend"] is None
        assert seen_kwargs[0]["tools"] is None
    finally:
        await rt.close()


async def test_chat_runner_for_follows_pull_mode_not_live_settings_flag(settings, monkeypatch):
    """CPT-M5 final-review fix: a mid-session chat_tools_enabled flip on the
    live settings must NOT split-brain the tooling away from the pull_mode
    pinned at start() -- _chat_runner_for gates on rt.chat.pull_mode, not
    rt.settings.chat_tools_enabled."""
    settings = settings.model_copy(update={"chat_tools_enabled": True})
    runners = _all_fake_runners()
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert rt.chat.pull_mode is True

        # Simulate a live flag flip without a restart (pull_mode stays pinned).
        rt.settings = rt.settings.model_copy(update={"chat_tools_enabled": False})

        seen_kwargs: list[dict] = []

        def _spy_build_chat_runner(settings, agent_name, callbacks=None, backend=None, tools=None):
            seen_kwargs.append({"backend": backend, "tools": tools})
            return _FakeAgentRunner()

        monkeypatch.setattr("novelizer.runtime.build_chat_runner", _spy_build_chat_runner)

        rt._chat_runner_for("editor")

        assert len(seen_kwargs) == 1
        assert seen_kwargs[0]["backend"] is rt._canon_backend
        assert seen_kwargs[0]["tools"] is rt._canon_tools
    finally:
        await rt.close()


async def test_chat_runner_for_falls_back_to_bare_before_start(settings, monkeypatch):
    """_chat_runner_for can be invoked without start() having run (no
    self._canon_backend/_canon_tools yet) -- must fall back to the bare
    builder rather than raising AttributeError."""
    settings = settings.model_copy(update={"chat_tools_enabled": True})
    rt = Runtime(settings, runners=_all_fake_runners())

    seen_kwargs: list[dict] = []

    def _spy_build_chat_runner(settings, agent_name, callbacks=None, backend=None, tools=None):
        seen_kwargs.append({"backend": backend, "tools": tools})
        return _FakeAgentRunner()

    monkeypatch.setattr("novelizer.runtime.build_chat_runner", _spy_build_chat_runner)

    rt._chat_runner_for("author")

    assert len(seen_kwargs) == 1
    assert seen_kwargs[0]["backend"] is None
    assert seen_kwargs[0]["tools"] is None


_PHASE_B_AGENTS = [
    ("world_architect", "world_architect_tools_enabled", "novelizer.agents.world_architect.build_world_architect_runner"),
    ("character_keeper", "character_keeper_tools_enabled", "novelizer.agents.character_keeper.build_character_keeper_runner"),
    ("editor", "editor_tools_enabled", "novelizer.agents.editor.build_editor_runner"),
    ("retconner", "retconner_tools_enabled", "novelizer.agents.retconner.build_retconner_runner"),
    ("structure_analyst", "structure_analyst_tools_enabled", "novelizer.agents.structure_analyst.build_structure_analyst_runner"),
    ("plotter", "plotter_tools_enabled", "novelizer.agents.plotter.build_plotter_runner"),
]


@pytest.mark.parametrize("agent_name,flag_name,builder_path", _PHASE_B_AGENTS)
async def test_phase_b_flags_on_wire_backend_and_tools(settings, monkeypatch, agent_name, flag_name, builder_path):
    """CPT-M6: <agent>_tools_enabled=True must build the real runner via the
    toolkit-wrapped closure with the runtime's canon backend/tools."""
    settings = settings.model_copy(update={flag_name: True})
    runners = _all_fake_runners()
    runners.pop(agent_name, None)
    rt = Runtime(settings, runners=runners)

    seen_kwargs: list[dict] = []

    def _spy_builder(settings, callbacks=None, backend=None, tools=None, subagents=None):
        seen_kwargs.append({"callbacks": callbacks, "backend": backend, "tools": tools})
        return _FakeAgentRunner()

    monkeypatch.setattr(builder_path, _spy_builder)

    await rt.start()
    try:
        assert len(seen_kwargs) == 1
        assert seen_kwargs[0]["backend"] is rt._canon_backend
        assert seen_kwargs[0]["tools"] is rt._canon_tools
        assert seen_kwargs[0]["callbacks"] is rt._llm_callbacks
    finally:
        await rt.close()


@pytest.mark.parametrize("agent_name,flag_name,builder_path", _PHASE_B_AGENTS)
async def test_phase_b_flags_off_uses_bare_builder(settings, monkeypatch, agent_name, flag_name, builder_path):
    """CPT-M6: <agent>_tools_enabled=False must build via the bare builder --
    no backend/tools passed."""
    settings = settings.model_copy(update={flag_name: False})
    runners = _all_fake_runners()
    runners.pop(agent_name, None)
    rt = Runtime(settings, runners=runners)

    seen_kwargs: list[dict] = []

    def _spy_builder(settings, callbacks=None, backend=None, tools=None, subagents=None):
        seen_kwargs.append({"callbacks": callbacks, "backend": backend, "tools": tools})
        return _FakeAgentRunner()

    monkeypatch.setattr(builder_path, _spy_builder)

    await rt.start()
    try:
        assert len(seen_kwargs) == 1
        assert seen_kwargs[0]["backend"] is None
        assert seen_kwargs[0]["tools"] is None
    finally:
        await rt.close()


async def test_phase_a_toolkit_backend_is_composite_with_outline_route(settings):
    from deepagents.backends import CompositeBackend, StateBackend
    from novelizer.canon_fs.outline import OutlineBackend
    from novelizer.canon_fs.skills_route import ReadOnlyBackend

    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    try:
        await rt.start()
        assert isinstance(rt._canon_backend, CompositeBackend)
        assert "/outline/" in rt._canon_backend.routes
        assert isinstance(rt._canon_backend.routes["/outline/"], OutlineBackend)
        assert "/skills/" in rt._canon_backend.routes
        assert isinstance(rt._canon_backend.routes["/skills/"], ReadOnlyBackend)
        assert "/workspace/" in rt._canon_backend.routes
        assert isinstance(rt._canon_backend.routes["/workspace/"], StateBackend)
    finally:
        await rt.close()


def test_tooled_passes_no_subagents_kwarg_when_subagent_disabled(runtime):
    calls = []

    def builder(settings, callbacks=None, backend=None, tools=None, subagents=None):
        calls.append(subagents)
        return "runner"

    wrapped = runtime._tooled(builder, enabled=True, subagent_enabled=False, subagent_agent_name="character_keeper")
    wrapped(runtime.settings)
    assert calls == [None]


def test_tooled_passes_researcher_subagent_when_enabled(runtime):
    calls = []

    def builder(settings, callbacks=None, backend=None, tools=None, subagents=None):
        calls.append(subagents)
        return "runner"

    wrapped = runtime._tooled(builder, enabled=True, subagent_enabled=True, subagent_agent_name="character_keeper")
    wrapped(runtime.settings)
    assert len(calls) == 1
    assert calls[0][0]["name"] == "researcher"


def test_tooled_bare_builder_unchanged_when_tools_disabled(runtime):
    def builder(settings, callbacks=None):
        return "bare-runner"

    wrapped = runtime._tooled(builder, enabled=False, subagent_enabled=True, subagent_agent_name="character_keeper")
    assert wrapped is builder


class _FakeEventsForRun:
    """Minimal stand-in for EventStore.events_for_run — the probe only ever
    reads event_type off what it gets back."""

    def __init__(self, by_run: dict) -> None:
        self._by_run = by_run

    async def events_for_run(self, run_id: str):
        return self._by_run.get(run_id, [])


class _Ev:
    def __init__(self, event_type: str) -> None:
        self.event_type = event_type


async def test_progress_probe_counts_real_canon_commits():
    from novelizer.runtime import _make_progress_probe
    probe = _make_progress_probe(_FakeEventsForRun({"r1": [_Ev(EventType.CHAPTER_CREATED)]}))
    assert await probe("r1") is True


async def test_progress_probe_ignores_a_run_that_only_chattered():
    from novelizer.runtime import _make_progress_probe
    probe = _make_progress_probe(_FakeEventsForRun({"r1": [_Ev(EventType.AGENT_REMARKED)]}))
    assert await probe("r1") is False


async def test_progress_probe_ignores_a_run_that_only_closed_out_signals():
    """An agent that read the director's signals, declined to act, and marked
    them consumed has done bookkeeping, not work. Counting it as progress
    keeps a converged agent at full cadence for as long as a director keeps
    trickling signals in — see world_architect's deliberate skip of
    note_pass() when signals are pending but the draft is no_action."""
    from novelizer.runtime import _make_progress_probe
    probe = _make_progress_probe(_FakeEventsForRun({
        "r1": [_Ev(EventType.DIRECTOR_SIGNAL_CONSUMED), _Ev(EventType.AGENT_REMARKED)],
    }))
    assert await probe("r1") is False


async def test_progress_probe_counts_work_done_alongside_bookkeeping():
    from novelizer.runtime import _make_progress_probe
    probe = _make_progress_probe(_FakeEventsForRun({
        "r1": [_Ev(EventType.DIRECTOR_SIGNAL_CONSUMED), _Ev(EventType.WORLD_ENTRY_CREATED)],
    }))
    assert await probe("r1") is True


async def test_progress_probe_reports_a_silent_run_as_no_progress():
    from novelizer.runtime import _make_progress_probe
    probe = _make_progress_probe(_FakeEventsForRun({}))
    assert await probe("nothing-committed") is False


# --- Phase 4: the strict background catch-up gate ---------------------------
#
# Background work (embedding indexing + KG extraction) is HIGHER priority than
# agent runs, and blocking is STRICT: while EITHER indexer lags, no agent is
# dispatched at all. Novelizer wires this into agent_kit.Scheduler's generic
# gate_provider seam via a _make_gate_provider(indexer, kg_projector) factory
# that mirrors _make_override_provider: an async predicate, OPEN (True) iff both
# lags are zero.
#
# These start RED: _make_gate_provider does not exist (ImportError), and start()
# does not pass gate_provider= to the Scheduler yet, so rt.scheduler has no
# _gate_provider wired.


class _FakeLagger:
    """The only surface _make_gate_provider touches on either indexer: an async
    lag() returning an int. Real CanonIndexer/KGProjector both expose exactly
    this."""

    def __init__(self, lag: int) -> None:
        self._lag = lag

    async def lag(self) -> int:
        return self._lag


async def test_gate_provider_is_open_only_when_both_indexers_are_caught_up():
    """The pinned predicate: OPEN iff indexer.lag() == 0 AND
    kg_projector.lag() == 0. Any lag on either side closes the gate -- the two
    lags count different event sets and are not interchangeable, so both must be
    drained before agents may act."""
    from novelizer.runtime import _make_gate_provider
    assert await _make_gate_provider(_FakeLagger(0), _FakeLagger(0))() is True
    assert await _make_gate_provider(_FakeLagger(1), _FakeLagger(0))() is False
    assert await _make_gate_provider(_FakeLagger(0), _FakeLagger(3))() is False
    assert await _make_gate_provider(_FakeLagger(4), _FakeLagger(2))() is False


async def test_start_wires_the_background_gate_into_the_scheduler(settings):
    """start() must hand the Scheduler a gate_provider built over the runtime's
    OWN indexer and kg_projector. On a fresh, fully-drained room both lags are
    zero, so the wired gate reads open."""
    rt = Runtime(settings, runners=_all_fake_runners())
    await rt.start()
    try:
        assert rt.scheduler._gate_provider is not None
        assert await rt.scheduler._gate_provider() is True
    finally:
        await rt.close()


async def test_pending_index_lag_holds_every_agent_then_releases_on_catch_up(settings, tmp_path):
    """End to end at the runtime level. A THREAD_PLANTED event is indexable by
    the CanonIndexer but NOT by the KGProjector, so it raises indexer lag alone
    -- no KG LLM call is ever provoked, keeping the test hermetic. With that lag
    pending, the real wired gate is closed and the scheduler dispatches NOTHING,
    even though the Author is ready and scores 1.0. Once index_catch_up drains
    the cursor (lag -> 0), the gate reopens and agents dispatch again."""
    from novelizer.canon.events import ThreadPlanted
    from novelizer.store.embeddings import EmbeddingStore
    from tests.conftest import FakeEmbeddingFunction

    # Inject the deterministic embedding function (the store's documented CI
    # seam): draining the backlog now requires really embedding the record --
    # an unindexed event no longer advances the cursor -- and no live embed
    # endpoint is available here. The gate and both indexers stay real.
    rt = Runtime(settings, runners=_all_fake_runners(),
                 embedding_store=EmbeddingStore(str(tmp_path / "emb"),
                                                embedding_function=FakeEmbeddingFunction()))
    await rt.start()
    try:
        # Sanity: a fresh room is caught up, so the gate starts open.
        assert await rt.scheduler._gate_provider() is True

        # Create indexer lag without touching the KG projector or its LLM.
        await rt.events.append(EventType.THREAD_PLANTED, "t1",
                               ThreadPlanted(id="t1", name="The Ledger"))
        # And project it: the indexer hydrates the CURRENT record from the read
        # store, so an unprojected event is not yet indexable and its cursor
        # deliberately refuses to advance (ProjectionNotReady).
        await rt.projector.catch_up()
        assert await rt.indexer.lag() > 0
        assert await rt.kg_projector.lag() == 0
        assert await rt.scheduler._gate_provider() is False, "pending index lag must close the gate"

        # Strict gate: no agent runs while the backlog is pending.
        assert await rt.scheduler.tick() == []
        assert len(rt.scheduler._in_flight) == 0

        # Drain the backlog; the gate reopens and the room dispatches again.
        await rt.index_catch_up()
        assert await rt.indexer.lag() == 0
        assert await rt.scheduler._gate_provider() is True
        dispatched = await rt.scheduler.tick()
        assert dispatched, "agents must dispatch again once the indexers have caught up"
    finally:
        await rt.scheduler.drain_in_flight()
        await rt.close()


# --- Phase 6: background-progress readout (legibility of the strict gate) ----
#
# The strict background gate (Phase 4) freezes EVERY agent while EITHER indexer
# lags. The user accepted that a down embed endpoint can freeze the whole room
# ON THE CONDITION that the operator can SEE the backlog draining -- otherwise a
# freeze is indistinguishable from a hang. So the runtime must expose a single
# queryable progress signal spanning BOTH the CanonIndexer AND the KGProjector.
# Today _brain_loop reads only indexer.lag(), missing KG entirely.
#
# Pinned shape: Runtime.background_progress() (async) returns a BackgroundProgress
# with named `index_lag` / `kg_lag` ints, a `total` (their sum) and a
# `caught_up` flag (total == 0). Named fields over a bare tuple because the
# status bar reads them by name and a swapped (index, kg) pair would be a silent
# bug. It must:
#   - be zero / caught_up when both indexers are drained,
#   - reflect pending work on EITHER side,
#   - NEVER raise -- a lag probe that hits "database is locked" must not crash
#     the status loop; that side reads as unknown == 0 (mirrors the never-raise
#     contract of index_catch_up / kg_catch_up),
#   - treat a None indexer / projector as 0 (the runtime can run without them,
#     exactly as _make_gate_provider already does).
#
# These start RED: Runtime has no background_progress() method and there is no
# BackgroundProgress type (ImportError / AttributeError).


class _RaisingLagger:
    """A lag() that raises the way a real indexer does under DB contention
    ("database is locked"). background_progress() must swallow this and report
    that side as unknown == 0 rather than letting it escape into the status
    loop -- the same never-raise contract index_catch_up / kg_catch_up hold."""

    async def lag(self) -> int:
        raise RuntimeError("database is locked")


async def test_background_progress_type_shape_is_index_kg_total_caught_up():
    """Pin the exact returned shape as a named type, not a bare (index, kg)
    tuple: the status bar reads these by name and a swapped pair would be a
    silent bug. total is the sum; caught_up is total == 0."""
    from novelizer.runtime import BackgroundProgress
    p = BackgroundProgress(index_lag=4, kg_lag=2)
    assert p.index_lag == 4
    assert p.kg_lag == 2
    assert p.total == 6
    assert p.caught_up is False
    assert BackgroundProgress(index_lag=0, kg_lag=0).caught_up is True


async def test_background_progress_is_zero_when_both_indexers_are_caught_up(settings):
    rt = Runtime(settings)
    rt.indexer = _FakeLagger(0)
    rt.kg_projector = _FakeLagger(0)
    p = await rt.background_progress()
    assert p.index_lag == 0
    assert p.kg_lag == 0
    assert p.total == 0
    assert p.caught_up is True


async def test_background_progress_reflects_pending_work_on_either_indexer(settings):
    """Either side lagging must surface: the two lags count different event
    sets (24 canon event types vs the projector's 6) and are not
    interchangeable, so a readout that watched only one would miss a freeze
    caused by the other."""
    rt = Runtime(settings)

    rt.indexer, rt.kg_projector = _FakeLagger(5), _FakeLagger(0)
    p = await rt.background_progress()
    assert (p.index_lag, p.kg_lag, p.total) == (5, 0, 5)
    assert p.caught_up is False

    rt.indexer, rt.kg_projector = _FakeLagger(0), _FakeLagger(3)
    p = await rt.background_progress()
    assert (p.index_lag, p.kg_lag, p.total) == (0, 3, 3)
    assert p.caught_up is False

    rt.indexer, rt.kg_projector = _FakeLagger(4), _FakeLagger(2)
    p = await rt.background_progress()
    assert (p.index_lag, p.kg_lag, p.total) == (4, 2, 6)
    assert p.caught_up is False


async def test_background_progress_never_raises_when_a_lag_probe_fails(settings):
    """A "database is locked" on one probe must not crash the status loop. The
    failing side reads as unknown == 0; the healthy side is still reported, so
    the operator keeps seeing real progress from whichever indexer is up."""
    rt = Runtime(settings)
    rt.indexer = _RaisingLagger()
    rt.kg_projector = _FakeLagger(7)
    p = await rt.background_progress()  # must not raise
    assert p.index_lag == 0  # failing side treated as unknown/zero
    assert p.kg_lag == 7
    assert p.total == 7
    assert p.caught_up is False


async def test_background_progress_handles_missing_indexers(settings):
    """The runtime can run without an indexer / projector (both default to
    None). A None side contributes 0, mirroring _make_gate_provider's None
    handling -- nothing to wait on, so nothing to report."""
    rt = Runtime(settings)  # __init__ leaves indexer and kg_projector None
    p = await rt.background_progress()
    assert p.index_lag == 0
    assert p.kg_lag == 0
    assert p.caught_up is True

    rt.kg_projector = _FakeLagger(9)
    p = await rt.background_progress()
    assert p.index_lag == 0  # indexer still None
    assert p.kg_lag == 9
    assert p.total == 9


# --- semantic index size: the one number that makes a dead index visible ----
#
# A zero-document index is invisible from every existing readout: lag reads 0
# (the cursor believes it consumed the backlog), catch_up reports success, and
# search_canon answers every question with a confident miss. In production that
# state ran for 690 consecutive search_canon calls before anyone noticed. The
# document count is the signal, so the runtime must be able to report it.
#
# Unknown is NOT zero here, unlike _safe_lag: zero IS the alarm value, so a
# missing store or a failed probe must read as None rather than raise the alarm.


class _FakeCountingStore:
    def __init__(self, count: int) -> None:
        self._count = count

    async def document_count(self) -> int:
        return self._count


class _RaisingCountingStore:
    async def document_count(self) -> int:
        raise RuntimeError("database is locked")


async def test_index_document_count_reports_the_semantic_index_size(settings):
    rt = Runtime(settings)
    rt.embeddings = _FakeCountingStore(42)
    assert await rt.index_document_count() == 42


async def test_index_document_count_reports_zero_for_a_dead_index(settings):
    """Zero must come through as zero, not be smoothed into None: it is the
    whole point of the readout."""
    rt = Runtime(settings)
    rt.embeddings = _FakeCountingStore(0)
    assert await rt.index_document_count() == 0


async def test_index_document_count_is_none_when_unknown(settings):
    """No store, or a probe that raises, is UNKNOWN -- distinct from a store
    that genuinely holds nothing, and never a crash in the status loop."""
    rt = Runtime(settings)  # __init__ leaves embeddings None
    assert await rt.index_document_count() is None

    rt.embeddings = _RaisingCountingStore()
    assert await rt.index_document_count() is None


# "The index is empty" is only an alarm when there is canon that SHOULD have
# been indexed. The corroborating signal is the count of events the indexer
# itself consumes (INDEXED_EVENT_TYPES from sequence 0) -- exactly what would
# have populated the index, rather than a proxy like chapters.


async def test_indexable_event_count_is_zero_for_a_fresh_story(settings):
    rt = Runtime(settings)
    await rt.events.init()
    try:
        assert await rt.indexable_event_count() == 0
    finally:
        await rt.events.close()


async def test_indexable_event_count_counts_canon_the_indexer_consumes(settings):
    """A THREAD_PLANTED is indexable but produces no chapter -- which is why
    chapters would be the wrong signal for 'there is canon to index'."""
    from novelizer.canon.events import ThreadPlanted

    rt = Runtime(settings)
    await rt.events.init()
    try:
        await rt.events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="T"))
        assert await rt.indexable_event_count() == 1
    finally:
        await rt.events.close()


async def test_indexable_event_count_ignores_events_the_indexer_never_embeds(settings):
    from novelizer.canon.events import AnnotationStructureScored

    rt = Runtime(settings)
    await rt.events.init()
    try:
        await rt.events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                               AnnotationStructureScored(chapter_id="c1", tension=0.5,
                                                         pacing_label=""))
        assert await rt.indexable_event_count() == 0
    finally:
        await rt.events.close()


async def test_indexable_event_count_is_none_when_unknown(settings):
    """A locked/unreadable log is UNKNOWN, never 0: reading it as 0 would
    suppress the empty-index alarm in exactly the state it exists for."""
    rt = Runtime(settings)  # never connected -- the count cannot be read
    assert await rt.indexable_event_count() is None


async def test_background_progress_spans_both_real_indexers_end_to_end(settings, tmp_path):
    """End to end against the REAL wired indexers, mirroring
    test_pending_index_lag_holds_every_agent_then_releases_on_catch_up. A
    THREAD_PLANTED event is indexable by the CanonIndexer but NOT by the
    KGProjector, so it raises canon index lag alone -- no KG LLM call is ever
    provoked, keeping the test hermetic. background_progress() must see that
    canon lag (and only it), then read zero again once the drain catches up."""
    from novelizer.canon.events import ThreadPlanted
    from novelizer.store.embeddings import EmbeddingStore
    from tests.conftest import FakeEmbeddingFunction

    # Inject the deterministic embedding function (the store's documented CI
    # seam): draining the backlog now requires really embedding the record, and
    # no live embed endpoint is available here. Both indexers stay real.
    rt = Runtime(settings, runners=_all_fake_runners(),
                 embedding_store=EmbeddingStore(str(tmp_path / "emb"),
                                                embedding_function=FakeEmbeddingFunction()))
    await rt.start()
    try:
        # Fresh, fully-drained room: nothing pending on either side.
        p = await rt.background_progress()
        assert p.total == 0
        assert p.caught_up is True

        # Raise canon index lag without touching the KG projector or its LLM.
        await rt.events.append(EventType.THREAD_PLANTED, "t1",
                               ThreadPlanted(id="t1", name="The Ledger"))
        # Project it too: the indexer embeds the CURRENT record from the read
        # store, so an unprojected event is not indexable yet and its cursor
        # deliberately refuses to advance (ProjectionNotReady). Without this the
        # drain below could only "catch up" by losing the event.
        await rt.projector.catch_up()
        p = await rt.background_progress()
        assert p.index_lag > 0
        assert p.kg_lag == 0, "THREAD_PLANTED is not a KG-indexed event; kg lag stays 0"
        assert p.total == p.index_lag
        assert p.caught_up is False

        # Drain the embedding backlog; the readout returns to caught-up.
        await rt.index_catch_up()
        p = await rt.background_progress()
        assert p.total == 0
        assert p.caught_up is True
    finally:
        await rt.close()


# --- embedding-endpoint probe at boot ----------------------------------------
#
# The root cause behind the 690-miss incident was configuration, and it was
# undetectable from inside the running system: embed_model defaults to
# "nomic-embed-text" while resolved_embed_base_url falls back to llm_base_url,
# so pointing the room at a chat-only proxy is a SUPPORTED-looking setting that
# silently guarantees an empty index. One embed round-trip at boot turns that
# into a single legible line naming the endpoint and model to go and fix.
#
# Policy pinned below: the probe NEVER refuses to boot, in either configuration.
# It is loud (ERROR) and it is precise about the remedy, which differs by case.


from tests.conftest import FakeEmbeddingFunction  # noqa: E402  (section-local helper base)


class _ProbeRecordingEmbeddingFunction(FakeEmbeddingFunction):
    """Records every embed input so the ORDER of probe-vs-backfill is testable."""

    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    def __call__(self, input):
        self.inputs.append(list(input))
        return super().__call__(input)


class _DeadEmbeddingFunction(FakeEmbeddingFunction):
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or ConnectionError("connection refused")

    def __call__(self, input):
        raise self._exc


def _probe_store(tmp_path, ef, name="emb"):
    from novelizer.store.embeddings import EmbeddingStore
    return EmbeddingStore(str(tmp_path / name), embed_model="nomic-embed-text",
                          base_url="http://embed.invalid/v1", embedding_function=ef)


async def test_embed_probe_message_names_the_resolved_endpoint_and_model():
    """The line an operator reads at 2am. It must name the RESOLVED endpoint --
    not the raw embed_base_url, which is empty in the fallback case and would
    tell them nothing -- and the model, since a 404 is usually a model typo."""
    from novelizer.runtime import embed_probe_message
    from novelizer.store.embeddings import EmbedProbe, EmbedProbeFailure

    settings = Settings(db_path="/tmp/x.db", llm_base_url="http://chat.invalid/v1",
                        embed_base_url="http://embed.invalid/v1",
                        embed_model="nomic-embed-text")
    probe = EmbedProbe(endpoint="http://embed.invalid/v1", model="nomic-embed-text",
                       ok=False, failure=EmbedProbeFailure.no_such_model,
                       error="NotFoundError: model not found")
    message = embed_probe_message(probe, settings)

    assert message.startswith(
        "embedding endpoint http://embed.invalid/v1 has no model 'nomic-embed-text'; "
        "semantic search will be unavailable"
    )
    assert "NotFoundError" in message
    assert "\n" not in message, "one line: this goes to a log and a status readout"


async def test_embed_probe_message_distinguishes_shared_from_dedicated_endpoint():
    """The whole difference between a useful check and one that blames the user
    for a setting they never made. With embed_base_url unset the endpoint is the
    CHAT endpoint by design (the supported all-local setup), so the remedy is
    "set embed_base_url" -- not "check the one you configured"."""
    from novelizer.runtime import embed_probe_message
    from novelizer.store.embeddings import EmbedProbe, EmbedProbeFailure

    probe = EmbedProbe(endpoint="http://localhost:8080/v1", model="nomic-embed-text",
                       ok=False, failure=EmbedProbeFailure.no_such_model, error="x: y")

    shared = embed_probe_message(
        probe, Settings(db_path="/tmp/x.db", llm_base_url="http://localhost:8080/v1",
                        embed_base_url=""))
    assert "shared with the chat endpoint" in shared
    assert "set embed_base_url" in shared

    dedicated = embed_probe_message(
        probe, Settings(db_path="/tmp/x.db", llm_base_url="http://chat.invalid/v1",
                        embed_base_url="http://localhost:8080/v1"))
    assert "shared with the chat endpoint" not in dedicated
    assert "check embed_base_url and embed_model" in dedicated


async def test_embed_probe_covers_every_failure_mode_with_its_own_wording():
    """No failure mode may fall through to a generic sentence: each one is a
    different thing to go and do."""
    from novelizer.runtime import embed_probe_message
    from novelizer.store.embeddings import EmbedProbe, EmbedProbeFailure

    settings = Settings(db_path="/tmp/x.db", embed_base_url="http://e.invalid/v1")
    expected = {
        EmbedProbeFailure.unreachable: "is unreachable",
        EmbedProbeFailure.timeout: "did not respond in time",
        EmbedProbeFailure.unauthorized: "rejected our credentials",
        EmbedProbeFailure.no_such_model: "has no model",
        EmbedProbeFailure.http_error: "could not be reached",
        EmbedProbeFailure.no_vectors: "returned no vector",
    }
    assert set(expected) == set(EmbedProbeFailure), "a new failure mode needs wording"
    for failure, phrase in expected.items():
        probe = EmbedProbe(endpoint="http://e.invalid/v1", model="m", ok=False,
                           failure=failure, error="E: detail")
        message = embed_probe_message(probe, settings)
        assert phrase in message, f"{failure} has no wording of its own: {message}"
        assert "semantic search will be unavailable" in message


async def test_start_probes_the_endpoint_before_backfilling_the_index(settings, tmp_path):
    """Order matters: the probe exists to explain a backfill that is about to
    fail, so it must run BEFORE index_catch_up, not after it."""
    from novelizer.canon.events import ThreadPlanted

    ef = _ProbeRecordingEmbeddingFunction()
    rt = Runtime(settings, runners=_all_fake_runners(),
                 embedding_store=_probe_store(tmp_path, ef))
    # A pending indexable event, so the backfill has real work to embed.
    await rt.events.init()
    await rt.events.append(EventType.THREAD_PLANTED, "t1",
                           ThreadPlanted(id="t1", name="The Ledger"))
    try:
        await rt.start()
        assert ef.inputs, "start() never embedded anything, so it never probed"
        assert ef.inputs[0] == ["novelizer embedding endpoint probe"], \
            "the first embed call must be the probe, before any backfill"
        assert rt.embed_probe is not None and rt.embed_probe.ok is True
    finally:
        await rt.close()


async def test_start_never_refuses_to_boot_when_the_shared_endpoint_is_dead(settings, tmp_path, caplog):
    """The all-local exemption, and the reason it exists: embed_base_url unset
    means the embedding endpoint IS the chat endpoint, which on a cold local
    server may simply not be up yet. Refusing to boot over a boot-order race
    would brick a legitimate config -- and since a failed probe now degrades
    honestly (search_canon answers "unavailable, use ls/glob/grep"), booting
    degraded is strictly better than not booting at all."""
    shared = settings.model_copy(update={"llm_base_url": "http://localhost:8080/v1",
                                         "embed_base_url": ""})
    rt = Runtime(shared, runners=_all_fake_runners(),
                 embedding_store=_probe_store(tmp_path, _DeadEmbeddingFunction()))
    caplog.clear()
    try:
        with caplog.at_level("ERROR"):
            await rt.start()  # must NOT raise
        assert rt.embed_probe is not None and rt.embed_probe.ok is False
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("semantic search will be unavailable" in m for m in errors), \
            "a dead index must be loud even when it is not fatal"
        assert any("set embed_base_url" in m for m in errors)
    finally:
        await rt.close()


async def test_start_never_refuses_to_boot_when_a_dedicated_endpoint_is_dead(settings, tmp_path, caplog):
    """The same policy with the sharper message: the operator DID configure an
    embedding endpoint, so name it as the thing to check. Still not fatal -- the
    room's job is to keep the novel running, and `novelizer doctor` is where a
    hard, exit-code failure belongs."""
    dedicated = settings.model_copy(update={"embed_base_url": "http://embed.invalid/v1"})
    rt = Runtime(dedicated, runners=_all_fake_runners(),
                 embedding_store=_probe_store(tmp_path, _DeadEmbeddingFunction()))
    caplog.clear()
    try:
        with caplog.at_level("ERROR"):
            await rt.start()  # must NOT raise
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("check embed_base_url and embed_model" in m for m in errors)
        assert not any("set embed_base_url if" in m for m in errors)
    finally:
        await rt.close()


async def test_start_says_nothing_when_the_endpoint_is_healthy(settings, tmp_path, caplog):
    """A healthy boot must stay quiet: an ERROR line every start would train the
    operator to ignore the one that matters."""
    rt = Runtime(settings, runners=_all_fake_runners(),
                 embedding_store=_probe_store(tmp_path, FakeEmbeddingFunction()))
    caplog.clear()
    try:
        with caplog.at_level("ERROR"):
            await rt.start()
        assert not [r for r in caplog.records
                    if r.levelname == "ERROR" and "semantic search" in r.getMessage()]
        assert rt.embed_probe.ok is True
    finally:
        await rt.close()
