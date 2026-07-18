import os
import tempfile
import pytest
from novelizer.config import Settings
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
    }
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert {a.name for a in rt.agents} == {
            "world_architect", "author", "character_keeper", "editor", "continuity_checker",
            "retconner", "structure_analyst",
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
            await rt.projector.catch_up()
            if ran == "continuity_checker" and await rt.read.list_retcon_requests(status="open"):
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
            await rt.projector.catch_up()
            if ran == "retconner" and await rt.read.list_retcon_requests(status="resolved"):
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
            "continuity_checker", "retconner", "structure_analyst",
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
            "continuity_checker", "retconner", "structure_analyst",
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
