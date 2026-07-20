import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.canon.events import EventType
from novelizer.agents.author import ChapterDraft
from novelizer.store.models import DirectorSignal, SignalKind
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, CharacterUpdate,
    EditorVerdict, ContinuityOutput, RetconAmendments, RetconDraft,
)
from novelizer.agents.base import ChapterDraft
from novelizer.canon.committer import GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.proposal_service import ProposalService
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState


class FakeRunner:
    def __init__(self, draft): self._draft = draft
    async def ainvoke(self, inputs):
        return {"structured_response": self._draft}


@pytest.fixture
def settings():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    yield Settings(db_path=path)
    os.unlink(path)


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
    }
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert {a.name for a in rt.agents} == {
            "world_architect", "author", "character_keeper", "editor", "continuity_checker",
            "retconner", "structure_analyst", "muse", "plotter",
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
    """Returns one RetconDraft referencing a known world-entry id on its first call,
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
    """A fake monotonic clock that jumps well past every agent interval on each
    call, so interval-gating never blocks agent eligibility across ticks."""

    def __init__(self, step: float = 10_000.0) -> None:
        self._t = 0.0
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
    retcon = ContinuityOutput(retcon_requests=[
        RetconDraft(
            description="two suns vs one sun",
            conflicting_entry_ids=[known_world_entry_id],
            proposed_resolution="there is only one sun",
        )
    ])
    runners = {
        "world_architect": ScriptedRunner(WorldEntriesDraft(entries=[])),
        "author": ScriptedRunner(ChapterDraft(title="", prose="")),
        "character_keeper": ScriptedRunner(KeeperOutput()),
        "editor": ScriptedRunner(EditorVerdict(verdict="approve", notes="")),
        "continuity_checker": ScriptedContinuityRunner(retcon, ContinuityOutput()),
        "retconner": ScriptedRunner(RetconAmendments(amended_entries=[
            WorldEntryDraft(title="Suns", body="One sun.", supersedes_id=known_world_entry_id)
        ])),
        "structure_analyst": _FakeAgentRunner(),
    }
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

        # Give the scheduler an advancing clock so interval-gating never blocks
        # agent eligibility across many ticks.
        rt.scheduler._clock = AdvancingClock()

        # Phase 1: only ContinuityChecker is eligible -- this forces the scheduler
        # to select it (deterministically) to file the retcon.
        for name in ("world_architect", "author", "character_keeper", "editor", "retconner"):
            rt.scheduler.pause_agent(name)
        for _ in range(10):
            ran = await rt.scheduler.tick()
            await rt.scheduler.drain_in_flight()
            await rt.projector.catch_up()
            if "continuity_checker" in ran and await rt.read.list_retcon_requests(status="open"):
                break
        open_retcons = await rt.read.list_retcon_requests(status="open")
        assert len(open_retcons) == 1, "scheduler did not drive ContinuityChecker to file a retcon"
        assert open_retcons[0].conflicting_entry_ids == [known_world_entry_id]

        # Phase 2: pause ContinuityChecker, resume Retconner -- this forces the
        # scheduler to select Retconner (deterministically) to resolve the retcon.
        rt.scheduler.pause_agent("continuity_checker")
        rt.scheduler.resume_agent("retconner")
        for _ in range(10):
            ran = await rt.scheduler.tick()
            await rt.scheduler.drain_in_flight()
            await rt.projector.catch_up()
            if "retconner" in ran and await rt.read.list_retcon_requests(status="resolved"):
                break

        # Real, non-vacuous assertions: the retcon actually resolved, and the world
        # entry it targeted was actually superseded (old id gone, new body active).
        resolved = await rt.read.list_retcon_requests(status="resolved")
        assert len(resolved) >= 1
        assert await rt.read.list_retcon_requests(status="open") == []
        active_ids = {e.id for e in await rt.read.list_world_entries()}
        assert known_world_entry_id not in active_ids
        active_bodies = {e.body for e in await rt.read.list_world_entries()}
        assert "One sun." in active_bodies
    finally:
        await rt.close()


class _FakeAgentRunner:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


def _all_fake_runners():
    return {
        name: _FakeAgentRunner()
        for name in (
            "author", "world_architect", "character_keeper", "editor",
            "continuity_checker", "retconner", "structure_analyst", "plotter",
        )
    }


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
            "continuity_checker", "retconner", "structure_analyst", "muse",
            "plotter",
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
        "continuity_checker", "continuity_checker_mining", "retconner", "structure_analyst"]})
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
        "continuity_checker", "continuity_checker_mining", "retconner", "structure_analyst"]})
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
    ("world_architect", "world_architect_tools_enabled", "novelizer.runtime.build_world_architect_runner"),
    ("character_keeper", "character_keeper_tools_enabled", "novelizer.runtime.build_character_keeper_runner"),
    ("editor", "editor_tools_enabled", "novelizer.runtime.build_editor_runner"),
    ("retconner", "retconner_tools_enabled", "novelizer.runtime.build_retconner_runner"),
    ("structure_analyst", "structure_analyst_tools_enabled", "novelizer.runtime.build_structure_analyst_runner"),
    ("plotter", "plotter_tools_enabled", "novelizer.runtime.build_plotter_runner"),
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

    def _spy_builder(settings, callbacks=None, backend=None, tools=None):
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

    def _spy_builder(settings, callbacks=None, backend=None, tools=None):
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
    from deepagents.backends import CompositeBackend
    from novelizer.canon_fs.outline import OutlineBackend

    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    try:
        await rt.start()
        assert isinstance(rt._canon_backend, CompositeBackend)
        assert "/outline/" in rt._canon_backend.routes
        assert isinstance(rt._canon_backend.routes["/outline/"], OutlineBackend)
    finally:
        await rt.close()
