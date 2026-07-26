import os
import tempfile
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

import novelizer.tui.app
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.widgets.brain_panel import BrainPanel

from novelizer.tui.widgets.brain_model import (
    ARCS_EMPTY, CAUSEWAY_EMPTY, OUTLINE_EMPTY, SECRETS_EMPTY, SHAPE_EMPTY, THREADS_EMPTY,
)
from novelizer.agents.schemas import SummarizerOutput
from tests.tui.conftest import stub_runners

# The panel takes these as arguments; _brain_loop passes them from settings, so
# a harness test reads them off the same defaults rather than inventing numbers.
_DEFAULTS = Settings(db_path="unused.db")
DEFAULT_STALENESS_THRESHOLD = _DEFAULTS.staleness_threshold_chapters
DEFAULT_SAG_SPIKE_DELTA = _DEFAULTS.sag_spike_delta


class _R:
    async def ainvoke(self, inputs):
        return {"structured_response": SummarizerOutput(gist="g", summary="s")}


class _PanelHarness(App):
    """Mounts BrainPanel and nothing else.

    The app-level tests below need the real NovelizerApp -- bindings, mode
    classes, the _brain_loop wiring. A test that only asserts on what the panel
    renders does not, and paying for the full widget tree to find out is the
    single biggest cost in this band.
    """

    CSS_PATH = str(Path(novelizer.tui.app.__file__).parent / "app.tcss")

    def compose(self) -> ComposeResult:
        yield BrainPanel(id="brain")


async def _panel_harness():
    """A BrainPanel harness over a real (empty) ReadStore.

    A ReadStore, not a Runtime: the panel reads canon through it, and nothing
    here needs agents, a scheduler, or an embedding index. The Projector owns
    the read-model schema, so it creates the tables the store then reads.
    """
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path)
    await events.init()
    projector = Projector(events, path)
    await projector.init()
    await projector.close()
    read = ReadStore(path)
    await read.init()
    return _PanelHarness(), read, path


async def _app(**settings_overrides):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1, **settings_overrides)
    rt = Runtime(settings, runners=stub_runners(**{"summarizer": _R()}))
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
            assert tabs.active == "tab_outline"
            for pane_id in (
                "tab_shape", "tab_threads", "tab_secrets", "tab_causeway", "tab_outline", "tab_arcs",
            ):
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
            await pilot.press("5")
            assert tabs.active == "tab_outline"
            await pilot.press("6")
            assert tabs.active == "tab_arcs"
            await pilot.press("1")
            assert tabs.active == "tab_shape"
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_fresh_story_shows_designed_empty_states_and_quiet_strip():
    """What the panel RENDERS for an empty story -- so it mounts the panel, not
    the whole app.

    The full app costs ~1.27s to mount and tear down (14 widgets and their
    layout) plus ~0.46s for a Runtime, and then this test had to wait out a
    _brain_loop tick on top. None of that is under test here: the subject is
    BrainPanel's own output, and refresh_from() is the same call the loop makes.
    Driving it directly is ~6x cheaper AND deterministic -- there is no tick to
    race, which is what made this test one of the band's intermittent failures.
    """
    from textual.widgets import Static

    app, read, path = await _panel_harness()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.query_one(BrainPanel).refresh_from(
                read, threshold=DEFAULT_STALENESS_THRESHOLD, delta=DEFAULT_SAG_SPIKE_DELTA,
            )
            await pilot.pause()
            shape_body = app.query_one("#shape_body", Static).renderable
            assert [t.plain for t in shape_body.renderables] == [SHAPE_EMPTY]
            assert str(app.query_one("#threads_body", Static).renderable) == THREADS_EMPTY
            assert str(app.query_one("#secrets_body", Static).renderable) == SECRETS_EMPTY
            assert str(app.query_one("#causeway_body", Static).renderable) == CAUSEWAY_EMPTY
            assert str(app.query_one("#outline_body", Static).renderable) == OUTLINE_EMPTY
            assert str(app.query_one("#arcs_body", Static).renderable) == ARCS_EMPTY
            assert not app.query("#shape_spark")   # the widget is gone entirely
            assert str(app.query_one("#brain_strip", Static).renderable) == "Shape · Threads · Secrets · Cause · Outline · Arcs"
    finally:
        await read.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_strip_reports_the_semantic_index_size_from_the_runtime():
    """Wiring proof: _brain_loop reads Runtime.index_document_count() and passes
    it through refresh_from into the strip. Without it a dead index is invisible
    -- once the drain abandons an aggregate, lag() reads 0 forever and the
    readout beside this one actively reassures."""
    from textual.widgets import Static

    app, rt, path = await _app()
    try:
        async def _count():
            return 1284
        rt.index_document_count = _count
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            assert "Index 1284" in str(app.query_one("#brain_strip", Static).renderable)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_strip_alarms_when_canon_exists_and_the_index_is_empty():
    """The state this readout exists for, end to end through the real runtime:
    canon in the log, nothing in the index. lag() cannot show it once the drain
    has abandoned the backlog, so the size must -- and the suppression that
    keeps a fresh story quiet must not reach this far."""
    from textual.widgets import Static
    from novelizer.canon.events import EventType, ThreadPlanted

    app, rt, path = await _app()
    try:
        await rt.events.append(EventType.THREAD_PLANTED, "the-ledger",
                               ThreadPlanted(id="the-ledger", name="The Ledger"))
        assert await rt.index_document_count() == 0     # the dead index
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            assert "Index ⚠empty" in str(app.query_one("#brain_strip", Static).renderable)
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
