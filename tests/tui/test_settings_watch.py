from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings, create_story
from novelizer.settings.layers import global_config_path
from novelizer.settings.toml_io import load_toml_file, write_toml_file
from novelizer.tui.app import NovelizerApp
from tests.tui.test_app_smoke import _room_runners


async def _story_app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    sd = create_story(tmp_path / "novel", title="N")
    settings = EffectiveSettings(
        db_path=str(sd.db_path),
        chroma_path=str(sd.chroma_path),
        author_interval=300,
        projector_interval=0.1,
    )
    rt = Runtime(settings, runners=_room_runners())
    await rt.start()
    app = NovelizerApp(rt)
    app.SETTINGS_POLL_INTERVAL = 0.05
    return app, rt, sd


async def test_story_toml_edit_applies_live(tmp_path, monkeypatch):
    app, rt, sd = await _story_app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            data = load_toml_file(sd.story_toml)
            data["author_interval"] = 30
            write_toml_file(sd.story_toml, data)
            await pilot.pause(0.5)
            assert rt.author.interval == 30
            assert any("settings applied" in m for m in app.messages)
    finally:
        await rt.close()


async def test_global_config_edit_applies_live(tmp_path, monkeypatch):
    app, rt, sd = await _story_app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            gpath = global_config_path()
            gpath.parent.mkdir(parents=True, exist_ok=True)
            write_toml_file(gpath, {"author_temperature": 0.11})
            await pilot.pause(0.5)
            assert rt.settings.author_temperature == 0.11
            assert any("settings applied" in m for m in app.messages)
    finally:
        await rt.close()


async def test_invalid_story_toml_reports_not_crashes(tmp_path, monkeypatch):
    app, rt, sd = await _story_app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            sd.story_toml.write_text("author_interval = \n")
            await pilot.pause(0.5)
            assert rt.author.interval == 300  # unchanged
            # loop survived: fix the file and it still applies
            write_toml_file(sd.story_toml, {"title": "N", "author_interval": 45})
            await pilot.pause(0.5)
            assert rt.author.interval == 45
    finally:
        await rt.close()
