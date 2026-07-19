import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFinished, AgentRunFailed,
    LlmCallStarted, LlmCallFinished, TokenDelta,
)


@pytest.fixture
async def rig():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = EventStore(path)
    await store.init()
    bus = TelemetryBus()
    yield store, bus, TelemetryRecorder(store, bus)
    await store.close()
    os.unlink(path)


async def test_emit_persists_and_mirrors_to_bus(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    await rec.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                   AgentRunStarted(run_id="r1", agent_name="author"))
    persisted = await store.events_since(0)
    assert persisted[0].event_type == TelemetryEventType.AGENT_RUN_STARTED
    mirrored = q.get_nowait()
    assert mirrored.sequence == persisted[0].sequence
    assert mirrored.payload["agent_name"] == "author"


async def test_store_failure_warns_drops_and_still_mirrors(rig, caplog):
    store, bus, rec = rig
    q = bus.subscribe()
    await store.close()  # subsequent appends now raise
    await rec.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                   AgentRunStarted(run_id="r1", agent_name="author"))  # must not raise
    assert any("telemetry" in r.message for r in caplog.records)
    mirrored = q.get_nowait()  # bus mirror still fires (spec: graceful degradation)
    assert mirrored.sequence == -1
    assert mirrored.payload["agent_name"] == "author"
    store._conn = None  # keep the fixture's second close() harmless


async def test_publish_token_reaches_bus_but_never_the_store(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text="The "))
    assert q.get_nowait().text == "The "
    assert await store.events_since(0) == []


async def test_in_llm_call_tracks_open_calls(rig):
    store, bus, rec = rig
    assert rec.in_llm_call("r1") is False
    await rec.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                   LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                  model="m", prompt="p"))
    assert rec.in_llm_call("r1") is True
    await rec.emit(TelemetryEventType.LLM_CALL_FINISHED, "r1",
                   LlmCallFinished(run_id="r1", agent_name="author", call_index=1,
                                   model="m", duration_s=1.0, output_tokens=5))
    assert rec.in_llm_call("r1") is False


async def test_next_call_index_counts_per_run_and_resets_at_run_end(rig):
    store, bus, rec = rig
    assert rec.next_call_index("r1") == 1
    assert rec.next_call_index("r1") == 2
    assert rec.next_call_index("r2") == 1
    await rec.emit(TelemetryEventType.AGENT_RUN_FINISHED, "r1",
                   AgentRunFinished(run_id="r1", agent_name="author", duration_s=1.0))
    assert rec.next_call_index("r1") == 1  # bookkeeping cleared
