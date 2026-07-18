from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings, create_story
from novelizer.tui.app import NovelizerApp
from novelizer.tui.settings_screen import SettingsScreen
from tests.tui.test_app_smoke import _room_runners
from textual.widgets import DataTable


async def _app(tmp_path, monkeypatch, **settings_kwargs):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    sd = create_story(tmp_path / "novel", title="N")
    settings = EffectiveSettings(
        db_path=str(sd.db_path), chroma_path=str(sd.chroma_path),
        projector_interval=0.1, **settings_kwargs,
    )
    rt = Runtime(settings, runners=_room_runners())
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
