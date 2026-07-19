import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.widgets.activity_strip import ActivityStrip
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFailed, TokenDelta,
)
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict,
    ContinuityOutput, RetconAmendments, StructureAnalystOutput,
)
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out):
        self._out = out

    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


def _runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[])),
        "author": _R(ChapterDraft(title="T", prose="P")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "continuity_checker_mining": _R(None),
        "retconner": _R(RetconAmendments()),
        "structure_analyst": _R(StructureAnalystOutput()),
    }


@pytest.fixture
async def rt(tmp_path):
    settings = Settings(db_path=str(tmp_path / "world.db"),
                        author_interval=3600, default_agent_interval=3600,
                        continuity_interval=3600, projector_interval=0.1)
    runtime = Runtime(settings, runners=_runners())
    await runtime.start()
    # Long intervals: agents were never marked ran, so first tick would run one.
    # Pause them all so tests drive telemetry by hand.
    for a in runtime.agents:
        a.pause()
    # Pre-seed scheduler eligibility as already "paused" so the scheduler loop's
    # first tick doesn't emit a SCHEDULER_ELIGIBILITY_CHANGED per agent (it only
    # emits on state *change* — without this, tests would see unrelated noise
    # in _trace_events, defeating "tests drive telemetry by hand" above).
    runtime.scheduler._eligibility = {a.name: (False, "paused") for a in runtime.agents}
    yield runtime
    await runtime.close()


async def test_strip_shows_live_run_from_bus_traffic(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="author", text="The sea"))
        await pilot.pause(0.8)
        strip = app.query_one("#activity_strip", ActivityStrip)
        text = str(strip.renderable)
        assert "▶" in text and "author" in text and "drafting" in text


async def test_strip_shows_crash_until_next_run(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_FAILED, "r1",
                                AgentRunFailed(run_id="r1", agent_name="author",
                                               error_type="TimeoutError", error_message="proxy",
                                               phase="llm_call", duration_s=4.0))
        await pilot.pause(0.8)
        text = str(app.query_one("#activity_strip", ActivityStrip).renderable)
        assert "✗" in text and "author" in text and "Engine Room" in text


async def test_strip_idle_shows_next_agent_hint(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.8)
        text = str(app.query_one("#activity_strip", ActivityStrip).renderable)
        assert text.startswith("idle")


async def test_engine_room_hidden_by_default_and_toggles_with_e(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)  # keys must reach app bindings, not a focused widget
        body = app.query_one("#body")
        assert not body.has_class("engine")
        await pilot.press("e")
        assert body.has_class("engine")
        await pilot.press("e")
        assert not body.has_class("engine")


async def test_engine_room_streams_tokens_into_live_pane(rt):
    from novelizer.tui.widgets.engine_room import EngineRoom
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="author", text="The sea rose."))
        await pilot.pause(0.8)
        room = app.query_one("#engine_room", EngineRoom)
        vitals = str(app.query_one("#er_vitals").renderable)
        assert "author" in vitals and "drafting" in vitals
        assert "The sea rose." in room.stream_text()


async def test_prompt_pane_off_by_default_and_p_toggles_it(rt):
    from novelizer.telemetry.events import LlmCallStarted
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                                LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                               model="qwen", prompt="[system]\nWrite the chapter."))
        await pilot.pause(0.8)
        prompt_pane = app.query_one("#er_prompt")
        assert prompt_pane.display is False  # off by default (spec)
        await pilot.press("p")
        assert prompt_pane.display is True
        assert "Write the chapter." in str(prompt_pane.renderable)
        await pilot.press("p")
        assert prompt_pane.display is False


async def test_p_outside_engine_view_does_nothing(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("p")  # engine view not open: must not crash or toggle
        assert app.query_one("#er_prompt").display is False


async def test_trace_rows_appear_newest_first(rt):
    from textual.widgets import DataTable
    from novelizer.telemetry.events import AgentRunFinished
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_FINISHED, "r1",
                                AgentRunFinished(run_id="r1", agent_name="author", duration_s=52.0))
        await pilot.pause(0.8)
        table = app.query_one("#er_trace", DataTable)
        assert table.row_count == 2
        first_row = table.get_row_at(0)
        assert "✓" in first_row[0]  # newest (run finished) first


async def test_selecting_a_trace_row_shows_detail_with_prompt_and_produced(rt):
    from textual.widgets import DataTable
    from novelizer.telemetry.events import LlmCallStarted
    from novelizer.canon.events import EventType, AgentRemark
    from novelizer.run_context import current_run_id
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        # A domain event stamped with the run, so detail can show "produced:"
        token = current_run_id.set("r1")
        try:
            await rt.committer.commit("author", EventType.AGENT_REMARKED, "author",
                                      AgentRemark(agent_name="author", note="done"))
        finally:
            current_run_id.reset(token)
        await rt.telemetry.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                                LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                               model="qwen", prompt="[system]\nWrite it."))
        await pilot.pause(0.8)
        app.set_focus(None)
        await pilot.press("e")
        table = app.query_one("#er_trace", DataTable)
        table.focus()
        await pilot.press("enter")
        await pilot.pause(0.3)
        detail = app.query_one("#er_detail")
        assert detail.display is True
        text = str(detail.renderable)
        assert "Write it." in text                       # stored prompt round-trips (C-in-D)
        assert "produced: agent.remarked author" in text  # run_id join to domain log


async def test_seeded_trace_survives_restart(rt):
    from textual.widgets import DataTable
    await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                            AgentRunStarted(run_id="r1", agent_name="author"))
    # A fresh app instance (a "restart") must show the persisted trace row.
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await pilot.pause(0.8)
        assert app.query_one("#er_trace", DataTable).row_count == 1
