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
    EditorVerdict, ContinuityOutput, RetconAmendments,
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
