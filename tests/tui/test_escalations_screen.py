import os
import tempfile
import pytest
from textual.widgets import DataTable

from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.escalations_screen import EscalationsScreen
from novelizer.canon.events import EventType
from novelizer.store.models import Flag
from tests.tui.test_app_smoke import _room_runners
from tests.tui.conftest import stub_runners


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _app_with_escalated_flag(db_path):
    settings = Settings(db_path=db_path, projector_interval=0.05)
    rt = Runtime(settings, runners=stub_runners(**_room_runners()))
    await rt.start()
    flag = Flag(
        id="f1", category="contradiction", description="critical one",
        escalated=True, severity="critical",
    )
    await rt.events.append(EventType.FLAG_CREATED, "f1", flag)
    await rt.events.append(EventType.FLAG_ESCALATED, "f1", flag)
    await rt.projector.catch_up()
    return NovelizerApp(rt), rt


async def test_escalations_command_opens_screen_with_rows(db_path):
    app, rt = await _app_with_escalated_flag(db_path)
    try:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert isinstance(app.screen, EscalationsScreen)
            table = app.screen.query_one("#escalations-table", DataTable)
            assert table.row_count == 1
    finally:
        await rt.close()


async def test_escape_dismisses(db_path):
    app, rt = await _app_with_escalated_flag(db_path)
    try:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+e")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, EscalationsScreen)
    finally:
        await rt.close()


async def test_ctrl_e_binding_opens_screen(db_path):
    app, rt = await _app_with_escalated_flag(db_path)
    try:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert isinstance(app.screen, EscalationsScreen)
    finally:
        await rt.close()


async def test_clear_escalation_commits_event_and_refreshes(db_path):
    from textual.widgets import Button, Input

    app, rt = await _app_with_escalated_flag(db_path)
    try:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+e")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#escalations-table", DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            screen.query_one("#escalations-clear-note", Input).value = "resolved by hand"
            await screen.on_button_pressed(
                Button.Pressed(screen.query_one("#escalations-clear-button", Button))
            )
            # The clear commits an event and the table refreshes off it; under
            # parallel load one pause() is not always enough for that round trip
            # to land (seen as row_count 1 under pytest -n). Pump the event loop
            # until it does, then assert exactly as before -- a table that never
            # empties still fails, just without the timing race.
            for _ in range(50):
                if table.row_count == 0:
                    break
                await pilot.pause()
            assert table.row_count == 0

            # Runtime is fully running (real agent polling loops), so the
            # retconner agent may also resolve/clear the same flag on its
            # own — assert at least one clear event exists rather than
            # exactly one, to avoid a race with the background agent.
            events = await rt.events.events_for_aggregate("f1")
            cleared_events = [e for e in events if e.event_type == EventType.FLAG_ESCALATION_CLEARED]
            assert len(cleared_events) >= 1
            human_cleared = [
                e for e in cleared_events if e.payload.get("escalation_cleared_by") == "human"
            ]
            assert len(human_cleared) == 1
            assert human_cleared[0].payload.get("escalation_clear_note") == "resolved by hand"
    finally:
        await rt.close()


async def test_clearing_an_already_cleared_flag_commits_nothing(db_path):
    # The selected flag can be cleared out from under the screen by the owning
    # agent resolving it. Pressing the button on that stale selection must not
    # append a second FLAG_ESCALATION_CLEARED -- the projection is a faithful
    # fold of the log, so the guard has to sit upstream of the commit.
    from textual.widgets import Button

    app, rt = await _app_with_escalated_flag(db_path)
    try:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+e")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#escalations-table", DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            screen._selected = screen._selected.model_copy(update={"escalated": False})
            await screen.on_button_pressed(
                Button.Pressed(screen.query_one("#escalations-clear-button", Button))
            )
            await pilot.pause()
            events = await rt.events.events_for_aggregate("f1")
            human_cleared = [
                e for e in events
                if e.event_type == EventType.FLAG_ESCALATION_CLEARED
                and e.payload.get("escalation_cleared_by") == "human"
            ]
            assert human_cleared == []
    finally:
        await rt.close()
