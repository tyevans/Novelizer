import os
import tempfile
import pytest
from textual.widgets import Input, RichLog
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.research_screen import ResearchScreen
from novelizer.research.schemas import ResearchAnswer
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFinished, TokenDelta,
)
from tui_kit.widgets.live_stream_panel import LiveStreamPanel


class _R:
    def __init__(self, out, delay_event=None):
        self._out = out
        self._delay_event = delay_event

    async def ainvoke(self, inputs):
        if self._delay_event is not None:
            await self._delay_event.wait()
        return {"structured_response": self._out}


class _Boom:
    async def ainvoke(self, inputs):
        raise RuntimeError("endpoint down")


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _runtime(path, runner):
    settings = Settings(db_path=path, projector_interval=0.05)
    rt = Runtime(settings, runners={"research": runner})
    await rt.start()
    return rt


@pytest.mark.asyncio
async def test_submitting_a_question_disables_input_then_shows_answer(db_path):
    rt = await _runtime(db_path, _R(ResearchAnswer(answer_text="No leaks found.")))
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            input_widget = screen.query_one("#research_input", Input)
            input_widget.value = "any leaks?"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "any leaks?"})())
            await pilot.pause(0.3)
            log_text = screen.query_one("#research_log", RichLog)
            assert "No leaks found." in "\n".join(str(line) for line in log_text.lines)
            assert input_widget.disabled is False
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_second_submit_while_pending_is_a_no_op(db_path):
    import asyncio
    gate = asyncio.Event()
    rt = await _runtime(db_path, _R(ResearchAnswer(answer_text="answer"), delay_event=gate))
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            input_widget = screen.query_one("#research_input", Input)
            input_widget.value = "q1"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "q1"})())
            await pilot.pause(0.05)
            assert input_widget.disabled is True
            input_widget.value = "q2"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "q2"})())
            await pilot.pause(0.05)
            assert screen._pending is True
            gate.set()
            await pilot.pause(0.3)
            log = screen.query_one("#research_log", RichLog)
            joined = "\n".join(str(line) for line in log.lines)
            assert joined.count("q2") == 0  # the second question was dropped, not queued
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_failed_runner_shows_warning_and_reenables_input(db_path):
    rt = await _runtime(db_path, _Boom())
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            input_widget = screen.query_one("#research_input", Input)
            input_widget.value = "boom?"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "boom?"})())
            await pilot.pause(0.3)
            log = screen.query_one("#research_log", RichLog)
            joined = "\n".join(str(line) for line in log.lines)
            assert "research failed" in joined
            assert input_widget.disabled is False
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_panel_shows_running_state_during_a_turn(db_path):
    import asyncio
    gate = asyncio.Event()
    rt = await _runtime(db_path, _R(ResearchAnswer(answer_text="answer"), delay_event=gate))
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            input_widget = screen.query_one("#research_input", Input)
            input_widget.value = "q1"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "q1"})())
            await pilot.pause(0.05)

            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="research"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="research", text="thinking…"))
            await pilot.pause(0.2)

            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "thinking…" in str(body.renderable)

            gate.set()
            await pilot.pause(0.3)
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_panel_ignores_events_for_other_identities(db_path):
    rt = await _runtime(db_path, _R(ResearchAnswer(answer_text="answer")))
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="chat:author"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="chat:author", text="not mine"))
            await pilot.pause(0.2)
            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "not mine" not in str(body.renderable)
    finally:
        await rt.close()
