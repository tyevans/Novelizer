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


@pytest.mark.asyncio
async def test_detail_pane_scrolls_long_content_and_resets_on_update():
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=stub_runners(**{"summarizer": _R()}))
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    try:
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await pilot.pause()
            scroll = app.query_one("#detail_scroll", VerticalScroll)
            detail = app.query_one("#detail", Static)
            # Long content must overflow the pane so the container can scroll.
            detail.update("\n".join(f"line {i}" for i in range(200)))
            await pilot.pause()
            assert scroll.max_scroll_y > 0
            scroll.scroll_end(animate=False)
            await pilot.pause()
            assert scroll.scroll_y > 0
            # A fresh selection must start at the top, not wherever the
            # previous entry was scrolled to.
            app._update_detail("short text")
            await pilot.pause()
            assert scroll.scroll_y == 0
    finally:
        await rt.close(); os.unlink(path)
