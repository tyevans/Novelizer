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
    }
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert {a.name for a in rt.agents} == {
            "world_architect", "author", "character_keeper", "editor", "continuity_checker", "retconner"
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
