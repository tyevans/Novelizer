from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings, create_story
from novelizer.tui.app import NovelizerApp
from novelizer.tui.settings_screen import SettingsScreen
from tests.tui.test_app_smoke import _room_runners
from textual.widgets import DataTable
from tests.tui.conftest import stub_runners


async def _app(tmp_path, monkeypatch, **settings_kwargs):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    sd = create_story(tmp_path / "novel", title="N")
    settings = EffectiveSettings(
        db_path=str(sd.db_path), chroma_path=str(sd.chroma_path),
        projector_interval=0.1, **settings_kwargs,
    )
    rt = Runtime(settings, runners=stub_runners(**_room_runners()))
    await rt.start()
    return NovelizerApp(rt), rt, sd


async def test_settings_command_opens_screen_with_rows(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            table = app.screen.query_one("#settings_table", DataTable)
            assert table.row_count > 10
    finally:
        await rt.close()


async def test_api_key_redacted_and_inherited_marked(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch, llm_api_key="sk-very-secret")
    try:
        async with app.run_test() as pilot:
            await app._run_command("settings")
            await pilot.pause()
            table = app.screen.query_one("#settings_table", DataTable)
            cells = [str(cell) for row_key in list(table.rows) for cell in table.get_row(row_key)]
            joined = " | ".join(cells)
            assert "sk-very-secret" not in joined
            assert "(inherited)" in joined
            assert "(restart required)" in joined
    finally:
        await rt.close()


async def test_escape_dismisses(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SettingsScreen)
    finally:
        await rt.close()


from novelizer.settings.setup_core import ProbeResult
from novelizer.settings.toml_io import load_toml_file
from textual.widgets import Input, Static


def _row_index(screen: SettingsScreen, key: str) -> int:
    return next(i for i, r in enumerate(screen._rows) if r.key == key)


async def test_edit_story_scope_writes_story_toml(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "author_temperature"))
            await pilot.press("enter")
            box = screen.query_one("#edit_value", Input)
            assert box.display
            box.value = "0.25"
            box.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert load_toml_file(sd.story_toml)["author_temperature"] == 0.25
    finally:
        await rt.close()


async def test_clear_story_override(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    from novelizer.settings.toml_io import write_toml_file

    write_toml_file(sd.story_toml, {"title": "N", "author_temperature": 0.9})
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "author_temperature"))
            await pilot.press("enter")
            box = screen.query_one("#edit_value", Input)
            box.value = ""
            box.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "author_temperature" not in load_toml_file(sd.story_toml)
    finally:
        await rt.close()


async def test_env_row_not_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELIZER_PROSE_PROFILE", "lush")
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "prose_profile"))
            await pilot.press("enter")
            assert screen.query_one("#edit_value", Input).display is False
            assert "read only" in str(screen.query_one("#settings_msg", Static).renderable)
    finally:
        await rt.close()


async def test_invalid_value_shows_error(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "author_interval"))
            await pilot.press("enter")
            box = screen.query_one("#edit_value", Input)
            box.value = "soon"
            box.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "not a valid" in str(screen.query_one("#settings_msg", Static).renderable)
            assert "author_interval" not in load_toml_file(sd.story_toml)
    finally:
        await rt.close()


async def test_secret_row_edit_uses_password_input(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch, llm_api_key="sk-very-secret")
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            box = screen.query_one("#edit_value", Input)

            screen._begin_edit(_row_index(screen, "llm_api_key"))
            assert box.password is True

            # Non-secret row must not be a password field.
            screen._begin_edit(_row_index(screen, "author_temperature"))
            assert box.password is False
    finally:
        await rt.close()


async def test_table_converges_after_external_apply(tmp_path, monkeypatch):
    """The effective Value column must not go stale after an edit — it should
    converge to what the watcher actually applied, on its own, without the
    user re-opening the screen."""
    from novelizer.settings.toml_io import write_toml_file

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    sd = create_story(tmp_path / "novel", title="N")
    settings = EffectiveSettings(
        db_path=str(sd.db_path), chroma_path=str(sd.chroma_path),
        projector_interval=0.1, author_interval=300,
    )
    rt = Runtime(settings, runners=stub_runners(**_room_runners()))
    await rt.start()
    app = NovelizerApp(rt)
    app.SETTINGS_POLL_INTERVAL = 0.05
    SettingsScreen.REFRESH_INTERVAL = 0.05
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            row = _row_index(screen, "author_interval")
            assert table.get_row_at(row)[1] == "300"

            write_toml_file(sd.story_toml, {"title": "N", "author_interval": 45})
            await pilot.pause(0.5)

            assert table.get_row_at(row)[1] == "45"
    finally:
        SettingsScreen.REFRESH_INTERVAL = 1.0
        await rt.close()


async def test_refresh_does_not_reset_cursor(tmp_path, monkeypatch):
    """The 1s periodic refresh must not snap the cursor back to row 0."""
    app, rt, sd = await _app(tmp_path, monkeypatch)
    SettingsScreen.REFRESH_INTERVAL = 0.05
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            target_row = 5
            table.move_cursor(row=target_row)
            await pilot.pause()
            assert table.cursor_row == target_row

            await pilot.pause(0.3)  # let several refresh intervals elapse

            assert table.cursor_row == target_row
    finally:
        SettingsScreen.REFRESH_INTERVAL = 1.0
        await rt.close()


async def test_external_change_updates_table_and_preserves_cursor(tmp_path, monkeypatch):
    """After an external settings change is applied, the table updates and
    the cursor stays on the same row (rows are stably sorted)."""
    from novelizer.settings.toml_io import write_toml_file

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    sd = create_story(tmp_path / "novel", title="N")
    settings = EffectiveSettings(
        db_path=str(sd.db_path), chroma_path=str(sd.chroma_path),
        projector_interval=0.1, author_interval=300,
    )
    rt = Runtime(settings, runners=stub_runners(**_room_runners()))
    await rt.start()
    app = NovelizerApp(rt)
    app.SETTINGS_POLL_INTERVAL = 0.05
    SettingsScreen.REFRESH_INTERVAL = 0.05
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            row = _row_index(screen, "author_interval")
            table.move_cursor(row=row)
            await pilot.pause()
            assert table.get_row_at(row)[1] == "300"

            write_toml_file(sd.story_toml, {"title": "N", "author_interval": 45})
            await pilot.pause(0.5)

            assert table.get_row_at(row)[1] == "45"
            assert table.cursor_row == row
    finally:
        SettingsScreen.REFRESH_INTERVAL = 1.0
        await rt.close()


async def test_probe_action_shows_result(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)

    async def fake_probe(base_url, api_key="not-needed", **kwargs):
        return ProbeResult(ok=True, models=["m-live"])

    try:
        async with app.run_test() as pilot:
            story_dir_screen = SettingsScreen(
                sd,
                lambda: rt.settings,
                probe=fake_probe,
            )
            await app.push_screen(story_dir_screen)
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert "m-live" in str(story_dir_screen.query_one("#settings_msg", Static).renderable)
    finally:
        await rt.close()
