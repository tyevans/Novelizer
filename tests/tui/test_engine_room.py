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
