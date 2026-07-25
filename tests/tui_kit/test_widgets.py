import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static
from tui_kit.run_model import Block, LiveRunState
from tui_kit.widgets.activity_strip import ActivityStrip
from tui_kit.widgets.engine_room import EngineRoom
from tui_kit.widgets.live_stream_panel import LiveStreamPanel


class _FakeTheme:
    _GLYPHS = {"author": "@", "editor": "#"}

    def glyph(self, agent_name):
        return self._GLYPHS.get(agent_name, "?")

    def label(self, agent_name):
        return agent_name.title()

    def style(self, agent_name):
        return "gold3"

    def verb(self, agent_name):
        return "drafting"


THEME = _FakeTheme()
AGENTS = ["author", "editor"]


class _LSPHarness(App):
    def compose(self) -> ComposeResult:
        yield LiveStreamPanel(theme=THEME, id="panel")


@pytest.mark.asyncio
async def test_live_stream_panel_idle_state_renders_idle_body():
    app = _LSPHarness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        panel.render(LiveRunState(), now=0.0)
        await pilot.pause()
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "no run yet" in str(body.renderable)


@pytest.mark.asyncio
async def test_live_stream_panel_running_state_shows_agent_name_in_vitals():
    app = _LSPHarness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                             blocks=(Block(kind="prose", text="hello"),))
        panel.render(state, now=1.0)
        await pilot.pause()
        vitals = panel.query_one(LiveStreamPanel._VITALS_ID, Static)
        assert "author" in str(vitals.renderable)
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "hello" in str(body.renderable)


class _StripHarness(App):
    def compose(self) -> ComposeResult:
        yield ActivityStrip("idle", theme=THEME, id="strip")


@pytest.mark.asyncio
async def test_activity_strip_renders_running_state():
    app = _StripHarness()
    async with app.run_test() as pilot:
        strip = app.query_one("#strip", ActivityStrip)
        state = LiveRunState(status="running", agent_name="author", started_at=0.0, tokens=10)
        strip.render_state(state, now=2.0)
        await pilot.pause()
        assert "author" in str(strip.renderable) and "drafting" in str(strip.renderable)


class _EngineRoomHarness(App):
    def compose(self) -> ComposeResult:
        yield EngineRoom(agent_names=AGENTS, theme=THEME, id="engine_room")


@pytest.mark.asyncio
async def test_engine_room_has_a_tab_per_agent_with_theme_glyph():
    from textual.content import Content
    from textual.widgets import TabbedContent
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        tabs = app.query_one("#er_tabs", TabbedContent)
        author_tab = tabs.query_one("#er_tab_author")._title
        assert isinstance(author_tab, Content)
        assert author_tab.plain == "@ Author"


@pytest.mark.asyncio
async def test_engine_room_renders_live_state_into_the_all_pane():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                             blocks=(Block(kind="prose", text="The sea rose."),))
        room.render_live(state, now=1.0)
        await pilot.pause()
        assert "author" in str(app.query_one("#er_vitals").renderable)
        assert "The sea rose." in room.stream_text()


@pytest.mark.asyncio
async def test_engine_room_renders_per_agent_pane_independently():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        state = LiveRunState(status="running", agent_name="editor", started_at=0.0,
                             blocks=(Block(kind="prose", text="looks good"),))
        room.render_agent_live("editor", state, now=1.0)
        await pilot.pause()
        body = app.query_one("#er_stream_editor", Static).renderable
        assert "looks good" in str(body)


@pytest.mark.asyncio
async def test_engine_room_prompt_pane_toggles():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        assert app.query_one("#er_prompt", Static).display is False
        assert room.toggle_prompt() is True
        assert app.query_one("#er_prompt", Static).display is True


@pytest.mark.asyncio
async def test_engine_room_trace_rows_and_detail_pane():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        room.set_trace_rows([("k1", "12:00:00 author run started")])
        table = app.query_one("#er_trace", DataTable)
        rows = [table.get_row_at(i)[0] for i in range(table.row_count)]
        assert any("run started" in str(r) for r in rows)
        room.show_detail("some detail text")
        detail = app.query_one("#er_detail", Static)
        assert detail.display is True
        assert "some detail text" in str(detail.renderable)


@pytest.mark.asyncio
async def test_engine_room_agent_pane_shows_why_an_idle_agent_is_not_producing():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        room.render_agent_live("editor", LiveRunState(), now=1.0,
                              hold="backing off after error · retry in 12s")
        await pilot.pause()
        vitals = app.query_one("#er_vitals_editor", Static)
        assert "backing off after error · retry in 12s" in str(vitals.renderable)
