import os
import tempfile
import pytest
from novelizer.canon.events import EventType
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments, StructureAnalystOutput,
)
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {k: _R(v) for k, v in {
        "world_architect": WorldEntriesDraft(), "author": ChapterDraft(title="X", prose="y"),
        "character_keeper": KeeperOutput(), "editor": EditorVerdict(), "continuity_checker": ContinuityOutput(),
        "retconner": RetconAmendments(),
        "structure_analyst": StructureAnalystOutput(),
    }.items()}


@pytest.mark.asyncio
async def test_command_input_seeds_via_dispatch():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await app._run_command("seed a storm is coming")
            await pilot.pause(0.3)
            log = await rt.events.events_since(0)
            created = [
                e for e in log
                if e.event_type == EventType.DIRECTOR_SIGNAL_CREATED
                and "storm" in e.payload.get("body", "")
            ]
            assert created, "seed command should append a director_signal.created event"
            assert len(created) == 1, "seed command should append exactly one event, not double-append"
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_room_toggle_hides_right_column():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            app.action_toggle_room()
            await pilot.pause()
            assert app.query_one("#body").has_class("room")
            app.action_toggle_room()
            await pilot.pause()
            assert not app.query_one("#body").has_class("room")
    finally:
        await rt.close(); os.unlink(path)
