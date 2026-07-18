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
