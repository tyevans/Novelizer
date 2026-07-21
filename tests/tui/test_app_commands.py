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


def _expected_command_names_from_bindings():
    import re
    from novelizer.tui.app import NovelizerApp

    brain_tab_re = re.compile(r"^brain_tab\('(?P<pane>tab_\w+)'\)$")
    names = set()
    for binding in NovelizerApp.BINDINGS:
        action = binding[1]
        if action == "quit":
            # quit has an APP_COMMANDS entry too, so include it directly.
            names.add("quit")
            continue
        match = brain_tab_re.match(action)
        if match:
            names.add(f"brain_tab_{match.group('pane')[len('tab_'):]}")
            continue
        names.add(action)
    # "settings" has no keybinding (only reachable via the palette / :settings
    # command-line), so it isn't derivable from BINDINGS -- add it explicitly.
    names.add("settings")
    return names


@pytest.mark.asyncio
async def test_app_commands_cover_every_binding_action():
    from novelizer.tui.app import APP_COMMANDS

    covered = {c.name for c in APP_COMMANDS}
    # Every BINDINGS action (mapped through the brain_tab('tab_X') ->
    # brain_tab_X convention) must have a same-named entry in APP_COMMANDS
    # so the palette can reach it. This is derived from BINDINGS itself so
    # a new keybinding added without a matching APP_COMMANDS entry fails
    # this test.
    expected = _expected_command_names_from_bindings()
    assert covered == expected


@pytest.mark.asyncio
async def test_command_provider_search_narrows_to_matching_commands():
    from novelizer.tui.app import NovelizerCommandProvider

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            provider = NovelizerCommandProvider(app.screen)
            all_hits = [hit async for hit in provider.discover()]
            hits = [hit async for hit in provider.search("seed")]
            names = {hit.text for hit in hits}
            assert names, "expected at least one match for 'seed'"
            assert names < {h.text for h in all_hits}, (
                "search should narrow results, not return the full list"
            )
            assert "seed" in names
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_command_provider_run_dispatches_args_command_to_followup():
    from novelizer.tui.app import NovelizerCommandProvider

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            from textual.widgets import Input

            provider = NovelizerCommandProvider(app.screen)
            hits = [hit async for hit in provider.search("seed")]
            hit = next(h for h in hits if h.text == "seed")
            hit.command()
            await pilot.pause()
            box = app.query_one("#command_followup", Input)
            assert box.value == "seed "
            assert box.display is True
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_command_provider_run_executes_zero_arg_command():
    from novelizer.tui.app import NovelizerCommandProvider

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            provider = NovelizerCommandProvider(app.screen)
            hits = [hit async for hit in provider.discover()]
            hit = next(h for h in hits if h.text == "toggle_room")
            assert not app.query_one("#body").has_class("room")
            hit.command()
            await pilot.pause()
            assert app.query_one("#body").has_class("room")
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_ctrl_r_opens_research_screen():
    from novelizer.tui.research_screen import ResearchScreen

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+r")
            await pilot.pause(0.1)
            assert isinstance(app.screen, ResearchScreen)
    finally:
        await rt.close(); os.unlink(path)
