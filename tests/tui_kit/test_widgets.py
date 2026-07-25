import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static
from tui_kit.run_model import (
    ProseBlock, ToolBlock, LiveRunState, apply_bus_item,
)
from tui_kit.contracts import ToolCallStarted, ToolCallFinished
from tui_kit.widgets.live_stream_panel import (
    live_body, stream_line_kind, styled_body,
)
from tui_kit.widgets.activity_strip import ActivityStrip
from tui_kit.widgets.engine_room import EngineRoom
from tui_kit.widgets.live_stream_panel import LiveStreamPanel
from tui_kit.widgets.stream_view import StreamView


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
                             blocks=(ProseBlock(text="hello"),))
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


class _NullSource:
    async def page_before(self, sequence, limit):
        return []

    async def fetch_output(self, sequence):
        return ""


class _EngineRoomHarness(App):
    def compose(self) -> ComposeResult:
        yield EngineRoom(theme=THEME, source=_NullSource(), id="engine_room")

    def on_mount(self) -> None:
        self.query_one("#engine_room", EngineRoom).set_agents(AGENTS)


@pytest.mark.asyncio
async def test_engine_room_has_no_tabs():
    """The tab bar stopped fitting at thirteen agents and structurally hid
    concurrency; one unified stream replaced it."""
    from textual.widgets import TabbedContent
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        assert not app.query(TabbedContent)


@pytest.mark.asyncio
async def test_engine_room_exposes_one_unified_stream():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        assert len(app.query(StreamView)) == 1


@pytest.mark.asyncio
async def test_engine_room_has_a_filter_chip_per_agent_with_theme_glyph():
    from textual.widgets import Button
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        chip = app.query_one("#sv_chip_author", Button)
        assert "@ Author" in str(chip.label)


@pytest.mark.asyncio
async def test_engine_room_renders_live_state_into_the_stream():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        state = LiveRunState(status="running", run_id="r1", agent_name="author",
                             started_at=0.0,
                             blocks=(ProseBlock(text="The sea rose.", agent_name="author"),))
        room.render_live(state, now=1.0)
        await pilot.pause()
        assert "author" in str(app.query_one("#er_vitals").renderable)
        assert "The sea rose." in room.stream_text()


@pytest.mark.asyncio
async def test_render_live_appends_only_blocks_not_yet_shown():
    """app.py calls render_live on every bus item AND on a 0.5s timer, each
    time with the whole current state; re-sending it must not duplicate."""
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        state = LiveRunState(status="running", run_id="r1",
                             blocks=(ProseBlock(text="a", agent_name="author"),))
        room.render_live(state, now=1.0)
        room.render_live(state, now=1.0)
        await pilot.pause()
        assert len(room.query_one(StreamView).mounted_keys()) == 1


@pytest.mark.asyncio
async def test_render_live_updates_a_block_that_mutated_in_place():
    """A prose block grows as tokens stream and a tool block goes
    running -> done. Forwarding only *new* blocks would freeze the mutated
    ones on screen at their first-seen value."""
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        room.render_live(LiveRunState(status="running", run_id="r1",
                                      blocks=(ProseBlock(text="The sea",
                                                         agent_name="author"),)), now=1.0)
        room.render_live(LiveRunState(status="running", run_id="r1",
                                      blocks=(ProseBlock(text="The sea rose.",
                                                         agent_name="author"),)), now=1.0)
        await pilot.pause()
        assert len(room.query_one(StreamView).mounted_keys()) == 1
        assert "The sea rose." in room.stream_text()


@pytest.mark.asyncio
async def test_render_live_keeps_the_previous_run_when_a_new_run_starts():
    """The unified stream is a history, not a per-run pane."""
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        room.render_live(LiveRunState(status="running", run_id="r1",
                                      blocks=(ProseBlock(text="first run",
                                                         agent_name="author"),)), now=1.0)
        room.render_live(LiveRunState(status="running", run_id="r2",
                                      blocks=(ProseBlock(text="second run",
                                                         agent_name="editor"),)), now=2.0)
        await pilot.pause()
        assert len(room.query_one(StreamView).mounted_keys()) == 2
        text = room.stream_text()
        assert "first run" in text and "second run" in text


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


# -- the string renderer behind LiveStreamPanel -------------------------------
# These moved here with live_body/stream_line_kind/styled_body when the Engine
# Room stopped rendering the stream as one string; LiveStreamPanel (Chat,
# Research) is their only remaining consumer.

def test_live_body_renders_a_tool_block_as_a_grouped_multiline_unit():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="search_web", input_summary="dragons"), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="search_web", duration_s=1.2), now=2.0)
    body = live_body(s)
    assert "⚒ search_web(dragons)" in body and "done in 1.2s" in body


def test_live_body_indents_delegated_tool_calls():
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper",
                     blocks=(ToolBlock(tool_name="read_file",
                                   input_summary="/chapters/ch-0012.md",
                                   status="running", delegate="researcher"),))
    body = live_body(s)
    assert "    ⚒ ↳ researcher: read_file(/chapters/ch-0012.md)" in body


def test_stream_line_kind_classifies_marker_lines():
    assert stream_line_kind("⚒ search_web(dragons)") == "tool"
    assert stream_line_kind("   ↳ done in 1.2s") == "call"
    assert stream_line_kind("▸ call 1 (qwen)") == "call"
    assert stream_line_kind("💭 thinking about it") == "thinking"
    assert stream_line_kind("Once upon a time") == "prose"


def test_styled_body_applies_tool_style_to_tool_lines():
    text = styled_body("\n⚒ search_canon(query)\n")
    styles = [span.style for span in text.spans]
    assert "bold cyan" in styles


def test_styled_body_leaves_prose_unstyled():
    text = styled_body("plain prose line")
    assert text.spans == []
