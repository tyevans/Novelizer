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
async def test_command_provider_discovers_every_registered_command():
    from novelizer.tui.app import APP_COMMANDS, NovelizerApp, NovelizerCommandProvider
    from novelizer.director import commands as director_commands

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            provider = NovelizerCommandProvider(app.screen)
            hits = [hit async for hit in provider.discover()]
            names = {hit.text for hit in hits}
            expected = {c.name for c in director_commands.COMMAND_REGISTRY} | {
                c.name for c in APP_COMMANDS
            }
            assert names == expected
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


@pytest.mark.asyncio
async def test_followup_input_prefills_and_dispatches_on_submit():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            from textual.widgets import Input
            app.open_command_followup("seed")
            await pilot.pause()
            box = app.query_one("#command_followup", Input)
            assert box.value == "seed "
            assert box.display is True
            box.value = "seed a storm is coming"
            await pilot.press("enter")
            await pilot.pause(0.3)
            log = await rt.events.events_since(0)
            created = [
                e for e in log
                if e.event_type == EventType.DIRECTOR_SIGNAL_CREATED
                and "storm" in e.payload.get("body", "")
            ]
            assert created
            # Submitting hides the box again and clears it for next time.
            assert box.display is False
            assert box.value == ""
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_app_commands_cover_every_binding_action():
    from novelizer.tui.app import APP_COMMANDS, NovelizerApp

    covered = {c.name for c in APP_COMMANDS}
    # Every non-command, non-quit BINDINGS action must have a same-named
    # entry in APP_COMMANDS so the palette can reach it.
    expected = {
        "approvals", "toggle_room", "toggle_engine", "toggle_prompt",
        "toggle_reading", "quit", "settings",
        "brain_tab_shape", "brain_tab_threads", "brain_tab_secrets",
        "brain_tab_causeway", "brain_tab_outline", "brain_tab_arcs",
    }
    assert covered == expected
