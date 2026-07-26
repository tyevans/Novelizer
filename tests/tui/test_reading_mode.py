import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import SummarizerOutput
from tests.tui.conftest import stub_runners


class _R:
    async def ainvoke(self, inputs):
        return {"structured_response": SummarizerOutput(gist="g", summary="s")}


async def _app():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=stub_runners(**{"summarizer": _R()}))
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    return NovelizerApp(rt), rt, path


@pytest.mark.asyncio
async def test_v_toggles_reading_mode_class():
    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            body = app.query_one("#body")
            assert not body.has_class("reading")
            await pilot.press("v")
            assert body.has_class("reading")
            await pilot.press("v")
            assert not body.has_class("reading")
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_reading_mode_lays_tree_and_reading_pane_side_by_side():
    app, rt, path = await _app()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("v")
            await pilot.pause()
            left = app.query_one("#left")
            browser = app.query_one("#browser")
            detail_scroll = app.query_one("#detail_scroll")
            # Mission-control column is hidden; tree and reading pane share the row.
            assert not left.display
            assert browser.region.y == detail_scroll.region.y
            assert browser.region.x < detail_scroll.region.x
            # The reading pane is the large one.
            assert detail_scroll.region.width > browser.region.width
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_reading_and_room_modes_are_mutually_exclusive():
    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            body = app.query_one("#body")
            await pilot.press("r")
            assert body.has_class("room")
            await pilot.press("v")
            assert body.has_class("reading") and not body.has_class("room")
            await pilot.press("r")
            assert body.has_class("room") and not body.has_class("reading")
    finally:
        await rt.close(); os.unlink(path)
