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
        rows = [table.get_row_at(i)[0] for i in range(table.row_count)]
        finished_idx = next(i for i, r in enumerate(rows) if "✓" in r)
        started_idx = next(i for i, r in enumerate(rows) if "run started" in r)
        assert finished_idx < started_idx  # newest (run finished) first


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
        rows = [table.get_row_at(i)[0] for i in range(table.row_count)]
        target = next(i for i, r in enumerate(rows) if "llm call 1 started" in r)
        table.move_cursor(row=target)
        await pilot.press("enter")
        await pilot.pause(0.3)
        detail = app.query_one("#er_detail")
        assert detail.display is True
        text = str(detail.renderable)
        assert "Write it." in text                       # stored prompt round-trips (C-in-D)
        assert "produced: agent.remarked author" in text  # run_id join to domain log


async def test_trace_rows_unique_when_store_fails_and_events_fall_back_to_sequence_minus_one(rt):
    from textual.widgets import DataTable
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await rt.telemetry_store.close()
        rt.telemetry_store._conn = None  # guard EventStore.close() no-op at fixture teardown
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r2",
                                AgentRunStarted(run_id="r2", agent_name="editor"))
        await pilot.pause(0.8)
        table = app.query_one("#er_trace", DataTable)
        rows = [table.get_row_at(i)[0] for i in range(table.row_count)]
        assert any("author" in r for r in rows)
        assert any("editor" in r for r in rows)
        assert not any("telemetry error" in m for m in app.messages)


# Prose/prompt text is untrusted: real agent prompts contain sequences like
# "[system]" and "key=value" inside brackets that Textual's markup parser
# rejects (MarkupError: "Expected markup value"), which crashed the telemetry
# loops every refresh. The Engine Room must render such text verbatim.
_HOSTILE = ("[system]\n[{'type': 'text', 'text': 'set known_id=False if you cannot "
            "confidently match the fact to an existing\nsecret/thread id.'}]")


async def test_prompt_pane_renders_markup_hostile_prompt_verbatim(rt):
    from novelizer.telemetry.events import LlmCallStarted
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                                LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                               model="qwen", prompt=_HOSTILE))
        await pilot.pause(0.8)
        assert not any("telemetry" in m and "error" in m for m in app.messages)
        assert "known_id=False" in str(app.query_one("#er_prompt").renderable)


async def test_stream_pane_renders_markup_hostile_tokens_verbatim(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="author", text=_HOSTILE))
        await pilot.pause(0.8)
        assert not any("telemetry" in m and "error" in m for m in app.messages)
        from novelizer.tui.widgets.engine_room import EngineRoom
        assert "known_id=False" in app.query_one("#engine_room", EngineRoom).stream_text()


async def test_trace_rows_tolerate_markup_hostile_tool_summaries(rt):
    from textual.widgets import DataTable
    from novelizer.telemetry.events import ToolCallStarted
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await rt.telemetry.emit(TelemetryEventType.TOOL_CALL_STARTED, "r1",
                                ToolCallStarted(run_id="r1", agent_name="author",
                                                tool_name="read",
                                                input_summary="path=[/stories/ch1.md]"))
        await pilot.pause(0.8)
        assert not any("telemetry" in m and "error" in m for m in app.messages)
        table = app.query_one("#er_trace", DataTable)
        rows = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
        assert any("read" in r for r in rows)


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
        table = app.query_one("#er_trace", DataTable)
        rows = [table.get_row_at(i)[0] for i in range(table.row_count)]
        assert any("run started" in r for r in rows)
