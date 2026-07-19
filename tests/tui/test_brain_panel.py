import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.widgets.brain_model import (
    CAUSEWAY_EMPTY, SECRETS_EMPTY, SHAPE_EMPTY, THREADS_EMPTY,
)


async def _app(**settings_overrides):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1, **settings_overrides)
    rt = Runtime(settings, runners={})
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    return NovelizerApp(rt), rt, path


@pytest.mark.asyncio
async def test_brain_panel_replaces_the_four_stacked_panes():
    from novelizer.tui.widgets.brain_panel import BrainPanel
    from textual.widgets import TabbedContent

    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#brain", BrainPanel)
            assert str(panel.border_title) == "STORY BRAIN"
            tabs = app.query_one("#brain_tabs", TabbedContent)
            assert tabs.active == "tab_shape"
            for pane_id in ("tab_shape", "tab_threads", "tab_secrets", "tab_causeway"):
                assert tabs.get_pane(pane_id) is not None
            for old_id in ("#thread_board", "#story_shape", "#who_knows_what", "#causeway"):
                assert not app.query(old_id)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_keys_1_to_4_switch_brain_tabs():
    from textual.widgets import TabbedContent

    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            tabs = app.query_one("#brain_tabs", TabbedContent)
            await pilot.press("2")
            assert tabs.active == "tab_threads"
            await pilot.press("3")
            assert tabs.active == "tab_secrets"
            await pilot.press("4")
            assert tabs.active == "tab_causeway"
            await pilot.press("1")
            assert tabs.active == "tab_shape"
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_fresh_story_shows_designed_empty_states_and_quiet_strip():
    from textual.widgets import Sparkline, Static

    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.5)  # first _brain_loop refresh
            assert str(app.query_one("#shape_body", Static).renderable) == SHAPE_EMPTY
            assert str(app.query_one("#threads_body", Static).renderable) == THREADS_EMPTY
            assert str(app.query_one("#secrets_body", Static).renderable) == SECRETS_EMPTY
            assert str(app.query_one("#causeway_body", Static).renderable) == CAUSEWAY_EMPTY
            assert not app.query_one("#shape_spark", Sparkline).display
            assert str(app.query_one("#brain_strip", Static).renderable) == "Shape · Threads · Secrets · Cause"
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_strip_reflects_settings_thresholds_while_another_tab_is_open():
    """M5.3 wiring proof: _brain_loop passes settings.staleness_threshold_chapters
    and settings.sag_spike_delta through refresh_from into the pure models.
    With the defaults (3 / 0.3) this fixture is completely quiet — only the
    tightened settings below produce the alarms asserted here. The strip is
    visible regardless of the active tab (Shape)."""
    from textual.widgets import Static
    from novelizer.canon.events import EventType, ThreadPlanted, AnnotationStructureScored
    from novelizer.store.models import Chapter

    app, rt, path = await _app(staleness_threshold_chapters=2, sag_spike_delta=0.1)
    try:
        await rt.events.append(EventType.THREAD_PLANTED, "the-boys-gift",
                               ThreadPlanted(id="the-boys-gift", name="The Boy's Gift"))
        for i, tension in enumerate([0.6, 0.2]):
            await rt.events.append(EventType.CHAPTER_CREATED, f"c{i}",
                                   Chapter(id=f"c{i}", title=f"Ch {i}", prose="p"))
            await rt.events.append(EventType.ANNOTATION_STRUCTURE_SCORED, f"c{i}",
                                   AnnotationStructureScored(chapter_id=f"c{i}", tension=tension, pacing_label=""))
        await rt.projector.catch_up()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            strip = str(app.query_one("#brain_strip", Static).renderable)
            assert "Threads ⚠1" in strip   # 2 chapters untouched ≥ threshold 2 (default 3: quiet)
            assert "Shape ⚠2" in strip     # ±0.2 from the mean ≥ delta 0.1 (default 0.3: quiet)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_brain_panel_hides_in_reading_and_engine_modes_and_survives_room_mode():
    from textual.widgets import TabbedContent

    app, rt, path = await _app()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            brain = app.query_one("#brain")
            assert brain.region.width > 0
            await pilot.press("v")
            await pilot.pause()
            assert brain.region.width == 0       # #left is display:none in reading mode
            await pilot.press("v")
            await pilot.pause()
            assert brain.region.width > 0        # back on the home screen
            await pilot.press("r")
            await pilot.pause()
            assert brain.region.width > 0        # room mode keeps the left column
            await pilot.press("e")
            await pilot.pause()
            assert brain.region.width == 0       # engine mode hides #brain (member-swapped rule)
            assert app.query_one("#engine_room").region.width > 0
            await pilot.press("e")
            await pilot.pause()
            assert brain.region.width > 0        # engine off: panel is back
            await pilot.press("2")               # keys still switch tabs after toggling
            assert app.query_one("#brain_tabs", TabbedContent).active == "tab_threads"
    finally:
        await rt.close(); os.unlink(path)
