import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import VerticalScroll
from tui_kit.widgets.live_stream_panel import LiveStreamPanel
from tui_kit.run_model import Block, LiveRunState
from novelizer.tui.identity import NOVELIZER_AGENT_THEME


class _Harness(App):
    def compose(self) -> ComposeResult:
        yield LiveStreamPanel(theme=NOVELIZER_AGENT_THEME, id="panel")


@pytest.mark.asyncio
async def test_idle_state_renders_idle_body():
    app = _Harness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        panel.render(LiveRunState(), now=0.0)
        await pilot.pause()
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "no run yet" in str(body.renderable)


@pytest.mark.asyncio
async def test_running_state_shows_agent_name_in_vitals():
    app = _Harness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        state = LiveRunState(status="running", agent_name="research", started_at=0.0,
                             blocks=(Block(kind="prose", text="hello"),))
        panel.render(state, now=1.0)
        await pilot.pause()
        vitals = panel.query_one(LiveStreamPanel._VITALS_ID, Static)
        assert "research" in str(vitals.renderable)
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "hello" in str(body.renderable)
